"""Durable event bus — publish once, deliver at-least-once over HTTP.

The in-process bus (agentboom_sdk.events) is fast but loses events when the
gateway restarts and cannot reach Node sidecars. This bus is the durable
half of the same idea:

- ``publish()`` records the event in ``events_log`` (``dedupe_key`` makes
  re-ingestion idempotent) and queues one delivery row per subscriber.
- ``drain()`` POSTs every due delivery to its endpoint, claims rows
  atomically (two drains never hand the same delivery to two POSTs), and
  retries failures with exponential backoff until ``max_retries`` — then
  the row is ``dead`` and visible in ``health()``.
- ``replay()`` re-queues a past event, e.g. after fixing a broken handler
  or for a subscriber that did not exist when the event was published.

Portability: every timestamp is an ISO-8601 UTC string written by Python
and payload/response are JSON in TEXT, so the same module runs unchanged
on SQLite (the default) and PostgreSQL. The only backend-specific code is
the claim: PostgreSQL uses ``FOR UPDATE SKIP LOCKED``, SQLite takes the
write lock for the whole claim (it serialises everything anyway).

Subscribers declare a PATH (``/api/mfa-relay/on-mfa``); delivery resolves
it against ``PLATFORM_INTERNAL_URL``. A full http(s) URL is also accepted
for external subscribers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from . import db

log = logging.getLogger("agentboom_sdk.durable_events")

DELIVERY_TIMEOUT_SEC = 30
MAX_PARALLEL_DELIVERIES = 8
# A claim holds a row 'delivering' for this long: longer than the delivery
# timeout, so a live claim is never stolen mid-flight, but a gateway that
# dies mid-delivery leaves the row due again instead of wedged.
CLAIM_WINDOW_SEC = 45
MAX_BACKOFF_SEC = 300

INTERNAL_URL = os.environ.get("PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")

_bg_tasks: set = set()


def _spawn(coro) -> None:
    """Run a drain without blocking the publisher; keep a reference so the
    task cannot be garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _later(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat()


def _matches(subscribed: str, event_type: str) -> bool:
    """'email.*' matches 'email.received'; '*' matches everything."""
    if subscribed in (event_type, "*"):
        return True
    if subscribed.endswith(".*"):
        prefix = subscribed[:-1]  # keep the dot: 'email.'
        return event_type.startswith(prefix)
    return False


def _resolve_endpoint(endpoint: str) -> str:
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return INTERNAL_URL.rstrip("/") + endpoint


# ─────────────────────────────────────────────────────────────────────────────
# Subscriptions
# ─────────────────────────────────────────────────────────────────────────────

async def register_subscription(
    app_name: str, event_type: str, endpoint: str, max_retries: int = 5
) -> None:
    """Declare that `app_name` wants `event_type` delivered to `endpoint`."""
    await db.execute(
        """INSERT INTO events_subscriptions
             (app_name, event_type, endpoint, max_retries, is_enabled, updated_at)
           VALUES ($1, $2, $3, $4, 1, $5)
           ON CONFLICT (app_name, event_type, endpoint)
           DO UPDATE SET is_enabled = 1, max_retries = $4, updated_at = $5""",
        app_name, event_type, endpoint, max_retries, _now(),
    )


async def clear_subscriptions(app_name: str) -> None:
    """Disable every subscription for an app before re-declaring from its
    manifest. Disabled rather than deleted so an app that is briefly
    unloaded during a reload keeps its delivery history."""
    await db.execute(
        "UPDATE events_subscriptions SET is_enabled = 0, updated_at = $1 "
        "WHERE app_name = $2",
        _now(), app_name,
    )


async def subscribers_for(event_type: str) -> List[dict]:
    """Enabled subscriptions matching an event type, wildcards included."""
    rows = await db.fetchall(
        "SELECT app_name, event_type, endpoint, max_retries "
        "FROM events_subscriptions WHERE is_enabled = 1"
    )
    return [r for r in rows if _matches(r["event_type"], event_type)]


# ─────────────────────────────────────────────────────────────────────────────
# Publishing
# ─────────────────────────────────────────────────────────────────────────────

async def publish(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    source: str = "platform",
    subject: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    deliver_now: bool = True,
) -> Optional[int]:
    """Record an event and queue it for every subscriber.

    Returns the event id, or None if `dedupe_key` matched an event already
    published — which makes re-processing a mailbox (or a retrying connector)
    safe.
    """
    now = _now()
    row = await db.fetchone(
        """INSERT INTO events_log (type, source, subject, payload, dedupe_key, published_at)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id""",
        event_type, source, subject,
        json.dumps(payload or {}, default=str), dedupe_key, now,
    )
    if not row:
        log.debug("Event %s/%s already published — skipping", event_type, dedupe_key)
        return None

    event_id = int(row["id"])
    subscribers = await subscribers_for(event_type)
    for sub in subscribers:
        await db.execute(
            """INSERT INTO events_deliveries
                 (event_id, app_name, endpoint, max_retries, status, attempts,
                  next_retry_at, created_at)
               VALUES ($1, $2, $3, $4, 'pending', 0, $5, $5)
               ON CONFLICT (event_id, app_name, endpoint) DO NOTHING""",
            event_id, sub["app_name"], sub["endpoint"], sub["max_retries"], now,
        )

    log.info("Published %s (id=%d) to %d subscriber(s)",
             event_type, event_id, len(subscribers))

    if deliver_now and subscribers:
        _spawn(drain())
    return event_id


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

async def _claim_due(limit: int) -> List[dict]:
    """Atomically select the due deliveries and flip them to 'delivering'."""
    if db.is_postgres():
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """WITH claimed AS (
                     UPDATE events_deliveries d
                        SET status = 'delivering',
                            next_retry_at = $2
                       WHERE d.id IN (
                                 SELECT id FROM (
                                    SELECT id FROM events_deliveries
                                     WHERE (
                                         (status IN ('pending', 'failed')
                                          AND next_retry_at <= $1)
                                         OR (status = 'delivering'
                                             AND next_retry_at <= $1)
                                       )
                                     ORDER BY next_retry_at
                                     LIMIT $3
                                     FOR UPDATE SKIP LOCKED
                                 ) due
                              )
                     RETURNING d.id
                   )
                   SELECT d.id, d.event_id, d.app_name, d.endpoint,
                          d.attempts, d.max_retries,
                          e.type, e.source, e.subject, e.payload, e.published_at
                     FROM claimed c
                     JOIN events_deliveries d ON d.id = c.id
                     JOIN events_log e ON e.id = d.event_id""",
                _now(), _later(CLAIM_WINDOW_SEC), limit,
            )
        return [dict(r) for r in rows]

    # SQLite: select, claim, read — inside one transaction, which holds the
    # backend-wide write lock, so nothing else can interleave. Raw conn:
    # `?` placeholders only, no per-statement commit (db.execute commits).
    async with db.transaction() as conn:
        now_s, claim_until = _now(), _later(CLAIM_WINDOW_SEC)
        cursor = await conn.execute(
            "SELECT id FROM events_deliveries WHERE "
            "(status IN ('pending', 'failed') AND next_retry_at <= ?) "
            "OR (status = 'delivering' AND next_retry_at <= ?) "
            "ORDER BY next_retry_at LIMIT ?",
            (now_s, now_s, limit),
        )
        due_ids = [r[0] for r in await cursor.fetchall()]
        if not due_ids:
            return []
        ph = ", ".join("?" for _ in due_ids)
        await conn.execute(
            "UPDATE events_deliveries SET status = 'delivering', "
            f"next_retry_at = ? WHERE id IN ({ph})",
            [claim_until, *due_ids],
        )
        cursor = await conn.execute(
            f"""SELECT d.id, d.event_id, d.app_name, d.endpoint, d.attempts,
                       d.max_retries, e.type, e.source, e.subject, e.payload,
                       e.published_at
                FROM events_deliveries d
                JOIN events_log e ON e.id = d.event_id
                WHERE d.id IN ({ph})""",
            due_ids,
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def _deliver_one(delivery: dict, client: httpx.AsyncClient) -> None:
    """POST one event to one subscriber and record the outcome."""
    delivery_id = delivery["id"]
    attempts = (delivery["attempts"] or 0) + 1
    try:
        payload = delivery["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        payload = {"raw": str(delivery["payload"])}
    body = {
        "event_id": delivery["event_id"],
        "type": delivery["type"],
        "source": delivery["source"],
        "subject": delivery["subject"],
        "payload": payload,
        "published_at": delivery["published_at"],
        "attempt": attempts,
    }

    url = _resolve_endpoint(delivery["endpoint"])
    try:
        resp = await client.post(url, json=body)
        if resp.status_code < 400:
            try:
                parsed = resp.json()
            except Exception:
                parsed = {"status_code": resp.status_code}
            await db.execute(
                """UPDATE events_deliveries
                      SET status = 'delivered', attempts = $2, delivered_at = $3,
                          response = $4, last_error = NULL
                    WHERE id = $1""",
                delivery_id, attempts, _now(),
                json.dumps(parsed, default=str)[:4000],
            )
            return
        error = f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:400]

    max_retries = delivery["max_retries"] or 5
    if attempts >= max_retries:
        await db.execute(
            "UPDATE events_deliveries SET status = 'dead', attempts = $2, "
            "last_error = $3 WHERE id = $1",
            delivery_id, attempts, error,
        )
        log.warning("Delivery %d to %s is dead after %d attempts: %s",
                    delivery_id, delivery["app_name"], attempts, error)
        return

    # Exponential backoff, capped — a subscriber that is down for an hour
    # should not be retried every few seconds for that hour.
    delay = min(2 ** attempts, MAX_BACKOFF_SEC)
    await db.execute(
        """UPDATE events_deliveries
              SET status = 'failed', attempts = $2, last_error = $3,
                  next_retry_at = $4
            WHERE id = $1""",
        delivery_id, attempts, error, _later(delay),
    )
    log.info("Delivery %d to %s failed (attempt %d), retrying in %ds: %s",
             delivery_id, delivery["app_name"], attempts, delay, error)


async def drain(limit: int = 100) -> Dict[str, int]:
    """Attempt every delivery that is due. Safe to call concurrently."""
    rows = await _claim_due(limit)
    if not rows:
        return {"attempted": 0}

    semaphore = asyncio.Semaphore(MAX_PARALLEL_DELIVERIES)

    async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SEC) as client:
        async def guarded(delivery: dict) -> None:
            async with semaphore:
                await _deliver_one(delivery, client)

        await asyncio.gather(*(guarded(r) for r in rows), return_exceptions=True)
    return {"attempted": len(rows)}


