"""Settings mini-app — runtime-editable global config.

The agent's global configuration is *living* state, not build-time setup:
the user's profile (profile.json) and the editable AGENTS.md context
blocks can be read and updated at runtime — from the dashboard, by the
agent itself, or by any mini-app (via the settings.* capabilities). The
framework is a base for a growing agent, so these inputs adapt as the
user's needs change.

Files live in AGENT_HOME (the mounted agent home, ./.qwen-docker). If
that mount is missing the endpoints return 503 with a clear message.

Endpoints (mounted at /api/settings/):
  GET  /health
  GET  /profile                 the user profile object
  PUT  /profile                 replace the profile (any JSON object)
  GET  /context                 the editable AGENTS.md blocks
  PUT  /context                 {section, content} rewrite one block
  GET  /agents-md               full AGENTS.md text (read-only view)

Security: these endpoints write files. They are loopback-only like the
rest of the platform; do NOT expose /api/settings through any public
proxy.
"""
import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("miniapps.settings")

router = APIRouter()

AGENT_HOME = Path(os.environ.get("AGENT_HOME", "/agent-home"))
PROFILE_PATH = AGENT_HOME / "profile.json"
AGENTS_MD_PATH = AGENT_HOME / "AGENTS.md"

# Editable blocks are fenced in AGENTS.md with marker comments:
#   <!-- BEGIN-EDITABLE: about-user --> ... <!-- END-EDITABLE: about-user -->
_BLOCK_RE = (
    r"<!--\s*BEGIN-EDITABLE:\s*(?P<name>[\w-]+)\s*-->"
    r"(?P<body>.*?)"
    r"<!--\s*END-EDITABLE:\s*(?P=name)\s*-->"
)
KNOWN_SECTIONS = ("about-user", "standing-instructions")


def _home_ok() -> bool:
    return AGENT_HOME.is_dir()


def _no_home() -> JSONResponse:
    return JSONResponse(
        {"error": f"AGENT_HOME not found at {AGENT_HOME} — is the agent "
                  "home mounted into the platform container?"},
        status_code=503)


# ── profile ──────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "app": "settings",
            "agent_home": str(AGENT_HOME), "mounted": _home_ok()}


@router.get("/profile")
async def get_profile():
    if not _home_ok():
        return _no_home()
    if not PROFILE_PATH.is_file():
        return {"profile": {}, "note": "no profile.json yet — PUT one to create it"}
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return JSONResponse({"error": f"profile.json is invalid: {exc}"}, status_code=500)
    return {"profile": data}


@router.put("/profile")
async def put_profile(payload: dict):
    if not _home_ok():
        return _no_home()
    profile = payload.get("profile", payload)
    if not isinstance(profile, dict):
        return JSONResponse({"error": "profile must be a JSON object"}, status_code=400)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    log.info("settings: profile.json updated (%d top-level keys)", len(profile))
    return {"ok": True, "profile": profile}


# ── AGENTS.md editable context ───────────────────────────────────────


def _read_blocks() -> dict:
    text = AGENTS_MD_PATH.read_text(encoding="utf-8")
    return {m.group("name"): m.group("body").strip()
            for m in re.finditer(_BLOCK_RE, text, re.DOTALL)}


@router.get("/context")
async def get_context():
    if not _home_ok():
        return _no_home()
    if not AGENTS_MD_PATH.is_file():
        return JSONResponse({"error": "AGENTS.md not found"}, status_code=503)
    return {"sections": _read_blocks()}


@router.put("/context")
async def put_context(payload: dict):
    if not _home_ok():
        return _no_home()
    section = (payload.get("section") or "").strip()
    content = payload.get("content")
    if section not in KNOWN_SECTIONS:
        return JSONResponse(
            {"error": f"section must be one of {list(KNOWN_SECTIONS)}"},
            status_code=400)
    if content is None:
        return JSONResponse({"error": "content is required"}, status_code=400)
    if not AGENTS_MD_PATH.is_file():
        return JSONResponse({"error": "AGENTS.md not found"}, status_code=503)

    text = AGENTS_MD_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(<!--\s*BEGIN-EDITABLE:\s*" + re.escape(section) + r"\s*-->)"
        r".*?"
        r"(<!--\s*END-EDITABLE:\s*" + re.escape(section) + r"\s*-->)",
        re.DOTALL)
    if not pattern.search(text):
        return JSONResponse(
            {"error": f"no editable block '{section}' found in AGENTS.md"},
            status_code=404)
    body = "\n" + str(content).strip() + "\n"
    new_text = pattern.sub(lambda m: m.group(1) + body + m.group(2), text, count=1)
    AGENTS_MD_PATH.write_text(new_text, encoding="utf-8")
    log.info("settings: AGENTS.md block '%s' updated", section)
    return {"ok": True, "section": section}


@router.get("/agents-md")
async def get_agents_md():
    if not _home_ok():
        return _no_home()
    if not AGENTS_MD_PATH.is_file():
        return JSONResponse({"error": "AGENTS.md not found"}, status_code=503)
    return {"text": AGENTS_MD_PATH.read_text(encoding="utf-8")}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
