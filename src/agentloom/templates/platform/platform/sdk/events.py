# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""In-process event bus for cross-mini-app communication.

Mini-apps publish domain events; other mini-apps subscribe in their
manifest (`subscribes`) and implement `handle_event(event)` in main.py.
Events are delivered in-process only (not durable). Event names use
dot-notation: `alert.created`, `invoice.received`, ...
"""
import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List

log = logging.getLogger("sdk.events")

Handler = Callable[[Dict[str, Any]], Coroutine]
_subscribers: Dict[str, List[Handler]] = {}


def subscribe(event_type: str, handler: Handler) -> None:
    _subscribers.setdefault(event_type, []).append(handler)
    log.debug("Subscribed to %s (%d handlers)", event_type, len(_subscribers[event_type]))


async def publish(event_type: str, data: Dict[str, Any]) -> int:
    """Publish an event. Returns number of handlers notified."""
    event = {"type": event_type, "data": data}
    handlers = _subscribers.get(event_type, [])
    if not handlers:
        return 0

    # Per-handler timeout: one broken handler must not block the others.
    async def _call_with_timeout(handler):
        try:
            await asyncio.wait_for(handler(event), timeout=30)
        except asyncio.TimeoutError:
            log.error("Event handler timed out for %s", event_type)
        except Exception:
            log.exception("Event handler failed for %s", event_type)

    await asyncio.gather(*[_call_with_timeout(h) for h in handlers])
    return len(handlers)


def get_subscribers() -> Dict[str, int]:
    return {evt: len(hs) for evt, hs in _subscribers.items()}


def clear() -> None:
    _subscribers.clear()