# ─────────────────────────────────────────────────────────────────────────────
# Introspection
# ─────────────────────────────────────────────────────────────────────────────

async def replay(event_id: int, app_name: Optional[str] = None) -> Dict[str, Any]:
    """Re-deliver a past event (after fixing a handler, or to a subscriber
    that did not exist when the event was first published)."""
    event = await db.fetchone("SELECT type FROM events_log WHERE id = $1", event_id)
    if not event:
        raise KeyError(f"No event {event_id}")

    subscribers = await subscribers_for(event["type"])
    if app_name:
        subscribers = [s for s in subscribers if s["app_name"] == app_name]
    if not subscribers:
        return {"ok": True, "queued": 0, "note": "no matching subscribers"}

    now = _now()
    for sub in subscribers:
        await db.execute(
            """INSERT INTO events_deliveries
                 (event_id, app_name, endpoint, max_retries, status, attempts,
                  next_retry_at, created_at)
               VALUES ($1, $2, $3, $4, 'pending', 0, $5, $5)
               ON CONFLICT (event_id, app_name, endpoint)
               DO UPDATE SET status = 'pending', attempts = 0,
                             next_retry_at = $5, last_error = NULL""",
            event_id, sub["app_name"], sub["endpoint"], sub["max_retries"], now,
        )
    await drain()
    return {"ok": True, "queued": len(subscribers)}


