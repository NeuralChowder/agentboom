"""Email-sync mini-app — the collection engine (agentboom package: email).

Polls every enabled mailbox, caches new messages in SQLite, applies
ignore-filters (with receipts — a filter that drops silently is
indistinguishable from a bug), and publishes `email.received` for every
message that survives. Downstream packages (email-actions, email-search)
subscribe to that event.

Endpoints (mounted at /api/email-sync/):
  GET    /health
  POST   /sync                 run a sync now (manifest job target)
  GET    /emails?account=&since=&limit=    cached mail, newest first
  GET    /emails/{email_id}    one cached message (with body)
  GET    /stats
  GET    /filters              ignore rules
  POST   /filters              {name, match_from?, match_subject?}
  DELETE /filters/{filter_id}
  GET    /filters/skipped      the receipts
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db, events
from connectors.email import EmailError, fetch_new, vault_password

log = logging.getLogger("miniapps.email-sync")

router = APIRouter()

_FIRST_SYNC_DAYS = 14   # how far back the very first sync reaches
_SYNC_BATCH = 50        # messages per mailbox per pass


@router.get("/health")
async def health():
    accounts = await db.fetchval(
        "SELECT count(*) FROM email_accounts WHERE enabled = 1")
    return {"status": "ok", "app": "email-sync", "enabled_accounts": accounts}


def _matches_filters(row_filters, from_email: str, subject: str):
    from_email = (from_email or "").lower()
    subject = (subject or "").lower()
    for f in row_filters:
        hit_from = f["match_from"] and f["match_from"].lower() in from_email
        hit_subject = f["match_subject"] and f["match_subject"].lower() in subject
        if hit_from or hit_subject:
            return f
    return None


@router.post("/sync")
async def sync_now():
    """Manifest job target: collect from every enabled mailbox."""
    accounts = await db.fetchall(
        "SELECT * FROM email_accounts WHERE enabled = 1 ORDER BY id")
    filters = await db.fetchall(
        "SELECT * FROM email_filters WHERE enabled = 1")
    total_new = total_skipped = 0
    errors = []

    for account in accounts:
        account = dict(account)
        since = None
        if not account["last_sync_at"]:
            since = (datetime.now(timezone.utc)
                     - timedelta(days=_FIRST_SYNC_DAYS)).strftime("%d-%b-%Y")
        try:
            password = await vault_password(account["email"])
            messages = await fetch_new(
                account["imap_host"], account["imap_port"],
                account["email"], password,
                folder="INBOX", since=since, limit=_SYNC_BATCH)
        except EmailError as exc:
            errors.append({"account": account["email"], "error": str(exc)[:200]})
            await db.execute(
                "UPDATE email_accounts SET last_sync_at = CURRENT_TIMESTAMP, "
                "last_error = ? WHERE id = ?",
                (str(exc)[:200], account["id"]))
            continue

        new_here = 0
        for msg in messages:
            matched = _matches_filters(filters, msg["from_email"], msg["subject"])
            if matched:
                await db.execute(
                    "INSERT INTO email_filter_log "
                    "(filter_name, account_email, from_email, subject) "
                    "VALUES (?, ?, ?, ?)",
                    (matched["name"], account["email"],
                     msg["from_email"], msg["subject"]))
                total_skipped += 1
                continue
            inserted = await db.execute(
                "INSERT OR IGNORE INTO emails "
                "(account_id, folder, uid, message_id, from_email, from_name, "
                " subject, received_at, has_attachment, body_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account["id"], msg["folder"], msg["uid"], msg["message_id"],
                 msg["from_email"], msg["from_name"], msg["subject"],
                 msg["received_at"], msg["has_attachment"], msg["body_text"]))
            if inserted:
                row = await db.fetchone(
                    "SELECT id FROM emails WHERE account_id = ? AND folder = ? "
                    "AND uid = ?",
                    (account["id"], msg["folder"], msg["uid"]))
                new_here += 1
                await events.publish("email.received", {
                    "email_id": row["id"] if row else None,
                    "account_email": account["email"],
                    "from_email": msg["from_email"],
                    "from_name": msg["from_name"],
                    "subject": msg["subject"],
                    "has_attachment": bool(msg["has_attachment"]),
                })
        await db.execute(
            "UPDATE email_accounts SET last_sync_at = CURRENT_TIMESTAMP, "
            "last_error = NULL WHERE id = ?", (account["id"],))
        total_new += new_here

    log.info("email-sync: %d account(s), %d new, %d filtered, %d error(s)",
             len(accounts), total_new, total_skipped, len(errors))
    return {"ok": True, "accounts": len(accounts), "new": total_new,
            "filtered": total_skipped, "errors": errors}


@router.get("/emails")
async def list_emails(account: str = "", since: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 200))
    where, params = ["1=1"], []
    if account:
        where.append("a.email = ?")
        params.append(account.strip().lower())
    if since:
        where.append("e.received_at > ?")
        params.append(since)
    rows = await db.fetchall(
        f"""
        SELECT e.id, a.email AS account_email, e.from_email, e.from_name,
               e.subject, e.received_at, e.has_attachment, e.folder, e.uid
        FROM emails e JOIN email_accounts a ON a.id = e.account_id
        WHERE {' AND '.join(where)}
        ORDER BY e.received_at DESC, e.id DESC
        LIMIT ?
        """,
        (*params, limit))
    return {"emails": rows}


@router.get("/emails/{email_id}")
async def one_email(email_id: int):
    row = await db.fetchone(
        "SELECT e.*, a.email AS account_email FROM emails e "
        "JOIN email_accounts a ON a.id = e.account_id WHERE e.id = ?",
        email_id)
    if not row:
        return JSONResponse({"error": "no such email"}, status_code=404)
    return dict(row)


@router.get("/stats")
async def stats():
    accounts = await db.fetchval("SELECT count(*) FROM email_accounts")
    messages = await db.fetchval("SELECT count(*) FROM emails")
    skipped = await db.fetchval("SELECT count(*) FROM email_filter_log")
    last = await db.fetchval(
        "SELECT max(last_sync_at) FROM email_accounts WHERE enabled = 1")
    return {"accounts": accounts, "cached_emails": messages,
            "filtered_out": skipped, "last_sync_at": last}


@router.get("/filters")
async def list_filters():
    rows = await db.fetchall("SELECT * FROM email_filters ORDER BY id")
    return {"filters": rows}


@router.post("/filters")
async def add_filter(payload: dict):
    name = (payload.get("name") or "").strip()
    match_from = (payload.get("match_from") or "").strip()
    match_subject = (payload.get("match_subject") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not match_from and not match_subject:
        return JSONResponse(
            {"error": "give match_from and/or match_subject"}, status_code=400)
    existing = await db.fetchone(
        "SELECT id FROM email_filters WHERE name = ?", name)
    if existing:
        return JSONResponse({"error": "filter exists"}, status_code=409)
    await db.execute(
        "INSERT INTO email_filters (name, match_from, match_subject) "
        "VALUES (?, ?, ?)",
        (name, match_from or None, match_subject or None))
    return {"ok": True, "name": name,
            "note": "matching mail is skipped at sync time and recorded "
                    "in /filters/skipped"}


@router.delete("/filters/{filter_id}")
async def remove_filter(filter_id: int):
    removed = await db.execute(
        "DELETE FROM email_filters WHERE id = ?", filter_id)
    if not removed:
        return JSONResponse({"error": "no such filter"}, status_code=404)
    return {"deleted": True}


@router.get("/filters/skipped")
async def skipped(limit: int = 100):
    limit = max(1, min(int(limit), 1000))
    rows = await db.fetchall(
        "SELECT filter_name, account_email, from_email, subject, skipped_at "
        "FROM email_filter_log ORDER BY skipped_at DESC, id DESC LIMIT ?",
        limit)
    return {"skipped": rows}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
