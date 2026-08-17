"""In-process event bus for cross-mini-app communication.

Mini-apps publish domain events; other mini-apps subscribe in their
manifest (`subscribes`) and implement `handle_event(event)` in main.py.
Events are delivered in-process only (not durable). Event names use
dot-notation: `alert.created`, `invoice.received`, ...
"""
import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

log = logging.getLogger("agentboom_sdk.events")

Handler = Callable[[Dict[str, Any]], Coroutine]
# (key, handler) pairs. key lets a caller replace its own previous
# subscription — the gateway re-subscribes mini-apps on every hot reload,
# and without replacement each reload would duplicate every handler.
_subscribers: Dict[str, List[Tuple[Optional[str], Handler]]] = {}


def subscribe(event_type: str, handler: Handler, key: Optional[str] = None) -> None:
    """Register a handler. With `key`, any earlier subscription carrying the
    same key on this event type is replaced (idempotent re-registration)."""
    handlers = _subscribers.setdefault(event_type, [])
    if key is not None:
        handlers[:] = [(k, h) for k, h in handlers if k != key]
    handlers.append((key, handler))
    log.debug("Subscribed to %s (%d handlers)", event_type, len(handlers))


def unsubscribe(event_type: str, key: str) -> int:
    """Remove every subscription of `key` on this event type. Returns count."""
    handlers = _subscribers.get(event_type, [])
    before = len(handlers)
    handlers[:] = [(k, h) for k, h in handlers if k != key]
    return before - len(handlers)


def unsubscribe_key(key: str) -> int:
    """Remove every subscription of `key` across all event types. Returns
    the number of handlers removed (an unloaded mini-app leaves no trace)."""
    removed = 0
    for event_type in list(_subscribers):
        removed += unsubscribe(event_type, key)
    return removed


async def publish(event_type: str, data: Dict[str, Any]) -> int:
    """Publish an event. Returns number of handlers notified."""
    event = {"type": event_type, "data": data}
    handlers = [h for _, h in _subscribers.get(event_type, [])]
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
