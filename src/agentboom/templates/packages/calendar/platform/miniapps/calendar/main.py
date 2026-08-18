"""Calendar mini-app — CalDAV accounts + agenda
(agentboom package: calendar).

Accounts are added through the API (multiple calendars welcome); the
password goes to the vault and the account is fetched BEFORE it is
saved. A 30-minute sync keeps the coming weeks of events cached and
queryable: today / upcoming / arbitrary range.

Endpoints (mounted at /api/calendar/):
  GET    /health
  GET    /providers
  GET    /accounts               POST /accounts
  PUT    /accounts/{id}          DELETE /accounts/{id}
  POST   /accounts/{id}/test
  POST   /sync                   (manifest job target)
  GET    /today
  GET    /upcoming?days=7
  GET    /events?from=YYYY-MM-DD&to=YYYY-MM-DD&account=
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from connectors.caldav import PROVIDERS, CalDavError, fetch_events

log = logging.getLogger("miniapps.calendar")

router = APIRouter()

SYNC_DAYS = int(os.environ.get("CAL_SYNC_DAYS", "30"))
_PLATFORM_INTERNAL_URL = os.environ.get(
    "PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")


def _vault_service(account_id) -> str:
    return f"calendar:{account_id}"


# The vault is reached over its HTTP API — same pattern the email
# connector uses, kept local so calendar works without email installed.
async def _vault_put(service: str, password: str) -> None:
    url = f"{_PLATFORM_INTERNAL_URL}/api/vault/credentials/{service}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json={
            "secret": password, "note": "CalDAV password (calendar package)"})
    if resp.status_code >= 400:
        raise CalDavError(f"vault refused the credential: HTTP {resp.status_code}")


async def _vault_get(service: str) -> str:
    url = f"{_PLATFORM_INTERNAL_URL}/api/vault/credentials/{service}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise CalDavError(
            f"no vault credential '{service}' (HTTP {resp.status_code})")
    return resp.json()["secret"]


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in
            ("id", "label", "username", "provider", "caldav_url", "enabled",
             "last_sync_at", "last_error", "created_at")}


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── accounts ───────────────────────────────────────────────────────


@router.get("/health")
async def health():
    count = await db.fetchval(
        "SELECT count(*) FROM cal_accounts WHERE enabled = 1")
    return {"status": "ok", "app": "calendar", "accounts": count,
            "sync_days": SYNC_DAYS}


@router.get("/providers")
async def providers():
    return {"providers": PROVIDERS}


@router.get("/accounts")
async def list_accounts():
    rows = await db.fetchall("SELECT * FROM cal_accounts ORDER BY id")
    return {"accounts": [_public(dict(r)) for r in rows]}


@router.post("/accounts")
async def add_account(payload: dict):
    label = (payload.get("label") or "").strip().lower()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    provider = (payload.get("provider") or "caldav").strip().lower()
    caldav_url = (payload.get("caldav_url") or "").strip()
    if not label or not username:
        return JSONResponse({"error": "label and username are required"},
                            status_code=400)
    if provider not in PROVIDERS:
        return JSONResponse(
            {"error": f"unknown provider — one of: {', '.join(PROVIDERS)}"},
            status_code=400)
    if not caldav_url:
        note = PROVIDERS[provider]["note"]
        return JSONResponse({"error": f"caldav_url is required — {note}"},
                            status_code=400)
    if not password:
        return JSONResponse({"error": "password is required"}, status_code=400)
    if await db.fetchone("SELECT id FROM cal_accounts WHERE label = ?", label):
        return JSONResponse({"error": "label exists"}, status_code=409)

    # Verify BEFORE storing anything: bad credentials bounce at add-time.
    try:
        events = await fetch_events(caldav_url, username, password, days=7)
    except CalDavError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await db.execute(
        "INSERT INTO cal_accounts (label, username, provider, caldav_url, "
        "last_sync_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (label, username, provider, caldav_url))
    row = await db.fetchone("SELECT id FROM cal_accounts WHERE label = ?", label)
    try:
        await _vault_put(_vault_service(row["id"]), password)
    except CalDavError as exc:
        # Never keep an account whose secret cannot be vaulted.
        await db.execute("DELETE FROM cal_accounts WHERE id = ?", row["id"])
        return JSONResponse({"error": str(exc)}, status_code=502)
    for event in events:
        await db.execute(
            "INSERT INTO cal_events "
            "(account_id, uid, summary, start_at, end_at, all_day, location, "
            " description) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (row["id"], event["uid"], event["summary"], event["start"],
             event["end"], event["all_day"], event["location"] or None,
             event["description"] or None))
    log.info("calendar: account '%s' added (%d events seeded)",
             label, len(events))
    return {"ok": True, "account": _public(dict(row)),
            "note": f"connected — {len(events)} events in the next 7 days"}


@router.put("/accounts/{account_id}")
async def update_account(account_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM cal_accounts WHERE id = ?", account_id)
    if not row:
        return JSONResponse({"error": "no such account"}, status_code=404)
    if "enabled" in payload:
        await db.execute("UPDATE cal_accounts SET enabled = ? WHERE id = ?",
                         (1 if payload.get("enabled") else 0, account_id))
    if payload.get("password"):
        try:
            await fetch_events(row["caldav_url"], row["username"],
                               payload["password"], days=1)
        except CalDavError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await _vault_put(_vault_service(account_id), payload["password"])
    if payload.get("caldav_url"):
        await db.execute(
            "UPDATE cal_accounts SET caldav_url = ? WHERE id = ?",
            (str(payload["caldav_url"]).strip(), account_id))
    return {"ok": True}


@router.delete("/accounts/{account_id}")
async def remove_account(account_id: int):
    removed = await db.execute(
        "DELETE FROM cal_accounts WHERE id = ?", account_id)
    if not removed:
        return JSONResponse({"error": "no such account"}, status_code=404)
    return {"deleted": True,
            "note": "cached events were removed; delete the vault "
                    "credential separately if you want it gone too"}


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: int):
    row = await db.fetchone(
        "SELECT * FROM cal_accounts WHERE id = ?", account_id)
    if not row:
        return JSONResponse({"error": "no such account"}, status_code=404)
    try:
        password = await _vault_get(_vault_service(account_id))
        events = await fetch_events(row["caldav_url"], row["username"],
                                    password, days=7)
    except CalDavError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    return {"ok": True, "events_next_7_days": len(events)}


# ── sync (manifest job target) ─────────────────────────────────────


@router.post("/sync")
async def sync_all():
    accounts = await db.fetchall(
        "SELECT * FROM cal_accounts WHERE enabled = 1 ORDER BY id")
    total, errors = 0, []
    for account in accounts:
        account = dict(account)
        try:
            password = await _vault_get(_vault_service(account["id"]))
            events = await fetch_events(account["caldav_url"],
                                        account["username"], password,
                                        days=SYNC_DAYS)
        except CalDavError as exc:
            errors.append({"account": account["label"],
                           "error": str(exc)[:200]})
            await db.execute(
                "UPDATE cal_accounts SET last_sync_at = CURRENT_TIMESTAMP, "
                "last_error = ? WHERE id = ?",
                (str(exc)[:200], account["id"]))
            continue
        for event in events:
            # Portable upsert (SQLite 3.24+ and PostgreSQL both speak it).
            await db.execute(
                "INSERT INTO cal_events "
                "(account_id, uid, summary, start_at, end_at, all_day, "
                " location, description, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(account_id, uid) DO UPDATE SET "
                "summary = EXCLUDED.summary, start_at = EXCLUDED.start_at, "
                "end_at = EXCLUDED.end_at, all_day = EXCLUDED.all_day, "
                "location = EXCLUDED.location, "
                "description = EXCLUDED.description, "
                "synced_at = EXCLUDED.synced_at",
                (account["id"], event["uid"], event["summary"],
                 event["start"], event["end"], event["all_day"],
                 event["location"] or None, event["description"] or None))
        await db.execute(
            "UPDATE cal_accounts SET last_sync_at = CURRENT_TIMESTAMP, "
            "last_error = NULL WHERE id = ?", (account["id"],))
        total += len(events)
    log.info("calendar: synced %d account(s), %d event(s), %d error(s)",
             len(accounts), total, len(errors))
    return {"ok": True, "accounts": len(accounts), "events": total,
            "errors": errors}


# ── agenda ─────────────────────────────────────────────────────────


async def _events_between(start: datetime, end: datetime, account: str = ""):
    where = ["e.start_at IS NOT NULL",
             "e.start_at < ?",
             "COALESCE(e.end_at, e.start_at) >= ?"]
    params = [_fmt(end), _fmt(start)]
    if account:
        where.append("a.label = ?")
        params.append(account.strip().lower())
    return await db.fetchall(
        f"""
        SELECT e.id, a.label AS account, e.uid, e.summary, e.start_at,
               e.end_at, e.all_day, e.location
        FROM cal_events e JOIN cal_accounts a ON a.id = e.account_id
        WHERE {' AND '.join(where)}
        ORDER BY e.start_at
        LIMIT 500
        """,
        (*params,))


@router.get("/today")
async def agenda_today():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await _events_between(start, start + timedelta(days=1))
    return {"date": start.strftime("%Y-%m-%d"), "events": rows}


@router.get("/upcoming")
async def upcoming(days: int = 7):
    days = max(1, min(int(days), 90))
    now = datetime.now(timezone.utc)
    rows = await _events_between(now, now + timedelta(days=days))
    return {"days": days, "events": rows}


@router.get("/events")
async def events_range(from_: str = Query("", alias="from"),
                       to: str = "", account: str = ""):
    try:
        start = (datetime.strptime(from_, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                 if from_ else datetime.now(timezone.utc))
        end = (datetime.strptime(to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
               + timedelta(days=1) if to else start + timedelta(days=30))
    except ValueError:
        return JSONResponse(
            {"error": "from/to must be YYYY-MM-DD"}, status_code=400)
    rows = await _events_between(start, end, account)
    return {"from": start.strftime("%Y-%m-%d"),
            "to": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "events": rows}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
