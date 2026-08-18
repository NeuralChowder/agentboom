"""Contacts mini-app — an address book that fills itself
(agentboom package: contacts).

Every incoming message adds its sender (with the email package); anything
else is added manually. The `contacts.lookup` capability lets other
mini-apps resolve a name or address without hard-coding this app's URL.

Endpoints (mounted at /api/contacts/):
  GET    /health
  GET    /contacts?q=&limit=
  POST   /contacts               {email, name?, notes?}
  PUT    /contacts/{id}          DELETE /contacts/{id}
  POST   /lookup                 {text} -> matching contacts (capability)
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db

log = logging.getLogger("miniapps.contacts")

router = APIRouter()


def _norm(email) -> str:
    return (email or "").strip().lower()


async def handle_event(event: dict) -> None:
    """Auto-add senders of incoming mail."""
    if event.get("type") != "email.received":
        return
    data = event.get("data") or {}
    email = _norm(data.get("from_email"))
    if not email:
        return
    existing = await db.fetchone("SELECT id FROM contacts WHERE email = ?", email)
    if existing:
        # Refresh a missing name if the mail carried one.
        if data.get("from_name"):
            await db.execute(
                "UPDATE contacts SET name = COALESCE(NULLIF(name, ''), ?), "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (data["from_name"].strip(), existing["id"]))
        return
    await db.execute(
        "INSERT INTO contacts (email, name, source) VALUES (?, ?, 'email')",
        (email, (data.get("from_name") or "").strip() or None))
    log.info("contacts: added %s from mail", email)


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM contacts")
    return {"status": "ok", "app": "contacts", "contacts": count}


@router.get("/contacts")
async def list_contacts(q: str = "", limit: int = 100):
    limit = max(1, min(int(limit), 500))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        rows = await db.fetchall(
            "SELECT * FROM contacts WHERE lower(email) LIKE ? OR lower(name) LIKE ? "
            "ORDER BY name, email LIMIT ?", (like, like, limit))
    else:
        rows = await db.fetchall(
            "SELECT * FROM contacts ORDER BY name, email LIMIT ?", limit)
    return {"contacts": rows}


@router.post("/contacts")
async def add_contact(payload: dict):
    email = _norm(payload.get("email"))
    if not email or "@" not in email:
        return JSONResponse({"error": "a valid email is required"}, status_code=400)
    if await db.fetchone("SELECT id FROM contacts WHERE email = ?", email):
        return JSONResponse({"error": "contact exists"}, status_code=409)
    await db.execute(
        "INSERT INTO contacts (email, name, notes, source) VALUES (?, ?, ?, 'manual')",
        (email, (payload.get("name") or "").strip() or None,
         (payload.get("notes") or "").strip() or None))
    row = await db.fetchone("SELECT * FROM contacts WHERE email = ?", email)
    return {"ok": True, "contact": dict(row)}


@router.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, payload: dict):
    row = await db.fetchone("SELECT * FROM contacts WHERE id = ?", contact_id)
    if not row:
        return JSONResponse({"error": "no such contact"}, status_code=404)
    for field in ("name", "notes"):
        if field in payload:
            await db.execute(
                f"UPDATE contacts SET {field} = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?", ((payload.get(field) or "").strip() or None, contact_id))
    return {"ok": True}


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    removed = await db.execute("DELETE FROM contacts WHERE id = ?", contact_id)
    if not removed:
        return JSONResponse({"error": "no such contact"}, status_code=404)
    return {"deleted": True}


@router.post("/lookup")
async def lookup(payload: dict):
    """The contacts.lookup capability: resolve a name or address.

    Input {text}; returns best matches (email exact-match first, then
    name/email substring). Callers reach this via
    agentboom_sdk.capabilities.call("contacts.lookup", {...}) — or, from
    Node, the @agentboom/sdk capabilities bridge.
    """
    text = (payload.get("text") or "").strip()
    if not text:
        return {"ok": True, "matches": []}
    needle = text.lower()
    exact = await db.fetchall(
        "SELECT * FROM contacts WHERE lower(email) = ?", (needle,))
    if exact:
        return {"ok": True, "matches": exact}
    like = f"%{needle}%"
    rows = await db.fetchall(
        "SELECT * FROM contacts WHERE lower(name) LIKE ? OR lower(email) LIKE ? "
        "ORDER BY name LIMIT 10", (like, like))
    return {"ok": True, "matches": rows}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
