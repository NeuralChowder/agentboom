"""MFA relay — time-critical verification codes, forwarded at receive
(agentboom package: mfa-relay).

A verification code lives for minutes, but the agent queue where triage
normally runs has longer waits. No queue priority outruns a five-minute
expiry, so the code is forwarded *at receive*, deterministically, with no
LLM turn in the path: a durable `email.received` subscription, an exact
sender match, a subject match, a regex, one send.

The sender check is load-bearing, not decoration: this app hands a
credential-shaped value to a third party. Forwarding requires the address
to be exact AND the stored SPF/DKIM/DMARC verdict (sender_check, evidence
captured at ingest) to pass. Everything else is withheld, recorded and
explained — a withheld code is a minor annoyance that can be forwarded by
hand; a forwarded phishing code is not.

Everything configurable is a runtime setting — the dashboard form and a
conversation both edit the same row, and the handler reads the live value
per message, so a rule change applies to the next mail. The package is
OFF and inert until configured: with no sender/recipient set it simply
ignores every message.
"""
import asyncio
import json
import logging
import re
import unicodedata
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agentboom_sdk import db
from agentboom_sdk import durable_events
from agentboom_sdk import sender_check as sc
from agentboom_sdk.voice import sign as sign_off

log = logging.getLogger("miniapps.mfa-relay")

router = APIRouter()

STALE_IN_FLIGHT_SEC = 300  # a processing row older than this: gateway died mid-send

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "sender_email": "",
    "subject_contains": "",
    "account_email": "",
    "recipient": "",
    "require_auth_pass": True,
    "notify_user": True,
    "forward_subject": "Verification code",
    "forward_template": (
        "A service sent a verification code to this mailbox.\n\n"
        "Code: {code}\n\n"
        "If you were expecting this login, you can use the code above to "
        "complete it; otherwise please ignore this message."
    ),
}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# Dates and times first, so "23/08/2026 09:15" is never mistaken for a code.
_CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")

SUBSCRIPTION = {"app_name": "mfa-relay", "event_type": "email.received",
                "endpoint": "/api/mfa-relay/on-email"}


# ── settings ────────────────────────────────────────────────────────────────

async def _get_settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for row in await db.fetchall(
            "SELECT key, value FROM mfa_relay_settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            out[row["key"]] = row["value"]
    return out


def _as_bool(value: Union[bool, str, None], name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "1", "on", "yes"):
            return True
        if value.lower() in ("false", "0", "off", "no"):
            return False
    raise ValueError(f"{name} must be a boolean")


def _configured(settings: Dict[str, Any]) -> bool:
    return bool(str(settings.get("sender_email") or "").strip()
                and str(settings.get("recipient") or "").strip())


class RelaySettingsUpdate(BaseModel):
    enabled: Optional[Union[bool, str]] = None
    require_auth_pass: Optional[Union[bool, str]] = None
    notify_user: Optional[Union[bool, str]] = None
    sender_email: Optional[str] = None
    subject_contains: Optional[str] = None
    account_email: Optional[str] = None
    recipient: Optional[str] = None
    forward_subject: Optional[str] = None
    forward_template: Optional[str] = None


# ── subscription ────────────────────────────────────────────────────────────

async def _ensure_subscription() -> bool:
    """Register the durable email.received subscription (idempotent).

    Returns False when the events package is not installed — the app still
    works via /test, it just does not react to live mail.
    """
    try:
        await durable_events.register_subscription(**SUBSCRIPTION)
        return True
    except Exception as exc:
        log.warning("mfa-relay: could not register durable subscription "
                    "(is the events package installed?): %s", exc)
        return False


def _schedule_subscription() -> None:
    """Best-effort registration from the (sync) load hook."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_ensure_subscription())


# ── pipeline ────────────────────────────────────────────────────────────────

def _fold(text: Optional[str]) -> str:
    """Case- and accent-folded, so 'Codigo de verificacao' matches a
    configured 'código de verificação' whichever way the sender spells it."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _extract_code(body: Optional[str]) -> Optional[str]:
    text = re.sub(r"\b\d{1,2}[:hH]\d{2}\b", " ", body or "")
    text = re.sub(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", " ", text)
    match = _CODE_RE.search(text)
    return match.group(1) if match else None


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _claim(email_id: int, account_email: str, from_email: str,
                 recipient: str) -> Optional[int]:
    """Atomically claim this message. One statement, so two concurrent
    on-email calls for the same message can never both win.

    A fresh insert wins; the upsert only supersedes a dry-run row or a STALE
    in-flight row (the gateway died mid-send). A final outcome or a LIVE
    in-flight claim returns nothing."""
    now = _now()
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc)
             - timedelta(seconds=STALE_IN_FLIGHT_SEC)).replace(
        microsecond=0).isoformat()
    row = await db.fetchone(
        """INSERT INTO mfa_relay_forwards
             (email_id, account_email, from_email, recipient, action,
              reason, created_at, updated_at)
           VALUES ($1, $2, $3, $4, 'processing', 'in flight', $5, $5)
           ON CONFLICT (email_id) DO UPDATE
             SET account_email = $2, from_email = $3, recipient = $4,
                 action = 'processing', reason = 'in flight (reclaimed)',
                 updated_at = $5
             WHERE mfa_relay_forwards.action = 'dry-run'
                OR (mfa_relay_forwards.action = 'processing'
                    AND mfa_relay_forwards.updated_at < $6)
           RETURNING id""",
        email_id, account_email, from_email, recipient, now, stale)
    return int(row["id"]) if row else None


