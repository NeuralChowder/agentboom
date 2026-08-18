"""Agent/user profile — the minimal global configuration.

Reads `profile.json` (shipped in the agent home, user-editable) and
exposes the few facts an agent needs to do a great job without anyone
having to configure much:

    from agentboom_sdk.profile import (
        get_profile, user_name, language, timezone, country, currency,
        effective_timezone, effective_country, is_away,
    )

Fields (all optional; sensible defaults apply when absent):
    user.name / user.preferred_name   who the agent serves
    language                          "auto" (mirror the user) or a pinned code
    timezone                          home IANA zone (e.g. Europe/Lisbon)
    country / currency                home ISO country / ISO-4217 currency
    away.timezone / away.country      set only while travelling; override home

Resolution order for the file: $AGENT_PROFILE, then $QWEN_HOME/profile.json,
then ~/.qwen/profile.json, then /home/user/.qwen/profile.json. Missing file
or fields degrade to defaults — never an error.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("agentboom_sdk.profile")

_DEFAULTS: Dict[str, Any] = {
    "user": {"name": "", "preferred_name": ""},
    "language": "auto",
    "timezone": "UTC",
    "country": "",
    "currency": "",
    "away": {"timezone": None, "country": None},
}

_cache: Optional[Dict[str, Any]] = None


def _candidate_paths() -> list:
    paths = []
    env = os.environ.get("AGENT_PROFILE")
    if env:
        paths.append(Path(env))
    qwen_home = os.environ.get("QWEN_HOME")
    if qwen_home:
        paths.append(Path(qwen_home) / "profile.json")
    paths.append(Path.home() / ".qwen" / "profile.json")
    paths.append(Path("/home/user/.qwen/profile.json"))
    return paths


def get_profile(force_reload: bool = False) -> Dict[str, Any]:
    """The merged profile (defaults <- file). Cached; never raises."""
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    data: Dict[str, Any] = {}
    for path in _candidate_paths():
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                break
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("profile: unreadable %s: %s", path, exc)
    merged = json.loads(json.dumps(_DEFAULTS))  # deep copy
    for key, value in (data or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    _cache = merged
    return _cache


def user_name() -> str:
    user = get_profile().get("user") or {}
    return user.get("preferred_name") or user.get("name") or ""


def language() -> str:
    """'auto' (mirror the user's language) or a pinned language code."""
    return get_profile().get("language") or "auto"


def timezone() -> str:
    return get_profile().get("timezone") or "UTC"


def country() -> str:
    return get_profile().get("country") or ""


def currency() -> str:
    return get_profile().get("currency") or ""


def is_away() -> bool:
    away = get_profile().get("away") or {}
    return bool(away.get("timezone") or away.get("country"))


def effective_timezone() -> str:
    """The timezone to act with: away overrides home while travelling."""
    away = (get_profile().get("away") or {}).get("timezone")
    return away or timezone()


def effective_country() -> str:
    away = (get_profile().get("away") or {}).get("country")
    return away or country()
