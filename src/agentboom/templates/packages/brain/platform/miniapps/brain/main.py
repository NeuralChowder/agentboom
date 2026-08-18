"""Brain mini-app — a lightweight knowledge graph
(agentboom package: brain).

People/companies/topics become entities; mail and notes become
observations. Senders of incoming mail are tracked deterministically;
when the contacts package is loaded, brain enriches entity names through
the contacts.lookup CAPABILITY — no hard-coded URL, and if the capability
is ever missing the app degrades instead of breaking.

Endpoints (mounted at /api/brain/):
  GET    /health
  GET    /entities?q=&kind=&limit=
  POST   /entities               {name, kind?, notes?}
  GET    /entities/{id}          entity + observations
  POST   /entities/{id}/observations   {text, source?}
  DELETE /entities/{id}
  GET    /search?q=              entities + observations
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from agentboom_sdk.capabilities import CapabilityError, call

log = logging.getLogger("miniapps.brain")

router = APIRouter()


async def _resolve_display_name(name: str, email: str) -> str:
    """Prefer a contact's name via the contacts.lookup capability.

    This is the intended way to reuse another mini-app's feature: call the
    capability, never hard-code its URL. When contacts is not loaded the
    capability raises CapabilityError and we fall back to what we have.
    """
    try:
        result = await call("contacts.lookup", {"text": email or name})
        for match in (result or {}).get("matches", []):
            if match.get("name"):
                return match["name"]
    except CapabilityError as exc:
        log.info("brain: contacts.lookup unavailable (%s) — using raw name", exc)
    return name


async def handle_event(event: dict) -> None:
    """Track senders of incoming mail as person entities (deterministic)."""
    if event.get("type") != "email.received":
        return
    data = event.get("data") or {}
    email = (data.get("from_email") or "").strip().lower()
    if not email:
        return
    name = await _resolve_display_name(data.get("from_name") or email, email)
    entity_name = name or email
    row = await db.fetchone(
        "SELECT id FROM brain_entities WHERE lower(name) = ?",
        (entity_name.lower(),))
    if not row:
        await db.execute(
            "INSERT INTO brain_entities (name, kind, notes) VALUES (?, 'person', ?)",
            (entity_name, f"email: {email}"))
        row = await db.fetchone(
            "SELECT id FROM brain_entities WHERE lower(name) = ?",
            (entity_name.lower(),))
    if row and data.get("email_id"):
        await db.execute(
            "INSERT INTO brain_mentions (entity_id, email_id) VALUES (?, ?)",
            (row["id"], data["email_id"]))


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM brain_entities")
    return {"status": "ok", "app": "brain", "entities": count}


@router.get("/entities")
async def list_entities(q: str = "", kind: str = "", limit: int = 100):
    limit = max(1, min(int(limit), 500))
    where, params = ["1=1"], []
    if q.strip():
        where.append("lower(name) LIKE ?")
        params.append(f"%{q.strip().lower()}%")
    if kind.strip():
        where.append("kind = ?")
        params.append(kind.strip())
    rows = await db.fetchall(
        f"SELECT * FROM brain_entities WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC, name LIMIT ?", (*params, limit))
    return {"entities": rows}


@router.post("/entities")
async def add_entity(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if await db.fetchone("SELECT id FROM brain_entities WHERE lower(name) = ?",
                         name.lower()):
        return JSONResponse({"error": "entity exists"}, status_code=409)
    await db.execute(
        "INSERT INTO brain_entities (name, kind, notes) VALUES (?, ?, ?)",
        (name, (payload.get("kind") or "topic").strip(),
         (payload.get("notes") or "").strip() or None))
    row = await db.fetchone(
        "SELECT * FROM brain_entities WHERE lower(name) = ?", name.lower())
    return {"ok": True, "entity": dict(row)}


@router.get("/entities/{entity_id}")
async def one_entity(entity_id: int):
    row = await db.fetchone("SELECT * FROM brain_entities WHERE id = ?", entity_id)
    if not row:
        return JSONResponse({"error": "no such entity"}, status_code=404)
    observations = await db.fetchall(
        "SELECT * FROM brain_observations WHERE entity_id = ? "
        "ORDER BY created_at DESC LIMIT 200", entity_id)
    return {"entity": dict(row), "observations": observations}


@router.post("/entities/{entity_id}/observations")
async def add_observation(entity_id: int, payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    row = await db.fetchone("SELECT id FROM brain_entities WHERE id = ?", entity_id)
    if not row:
        return JSONResponse({"error": "no such entity"}, status_code=404)
    await db.execute(
        "INSERT INTO brain_observations (entity_id, text, source) VALUES (?, ?, ?)",
        (entity_id, text, (payload.get("source") or "").strip() or None))
    await db.execute(
        "UPDATE brain_entities SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        entity_id)
    return {"ok": True}


@router.delete("/entities/{entity_id}")
async def delete_entity(entity_id: int):
    removed = await db.execute("DELETE FROM brain_entities WHERE id = ?", entity_id)
    if not removed:
        return JSONResponse({"error": "no such entity"}, status_code=404)
    return {"deleted": True}


@router.get("/search")
async def search(q: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 200))
    if not q.strip():
        return {"entities": [], "observations": []}
    like = f"%{q.strip().lower()}%"
    entities = await db.fetchall(
        "SELECT * FROM brain_entities WHERE lower(name) LIKE ? LIMIT ?",
        (like, limit))
    observations = await db.fetchall(
        "SELECT o.*, e.name AS entity FROM brain_observations o "
        "JOIN brain_entities e ON e.id = o.entity_id "
        "WHERE lower(o.text) LIKE ? ORDER BY o.created_at DESC LIMIT ?",
        (like, limit))
    return {"entities": entities, "observations": observations}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
