"""Continente mini-app — session panel for the continente.pt connector
(agentboom package: continente).

Thin facade over the continente connector so the agent and the dashboard
can manage the session and exercise the client over HTTP. The cookie jar
is a SECRET: it is stored in the vault and never echoed by any route.

Endpoints (mounted at /api/continente/):
  GET    /health             {ok, session, logged_in}
  GET    /status             full probe (fresh), never echoes cookie values
  POST   /session            {cookies: "<document.cookie> or JSON object"}
  DELETE /session            clear the stored session
  GET    /search?q=          connector passthrough (no session needed)
  POST   /items/{pid}/add    {quantity} → login-gated cart add
"""
import json
import logging
import time
from typing import Union

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from connectors.continente import (
    ContinenteError,
    SessionError,
    cart_add,
    clear_session,
    get_cookies,
    is_logged_in,
    parse_cookie_string,
    search,
    set_cookies,
)

log = logging.getLogger("miniapps.continente")

router = APIRouter()

#: Most recent operation error, for the panel's /status view.
_LAST_ERROR: dict = {"at": None, "message": None}


def _note_error(message: str) -> None:
    _LAST_ERROR.update(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       message=str(message)[:300])


def _parse_incoming_cookies(raw: Union[str, dict, None]) -> dict:
    """Accept a raw document.cookie string, a JSON object string, or a dict.

    Raises ValueError with a user-facing message when nothing usable
    parses out.
    """
    if raw is None:
        raise ValueError("cookies is required")
    if isinstance(raw, dict):
        cookies = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("cookies must not be empty")
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                raise ValueError(
                    "looks like a JSON object but does not parse — "
                    "paste the raw document.cookie string instead")
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object of cookie pairs")
            cookies = data
        else:
            cookies = parse_cookie_string(text)
    else:
        raise ValueError("cookies must be a string or an object")
    clean = {str(k): str(v) for k, v in cookies.items() if str(k)}
    if not clean:
        raise ValueError("no cookies parsed — expected 'name=value; name2=value2'")
    return clean


@router.get("/health")
async def health():
    cookies = await get_cookies()
    if not cookies:
        return {"ok": True, "app": "continente", "session": "missing",
                "logged_in": None}
    logged, _ = await is_logged_in(cookies)
    return {"ok": True, "app": "continente", "session": "stored",
            "logged_in": logged}


@router.get("/status")
async def status():
    """Full probe, flat scalar values for the stats view. Never echoes
    cookie names or values."""
    cookies = await get_cookies()
    if not cookies:
        return {"session": "missing", "cookie_count": 0,
                "logged_in": False, "probe": "no session stored",
                "last_error": _LAST_ERROR["message"],
                "last_error_at": _LAST_ERROR["at"],
                "hint": "No Continente session yet — ask the agent "
                        "'set up continente'."}
    logged, reason = await is_logged_in(cookies, force=True)
    return {"session": "stored", "cookie_count": len(cookies),
            "logged_in": logged, "probe": reason,
            "last_error": None if logged else _LAST_ERROR["message"],
            "last_error_at": _LAST_ERROR["at"],
            "hint": None if logged else "Session stored but the login probe "
                                        "failed — re-run setup."}


@router.post("/session")
async def store_session(payload: dict):
    """Store a session: {cookies: "<document.cookie string or JSON object>"}."""
    try:
        cookies = _parse_incoming_cookies(payload.get("cookies"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        stored = await set_cookies(cookies)
    except ContinenteError as exc:
        _note_error(str(exc))
        return JSONResponse({"error": str(exc)}, status_code=503)
    logged, reason = await is_logged_in(cookies, force=True)
    return {"ok": True, "stored": stored, "cookie_count": stored,
            "logged_in": logged, "probe": reason}


@router.delete("/session")
async def delete_session():
    removed = await clear_session()
    return {"ok": True, "deleted": removed}


@router.get("/search")
async def search_route(q: str = "", limit: int = 10):
    """Read-only passthrough — works without a session."""
    if not q.strip():
        return JSONResponse({"error": "q is required"}, status_code=400)
    try:
        results = await search(q.strip(), limit=limit)
    except ContinenteError as exc:
        _note_error(str(exc))
        log.warning("continente: search failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    return {"ok": True, "count": len(results), "results": results}


@router.post("/items/{pid}/add")
async def add_item(pid: str, payload: dict = None):
    """Login-gated cart add. 503 with a clear message when the session is
    missing, anonymous, or expired — never an unproven mutation."""
    payload = payload or {}
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        return JSONResponse({"error": "quantity must be an integer"},
                             status_code=400)
    if not 1 <= quantity <= 50:
        return JSONResponse({"error": "quantity must be 1-50"}, status_code=400)
    cookies = await get_cookies()
    if not cookies:
        return JSONResponse(
            {"error": "No Continente session stored — ask the agent "
                      "'set up continente'."},
            status_code=503)
    try:
        result = await cart_add(pid, quantity)
    except SessionError as exc:
        _note_error(str(exc))
        return JSONResponse({"error": str(exc)}, status_code=503)
    except ContinenteError as exc:
        _note_error(str(exc))
        return JSONResponse({"error": str(exc)}, status_code=409)
    return result


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
