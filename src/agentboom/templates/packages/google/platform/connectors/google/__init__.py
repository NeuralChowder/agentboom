"""Google connector — OAuth2 access for Gmail / Calendar (agentboom package: google).

Google's APIs need an OAuth2 access token that expires ~hourly. This
connector keeps the long-lived refresh_token in the vault and transparently
refreshes the access token when it is (near) expiry, so callers just ask for
a working token or make an authenticated request.

    from connectors.google import google_request, get_access_token

    token = await get_access_token("me@example.com")
    msgs  = await google_request("me@example.com", "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": 10})

Client credentials come from the environment (GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI). Per-account tokens live in the
vault under `google:<email>` as a JSON bundle:
    {"refresh_token", "access_token", "expiry", "scope"}
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("connectors.google")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/google/auth/callback")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

PLATFORM_INTERNAL_URL = os.environ.get(
    "PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")

# Refresh a little before the real expiry so a request never races the clock.
_REFRESH_SKEW_SEC = 120

#: Common scopes. Callers pick what they need.
SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.modify",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "calendar.readonly": "https://www.googleapis.com/auth/calendar.readonly",
}


class GoogleError(RuntimeError):
    """The Google call failed (missing credentials, bad token, HTTP error)."""


# ── vault-backed token storage ─────────────────────────────────────


def _vault_url(email: str) -> str:
    return f"{PLATFORM_INTERNAL_URL}/api/vault/credentials/google:{email}"


async def _load_bundle(email: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_vault_url(email))
    if resp.status_code != 200:
        return None
    try:
        secret = resp.json().get("secret")
        return json.loads(secret) if secret else None
    except (ValueError, AttributeError):
        return None


async def _save_bundle(email: str, bundle: Dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(_vault_url(email),
                                json={"secret": json.dumps(bundle),
                                      "note": "google oauth tokens"})
    if resp.status_code >= 400:
        raise GoogleError(f"vault refused the google tokens (HTTP {resp.status_code})")


# ── OAuth2 token lifecycle ─────────────────────────────────────────


def authorization_url(email: str, scopes: list) -> str:
    """The Google consent URL the user must visit to grant access."""
    if not GOOGLE_CLIENT_ID:
        raise GoogleError("GOOGLE_CLIENT_ID is not set (see .env.example)")
    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",      # get a refresh_token
        "prompt": "consent",           # force re-issuing a refresh_token
        "state": email,                # carry the account through the redirect
        "login_hint": email,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(email: str, code: str) -> Dict[str, Any]:
    """Exchange an authorization code for tokens and store them in the vault."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
        })
    if resp.status_code >= 400:
        raise GoogleError(f"token exchange failed: HTTP {resp.status_code}: "
                          f"{resp.text[:200]}")
    data = resp.json()
    if not data.get("refresh_token"):
        raise GoogleError("Google returned no refresh_token — re-authorize with "
                          "access_type=offline & prompt=consent")
    bundle = {
        "refresh_token": data["refresh_token"],
        "access_token": data.get("access_token", ""),
        "expiry": int(time.time()) + int(data.get("expires_in", 3600)),
        "scope": data.get("scope", ""),
    }
    await _save_bundle(email, bundle)
    return bundle


async def refresh_access_token(email: str) -> Dict[str, Any]:
    """Use the refresh_token to mint a new access_token; update the vault."""
    bundle = await _load_bundle(email)
    if not bundle or not bundle.get("refresh_token"):
        raise GoogleError(f"no google credential for {email} — authorize first")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": bundle["refresh_token"],
            "grant_type": "refresh_token",
        })
    if resp.status_code >= 400:
        raise GoogleError(f"refresh failed: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    bundle["access_token"] = data.get("access_token", bundle.get("access_token", ""))
    bundle["expiry"] = int(time.time()) + int(data.get("expires_in", 3600))
    await _save_bundle(email, bundle)
    return bundle


async def get_access_token(email: str) -> str:
    """A currently-valid access token, refreshing when (near) expiry."""
    bundle = await _load_bundle(email)
    if not bundle:
        raise GoogleError(f"no google credential for {email} — authorize first")
    if (not bundle.get("access_token")
            or int(bundle.get("expiry", 0)) - _REFRESH_SKEW_SEC <= time.time()):
        bundle = await refresh_access_token(email)
    return bundle["access_token"]


async def google_request(email: str, method: str, url: str, *,
                         params: Optional[dict] = None,
                         json_body: Optional[dict] = None) -> Any:
    """An authenticated Google API call; retries once after a forced refresh."""
    for attempt in range(2):
        token = await get_access_token(email)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method, url, params=params, json=json_body,
                headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401 and attempt == 0:
            await refresh_access_token(email)
            continue
        if resp.status_code >= 400:
            raise GoogleError(f"google API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return resp.text
    raise GoogleError("google API request failed after token refresh")