async def health() -> Dict[str, Any]:
    """Delivery health — check when a reaction stops happening."""
    counts = await db.fetchall(
        "SELECT app_name, status, count(*) AS n FROM events_deliveries "
        "GROUP BY app_name, status"
    )
    agg: Dict[str, Dict[str, Any]] = {}
    for c in counts:
        entry = agg.setdefault(
            c["app_name"],
            {"app_name": c["app_name"], "delivered": 0, "in_flight": 0, "dead": 0},
        )
        if c["status"] == "delivered":
            entry["delivered"] = c["n"]
        elif c["status"] == "dead":
            entry["dead"] = c["n"]
        else:
            entry["in_flight"] = c["n"]
    by_subscriber = sorted(agg.values(), key=lambda e: e["app_name"])

    overdue = await db.fetchval(
        "SELECT count(*) FROM events_deliveries WHERE status IN "
        "('pending', 'failed', 'delivering') AND next_retry_at < $1",
        _later(-300),
    )
    return {"by_subscriber": by_subscriber, "overdue_deliveries": int(overdue or 0)}


async def recent_events(
    type: Optional[str] = None, subject: Optional[str] = None, limit: int = 50
) -> List[dict]:
    """Recent events, newest first."""
    conditions, params = [], []
    if type:
        params.append(type)
        conditions.append(f"type = ${len(params)}")
    if subject:
        params.append(subject)
        conditions.append(f"subject = ${len(params)}")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return await db.fetchall(
        f"""SELECT id, type, source, subject, payload, published_at
            FROM events_log {where}
            ORDER BY published_at DESC, id DESC
            LIMIT ${len(params)}""",
        *params,
    )
