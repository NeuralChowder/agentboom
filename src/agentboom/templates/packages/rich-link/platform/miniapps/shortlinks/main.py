"""Shortlinks mini-app — self-contained HTML pages with shareable URLs.

The agent renders long/complex answers as mobile-friendly HTML, stores
them here, and shares `/api/shortlinks/p/<slug>`. Pages live on the data
volume; expired pages are refused and cleaned up by a scheduled job.
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("miniapps.shortlinks")

router = APIRouter()

DATA_DIR = Path(os.environ.get("DATA_DIR", "data")) / "shortlinks"
MAX_HTML_BYTES = 2 * 1024 * 1024
DEFAULT_TTL_HOURS = 7 * 24


def _meta_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.meta.json"


def _html_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.html"


def _load_meta(slug: str) -> dict | None:
    mp = _meta_path(slug)
    if not mp.is_file():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _expired(meta: dict) -> bool:
    return time.time() > float(meta.get("expires_at", 0))


@router.get("/health")
async def health():
    return {"status": "ok", "app": "shortlinks"}


@router.post("/links")
async def create_link(payload: dict):
    html = payload.get("html")
    if not html or not isinstance(html, str):
        return JSONResponse({"error": "html (string) is required"}, status_code=400)
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return JSONResponse({"error": f"html exceeds {MAX_HTML_BYTES} bytes"}, status_code=400)

    ttl_hours = float(payload.get("expire_hours") or DEFAULT_TTL_HOURS)
    slug = secrets.token_hex(6)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _html_path(slug).write_text(html, encoding="utf-8")
    meta = {
        "title": str(payload.get("title") or "")[:200],
        "created_at": time.time(),
        "expires_at": time.time() + ttl_hours * 3600,
    }
    _meta_path(slug).write_text(json.dumps(meta), encoding="utf-8")
    log.info("shortlink created: %s (%s)", slug, meta["title"] or "untitled")
    return {"slug": slug, "path": f"/api/shortlinks/p/{slug}", "expires_at": meta["expires_at"]}


@router.get("/links")
async def list_links():
    if not DATA_DIR.is_dir():
        return {"links": []}
    links = []
    for mp in sorted(DATA_DIR.glob("*.meta.json")):
        slug = mp.name[: -len(".meta.json")]
        meta = _load_meta(slug) or {}
        links.append({
            "slug": slug,
            "title": meta.get("title", ""),
            "path": f"/api/shortlinks/p/{slug}",
            "expired": _expired(meta),
        })
    return {"links": links}


@router.delete("/links/{slug}")
async def delete_link(slug: str):
    if "/" in slug or ".." in slug:
        return JSONResponse({"error": "invalid slug"}, status_code=400)
    removed = False
    for path in (_html_path(slug), _meta_path(slug)):
        if path.is_file():
            path.unlink()
            removed = True
    return {"deleted": removed}


@router.get("/p/{slug}", response_class=HTMLResponse)
async def serve_page(slug: str):
    if "/" in slug or ".." in slug:
        return JSONResponse({"error": "invalid slug"}, status_code=400)
    meta = _load_meta(slug)
    page = _html_path(slug)
    if meta is None or not page.is_file():
        return HTMLResponse("<h1>404 — link not found</h1>", status_code=404)
    if _expired(meta):
        return HTMLResponse("<h1>410 — link expired</h1>", status_code=410)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.post("/cleanup/run")
async def cleanup_run():
    """Job target: remove expired pages."""
    removed = 0
    if DATA_DIR.is_dir():
        for mp in DATA_DIR.glob("*.meta.json"):
            slug = mp.name[: -len(".meta.json")]
            meta = _load_meta(slug)
            if meta is None or _expired(meta):
                for path in (_html_path(slug), mp):
                    if path.is_file():
                        path.unlink()
                        removed += 1
    if removed:
        log.info("shortlinks cleanup: removed %d files", removed)
    return {"removed": removed}


def get_router() -> APIRouter:
    return router
