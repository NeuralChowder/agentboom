"""Google mini-app — connect Google accounts via OAuth2
(agentboom package: google).

Walks the OAuth2 authorization-code flow and keeps the resulting tokens in
the vault. The companion connector (`connectors.google`) hands out valid
access tokens and makes authenticated Gmail / Calendar calls.

Endpoints (mounted at /api/google/):
  GET    /health
  GET    /auth/start?email=&scopes=gmail,calendar   -> the consent URL
  GET    /auth/callback?code=&state=                -> exchange + store
  GET    /accounts                                  connected accounts
  POST   /accounts/{email}/refresh                  force a token refresh
  DELETE /accounts/{email}                          disconnect
  POST   /refresh-all                               manifest job target
"""

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from agentboom_sdk import db
from connectors.google import (
    SCOPES,
    GoogleError,
    authorization_url,
    exchange_code,
    refresh_access_token,
)

log = logging.getLogger("miniapps.google")

router = APIRouter()


def _resolve_scopes(raw: str) -> list:
    names = [s.strip() for s in (raw or "").split(",") if s.strip()]
    urls = []
    for name in names:
        if name in SCOPES:
            urls.append(SCOPES[name])
        elif name.startswith("https://"):
            urls.append(name)
    return urls or [SCOPES["gmail"]]


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM google_accounts")
    return {"status": "ok", "app": "google", "accounts": count}


@router.get("/auth/start")
async def auth_start(email: str, scopes: str = "gmail"):
    if not email or "@" not in email:
        return JSONResponse({"error": "a valid email is required"}, status_code=400)
    try:
        url = authorization_url(email.strip().lower(), _resolve_scopes(scopes))
    except GoogleError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"ok": True, "email": email.strip().lower(), "url": url,
            "note": "open the URL, approve, and Google redirects back to "
                    "/api/google/auth/callback"}


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<p>Google authorization failed: {error}</p>",
                            status_code=400)
    if not code or not state:
        return HTMLResponse("<p>Missing code/state.</p>", status_code=400)
    email = state.strip().lower()
    try:
        bundle = await exchange_code(email, code)
    except GoogleError as exc:
        return HTMLResponse(f"<p>Token exchange failed: {exc}</p>", status_code=502)
    await db.execute(
        "INSERT INTO google_accounts (email, scope) VALUES (?, ?) "
        "ON CONFLICT(email) DO UPDATE SET scope = EXCLUDED.scope",
        (email, bundle.get("scope", "")))
    log.info("google: account %s connected", email)
    return HTMLResponse(
        f"<p>Connected <b>{email}</b>. You can close this tab.</p>")


@router.get("/accounts")
async def list_accounts():
    rows = await db.fetchall(
        "SELECT email, scope, created_at, last_refresh_at FROM google_accounts "
        "ORDER BY email")
    return {"accounts": rows}


@router.post("/accounts/{email}/refresh")
async def refresh_account(email: str):
    try:
        await refresh_access_token(email.strip().lower())
    except GoogleError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    await db.execute(
        "UPDATE google_accounts SET last_refresh_at = CURRENT_TIMESTAMP "
        "WHERE email = ?", (email.strip().lower(),))
    return {"ok": True}


@router.delete("/accounts/{email}")
async def disconnect(email: str):
    email = email.strip().lower()
    removed = await db.execute("DELETE FROM google_accounts WHERE email = ?", email)
    if not removed:
        return JSONResponse({"error": "no such account"}, status_code=404)
    # Best-effort: drop the vault credential too.
    import httpx
    import os
    vault = f"{os.environ.get('PLATFORM_INTERNAL_URL', 'http://127.0.0.1:8000')}" \
            f"/api/vault/credentials/google:{email}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.delete(vault)
    except Exception:  # noqa: BLE001
        log.warning("google: could not delete vault credential for %s", email)
    return {"deleted": True}


@router.post("/refresh-all")
async def refresh_all():
    """Manifest job target: refresh any token near expiry so callers never
    hit a stale one."""
    rows = await db.fetchall("SELECT email FROM google_accounts")
    refreshed, errors = 0, []
    for row in rows:
        try:
            await refresh_access_token(row["email"])
            await db.execute(
                "UPDATE google_accounts SET last_refresh_at = CURRENT_TIMESTAMP "
                "WHERE email = ?", (row["email"],))
            refreshed += 1
        except GoogleError as exc:
            errors.append({"email": row["email"], "error": str(exc)[:200]})
    return {"ok": True, "refreshed": refreshed, "errors": errors}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
