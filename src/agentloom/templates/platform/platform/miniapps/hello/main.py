"""Hello mini-app — the tour guide for {{AGENT_TITLE}}.

Demonstrates the three ways a mini-app grows:
1. HTTP endpoints — mounted at /api/hello/...
2. Scheduled jobs — manifest `jobs` (see .miniapp.json; disabled by default)
3. Event subscribers — manifest `subscribes` + handle_event() below
"""
import logging

from fastapi import APIRouter

from agentloom_sdk import events

log = logging.getLogger("miniapps.hello")

router = APIRouter()
_heartbeats = 0


@router.get("/")
async def about():
    return {
        "app": "hello",
        "message": "This mini-app is a working example. Replace or delete it.",
        "heartbeats": _heartbeats,
    }


@router.get("/health")
async def health():
    return {"status": "ok", "app": "hello"}


@router.post("/heartbeat/run")
async def heartbeat_run():
    """Job target for the (disabled) heartbeat job in .miniapp.json."""
    global _heartbeats
    _heartbeats += 1
    log.info("heartbeat #%d", _heartbeats)
    await events.publish("hello.beat", {"count": _heartbeats})
    return {"ok": True, "heartbeats": _heartbeats}


async def handle_event(event: dict) -> None:
    """Called for every event type listed under `subscribes` in the manifest."""
    log.info("hello received event: %s", event.get("type"))


def get_router() -> APIRouter:
    """Gateway contract: called at load and on every hot-reload."""
    return router
