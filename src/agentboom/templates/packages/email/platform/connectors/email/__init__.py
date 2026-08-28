"""Email connector — IMAP read + SMTP send (agentboom package: email).

Stdlib-only (imaplib/smtplib/email run in worker threads), so the
package adds no pip dependencies. Mailbox passwords are read from the
vault mini-app at call time — never stored anywhere else, never logged.

Mini-app usage:

    from connectors.email import send_mail, fetch_new, vault_password

    password = await vault_password("you@example.com")
    messages = await fetch_new(host="imap.gmail.com", port=993,
                               user="you@example.com", password=password)
    await send_mail(smtp_host="smtp.gmail.com", smtp_port=587,
                    user="you@example.com", password=password,
                    to=["them@example.com"], subject="Re: ...", body="...")

Env:
  PLATFORM_INTERNAL_URL  where the vault answers (default
                         http://127.0.0.1:8000) — same var the scheduler
                         uses, so there is nothing new to configure.
"""
from __future__ import annotations

import asyncio
import email as emaillib
import email.utils
import imaplib
import logging
import os
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Dict, List, Optional

import httpx

from . import templates  # noqa: F401 — template engine for the send path

log = logging.getLogger("connectors.email")

PLATFORM_INTERNAL_URL = os.environ.get(
    "PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")
_TIMEOUT = float(os.environ.get("EMAIL_TIMEOUT_SEC", "30"))

# Provider presets — host/port defaults per provider. 'imap' means
# "bring your own hosts". gmail needs an APP password (2-step enabled).
PROVIDERS: Dict[str, dict] = {
    "gmail": {
        "imap": {"host": "imap.gmail.com", "port": 993},
        "smtp": {"host": "smtp.gmail.com", "port": 587},
        "note": "use an app password, not the account password",
    },
    "outlook": {
        "imap": {"host": "outlook.office365.com", "port": 993},
        "smtp": {"host": "smtp.office365.com", "port": 587},
        "note": "app password or OAuth may be required by Microsoft policy",
    },
    "privateemail": {
        "imap": {"host": "imap.privateemail.com", "port": 993},
        "smtp": {"host": "smtp.privateemail.com", "port": 587},
        "note": "",
    },
    "fastmail": {
        "imap": {"host": "imap.fastmail.com", "port": 993},
        "smtp": {"host": "smtp.fastmail.com", "port": 587},
        "note": "use an app password",
    },
    "imap": {
        "imap": {"host": "", "port": 993},
        "smtp": {"host": "", "port": 587},
        "note": "custom IMAP — provide both hosts",
    },
}


class EmailError(RuntimeError):
    """An email operation failed."""


def provider_preset(provider: str) -> dict:
    preset = PROVIDERS.get(provider)
    if preset is None:
        raise EmailError(
            f"unknown provider '{provider}' — one of: {', '.join(PROVIDERS)}")
    return preset


def vault_service_name(address: str) -> str:
    """Vault convention: one credential per mailbox, named email:<address>."""
    return f"email:{address.strip().lower()}"


