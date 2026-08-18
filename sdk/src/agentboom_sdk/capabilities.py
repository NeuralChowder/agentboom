"""Capability calls between mini-apps (agentboom base contract).

Mini-apps expose capabilities declaratively in their manifest:

    "provides": [{"name": "contacts.lookup", "endpoint": "POST /lookup",
                  "description": "resolve names to addresses"}]
    "uses": ["contacts.lookup"]

The gateway builds one registry from every loaded manifest (see
GET /api/capabilities) and validates `uses` at load time. Callers never
hard-code another app's URL — they call the capability:

    from agentboom_sdk.capabilities import call, CapabilityError

    try:
        result = await call("contacts.lookup", {"text": "Maria"})
    except CapabilityError as exc:
        ...  # exc explains exactly what is missing and why

Resolution is cached; the registry is small and changes only on reloads.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("agentboom_sdk.capabilities")

PLATFORM_INTERNAL_URL = os.environ.get(
    "PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")
_CACHE_TTL_SEC = float(os.environ.get("CAPABILITIES_CACHE_SEC", "60"))
_TIMEOUT = float(os.environ.get("CAPABILITY_TIMEOUT_SEC", "30"))

_cache: Dict[str, Any] = {"at": 0.0, "capabilities": {}}


class CapabilityError(RuntimeError):
    """The capability is missing or the provider failed.

    The message is written for the agent that has to fix it: what is
    missing, who was expected to provide it, and the usual remedy.
    """


async def registry(refresh: bool = False) -> Dict[str, dict]:
    """The capability map {name: {app, method, path, description}}."""
    now = time.time()
    if not refresh and _cache["capabilities"] and \
            now - _cache["at"] < _CACHE_TTL_SEC:
        return _cache["capabilities"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{PLATFORM_INTERNAL_URL}/api/capabilities")
    except httpx.HTTPError as exc:
        if _cache["capabilities"]:
            return _cache["capabilities"]  # stale beats dead
        raise CapabilityError(
            f"capability registry unreachable at {PLATFORM_INTERNAL_URL}: {exc}"
        ) from exc
    if resp.status_code >= 400:
        raise CapabilityError(
            f"capability registry returned HTTP {resp.status_code}")
    _cache["at"] = now
    _cache["capabilities"] = resp.json().get("capabilities", {})
    return _cache["capabilities"]


async def resolve(name: str, refresh: bool = False) -> dict:
    """Provider record for one capability; CapabilityError when absent."""
    caps = await registry(refresh=refresh)
    record = caps.get(name)
    if record is None:
        available = ", ".join(sorted(caps)) or "(none loaded)"
        raise CapabilityError(
            f"capability '{name}' is not provided by any loaded mini-app. "
            f"Install the package that provides it (agentboom add package ...). "
            f"Loaded capabilities: {available}")
    return record


async def call(name: str, payload: Optional[dict] = None, *,
               timeout: Optional[float] = None, refresh: bool = False) -> Any:
    """Call a capability and return its decoded response.

    Providers are plain HTTP endpoints; GET capabilities receive the
    payload as query params, everything else as a JSON body.
    """
    record = await resolve(name, refresh=refresh)
    url = (f"{PLATFORM_INTERNAL_URL}/api/{record['app']}{record['path']}")
    method = record.get("method", "POST").upper()
    try:
        async with httpx.AsyncClient(
                timeout=timeout or _TIMEOUT) as client:
            if method == "GET":
                resp = await client.get(url, params=payload or {})
            else:
                resp = await client.request(method, url, json=payload or {})
    except httpx.HTTPError as exc:
        raise CapabilityError(
            f"capability '{name}' ({record['app']}) unreachable: {exc}"
        ) from exc
    if resp.status_code >= 400:
        raise CapabilityError(
            f"capability '{name}' ({record['app']}) failed: "
            f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        return resp.text


def invalidate_cache() -> None:
    _cache["capabilities"] = {}
    _cache["at"] = 0.0
