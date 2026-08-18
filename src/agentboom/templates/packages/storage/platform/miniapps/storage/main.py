"""Storage mini-app — durable file store (agentboom package: storage).

Files live on the platform's data volume (survives restarts; SQLite is
the only other tenant). Names are sanitized to a flat namespace — no
directories, no traversal.

Endpoints (mounted at /api/storage/):
  GET    /health
  GET    /files                 list with sizes + times
  POST   /files                 multipart upload (field name: file)
  GET    /files/{name}          download
  DELETE /files/{name}
"""
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger("miniapps.storage")

router = APIRouter()

# DATA_DIR is the platform's data volume (/data in-container).
STORAGE_DIR = Path(os.environ.get(
    "STORAGE_DIR", str(Path(os.environ.get("DATA_DIR", "data")) / "storage")))
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB — keep the volume honest
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(raw: str) -> str:
    """Flat namespace: strip paths, keep a conservative character set."""
    name = Path(raw or "").name
    name = _SAFE_NAME.sub("-", name).strip("-.")
    return name[:180]


def _ensure_dir() -> Path:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR


@router.get("/health")
async def health():
    _ensure_dir()
    usage = shutil.disk_usage(STORAGE_DIR)
    return {"status": "ok", "app": "storage", "dir": str(STORAGE_DIR),
            "free_gb": round(usage.free / 1e9, 1)}


@router.get("/files")
async def list_files():
    root = _ensure_dir()
    files = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        st = path.stat()
        files.append({
            "name": path.name,
            "size": st.st_size,
            "modified_at": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return {"files": files, "dir": str(root)}


@router.post("/files")
async def upload(file: UploadFile):
    name = _safe_name(file.filename or "")
    if not name:
        return JSONResponse({"error": "a filename is required"}, status_code=400)
    root = _ensure_dir()
    target = root / name
    size = 0
    tmp = target.with_name(name + ".part")
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise ValueError("file exceeds the 200 MB limit")
                out.write(chunk)
        tmp.replace(target)  # atomic swap — no half-written files
    except ValueError as exc:
        tmp.unlink(missing_ok=True)
        return JSONResponse({"error": str(exc)}, status_code=413)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return JSONResponse({"error": f"write failed: {exc}"}, status_code=500)
    log.info("storage: saved %s (%d bytes)", name, size)
    return {"ok": True, "name": name, "size": size,
            "replaced": target.exists()}


@router.get("/files/{name}")
async def download(name: str):
    safe = _safe_name(name)
    path = _ensure_dir() / safe
    if not safe or not path.is_file():
        return JSONResponse({"error": "no such file"}, status_code=404)
    return FileResponse(path, filename=safe)


@router.delete("/files/{name}")
async def delete(name: str):
    safe = _safe_name(name)
    path = _ensure_dir() / safe
    if not safe or not path.is_file():
        return JSONResponse({"error": "no such file"}, status_code=404)
    path.unlink()
    log.info("storage: deleted %s", safe)
    return {"deleted": True}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