async def vault_password(address: str) -> str:
    """Fetch the mailbox password from the vault (audit-logged there)."""
    url = (f"{PLATFORM_INTERNAL_URL}/api/vault/credentials/"
           f"{vault_service_name(address)}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise EmailError(f"vault unreachable at {url}: {exc}") from exc
    if resp.status_code != 200:
        raise EmailError(
            f"no vault credential for {address} (HTTP {resp.status_code}) — "
            "store it first via the vault mini-app"
        )
    return resp.json()["secret"]


async def store_vault_password(address: str, password: str) -> None:
    url = (f"{PLATFORM_INTERNAL_URL}/api/vault/credentials/"
           f"{vault_service_name(address)}")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.put(url, json={"secret": password,
                                           "note": "mailbox password (email package)"})
    if resp.status_code >= 400:
        raise EmailError(f"vault refused the credential: HTTP {resp.status_code}")


# ── IMAP (blocking calls, worker thread) ──────────────────────────


def _decode(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — headers are hostile in the wild
        return str(value)


def _body_text(msg) -> str:
    """Plain-text body (falls back to a stripped HTML part)."""
    try:
        text = msg.get_body(preferencelist=("plain", "html"))
        if text is None:
            return ""
        content = text.get_content()
        if text.get_content_type() == "text/html":
            # Crude but dependency-free: drop tags for a readable gist.
            import re
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content)
        return content.strip()[:20000]
    except Exception:  # noqa: BLE001
        return ""


def _has_attachment(msg) -> bool:
    try:
        return any(part.get_content_disposition() == "attachment"
                   for part in msg.iter_attachments())
    except Exception:  # noqa: BLE001
        return False


def _parse(uid: str, raw: bytes, folder: str) -> dict:
    msg = emaillib.message_from_bytes(raw)
    from_name, from_email = email.utils.parseaddr(_decode(msg.get("From")))
    return {
        "folder": folder,
        "uid": uid,
        "message_id": _decode(msg.get("Message-ID")),
        "from_email": from_email.strip().lower(),
        "from_name": from_name.strip(),
        "subject": _decode(msg.get("Subject"))[:500],
        "received_at": email.utils.parsedate_to_datetime(
            msg.get("Date") or "").astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if msg.get("Date") else None,
        "has_attachment": 1 if _has_attachment(msg) else 0,
        # Threading metadata (RFC 5322). IMAP has no provider-level thread
        # notion, so these headers are the generic basis for grouping a
        # conversation. Empty when the message starts its own thread.
        "in_reply_to": _decode(msg.get("In-Reply-To")),
        "references": _decode(msg.get("References"))[:1000],
        # The receiving MX's own SPF/DKIM/DMARC verdict, stored raw at
        # ingest: consumers (sender_check, mfa-relay) read the same stored
        # evidence instead of re-deriving it. Multiple lines when several
        # hops recorded their own.
        "auth_results": "\n".join(
            _decode(v) for v in msg.get_all("Authentication-Results") or []
        ) or None,
        "body_text": _body_text(msg),
    }


def _imap_connect(host: str, port: int, user: str, password: str):
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        return conn
    except imaplib.IMAP4.error as exc:
        raise EmailError(f"IMAP sign-in failed for {user}@{host}:{port}: {exc}")
    except OSError as exc:
        raise EmailError(f"IMAP unreachable {host}:{port}: {exc}")


def _imap_test(host: str, port: int, user: str, password: str) -> int:
    conn = _imap_connect(host, port, user, password)
    try:
        _typ, data = conn.select("INBOX", readonly=True)
        return int(data[0]) if data and data[0] else 0
    finally:
        conn.logout()


def _imap_fetch_new(host: str, port: int, user: str, password: str,
                    folder: str, since: Optional[str], limit: int) -> List[dict]:
    """Fetch messages; `since` is a 'YYYY-MM-DD' cutoff (server-side SINCE)."""
    conn = _imap_connect(host, port, user, password)
    out: List[dict] = []
    try:
        typ, _data = conn.select(folder, readonly=True)
        if typ != "OK":
            raise EmailError(f"cannot open folder {folder} on {host}")
        criteria = ["UNSEEN"] if not since else ["SINCE", since]
        typ, data = conn.uid("search", None, *criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-limit:]  # newest last; take the tail
        for uid in uids:
            typ, parts = conn.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not parts or parts[0] is None:
                continue
            raw = parts[0][1]
            try:
                out.append(_parse(uid.decode(), raw, folder))
            except Exception:  # noqa: BLE001 — one bad message never kills a sync
                log.warning("email: unparseable message uid=%s on %s", uid, host)
        return out
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


async def test_imap(host: str, port: int, user: str, password: str) -> int:
    """Sign in and open INBOX. Returns the message count."""
    return await asyncio.to_thread(_imap_test, host, port, user, password)


async def fetch_new(host: str, port: int, user: str, password: str,
                    folder: str = "INBOX", since: Optional[str] = None,
                    limit: int = 50) -> List[dict]:
    """Fetch new(ish) messages from one folder, parsed to dicts."""
    return await asyncio.to_thread(
        _imap_fetch_new, host, port, user, password, folder, since, limit)


# ── SMTP ───────────────────────────────────────────────────────────


def _smtp_send(smtp_host: str, smtp_port: int, user: str, password: str,
               to: List[str], subject: str, body: str,
               html: Optional[str] = None) -> None:
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=_TIMEOUT) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass  # some servers are plaintext on 465/2525-style setups
            server.login(user, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(f"SMTP auth failed for {user}@{smtp_host}: {exc}")
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"SMTP send failed via {smtp_host}:{smtp_port}: {exc}")


async def send_mail(smtp_host: str, smtp_port: int, user: str, password: str,
                    to: List[str], subject: str, body: str,
                    html: Optional[str] = None,
                    account_email: Optional[str] = None) -> None:
    """Send one message. Raises EmailError on any failure.

    The single send path: the HTML part is automatically wrapped in the
    mailbox's active template (or the built-in default) via the template
    engine. The plain-text `body` is always sent untouched. Pass your own
    `html` for the message body to control the inner markup; the wrapper
    still applies around it. Set account_email to template against a
    specific mailbox (defaults to `user`).
    """
    if not to:
        raise EmailError("send_mail needs at least one recipient")
    final_html = await templates.render(body, account_email or user, html)
    await asyncio.to_thread(
        _smtp_send, smtp_host, smtp_port, user, password, to, subject, body, final_html)
    log.info("email: sent '%s' to %s via %s", subject[:60], ", ".join(to), smtp_host)


async def send_for_account(account: dict, to: List[str], subject: str,
                           body: str, html: Optional[str] = None) -> None:
    """Send using a stored account row (dict with email/smtp_host/smtp_port);
    the password comes from the vault just-in-time. The message is wrapped
    in that mailbox's active template automatically."""
    if not account.get("smtp_host"):
        raise EmailError(f"account {account.get('email')} has no SMTP host — "
                         "edit the mailbox to add one")
    password = await vault_password(account["email"])
    await send_mail(account["smtp_host"], account.get("smtp_port") or 587,
                    account["email"], password, to, subject, body, html,
                    account_email=account["email"])
