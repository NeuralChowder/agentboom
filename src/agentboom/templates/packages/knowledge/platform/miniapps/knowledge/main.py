"""Knowledge mini-app — the agent's durable memory
(agentboom package: knowledge).

Notes with tags and keyword search. The rule for agents: store facts on
sight, search before asking the user again.

Endpoints (mounted at /api/knowledge/):
  GET    /health
  GET    /notes?q=&tag=&limit=     newest first
  POST   /notes                    {title, body, tags?, source?}
  GET    /notes/{note_id}
  PUT    /notes/{note_id}          update any of title/body/tags/source
  DELETE /notes/{note_id}
  GET    /tags                     tags in use, with counts
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db

log = logging.getLogger("miniapps.knowledge")

router = APIRouter()


def _norm_tags(tags) -> str:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return ",".join(sorted({t.strip().lower() for t in (tags or []) if t.strip()}))


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM knowledge_notes")
    return {"status": "ok", "app": "knowledge", "notes": count}


@router.get("/notes")
async def list_notes(q: str = "", tag: str = "", limit: int = 30):
    limit = max(1, min(int(limit), 200))
    where, params = ["1=1"], []
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append("(lower(title) LIKE ? OR lower(body) LIKE ? OR lower(tags) LIKE ?)")
        params.extend([like, like, like])
    if tag.strip():
        where.append("(',' || tags || ',') LIKE ?")
        params.append(f"%,{tag.strip().lower()},%")
    rows = await db.fetchall(
        f"SELECT * FROM knowledge_notes WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC, id DESC LIMIT ?",
        (*params, limit))
    return {"notes": rows}


@router.post("/notes")
async def add_note(payload: dict):
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    if not title or not body:
        return JSONResponse({"error": "title and body are required"},
                            status_code=400)
    if db.is_postgres():
        row = await db.fetchone(
            "INSERT INTO knowledge_notes (title, body, tags, source) "
            "VALUES (?, ?, ?, ?) RETURNING *",
            (title[:300], body, _norm_tags(payload.get("tags")),
             (payload.get("source") or "")[:300] or None))
    else:
        await db.execute(
            "INSERT INTO knowledge_notes (title, body, tags, source) "
            "VALUES (?, ?, ?, ?)",
            (title[:300], body, _norm_tags(payload.get("tags")),
             (payload.get("source") or "")[:300] or None))
        row = await db.fetchone(
            "SELECT * FROM knowledge_notes WHERE id = last_insert_rowid()")
    log.info("knowledge: stored '%s'", title[:60])
    return {"ok": True, "note": dict(row)}


@router.get("/notes/{note_id}")
async def one_note(note_id: int):
    row = await db.fetchone(
        "SELECT * FROM knowledge_notes WHERE id = ?", note_id)
    if not row:
        return JSONResponse({"error": "no such note"}, status_code=404)
    return dict(row)


@router.put("/notes/{note_id}")
async def update_note(note_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM knowledge_notes WHERE id = ?", note_id)
    if not row:
        return JSONResponse({"error": "no such note"}, status_code=404)
    title = (payload.get("title") or row["title"]).strip() or row["title"]
    body = (payload.get("body") or row["body"]).strip() or row["body"]
    tags = _norm_tags(payload["tags"]) if "tags" in payload else row["tags"]
    source = payload.get("source", row["source"])
    await db.execute(
        "UPDATE knowledge_notes SET title = ?, body = ?, tags = ?, source = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (title[:300], body, tags, source, note_id))
    updated = await db.fetchone(
        "SELECT * FROM knowledge_notes WHERE id = ?", note_id)
    return {"ok": True, "note": dict(updated)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int):
    removed = await db.execute(
        "DELETE FROM knowledge_notes WHERE id = ?", note_id)
    if not removed:
        return JSONResponse({"error": "no such note"}, status_code=404)
    return {"deleted": True}


@router.get("/tags")
async def tags():
    rows = await db.fetchall("SELECT tags FROM knowledge_notes WHERE tags != ''")
    counts: dict = {}
    for row in rows:
        for tag in row["tags"].split(","):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return {"tags": [{"tag": t, "notes": n}
                     for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
