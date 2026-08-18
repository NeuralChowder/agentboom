"""Reminders mini-app — due-date reminders with optional recurrence
(agentboom package: reminders).

A once-a-minute job delivers anything that is due. Delivery is pushed to
the phone through the ntfy connector when that package is installed;
without it, reminders are still tracked and listed. Recurring reminders
re-arm instead of disappearing.

Endpoints (mounted at /api/reminders/):
  GET    /health
  GET    /reminders?include_delivered=
  POST   /reminders             {text, due_at, recurrence?}
  DELETE /reminders/{id}
  POST   /reminders/{id}/done
  POST   /reminders/{id}/snooze {minutes}
  POST   /check-due             manifest job target (every minute)
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db, events

log = logging.getLogger("miniapps.reminders")

router = APIRouter()

_TS = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _parse_due(raw: str) -> datetime:
    for fmt in (_TS, "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise ValueError(f"unrecognised due_at '{raw}' — use YYYY-MM-DD HH:MM")


async def _deliver(text: str) -> bool:
    """Push via the ntfy connector when installed; best-effort."""
    try:
        from connectors.ntfy import enabled, send
    except ImportError:
        return False
    if not enabled():
        return False
    try:
        await send(text, title="reminder", priority=4, tags=["alarm_clock"])
        return True
    except Exception as exc:  # noqa: BLE001 — delivery is best-effort
        log.warning("reminders: ntfy delivery failed: %s", exc)
        return False


@router.get("/health")
async def health():
    count = await db.fetchval(
        "SELECT count(*) FROM reminders WHERE delivered = 0")
    return {"status": "ok", "app": "reminders", "pending": count}


@router.get("/reminders")
async def list_reminders(include_delivered: bool = False):
    if include_delivered:
        rows = await db.fetchall(
            "SELECT * FROM reminders ORDER BY due_at DESC LIMIT 200")
    else:
        rows = await db.fetchall(
            "SELECT * FROM reminders WHERE delivered = 0 ORDER BY due_at LIMIT 200")
    return {"reminders": rows}


@router.post("/reminders")
async def add_reminder(payload: dict):
    text = (payload.get("text") or "").strip()
    raw_due = (payload.get("due_at") or "").strip()
    if not text or not raw_due:
        return JSONResponse({"error": "text and due_at are required"}, status_code=400)
    try:
        due = _parse_due(raw_due)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    recurrence = (payload.get("recurrence") or "").strip().lower() or None
    if recurrence and recurrence not in ("daily", "weekly"):
        return JSONResponse(
            {"error": "recurrence must be daily|weekly (or omitted)"}, status_code=400)
    await db.execute(
        "INSERT INTO reminders (text, due_at, recurrence) VALUES (?, ?, ?)",
        (text, _fmt(due), recurrence))
    row = await db.fetchone(
        "SELECT * FROM reminders WHERE id = last_insert_rowid()")
    return {"ok": True, "reminder": dict(row)}


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int):
    removed = await db.execute("DELETE FROM reminders WHERE id = ?", reminder_id)
    if not removed:
        return JSONResponse({"error": "no such reminder"}, status_code=404)
    return {"deleted": True}


@router.post("/reminders/{reminder_id}/done")
async def mark_done(reminder_id: int):
    updated = await db.execute(
        "UPDATE reminders SET delivered = 1, delivered_at = ? WHERE id = ?",
        (_fmt(_now()), reminder_id))
    if not updated:
        return JSONResponse({"error": "no such reminder"}, status_code=404)
    return {"ok": True}


@router.post("/reminders/{reminder_id}/snooze")
async def snooze(reminder_id: int, payload: dict):
    minutes = int(payload.get("minutes") or 60)
    row = await db.fetchone("SELECT * FROM reminders WHERE id = ?", reminder_id)
    if not row:
        return JSONResponse({"error": "no such reminder"}, status_code=404)
    new_due = _now() + timedelta(minutes=max(1, minutes))
    await db.execute(
        "UPDATE reminders SET due_at = ?, delivered = 0, delivered_at = NULL "
        "WHERE id = ?", (_fmt(new_due), reminder_id))
    return {"ok": True, "due_at": _fmt(new_due)}


@router.post("/check-due")
async def check_due():
    """Manifest job target: deliver everything that is due."""
    due = await db.fetchall(
        "SELECT * FROM reminders WHERE delivered = 0 AND due_at <= ? "
        "ORDER BY due_at", (_fmt(_now()),))
    delivered = []
    for reminder in due:
        pushed = await _deliver(reminder["text"])
        await events.publish("reminder.due", {
            "reminder_id": reminder["id"], "text": reminder["text"],
            "pushed": pushed,
        })
        if reminder["recurrence"] == "daily":
            nxt = _now() + timedelta(days=1)
        elif reminder["recurrence"] == "weekly":
            nxt = _now() + timedelta(weeks=1)
        else:
            nxt = None
        if nxt:
            await db.execute(
                "UPDATE reminders SET due_at = ?, delivered = 0 WHERE id = ?",
                (_fmt(nxt), reminder["id"]))
        else:
            await db.execute(
                "UPDATE reminders SET delivered = 1, delivered_at = ? WHERE id = ?",
                (_fmt(_now()), reminder["id"]))
        delivered.append(reminder["id"])
        log.info("reminders: delivered #%d '%s' (pushed=%s)",
                 reminder["id"], reminder["text"][:50], pushed)
    return {"ok": True, "delivered": delivered}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