async def _record(email_id: int, *, account_email: str, from_email: str,
                  code: Optional[str], recipient: str, action: str,
                  reason: str, dry_run: bool = False) -> Dict[str, Any]:
    """One row per message, ever — the idempotency anchor for at-least-once
    event delivery and for /test replays.

    A final outcome (forwarded, withheld, ignored, expired) is never
    overwritten: the only states a write may supersede are an in-flight
    claim and another dry-run."""
    final_action = "dry-run" if dry_run else action
    row = await db.fetchone(
        """INSERT INTO mfa_relay_forwards
             (email_id, account_email, from_email, code, recipient, action,
              reason, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
           ON CONFLICT (email_id) DO UPDATE
             SET code = $4, action = $6, reason = $7, updated_at = $8
             WHERE mfa_relay_forwards.action IN ('processing', 'dry-run')
           RETURNING id""",
        email_id, account_email, from_email, code, recipient, final_action,
        reason, _now())
    if row:
        return {"action": final_action, "reason": reason, "code": code}
    existing = await db.fetchone(
        "SELECT action, reason FROM mfa_relay_forwards WHERE email_id = $1",
        email_id)
    log.warning("mfa-relay: _record left existing '%s' row for %s untouched",
                existing["action"] if existing else "?", email_id)
    return {"action": final_action,
            "reason": reason + (" — existing record left untouched"
                                if existing else ""),
            "code": code}


async def _notify_user(settings: Dict[str, Any], message: str) -> None:
    """Best-effort push: ntfy when the package is installed and configured;
    otherwise just the log (the forwards row already says the truth)."""
    if not settings.get("notify_user"):
        return
    try:
        from connectors.ntfy import enabled, send
        if enabled():
            await send(message, title="MFA relay", priority=4)
    except Exception:
        log.warning("mfa-relay: user notification failed", exc_info=True)


def _forward_text(code: str, settings: Dict[str, Any]) -> str:
    """The forwarded message: the configured template with {code} filled in,
    closed with the assistant's own signature — never the user's name."""
    try:
        text = str(settings.get("forward_template") or "").format(code=code)
    except (KeyError, IndexError, ValueError):
        text = f"Verification code: {code}"
    return sign_off(text, None, automated=True)


