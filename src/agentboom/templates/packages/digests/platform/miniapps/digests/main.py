"""Digests mini-app — scheduled digests you define
(agentboom package: digests).

A digest is three API-managed pieces: sources (feeds, recent mail,
internal endpoints), a synthesis prompt, and a schedule (interval or
cron). The same engine builds a morning briefing, a news roundup, a
repo watch summary — whatever you configure.

Everything degrades gracefully:
- no LLM gateway   -> the collected raw material is stored instead
- no ntfy package  -> results are stored and readable over HTTP
- no email package -> 'emails' sources simply collect nothing

Endpoints (mounted at /api/digests/):
  GET    /health
  GET    /digests                  POST /digests
  PUT    /digests/{id}             DELETE /digests/{id}
  GET    /digests/{id}/sources     POST /digests/{id}/sources
  DELETE /digests/{id}/sources/{source_id}
  POST   /digests/{id}/run         run one now
  POST   /run-due                  manifest job target (15 min)
  GET    /digests/{id}/latest      last run's content
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import cron, db
from agentboom_sdk.llm import complete

log = logging.getLogger("miniapps.digests")

router = APIRouter()

_SOURCE_TIMEOUT = 30.0
_MAX_CONTENT_CHARS = 20000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── source collectors ──────────────────────────────────────────────


async def _collect_feed(ref: str) -> str:
    async with httpx.AsyncClient(timeout=_SOURCE_TIMEOUT,
                                 follow_redirects=True) as client:
        resp = await client.get(ref, headers={"User-Agent": "agentboom-digests"})
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} for {ref}")
    try:  # feedparser ships with the rss-feeds package; fall back to raw
        import feedparser
        parsed = feedparser.parse(resp.content)
        lines = []
        for entry in parsed.entries[:15]:
            title = (getattr(entry, "title", "") or "").strip()
            summary = (getattr(entry, "summary", "") or "").strip()[:200]
            lines.append(f"- {title}: {summary}")
        feed_title = (parsed.feed.get("title") or ref) if parsed.feed else ref
        return f"[feed {feed_title}]\n" + "\n".join(lines)
    except ImportError:
        return f"[feed {ref} (raw)]\n{resp.text[:2000]}"


async def _collect_emails(ref: str, since: datetime) -> str:
    limit = 20
    if ref.startswith("limit:"):
        try:
            limit = max(1, min(int(ref.split(":", 1)[1]), 100))
        except ValueError:
            pass
    try:
        rows = await db.fetchall(
            "SELECT subject, from_email, received_at FROM emails "
            "WHERE received_at > ? ORDER BY received_at DESC LIMIT ?",
            (_fmt(since), limit))
    except Exception:  # noqa: BLE001 — emails table absent without the email package
        return ""
    if not rows:
        return ""
    lines = [f"- [{r['received_at']}] {r['subject']} — {r['from_email']}"
             for r in rows]
    return f"[{len(rows)} recent emails]\n" + "\n".join(lines)


async def _collect_endpoint(ref: str) -> str:
    async with httpx.AsyncClient(timeout=_SOURCE_TIMEOUT) as client:
        resp = await client.get(ref)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} for {ref}")
    return f"[endpoint {ref}]\n{resp.text[:3000]}"


async def _collect_sources(digest_id: int, since: datetime) -> tuple:
    """Returns (material, errors). Empty material is not an error."""
    sources = await db.fetchall(
        "SELECT * FROM digest_sources WHERE digest_id = ? ORDER BY id",
        digest_id)
    parts, errors = [], []
    for source in sources:
        try:
            if source["kind"] == "feed":
                part = await _collect_feed(source["ref"])
            elif source["kind"] == "emails":
                part = await _collect_emails(source["ref"], since)
            else:
                part = await _collect_endpoint(source["ref"])
            if part:
                parts.append(part)
        except Exception as exc:  # noqa: BLE001 — one dead source never kills a digest
            errors.append(f"{source['kind']}:{source['ref']}: {str(exc)[:150]}")
    material = "\n\n".join(parts)[:_MAX_CONTENT_CHARS]
    return material, errors


# ── delivery (optional ntfy connector) ─────────────────────────────


async def _deliver(name: str, content: str) -> bool:
    try:
        from connectors.ntfy import enabled, send
    except ImportError:
        return False
    if not enabled():
        return False
    try:
        await send(content[:1000], title=f"digest: {name}", priority=3)
        return True
    except Exception as exc:  # noqa: BLE001 — delivery is best-effort
        log.warning("digests: ntfy delivery failed: %s", exc)
        return False


# ── running ────────────────────────────────────────────────────────


async def _run_digest(digest: dict) -> dict:
    since = _now() - timedelta(hours=24)
    if digest["last_run_at"]:
        try:
            since = datetime.fromisoformat(str(digest["last_run_at"]))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    material, source_errors = await _collect_sources(digest["id"], since)
    if not material:
        await db.execute(
            "UPDATE digests SET last_run_at = CURRENT_TIMESTAMP, "
            "last_error = ? WHERE id = ?",
            ("no material collected" + (f" ({'; '.join(source_errors)})"
                                        if source_errors else ""),
             digest["id"]))
        return {"ok": True, "skipped": "no material",
                "source_errors": source_errors}

    synthesized = await complete(
        f"{digest['prompt']}\n\nSource material:\n{material}",
        temperature=0.3, max_tokens=1200, timeout=180)
    if synthesized:
        content = synthesized.strip()
    else:
        content = ("(no LLM gateway configured — raw material)\n\n" + material)

    delivered = await _deliver(digest["name"], content)
    await db.execute(
        "INSERT INTO digest_runs (digest_id, content, delivered, error) "
        "VALUES (?, ?, ?, ?)",
        (digest["id"], content, 1 if delivered else 0,
         "; ".join(source_errors) or None))
    await db.execute(
        "UPDATE digests SET last_run_at = CURRENT_TIMESTAMP, last_error = NULL "
        "WHERE id = ?", (digest["id"],))
    log.info("digests: ran '%s' (%d chars, delivered=%s)",
             digest["name"], len(content), delivered)
    return {"ok": True, "chars": len(content), "delivered": delivered,
            "source_errors": source_errors}


def _due(digest: dict) -> bool:
    if not digest["enabled"]:
        return False
    if digest["cron_expr"]:
        if not digest["last_run_at"]:
            return True
        try:
            last = datetime.fromisoformat(str(digest["last_run_at"]))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        nxt = cron.next_cron_time(digest["cron_expr"], after=last)
        return nxt is not None and nxt <= _now()
    if digest["interval_min"]:
        if not digest["last_run_at"]:
            return True
        try:
            last = datetime.fromisoformat(str(digest["last_run_at"]))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return _now() >= last + timedelta(minutes=digest["interval_min"])
    return False  # manual-only digest


# ── endpoints ──────────────────────────────────────────────────────


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM digests WHERE enabled = 1")
    return {"status": "ok", "app": "digests", "enabled_digests": count}


@router.get("/digests")
async def list_digests():
    rows = await db.fetchall(
        "SELECT d.*, (SELECT count(*) FROM digest_sources s "
        " WHERE s.digest_id = d.id) AS sources "
        "FROM digests d ORDER BY d.name")
    return {"digests": rows}


@router.post("/digests")
async def add_digest(payload: dict):
    name = (payload.get("name") or "").strip().lower()
    prompt = (payload.get("prompt") or "").strip()
    if not name or not prompt:
        return JSONResponse({"error": "name and prompt are required"},
                            status_code=400)
    cron_expr = (payload.get("cron_expr") or "").strip() or None
    if cron_expr and not cron.is_valid_cron(cron_expr):
        return JSONResponse({"error": f"invalid cron '{cron_expr}'"},
                            status_code=400)
    interval_min = payload.get("interval_min")
    if not cron_expr and not interval_min:
        return JSONResponse(
            {"error": "give cron_expr or interval_min (or both)"},
            status_code=400)
    if await db.fetchone("SELECT id FROM digests WHERE name = ?", name):
        return JSONResponse({"error": "digest exists"}, status_code=409)
    await db.execute(
        "INSERT INTO digests (name, prompt, interval_min, cron_expr) "
        "VALUES (?, ?, ?, ?)",
        (name, prompt, interval_min, cron_expr))
    row = await db.fetchone("SELECT * FROM digests WHERE name = ?", name)
    return {"ok": True, "digest": dict(row),
            "next": "add sources at /digests/{id}/sources"}


@router.put("/digests/{digest_id}")
async def update_digest(digest_id: int, payload: dict):
    row = await db.fetchone("SELECT * FROM digests WHERE id = ?", digest_id)
    if not row:
        return JSONResponse({"error": "no such digest"}, status_code=404)
    if "enabled" in payload:
        await db.execute("UPDATE digests SET enabled = ? WHERE id = ?",
                         (1 if payload.get("enabled") else 0, digest_id))
    if payload.get("prompt"):
        await db.execute("UPDATE digests SET prompt = ? WHERE id = ?",
                         (str(payload["prompt"]).strip(), digest_id))
    if "interval_min" in payload:
        await db.execute("UPDATE digests SET interval_min = ? WHERE id = ?",
                         (payload.get("interval_min"), digest_id))
    if "cron_expr" in payload:
        expr = (payload.get("cron_expr") or "").strip() or None
        if expr and not cron.is_valid_cron(expr):
            return JSONResponse({"error": f"invalid cron '{expr}'"},
                                status_code=400)
        await db.execute("UPDATE digests SET cron_expr = ? WHERE id = ?",
                         (expr, digest_id))
    return {"ok": True}


@router.delete("/digests/{digest_id}")
async def delete_digest(digest_id: int):
    removed = await db.execute("DELETE FROM digests WHERE id = ?", digest_id)
    if not removed:
        return JSONResponse({"error": "no such digest"}, status_code=404)
    return {"deleted": True}


@router.get("/digests/{digest_id}/sources")
async def list_sources(digest_id: int):
    rows = await db.fetchall(
        "SELECT * FROM digest_sources WHERE digest_id = ? ORDER BY id",
        digest_id)
    return {"sources": rows}


@router.post("/digests/{digest_id}/sources")
async def add_source(digest_id: int, payload: dict):
    row = await db.fetchone("SELECT id FROM digests WHERE id = ?", digest_id)
    if not row:
        return JSONResponse({"error": "no such digest"}, status_code=404)
    kind = (payload.get("kind") or "").strip()
    ref = (payload.get("ref") or "").strip()
    if kind not in ("feed", "emails", "endpoint"):
        return JSONResponse(
            {"error": "kind must be feed|emails|endpoint"}, status_code=400)
    if not ref:
        ref = "limit:20" if kind == "emails" else ""
    if kind != "emails" and not ref.startswith(("http://", "https://")):
        return JSONResponse(
            {"error": "ref must be an http(s) URL for feed/endpoint"},
            status_code=400)
    existing = await db.fetchone(
        "SELECT id FROM digest_sources WHERE digest_id = ? AND kind = ? AND ref = ?",
        (digest_id, kind, ref))
    if existing:
        return JSONResponse({"error": "source exists"}, status_code=409)
    await db.execute(
        "INSERT INTO digest_sources (digest_id, kind, ref, note) "
        "VALUES (?, ?, ?, ?)",
        (digest_id, kind, ref, (payload.get("note") or "").strip() or None))
    return {"ok": True, "kind": kind, "ref": ref}


@router.delete("/digests/{digest_id}/sources/{source_id}")
async def delete_source(digest_id: int, source_id: int):
    removed = await db.execute(
        "DELETE FROM digest_sources WHERE id = ? AND digest_id = ?",
        (source_id, digest_id))
    if not removed:
        return JSONResponse({"error": "no such source"}, status_code=404)
    return {"deleted": True}


@router.post("/digests/{digest_id}/run")
async def run_one(digest_id: int):
    row = await db.fetchone("SELECT * FROM digests WHERE id = ?", digest_id)
    if not row:
        return JSONResponse({"error": "no such digest"}, status_code=404)
    return await _run_digest(dict(row))


@router.post("/run-due")
async def run_due():
    """Manifest job target: run every enabled digest that is due."""
    rows = await db.fetchall("SELECT * FROM digests")
    ran, skipped = [], []
    for row in rows:
        if not _due(dict(row)):
            continue
        result = await _run_digest(dict(row))
        (ran if result.get("ok") and not result.get("skipped")
         else skipped).append(row["name"])
    return {"ok": True, "ran": ran, "skipped_no_material": skipped}


@router.get("/digests/{digest_id}/latest")
async def latest(digest_id: int):
    run = await db.fetchone(
        "SELECT * FROM digest_runs WHERE digest_id = ? "
        "ORDER BY ran_at DESC, id DESC LIMIT 1", digest_id)
    if not run:
        return JSONResponse({"error": "no runs yet"}, status_code=404)
    return dict(run)


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
