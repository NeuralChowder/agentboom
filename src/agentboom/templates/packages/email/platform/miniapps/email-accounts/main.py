"""Email-accounts mini-app — mailboxes the agent reads and sends from
(agentboom package: email).

Passwords are written to the vault (never stored here, never returned by
any endpoint) and a mailbox is signed into BEFORE it is saved — bad
credentials bounce at add-time, not at 3am during a sync.

Endpoints (mounted at /api/email-accounts/):
  GET    /health              vault reachability
  GET    /providers           provider presets
  GET    /accounts            configured mailboxes (no secrets)
  POST   /accounts            add + verify {email,label,provider,password,...}
  PUT    /accounts/{id}       rename / enable / replace password
  POST   /accounts/{id}/test  sign in now with the vault credential
  DELETE /accounts/{id}       stop collecting (cached mail is removed;
                              the real mail stays at the provider)
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from connectors.email import (
    EmailError, PROVIDERS, provider_preset, store_vault_password,
    test_imap, vault_password,
)

log = logging.getLogger("miniapps.email-accounts")

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "app": "email-accounts",
            "providers": sorted(PROVIDERS)}


@router.get("/providers")
async def providers():
    return {"providers": PROVIDERS}


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in
            ("id", "email", "label", "provider", "imap_host", "imap_port",
             "smtp_host", "smtp_port", "enabled", "last_sync_at",
             "last_error", "created_at")}


@router.get("/accounts")
async def list_accounts():
    rows = await db.fetchall("SELECT * FROM email_accounts ORDER BY id")
    return {"accounts": [_public(dict(r)) for r in rows]}


@router.post("/accounts")
async def add_account(payload: dict):
    email_addr = (payload.get("email") or "").strip().lower()
    label = (payload.get("label") or "").strip()
    provider = (payload.get("provider") or "imap").strip().lower()
    password = payload.get("password") or ""
    if not email_addr or "@" not in email_addr:
        return JSONResponse({"error": "a valid email is required"}, status_code=400)
    if not label:
        return JSONResponse({"error": "label is required"}, status_code=400)
    if not password:
        return JSONResponse({"error": "password is required (app password for gmail)"},
                            status_code=400)
    try:
        preset = provider_preset(provider)
    except EmailError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    imap_host = (payload.get("imap_host") or "").strip() or preset["imap"]["host"]
    imap_port = int(payload.get("imap_port") or preset["imap"]["port"])
    smtp_host = (payload.get("smtp_host") or "").strip() or preset["smtp"]["host"]
    smtp_port = int(payload.get("smtp_port") or preset["smtp"]["port"]) if smtp_host else None
    if not imap_host:
        return JSONResponse(
            {"error": "imap_host is required for provider 'imap'"}, status_code=400)

    existing = await db.fetchone(
        "SELECT id FROM email_accounts WHERE email = ?", email_addr)
    if existing:
        return JSONResponse({"error": "mailbox already configured"}, status_code=409)

    # Verify BEFORE storing anything: the mailbox is signed into first.
    try:
        count = await test_imap(imap_host, imap_port, email_addr, password)
    except EmailError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await store_vault_password(email_addr, password)
    await db.execute(
        "INSERT INTO email_accounts "
        "(email, label, provider, imap_host, imap_port, smtp_host, smtp_port) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email_addr, label, provider, imap_host, imap_port, smtp_host, smtp_port),
    )
    row = await db.fetchone(
        "SELECT * FROM email_accounts WHERE email = ?", email_addr)
    log.info("email: mailbox added %s (%s, %d messages in INBOX)",
             email_addr, provider, count)
    return {"ok": True, "account": _public(dict(row)),
            "note": f"signed in — INBOX has {count} messages"}


@router.put("/accounts/{account_id}")
async def update_account(account_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM email_accounts WHERE id = ?", account_id)
    if not row:
        return JSONResponse({"error": "no such mailbox"}, status_code=404)
    if "label" in payload:
        await db.execute("UPDATE email_accounts SET label = ? WHERE id = ?",
                         ((payload.get("label") or "").strip() or row["label"],
                          account_id))
    if "enabled" in payload:
        await db.execute("UPDATE email_accounts SET enabled = ? WHERE id = ?",
                         (1 if payload.get("enabled") else 0, account_id))
    if payload.get("password"):
        # Replace the credential: verify the new one first, then vault it.
        try:
            await test_imap(row["imap_host"], row["imap_port"],
                            row["email"], payload["password"])
        except EmailError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await store_vault_password(row["email"], payload["password"])
    updated = await db.fetchone(
        "SELECT * FROM email_accounts WHERE id = ?", account_id)
    return {"ok": True, "account": _public(dict(updated))}


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: int):
    row = await db.fetchone(
        "SELECT * FROM email_accounts WHERE id = ?", account_id)
    if not row:
        return JSONResponse({"error": "no such mailbox"}, status_code=404)
    try:
        password = await vault_password(row["email"])
        count = await test_imap(row["imap_host"], row["imap_port"],
                                row["email"], password)
    except EmailError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    return {"ok": True, "inbox_messages": count}


@router.delete("/accounts/{account_id}")
async def remove_account(account_id: int):
    row = await db.fetchone(
        "SELECT email FROM email_accounts WHERE id = ?", account_id)
    if not row:
        return JSONResponse({"error": "no such mailbox"}, status_code=404)
    await db.execute("DELETE FROM email_accounts WHERE id = ?", account_id)
    log.info("email: mailbox removed %s (cached mail dropped; provider "
             "keeps the real mail)", row["email"])
    return {"deleted": True,
            "note": "cached mail was removed; the mailbox itself keeps its mail"}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