async def _relay(email_id: int, email_row: dict, payload: Dict[str, Any],
                 settings: Dict[str, Any], *,
                 dry_run: bool = False) -> Dict[str, Any]:
    """The whole pipeline for one message. Never raises: an error here would
    retry the delivery forever, and the row in `forwards` says the truth
    either way."""
    recipient = str(settings["recipient"])
    from_email = (email_row.get("from_email") or "").lower()
    from_name = email_row.get("from_name") or ""
    account_email = str(payload.get("account_email") or "")

    auth = sc.build_sender_auth(*str(email_row.get("auth_results") or "").splitlines())
    v = sc.verdict(
        expected_sender=str(settings["sender_email"]),
        from_header=f"{from_name} <{from_email}>".strip(),
        from_email=from_email,
        auth=auth)

    if v["status"] == "mismatch":
        return await _record(email_id, account_email=account_email,
                             from_email=from_email, code=None,
                             recipient=recipient, action="withheld",
                             reason="sender mismatch: " + "; ".join(v["reasons"]),
                             dry_run=dry_run)
    if v["status"] == "suspicious":
        result = await _record(
            email_id, account_email=account_email, from_email=from_email,
            code=None, recipient=recipient, action="withheld",
            reason="authentication FAILED — possible impersonation: "
                   + "; ".join(v["reasons"]),
            dry_run=dry_run)
        if not dry_run:
            await _notify_user(
                settings,
                f"⚠️ MFA relay: a message posing as "
                f"{settings['sender_email']} FAILED authentication "
                f"(SPF/DKIM/DMARC). It was NOT forwarded — possible "
                f"impersonation. Details in the MFA relay panel.")
        return result
    if settings.get("require_auth_pass") and v["status"] != "ok":
        result = await _record(
            email_id, account_email=account_email, from_email=from_email,
            code=None, recipient=recipient, action="withheld",
            reason="; ".join(v["reasons"]),
            dry_run=dry_run)
        if not dry_run:
            await _notify_user(
                settings,
                f"⚠️ MFA relay: a code from {settings['sender_email']} was "
                f"NOT forwarded — no SPF/DKIM/DMARC evidence for the "
                f"sender. Forward it by hand if you trust the source, or "
                f"adjust 'Require SPF/DKIM/DMARC pass' in the panel.")
        return result

    body = email_row.get("body_text")
    code = _extract_code(body)
    if not code:
        return await _record(email_id, account_email=account_email,
                             from_email=from_email, code=None,
                             recipient=recipient, action="withheld",
                             reason="subject matched but no code found in "
                                    "the body",
                             dry_run=dry_run)

    if dry_run:
        return await _record(email_id, account_email=account_email,
                             from_email=from_email, code=code,
                             recipient=recipient, action="dry-run",
                             reason="test: would forward code (dry run, "
                                    "nothing sent)",
                             dry_run=True)

    account = await db.fetchone(
        "SELECT * FROM email_accounts WHERE email = $1",
        str(settings.get("account_email") or account_email))
    if not account:
        return await _record(email_id, account_email=account_email,
                             from_email=from_email, code=code,
                             recipient=recipient, action="withheld",
                             reason="sending mailbox not configured — "
                                    "set 'account' in the relay settings")
    try:
        from connectors.email import send_for_account
        await send_for_account(
            account, [recipient],
            str(settings.get("forward_subject") or "Verification code"),
            _forward_text(code, settings))
    except Exception:
        log.warning("mfa-relay: send failed for %s", email_id, exc_info=True)
        return await _record(email_id, account_email=account_email,
                             from_email=from_email, code=code,
                             recipient=recipient, action="withheld",
                             reason="send failed — see logs",
                             dry_run=dry_run)

    result = await _record(email_id, account_email=account_email,
                           from_email=from_email, code=code,
                           recipient=recipient, action="forwarded",
                           reason="sender verified (%s)" % v["auth"])
    await _notify_user(
        settings,
        f"🔑 MFA relay: code forwarded to {recipient} — from {from_email}, "
        f"sender verified ({v['auth']}).")
    return result


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    settings = await _get_settings()
    forwarded = await db.fetchval(
        "SELECT count(*) FROM mfa_relay_forwards WHERE action = 'forwarded'")
    withheld = await db.fetchval(
        "SELECT count(*) FROM mfa_relay_forwards WHERE action = 'withheld'")
    return {"status": "ok", "app": "mfa-relay",
            "enabled": bool(settings.get("enabled")),
            "configured": _configured(settings),
            "forwarded": int(forwarded or 0), "withheld": int(withheld or 0)}


@router.post("/on-email")
async def on_email(event: dict):
    """The `email.received` subscriber (durable bus). Matches, verifies and
    forwards — or withholds and explains. Always answers 2xx: the event bus
    treats an error as "retry", and a code is not retryable into a
    different state."""
    await _ensure_subscription()
    payload = event.get("payload") or {}

    if payload.get("direction") and payload["direction"] != "in":
        return {"ok": True, "action": "ignored", "reason": "outbound mail"}

    settings = await _get_settings()
    if not settings.get("enabled"):
        return {"ok": True, "action": "ignored", "reason": "relay disabled"}
    if not _configured(settings):
        return {"ok": True, "action": "ignored",
                "reason": "relay not configured (sender/recipient empty)"}

    from_email = str(payload.get("from_email") or "").strip().lower()
    if from_email != str(settings["sender_email"]).strip().lower():
        return {"ok": True, "action": "ignored", "reason": "not the relay sender"}

    if _fold(settings["subject_contains"]) not in _fold(payload.get("subject")):
        return {"ok": True, "action": "ignored",
                "reason": "subject does not match"}

    email_id = payload.get("email_id")
    if not email_id:
        return {"ok": True, "action": "ignored", "reason": "no email_id in event"}

    email_row = await db.fetchone(
        "SELECT id, from_email, from_name, subject, body_text, "
        "auth_results FROM emails WHERE id = $1", email_id)
    if not email_row:
        return {"ok": True, "action": "ignored",
                "reason": "message not in the local cache"}

    claimed = await _claim(email_id, payload.get("account_email") or "",
                           from_email, str(settings["recipient"]))
    if not claimed:
        existing = await db.fetchone(
            "SELECT action, reason FROM mfa_relay_forwards WHERE email_id = $1",
            email_id)
        action = existing["action"] if existing else "ignored"
        if existing and existing["action"] == "processing":
            return {"ok": True, "action": "processing",
                    "reason": "already in flight — this redelivery is skipped"}
        return {"ok": True, "action": action,
                "reason": f"already handled: "
                          f"{existing['reason'] if existing else 'no row'}"}

    result = await _relay(email_id, email_row, payload, settings)
    return {"ok": True, **result}


