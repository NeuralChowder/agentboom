"""Events mini-app — durable event bus (agentboom package: events).

HTTP face of agentboom_sdk.durable_events: publish once, deliver
at-least-once to every subscriber, retry with backoff, replay the past.

Endpoints (mounted at /api/events/):
  GET    /health                    delivery health (also a stats view source)
  GET    /log?type=&subject=&limit= recent events
  POST   /publish     {type, payload, source?, subject?, dedupe_key?}
  POST   /drain                       manifest job: retry every due delivery
  GET    /subscriptions
  POST   /subscriptions {app_name, event_type, endpoint, max_retries?}
  DELETE /subscriptions/{app_name}
  POST   /replay/{event_id}?app_name=
"""
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from agentboom_sdk import durable_events

log = logging.getLogger("miniapps.events")

router = APIRouter()


@router.get("/health")
async def health():
    result = await durable_events.health()
    delivered = sum(e["delivered"] for e in result["by_subscriber"])
    in_flight = sum(e["in_flight"] for e in result["by_subscriber"])
    dead = sum(e["dead"] for e in result["by_subscriber"])
    events = await db.fetchval("SELECT count(*) FROM events_log")
    return {
        "status": "ok",
        "app": "events",
        "events": int(events or 0),
        "delivered": delivered,
        "in_flight": in_flight,
        "dead": dead,
        "overdue": result["overdue_deliveries"],
    }


@router.get("/log")
async def event_log(
    type: str = Query(None), subject: str = Query(None), limit: int = Query(50)
):
    rows = await durable_events.recent_events(
        type=type, subject=subject, limit=max(1, min(limit, 500))
    )
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {"events": rows}


@router.post("/publish")
async def publish(body: dict):
    event_type = body.get("type")
    if not event_type:
        return JSONResponse({"error": "type is required"}, status_code=400)
    event_id = await durable_events.publish(
        event_type,
        body.get("payload") or {},
        source=body.get("source") or "api",
        subject=body.get("subject"),
        dedupe_key=body.get("dedupe_key"),
    )
    return {"ok": True, "event_id": event_id, "duplicate": event_id is None}


@router.post("/drain")
async def drain():
    """Manifest job target: attempt every due delivery. Safe to call by hand."""
    return {"ok": True, **(await durable_events.drain())}


@router.get("/subscriptions")
async def list_subscriptions():
    rows = await db.fetchall(
        "SELECT app_name, event_type, endpoint, is_enabled, max_retries "
        "FROM events_subscriptions ORDER BY event_type, app_name"
    )
    return {"subscriptions": rows, "count": len(rows)}


@router.post("/subscriptions")
async def add_subscription(body: dict):
    app_name = (body.get("app_name") or "").strip()
    event_type = (body.get("event_type") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    if not (app_name and event_type and endpoint):
        return JSONResponse(
            {"error": "app_name, event_type and endpoint are required"},
            status_code=400,
        )
    if endpoint.startswith("/") and not endpoint.startswith("/api/"):
        return JSONResponse(
            {"error": "endpoint must be a gateway path under /api/ "
                      "(e.g. /api/mfa-relay/on-mfa) or a full http(s) URL"},
            status_code=400,
        )
    await durable_events.register_subscription(
        app_name, event_type, endpoint, int(body.get("max_retries") or 5)
    )
    return {"ok": True}


@router.delete("/subscriptions/{app_name}")
async def remove_subscriptions(app_name: str):
    await durable_events.clear_subscriptions(app_name)
    return {"ok": True, "disabled_for": app_name}


@router.post("/replay/{event_id}")
async def replay(event_id: int, app_name: str = Query(None)):
    try:
        return {"ok": True, **(await durable_events.replay(event_id, app_name))}
    except KeyError:
        return JSONResponse({"error": f"no event {event_id}"}, status_code=404)


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
