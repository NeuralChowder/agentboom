"""Documents mini-app — file things into collections you define
(agentboom package: documents).

One engine, any use case: invoices, receipts, warranties, contracts,
per-client folders — collections and their matching rules are plain API
resources. Mail matching a collection's rules files itself (with the
email package); anything else is added manually, optionally referencing
a file stored by the storage package.

Endpoints (mounted at /api/documents/):
  GET    /health
  GET    /collections                POST /collections
  PUT    /collections/{id}           DELETE /collections/{id}
  GET    /collections/{id}/documents
  GET    /documents?q=&collection=&limit=
  POST   /documents                  DELETE /documents/{id}
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db

log = logging.getLogger("miniapps.documents")

router = APIRouter()


def _lower(value) -> str:
    return (value or "").lower()


def _matches(collection: dict, from_email: str, subject: str) -> bool:
    hit_from = (collection["match_from"]
                and _lower(collection["match_from"]) in _lower(from_email))
    hit_subject = (collection["match_subject"]
                   and _lower(collection["match_subject"]) in _lower(subject))
    return bool(hit_from or hit_subject)


async def handle_event(event: dict) -> None:
    """File incoming mail into every matching enabled collection."""
    if event.get("type") != "email.received":
        return
    data = event.get("data") or {}
    if not data.get("email_id"):
        return
    collections = await db.fetchall(
        "SELECT * FROM document_collections WHERE enabled = 1")
    for collection in collections:
        if not _matches(dict(collection), data.get("from_email", ""),
                        data.get("subject", "")):
            continue
        # UNIQUE(collection_id, email_id) dedupes across repeated events.
        await db.execute(
            "INSERT OR IGNORE INTO documents "
            "(collection_id, source, email_id, title) VALUES (?, 'email', ?, ?)",
            (collection["id"], data["email_id"],
             data.get("subject") or "(no subject)"))
        log.info("documents: filed '%s' into '%s'",
                 (data.get("subject") or "")[:60], collection["name"])


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM document_collections")
    return {"status": "ok", "app": "documents", "collections": count}


# ── collections ────────────────────────────────────────────────────


@router.get("/collections")
async def list_collections():
    rows = await db.fetchall(
        "SELECT c.*, (SELECT count(*) FROM documents d "
        " WHERE d.collection_id = c.id) AS documents "
        "FROM document_collections c ORDER BY c.name")
    return {"collections": rows}


@router.post("/collections")
async def add_collection(payload: dict):
    name = (payload.get("name") or "").strip().lower()
    match_from = (payload.get("match_from") or "").strip()
    match_subject = (payload.get("match_subject") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if await db.fetchone(
            "SELECT id FROM document_collections WHERE name = ?", name):
        return JSONResponse({"error": "collection exists"}, status_code=409)
    await db.execute(
        "INSERT INTO document_collections (name, match_from, match_subject, note) "
        "VALUES (?, ?, ?, ?)",
        (name, match_from or None, match_subject or None,
         (payload.get("note") or "").strip() or None))
    row = await db.fetchone(
        "SELECT * FROM document_collections WHERE name = ?", name)
    return {"ok": True, "collection": dict(row),
            "note": "matching rules are optional — you can also file manually"}


@router.put("/collections/{collection_id}")
async def update_collection(collection_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM document_collections WHERE id = ?", collection_id)
    if not row:
        return JSONResponse({"error": "no such collection"}, status_code=404)
    if "enabled" in payload:
        await db.execute(
            "UPDATE document_collections SET enabled = ? WHERE id = ?",
            (1 if payload.get("enabled") else 0, collection_id))
    for field in ("match_from", "match_subject", "note"):
        if field in payload:
            await db.execute(
                f"UPDATE document_collections SET {field} = ? WHERE id = ?",
                ((payload.get(field) or "").strip() or None, collection_id))
    return {"ok": True}


@router.delete("/collections/{collection_id}")
async def delete_collection(collection_id: int):
    removed = await db.execute(
        "DELETE FROM document_collections WHERE id = ?", collection_id)
    if not removed:
        return JSONResponse({"error": "no such collection"}, status_code=404)
    return {"deleted": True, "note": "its documents were removed with it"}


@router.get("/collections/{collection_id}/documents")
async def collection_documents(collection_id: int, limit: int = 100):
    limit = max(1, min(int(limit), 500))
    row = await db.fetchone(
        "SELECT name FROM document_collections WHERE id = ?", collection_id)
    if not row:
        return JSONResponse({"error": "no such collection"}, status_code=404)
    docs = await db.fetchall(
        "SELECT * FROM documents WHERE collection_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (collection_id, limit))
    return {"collection": row["name"], "documents": docs}


# ── documents ──────────────────────────────────────────────────────


@router.get("/documents")
async def list_documents(q: str = "", collection: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 500))
    where, params = ["1=1"], []
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append("(lower(d.title) LIKE ? OR lower(d.notes) LIKE ?)")
        params.extend([like, like])
    if collection.strip():
        where.append("c.name = ?")
        params.append(collection.strip().lower())
    rows = await db.fetchall(
        f"""
        SELECT d.*, c.name AS collection
        FROM documents d
        JOIN document_collections c ON c.id = d.collection_id
        WHERE {' AND '.join(where)}
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (*params, limit))
    return {"documents": rows}


@router.post("/documents")
async def add_document(payload: dict):
    title = (payload.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    collection = None
    if payload.get("collection"):
        collection = await db.fetchone(
            "SELECT id FROM document_collections WHERE name = ?",
            str(payload["collection"]).strip().lower())
    elif payload.get("collection_id"):
        collection = await db.fetchone(
            "SELECT id FROM document_collections WHERE id = ?",
            payload["collection_id"])
    if not collection:
        return JSONResponse(
            {"error": "unknown collection — create it first"}, status_code=400)
    await db.execute(
        "INSERT INTO documents "
        "(collection_id, source, email_id, title, notes, file_name) "
        "VALUES (?, 'manual', ?, ?, ?, ?)",
        (collection["id"], payload.get("email_id"), title[:300],
         (payload.get("notes") or "").strip() or None,
         (payload.get("file_name") or "").strip() or None))
    row = await db.fetchone(
        "SELECT * FROM documents WHERE id = last_insert_rowid()")
    return {"ok": True, "document": dict(row)}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: int):
    removed = await db.execute(
        "DELETE FROM documents WHERE id = ?", document_id)
    if not removed:
        return JSONResponse({"error": "no such document"}, status_code=404)
    return {"deleted": True}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