@router.get("/forwards")
async def forwards(limit: int = Query(25, ge=1, le=200)):
    rows = await db.fetchall(
        "SELECT id, email_id, account_email, from_email, code, recipient, "
        "action, reason, created_at FROM mfa_relay_forwards "
        "ORDER BY created_at DESC, id DESC LIMIT $1", limit)
    return {"ok": True, "forwards": rows, "count": len(rows)}


class TestRequest(BaseModel):
    email_id: int


@router.post("/test")
async def test(req: TestRequest):
    """Replay the pipeline for a stored message WITHOUT sending. The only
    safe way to exercise the relay: a real send to a third party on a test
    would be an incident, not a test."""
    settings = await _get_settings()
    email_row = await db.fetchone(
        "SELECT e.id, a.email AS account_email, e.from_email, e.from_name, "
        "e.subject, e.body_text, e.auth_results "
        "FROM emails e LEFT JOIN email_accounts a ON a.id = e.account_id "
        "WHERE e.id = $1", req.email_id)
    if not email_row:
        raise HTTPException(404, "message not in the local cache")
    payload = {"email_id": req.email_id,
               "account_email": email_row["account_email"] or "",
               "from_email": email_row["from_email"],
               "subject": email_row["subject"],
               "direction": "in"}
    claimed = await _claim(req.email_id, email_row["account_email"] or "",
                           email_row["from_email"] or "",
                           str(settings["recipient"]))
    if not claimed:
        existing = await db.fetchone(
            "SELECT action, reason FROM mfa_relay_forwards WHERE email_id = $1",
            req.email_id)
        return {"ok": True,
                "action": existing["action"] if existing else "ignored",
                "reason": f"already handled: "
                          f"{existing['reason'] if existing else 'no row'} "
                          f"— dry run not performed, nothing sent"}
    result = await _relay(req.email_id, email_row, payload, settings,
                          dry_run=True)
    return {"ok": True, **result}


@router.get("/settings")
async def get_settings_route():
    settings = await _get_settings()
    settings["configured"] = _configured(settings)
    return settings


@router.put("/settings")
async def update_settings_route(req: RelaySettingsUpdate):
    changes: Dict[str, Any] = {}
    if req.enabled is not None:
        changes["enabled"] = _as_bool(req.enabled, "enabled")
    if req.require_auth_pass is not None:
        changes["require_auth_pass"] = _as_bool(req.require_auth_pass,
                                                "require_auth_pass")
    if req.notify_user is not None:
        changes["notify_user"] = _as_bool(req.notify_user, "notify_user")
    for field in ("sender_email", "account_email", "recipient"):
        value = getattr(req, field)
        if value is not None:
            if not _EMAIL_RE.match(value.strip()):
                raise HTTPException(400, f"{field} is not a valid address")
            changes[field] = value.strip().lower()
    if req.subject_contains is not None:
        if not req.subject_contains.strip() or len(req.subject_contains) > 200:
            raise HTTPException(400, "subject_contains must be 1-200 chars")
        changes["subject_contains"] = req.subject_contains.strip()
    if req.forward_subject is not None:
        if not req.forward_subject.strip() or len(req.forward_subject) > 200:
            raise HTTPException(400, "forward_subject must be 1-200 chars")
        changes["forward_subject"] = req.forward_subject.strip()
    if req.forward_template is not None:
        if not req.forward_template.strip():
            raise HTTPException(400, "forward_template must not be empty")
        if "{code}" not in req.forward_template:
            raise HTTPException(400,
                                 "forward_template must contain {code}")
        changes["forward_template"] = req.forward_template.strip()
    if not changes:
        raise HTTPException(400, "no recognised fields to update")
    for key, value in changes.items():
        await db.execute(
            "INSERT INTO mfa_relay_settings (key, value, updated_at) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3",
            key, json.dumps(value), _now())
    return {"ok": True, "settings": await get_settings_route()}


@router.post("/subscribe")
async def subscribe_route():
    """Register the durable email.received subscription (also done at load;
    this is the explicit form, e.g. after upgrading the events package)."""
    return {"ok": True, "subscribed": await _ensure_subscription()}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    _schedule_subscription()
    return router
