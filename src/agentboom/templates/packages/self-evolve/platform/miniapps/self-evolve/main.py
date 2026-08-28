"""Self-evolve — the agent's self-improvement loop, on a screen
(agentboom package: self-evolve).

The loop itself is an agent procedure (the self-evolve skill), fired
nightly and weekly by the scheduler's agent jobs. This app is its memory
and its control surface:

    runs    what each run looked at, changed, verified — the log
    backlog what it found but did not do — autonomous work the night drain
            builds, and the few proposals that wait on the user's decision

Two rules, each because the alternative fails quietly.

**The backlog is read before every run.** Without it the loop re-proposes
the same idea every night — the failure mode of a loop with no memory.
With it, a "seen, not now, because X" survives forever and is not
re-litigated.

**In-flight rows carry a visible lease, and the reclaim is the design.**
Implementation and repair turns run through the in-memory agent queue as
in-process tasks; a gateway restart kills them the way it kills any
in-flight work. Stale rows (inflight_stale_hours for the backlog, 2h for
running runs, the lease for repairs) are reclaimed by the ticks and the
nightly run instead of hanging "in-flight" forever.

Portable by doctrine: every timestamp is an ISO-8601 UTC string written
in Python, all JSON lives in TEXT columns, every query is dialect-neutral
($n placeholders, no SQL date arithmetic, no GREATEST), so the same code
runs on SQLite and PostgreSQL. Cross-app tables (the event bus, the
scheduler) are read through a declarative, fault-isolated registry — a
missing table degrades one metric or one repair source, never the loop.
"""
import asyncio
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentboom_sdk import activity
from agentboom_sdk import agent
from agentboom_sdk import db

log = logging.getLogger("miniapps.self-evolve")

router = APIRouter()

# Runtime settings. Values stored in selfevolve_settings — editable at
# runtime via the API, the dashboard form, or a conversation — override
# these defaults without a reload or a restart.
SETTING_DEFAULTS: Dict[str, Any] = {
    # Onboarding guard: the package ships OFF. The agent jobs no-op until
    # this is turned on (by the user or the onboarding flow).
    "enabled": False,
    # A backlog row in-flight longer than this has no live worker
    # (gateway restart, crash) and is reclaimed to open.
    "inflight_stale_hours": 12,
    # Silence budget for an implementation/repair turn: the agent turn
    # must finish within this window.
    "implement_timeout_sec": 1800,
    # The night drain stops starting new items at this local hour.
    "drain_cutoff_hour": 5,
    # "As many as fit before the cutoff" — the window is the governor;
    # the cap only guards against a pathological night of tiny failures.
    "drain_max_per_night": 12,
    # The loop's voice, rate-limited so a bug cannot spam.
    "notify_max_per_hour": 3,
    "notify_max_chars": 1500,
    # Selection over time: a change declares the metric it should move;
    # after outcome_measure_days the reconcile judges the delta. Past
    # baseline * regression_tolerance in the wrong direction = regressed.
    "outcome_measure_days": 3,
    "regression_tolerance": 1.0,
}

DRAIN_MAX_ATTEMPTS = 2
# The agent queue is serial ("one turn at a time"), so a waiting drain
# task costs nothing but keeps the queue warm: when the running item
# settles, the next one starts immediately. Depth 2 = one running + one
# waiting — bounded, no queue pile-up.
DRAIN_QUEUE_DEPTH = 2
REPAIR_MAX_ATTEMPTS = 2
ABANDONED_RUN_HOURS = 2
STALL_WINDOW_HOURS = 6
MODEL_PROBE_TIMEOUT_SEC = 15.0
GUARDRAIL_ALERT_COOLDOWN_HOURS = 72
RESOLUTION_MAX = 8000

# Strong references to in-flight tasks. asyncio does not keep a strong
# reference to a task on its own: without this set a create_task result
# can be garbage-collected mid-execution (documented stdlib hazard).
_inflight_tasks: "set[asyncio.Task]" = set()


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)


# ── time helpers (portable-SQL doctrine) ─────────────────────────────
# Every timestamp is an ISO-8601 UTC string written by Python; cutoffs
# are computed here and passed as parameters — never NOW() or SQL date
# arithmetic. All values carry the same +00:00 suffix, so string
# comparison is chronological.

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ago(**td) -> str:
    return (datetime.now(timezone.utc) - timedelta(**td)).replace(
        microsecond=0).isoformat()


def _shift(ts_iso: str, **td) -> str:
    return (datetime.fromisoformat(ts_iso) - timedelta(**td)).replace(
        microsecond=0).isoformat()


def _local_now() -> datetime:
    """Server local time, tz-aware — the drain window is local by design."""
    return datetime.now().astimezone()


def _sched_ago(**td) -> str:
    """Cutoff in the scheduler's storage format (UTC 'YYYY-MM-DD HH:MM:SS').

    job_rows/schedule_jobs store that shape (agentboom_sdk.services.
    scheduler), and a mixed-format string comparison would misorder
    same-day rows."""
    return (datetime.now(timezone.utc) - timedelta(**td)).strftime(
        "%Y-%m-%d %H:%M:%S")


# ── settings ────────────────────────────────────────────────────────

async def _ensure_settings() -> None:
    count = await db.fetchval("SELECT COUNT(*) FROM selfevolve_settings")
    if count:
        return
    now = _now()
    for key, value in SETTING_DEFAULTS.items():
        await db.execute(
            "INSERT INTO selfevolve_settings (key, value, updated_at) "
            "VALUES ($1, $2, $3) ON CONFLICT (key) DO NOTHING",
            key, json.dumps(value), now)


async def _get_settings() -> Dict[str, Any]:
    await _ensure_settings()
    out = dict(SETTING_DEFAULTS)
    for row in await db.fetchall(
            "SELECT key, value FROM selfevolve_settings"):
        value = row["value"]
        if isinstance(value, (str, bytes, bytearray)):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        out[row["key"]] = value
    return out


def _as_bool(value: Union[bool, str, None], name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "1", "on", "yes"):
            return True
        if value.lower() in ("false", "0", "off", "no"):
            return False
    raise ValueError(f"{name} must be a boolean")


# ---------------------------------------------------------------------------
# Health, runs
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    settings = await _get_settings()
    runs = await db.fetchval("SELECT COUNT(*) FROM selfevolve_runs")
    return {"status": "ok", "app": "self-evolve",
            "enabled": bool(settings.get("enabled")),
            "runs": int(runs or 0)}


class RunStart(BaseModel):
    trigger: str = Field("schedule", pattern="^(schedule|manual|follow-up)$")


@router.post("/runs", status_code=201)
async def start_run(req: RunStart):
    settings = await _get_settings()
    if not settings.get("enabled"):
        raise HTTPException(
            409, "self-evolve is disabled — set enabled=true in settings "
                 "before starting runs")
    row = await db.fetchone(
        """INSERT INTO selfevolve_runs (trigger, started_at, status)
           VALUES ($1, $2, 'running')
           RETURNING id, started_at, status""",
        req.trigger, _now())
    return {"ok": True, "run": dict(row)}


class ExpectedMetric(BaseModel):
    """A change's declared expectation: which fitness metric it should
    move, and which way. This is what makes selection possible later —
    the reconcile step measures the delta against the baseline and
    keeps or reverts the change by evidence, not by trust."""
    name: str = Field(..., min_length=2, max_length=80)
    direction: str = Field(..., pattern="^(up|down)$")
    baseline: float


class RunFinish(BaseModel):
    findings: Optional[str] = None
    changes: Optional[str] = None
    message_sent: bool = False
    error: Optional[str] = None
    expected_metrics: Optional[List[ExpectedMetric]] = None
    genome_commit: Optional[str] = Field(None, max_length=64)


@router.post("/runs/{run_id}/finish")
async def finish_run(run_id: int, req: RunFinish):
    status = "failed" if req.error else "done"
    row = await db.fetchone(
        """UPDATE selfevolve_runs
               SET finished_at = $2, status = $3, findings = $4,
                   changes = $5, message_sent = $6, error = $7
             WHERE id = $1
           RETURNING id, status""",
        run_id, _now(), status, req.findings, req.changes,
        1 if req.message_sent else 0, req.error)
    if not row:
        raise HTTPException(404, f"run {run_id} not found")
    if not req.error and req.expected_metrics:
        summary = (req.changes or f"run {run_id} change")[:300]
        now = _now()
        for em in req.expected_metrics:
            await db.execute(
                """INSERT INTO selfevolve_change_outcomes
                     (run_id, genome_commit, change_summary, metric_name,
                      direction, baseline_value, verdict, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)""",
                run_id, req.genome_commit, summary, em.name, em.direction,
                float(em.baseline), now)
    return {"ok": True, "run": dict(row)}


@router.get("/runs")
async def list_runs(limit: int = Query(40, ge=1, le=200)):
    rows = await db.fetchall(
        """SELECT id, started_at, finished_at, trigger, status, findings,
                  changes, message_sent, error
             FROM selfevolve_runs
            ORDER BY started_at DESC, id DESC
            LIMIT $1""",
        limit)
    return {"ok": True, "runs": [dict(r) for r in rows], "count": len(rows)}


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------

class BacklogAdd(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    why: str = Field(..., min_length=10, max_length=4000)
    tier: str = Field("proposal", pattern="^(autonomous|proposal|deferred)$")
    evidence: Optional[str] = Field(None, max_length=500)


@router.get("/backlog")
async def list_backlog(status: Optional[str] = Query(None),
                       limit: int = Query(100, ge=1, le=500)):
    if status:
        rows = await db.fetchall(
            """SELECT id, title, why, tier, evidence, status, resolution,
                      drain_attempts, created_at, updated_at,
                      (status = 'open') AS can_adopt,
                      (status IN ('open', 'in-flight')) AS can_dismiss
                 FROM selfevolve_backlog
                WHERE status = $1
                ORDER BY CASE status
                             WHEN 'in-flight' THEN 0 WHEN 'open' THEN 1
                             ELSE 2 END,
                         updated_at DESC
                LIMIT $2""",
            status, limit)
    else:
        rows = await db.fetchall(
            """SELECT id, title, why, tier, evidence, status, resolution,
                      drain_attempts, created_at, updated_at,
                      (status = 'open') AS can_adopt,
                      (status IN ('open', 'in-flight')) AS can_dismiss
                 FROM selfevolve_backlog
                ORDER BY CASE status
                             WHEN 'in-flight' THEN 0 WHEN 'open' THEN 1
                             ELSE 2 END,
                         updated_at DESC
                LIMIT $1""",
            limit)
    return {"ok": True, "items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/backlog", status_code=201)
async def add_backlog(req: BacklogAdd):
    """One entry per idea. An open item with the same title is the same
    idea."""
    existing = await db.fetchone(
        "SELECT id, status FROM selfevolve_backlog "
        "WHERE lower(title) = lower($1) LIMIT 1",
        req.title)
    if existing and existing["status"] in ("open", "in-flight"):
        return {
            "ok": True, "duplicate": True,
            "item": {"id": existing["id"], "status": existing["status"]},
            "message": (f"Already tracked as backlog #{existing['id']} "
                        f"({existing['status']})."),
        }
    now = _now()
    row = await db.fetchone(
        """INSERT INTO selfevolve_backlog
             (title, why, tier, evidence, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $5)
           RETURNING id, title, why, tier, evidence, status""",
        req.title, req.why, req.tier, req.evidence, now)
    return {"ok": True, "duplicate": False, "item": dict(row)}


def _implement_prompt(item: dict) -> str:
    """The implementation contract, shared by manual Adopt and the night
    drain — one set of guardrails, two entry points."""
    return f"""You are implementing an APPROVED self-evolve backlog item for this agent's platform.

ITEM #{item['id']}: {item['title']}
WHY: {item['why']}
EVIDENCE: {item.get('evidence') or '(none recorded)'}

First read the self-evolve skill at .qwen/skills/self-evolve/SKILL.md and
follow its guardrails exactly: the change must be focused, must not touch
credentials, data/, settings, or anything outward-facing, and it must be
verified end-to-end before you consider it done (health, and a counter
that only moves when the work happened — never trust a green light).

When finished, reply with a short report: what changed, exactly how you
verified it, and anything you deliberately left alone. If you discover the
item is wrong or no longer needed, say so plainly instead of forcing the
change.

An item is only closed when it is done A to Z: implemented AND verified
end-to-end. If you cannot fully complete it in this turn, start your final
reply with 'INCOMPLETE:' and list exactly what remains — a partially done
item is NOT done, and the drain will retry it. Never report a partial
implementation as finished."""


def _is_incomplete(answer: Optional[str]) -> bool:
    """A turn that declares itself partial is a failed attempt, not a
    success: an INCOMPLETE first line (case-insensitive) — or no answer at
    all — sends the item back to open to be retried."""
    if not answer:
        return True
    return str(answer).lstrip().lower().startswith("incomplete:")


async def _resolution(item_id: int) -> str:
    row = await db.fetchone(
        "SELECT resolution FROM selfevolve_backlog WHERE id = $1", item_id)
    return (row or {}).get("resolution") or ""


async def _mark_adopted(item_id: int, report: str) -> bool:
    # Only claim the row if it is still in-flight: the user may have
    # dismissed it while the implementation was running, and an
    # unconditional UPDATE would resurrect it as 'adopted'.
    row = await db.fetchone(
        """UPDATE selfevolve_backlog
               SET status = 'adopted', resolution = $2, updated_at = $3
             WHERE id = $1 AND status = 'in-flight'
           RETURNING id""",
        item_id, report[:RESOLUTION_MAX], _now())
    return row is not None


async def _fail_backlog(item_id: int, note: str) -> bool:
    # Back to open (only if still in-flight): the drain or the nightly run
    # can retry, and the user can dismiss. A row dismissed meanwhile stays
    # dismissed.
    existing = await _resolution(item_id)
    row = await db.fetchone(
        """UPDATE selfevolve_backlog
               SET status = 'open', resolution = $2, updated_at = $3
             WHERE id = $1 AND status = 'in-flight'
           RETURNING id""",
        item_id, ((note or "") + existing)[:RESOLUTION_MAX], _now())
    return row is not None


async def _implement(item: dict, priority: str = "high") -> None:
    """Run an approved item through one agent turn, then resolve the row.

    The implementation is a long, unattended turn. The row says in-flight
    while it runs; a crash or a gateway restart leaves it in-flight, and
    the stale-reclaim logic (inflight_stale_hours) recovers it rather than
    this code trying to be smarter than the process lifetime."""
    sid = item["id"]
    try:
        settings = await _get_settings()
        timeout = float(settings["implement_timeout_sec"])
        answer = await agent.ask(
            _implement_prompt(item), timeout=timeout, priority=priority)
        if answer is None or _is_incomplete(answer):
            detail = (str(answer).strip().splitlines()[0][:200]
                      if answer else "no answer")
            if not await _fail_backlog(
                    sid, f"Implementation incomplete ({detail}); retrying: "):
                log.info("Backlog %s closed while implementing — keeping "
                         "final state", sid)
            else:
                log.warning("Backlog %s implementation incomplete: %s",
                            sid, detail)
        elif await _mark_adopted(sid, str(answer)):
            log.info("Backlog %s implemented: %s", sid, str(answer)[:120])
        else:
            log.info("Backlog %s closed while implementing — keeping "
                     "final state", sid)
    except Exception as exc:  # noqa: BLE001 — row state is the truth
        if not await _fail_backlog(
                sid, f"Implementation failed, will retry: {str(exc)[:2000]}. "):
            log.info("Backlog %s closed while implementing — keeping "
                     "final state", sid)
        else:
            log.error("Backlog %s implementation failed: %s", sid, exc)


@router.post("/backlog/{item_id}/adopt")
async def adopt_backlog(item_id: int):
    """User override: start implementing an open item now, through the
    in-memory agent queue (high priority). The row is the lease; the
    answer resolves it (adopted / back to open)."""
    item = await db.fetchone(
        "SELECT * FROM selfevolve_backlog WHERE id = $1", item_id)
    if not item:
        raise HTTPException(404, f"backlog item {item_id} not found")
    if item["status"] != "open":
        raise HTTPException(409, f"item is {item['status']}, not open")
    await db.execute(
        "UPDATE selfevolve_backlog SET status = 'in-flight', updated_at = $2 "
        "WHERE id = $1", item_id, _now())
    dict(item)["status"] = "in-flight"
    _spawn(_implement(dict(item)))
    return {"ok": True, "status": "in-flight"}


class DismissReason(BaseModel):
    reason: str = Field("", max_length=4000)


@router.post("/backlog/{item_id}/dismiss")
async def dismiss_backlog(item_id: int,
                          req: Optional[DismissReason] = None):
    reason = (req.reason if req else "") or ""
    resolution = (f"Dismissed: {reason.strip()}" if reason.strip()
                  else "Dismissed")
    row = await db.fetchone(
        """UPDATE selfevolve_backlog
               SET status = 'dismissed', resolution = $2, updated_at = $3
             WHERE id = $1 AND status IN ('open', 'in-flight')
           RETURNING id, status""",
        item_id, resolution[:RESOLUTION_MAX], _now())
    if not row:
        raise HTTPException(409, f"item {item_id} is not open or in-flight")
    return {"ok": True, "item": dict(row)}


@router.post("/backlog/{item_id}/reset")
async def reset_backlog(item_id: int):
    """Reclaim an in-flight row whose worker vanished (gateway restart,
    crash)."""
    existing = await _resolution(item_id)
    row = await db.fetchone(
        """UPDATE selfevolve_backlog
               SET status = 'open', resolution = $2, updated_at = $3
             WHERE id = $1 AND status = 'in-flight'
           RETURNING id, status""",
        item_id,
        ("Reclaimed: in-flight was stale. " + existing)[:RESOLUTION_MAX],
        _now())
    if not row:
        raise HTTPException(409, f"item {item_id} is not in-flight")
    return {"ok": True, "item": dict(row)}


# ---------------------------------------------------------------------------
# Backlog drain — the night shift for tier=autonomous items
# ---------------------------------------------------------------------------
#
# The nightly run is the DECISION procedure (audit, learn, at most one
# discretionary change). Working the backlog out of it was the bottleneck:
# one item per night cannot out-drain the loop's own findings, repairs and
# escalations. So autonomous items get their own lane:
#
#   one tick every 15 minutes, inside the night window (until
#   drain_cutoff_hour, server local time):
#     - reconcile in-flight drain items (stale rows: the worker died);
#     - while the agent queue depth is below DRAIN_QUEUE_DEPTH (one
#       running, one waiting) and the per-night cap is not spent, claim
#       the OLDEST open tier=autonomous item and start its
#       implementation turn at LOW priority, so operational work always
#       runs ahead of waiting backlog work.
#
# One item per turn, not "do the whole backlog in one run": a single
# multi-hour turn dilutes the verify-each-change discipline; one item per
# turn means every item is focused and verified on its own, the cutoff
# hour stops the shift, and a failed item costs one attempt, not the whole
# night. A gateway restart loses in-flight turns — the stale-reclaim is
# what recovers them.

async def _drain_depth_ok() -> bool:
    """The shared in-memory agent queue must have room: one running + one
    waiting, no more. Skipped when the stat keys are absent."""
    try:
        stats = agent.stats()
    except Exception:  # noqa: BLE001 — never wedge the tick
        return True
    if "active" in stats and "queued" in stats:
        return int(stats["active"]) + int(stats["queued"]) < DRAIN_QUEUE_DEPTH
    return True


async def _drain_reconcile() -> List[dict]:
    """Settle in-flight items whose worker vanished (gateway restart).

    In this port the implementation turn is an in-process task, so the row
    itself is the lease: an in-flight row whose updated_at is older than
    inflight_stale_hours has no live worker and goes back to open."""
    settings = await _get_settings()
    stale = _ago(hours=float(settings["inflight_stale_hours"]))
    settled: List[dict] = []
    for row in await db.fetchall(
            """SELECT id, title, drain_attempts
                 FROM selfevolve_backlog
                WHERE status = 'in-flight' AND updated_at < $1""", stale):
        attempts = int(row["drain_attempts"])
        note = (f"In-flight was stale — the worker died (attempt "
                f"{attempts}/{DRAIN_MAX_ATTEMPTS}); reclaiming: ")
        existing = await _resolution(row["id"])
        await db.execute(
            """UPDATE selfevolve_backlog
                   SET status = 'open', resolution = $2, updated_at = $3
                 WHERE id = $1""",
            row["id"], (note + existing)[:RESOLUTION_MAX], _now())
        log.warning("Backlog %s drained: stale in-flight row reclaimed",
                    row["id"])
        settled.append({"id": row["id"], "verdict": "stale_reclaimed"})
    return settled


@router.post("/backlog/tick")
async def backlog_tick(force: bool = Query(False)):
    """One pass of the night drain: settle finished items, then — inside
    the window, under the cap and with the queue room — start the oldest
    open autonomous item. `force=true` skips the window, cap and depth
    check (manual step / testing)."""
    settings = await _get_settings()
    cutoff = int(settings["drain_cutoff_hour"])
    cap = int(settings["drain_max_per_night"])
    now_local = _local_now()
    window_open = now_local.hour < cutoff
    start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    drained_today = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_backlog
            WHERE status IN ('in-flight', 'adopted') AND updated_at >= $1""",
        start_of_day.astimezone(timezone.utc).replace(microsecond=0).isoformat()) or 0)

    settled = await _drain_reconcile()
    claimed = None
    skipped = None
    if not force and not await _drain_depth_ok():
        skipped = (f"agent queue busy — max depth {DRAIN_QUEUE_DEPTH}")
    elif not force and not window_open:
        skipped = (f"window closed (cutoff {cutoff:02d}:00, now "
                   f"{now_local:%H:%M})")
    elif not force and drained_today >= cap:
        skipped = f"cap reached ({drained_today}/{cap} items this night)"
    else:
        item = await db.fetchone(
            """SELECT * FROM selfevolve_backlog
                WHERE status = 'open' AND tier = 'autonomous'
                  AND drain_attempts < $1
                ORDER BY updated_at ASC, id ASC
                LIMIT 1""", DRAIN_MAX_ATTEMPTS)
        if item:
            item = dict(item)
            item["drain_attempts"] = int(item["drain_attempts"]) + 1
            await db.execute(
                """UPDATE selfevolve_backlog
                       SET status = 'in-flight',
                           drain_attempts = drain_attempts + 1,
                           updated_at = $2
                     WHERE id = $1""",
                item["id"], _now())
            _spawn(_implement(item, priority="low"))
            log.info("Backlog %s drained: turn started (attempt %s)",
                     item["id"], item["drain_attempts"])
            claimed = {"id": item["id"],
                       "attempt": item["drain_attempts"]}
        else:
            skipped = "no open autonomous items"

    return {"ok": True, "reconciled": settled, "claimed": claimed,
            "skipped": skipped,
            "window": {"cutoff_hour": cutoff, "open": window_open,
                       "now": now_local.strftime("%H:%M"),
                       "drained_today": drained_today, "cap": cap}}


# ---------------------------------------------------------------------------
# Notify — the loop's voice, with a rate limit so a bug cannot spam
# ---------------------------------------------------------------------------

class Notify(BaseModel):
    text: str = Field(..., min_length=1)


async def _deliver_notify(text: str) -> None:
    """The one place a message is sent: rate-limited, length-checked,
    attributed to a run. Both the /notify endpoint and the loop's ticks go
    through here so a bug in one cannot bypass the guard.

    Delivery, first that works: the ntfy connector (when the package is
    installed and configured), then a Telegram bot from env. Neither
    configured -> 503."""
    settings = await _get_settings()
    max_chars = int(settings["notify_max_chars"])
    max_per_hour = int(settings["notify_max_per_hour"])
    if len(text) > max_chars:
        raise HTTPException(429, f"message too long (max {max_chars} chars)")
    recent = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_runs
            WHERE message_sent = 1 AND started_at >= $1""",
        _ago(hours=1)) or 0)
    if recent >= max_per_hour:
        raise HTTPException(
            429, f"rate limit: {max_per_hour} messages/hour for self-evolve")
    delivered = False
    try:
        from connectors.ntfy import enabled as ntfy_enabled
        from connectors.ntfy import send as ntfy_send
        if ntfy_enabled():
            await ntfy_send(text, title="Self-evolve", priority=4)
            delivered = True
    except ImportError:
        pass  # the ntfy package is not installed
    except Exception as exc:  # noqa: BLE001 — fall through to the next channel
        log.warning("ntfy delivery failed: %s", exc)
    if not delivered:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if token and chat_id:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": text})
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Telegram returned {resp.status_code}: "
                        f"{resp.text[:200]}")
                delivered = True
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram delivery failed: %s", exc)
    if not delivered:
        raise HTTPException(503, "no notification channel configured")
    # Attribute the message to the newest running run, if there is one —
    # that is how the rate limit and the runs table stay honest.
    await db.execute(
        """UPDATE selfevolve_runs SET message_sent = 1
            WHERE id = (SELECT id FROM selfevolve_runs
                         WHERE status = 'running'
                         ORDER BY started_at DESC, id DESC LIMIT 1)""")


@router.post("/notify")
async def notify(req: Notify):
    await _deliver_notify(req.text)
    return {"ok": True, "sent": True}


async def _maybe_notify(text: str) -> None:
    """Notify on fixed/escalated/regressed, never raises: a rate-limited
    or dead channel must not wedge a tick."""
    try:
        await _deliver_notify(text)
    except HTTPException as exc:
        log.warning("notify refused (%s): %s", exc.status_code, text[:80])
    except Exception:  # noqa: BLE001
        log.warning("notify failed", exc_info=True)


# ---------------------------------------------------------------------------
# Stats — for the dashboard overview
# ---------------------------------------------------------------------------

@router.get("/stats")
async def stats():
    last = await db.fetchone(
        """SELECT started_at, status, changes FROM selfevolve_runs
            ORDER BY started_at DESC, id DESC LIMIT 1""")
    n_runs = int(await db.fetchval(
        "SELECT COUNT(*) FROM selfevolve_runs") or 0)
    n_7d = int(await db.fetchval(
        "SELECT COUNT(*) FROM selfevolve_runs WHERE started_at >= $1",
        _ago(days=7)) or 0)
    n_changed = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_runs
            WHERE changes IS NOT NULL AND changes <> ''
              AND started_at >= $1""",
        _ago(days=30)) or 0)
    n_open = int(await db.fetchval(
        "SELECT COUNT(*) FROM selfevolve_backlog WHERE status = 'open'") or 0)
    n_proposals = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_backlog
            WHERE status = 'open' AND tier = 'proposal'""") or 0)
    n_inflight = int(await db.fetchval(
        "SELECT COUNT(*) FROM selfevolve_backlog "
        "WHERE status = 'in-flight'") or 0)
    n_autonomous = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_backlog
            WHERE status = 'open' AND tier = 'autonomous'""") or 0)
    settings = await _get_settings()
    now_local = _local_now()
    cutoff = int(settings["drain_cutoff_hour"])
    start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    drained_today = int(await db.fetchval(
        """SELECT COUNT(*) FROM selfevolve_backlog
            WHERE status IN ('in-flight', 'adopted') AND updated_at >= $1""",
        start_of_day.astimezone(timezone.utc).replace(microsecond=0).isoformat()) or 0)
    drain_state = ("open until " if now_local.hour < cutoff
                   else "closed (reopens at the nightly run) ")
    return {
        "runs": n_runs,
        "runs_7d": n_7d,
        "changes_30d": n_changed,
        "backlog_open": n_open,
        "backlog_autonomous": n_autonomous,
        "proposals": n_proposals,
        "in_flight": n_inflight,
        "drained_today": drained_today,
        "drain_window": (f"{drain_state}{cutoff:02d}:00 · cap "
                         f"{int(settings['drain_max_per_night'])}/night"),
        "last_run": (f"{last['started_at']} · {last['status']} · "
                     + ("changed something" if last["changes"]
                        else "no changes")
                     if last else "never"),
    }


class SettingsUpdate(BaseModel):
    enabled: Optional[Union[bool, str]] = None
    inflight_stale_hours: Optional[float] = None
    implement_timeout_sec: Optional[float] = None
    drain_cutoff_hour: Optional[int] = None
    drain_max_per_night: Optional[int] = None
    notify_max_per_hour: Optional[int] = None
    notify_max_chars: Optional[int] = None
    outcome_measure_days: Optional[float] = None
    regression_tolerance: Optional[float] = None


@router.get("/settings")
async def get_settings_route():
    """The loop's knobs. A form view preloads from here; a conversation
    edits through the PUT."""
    return await _get_settings()


@router.put("/settings")
async def update_settings_route(req: SettingsUpdate):
    """Update the loop's knobs. Only provided fields change; each is
    range-checked so a typo cannot brick the loop (a 0-hour reclaim window
    or a 0 messages/hour limit would disable the safety the knob
    protects)."""
    updates: Dict[str, Any] = {k: v for k, v in req.model_dump().items()
                               if v is not None}
    if not updates:
        raise HTTPException(400, "no recognised fields to update")
    if "enabled" in updates:
        updates["enabled"] = _as_bool(updates["enabled"], "enabled")
    checks = (
        ("inflight_stale_hours", 1, 168),
        ("implement_timeout_sec", 60, 7200),
        ("drain_cutoff_hour", 0, 23),
        ("drain_max_per_night", 1, 12),
        ("notify_max_per_hour", 1, 12),
        ("notify_max_chars", 200, 4000),
        ("outcome_measure_days", 1, 14),
        ("regression_tolerance", 1.0, 3.0),
    )
    for key, lo, hi in checks:
        v = updates.get(key)
        if v is not None and not (lo <= float(v) <= hi):
            raise HTTPException(
                400, f"{key} must be {lo:g}-{hi:g}")
    now = _now()
    for key, value in updates.items():
        await db.execute(
            """INSERT INTO selfevolve_settings (key, value, updated_at)
               VALUES ($1, $2, $3)
               ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3""",
            key, json.dumps(value), now)
    return {"ok": True, "settings": await _get_settings()}


# ---------------------------------------------------------------------------
# Repair loop — same-day reaction to terminal failures
# ---------------------------------------------------------------------------
#
# Every work queue in the platform is bounded and ends in a visible
# terminal state (events_deliveries: max_retries, then dead;
# schedule_jobs: fail_count). What was missing was the reaction: a
# terminal row waited for the nightly defect pass.
#
# The loop closes that gap without a per-error trigger storm:
#
#   one tick every 15 minutes ->
#     1. SCAN the terminal rows and upsert repair_requests, deduped on
#        (kind, target_id, fingerprint). Twelve dead deliveries from one
#        subscriber are ONE request with count=12 — not twelve repair
#        turns.
#     2. RECLAIM in-flight requests whose lease lapsed (the worker died
#        in a gateway restart mid-turn); a live turn settles its own row
#        when it finishes.
#     3. CLAIM at most ONE requested request per tick and run its repair
#        turn through the in-memory agent queue (one repair in flight,
#        ever).
#
# The repair turn classifies before it touches anything:
#   expected  -> the error is the system working as designed; recorded,
#                not fixed
#   fixed     -> root-cause fix, small, reversible, platform-internal,
#                verified
#   escalated -> backlog item with the analysis and its autonomy-line tier
# A request that spends REPAIR_MAX_ATTEMPTS turns without a verdict is
# escalated automatically, so the repairer itself cannot loop: once
# 'expected' or 'escalated' a signature never re-triggers, and a
# 'resolved' request only re-opens if the same failure comes back (a
# regression).

REPAIR_PROMPT = """You are the error-repair agent for this agent's platform. A terminal failure produced the repair request below. Decide what the error MEANS before deciding what to do with it.

REQUEST #{rid} — attempt {attempts} of {max}
KIND: {kind}
TARGET: {target_id}
RECORDED ERROR: {error}

STEP 1 — LOCATE the failure.
- kind event_delivery → SELECT * FROM events_deliveries WHERE status = 'dead' for the subscriber named in the recorded error; read that subscriber mini-app's code.
- kind schedule_job → SELECT * FROM schedule_jobs WHERE id = {target_id}; then its last failed run: SELECT * FROM job_runs WHERE job_id = {target_id} AND status = 'failed' ORDER BY id DESC LIMIT 1; read the job's app and its target endpoint or prompt.
- kind selfevolve_run → SELECT * FROM selfevolve_runs WHERE id = {target_id}; the run's own turn was an agent turn that started near its started_at, and the run was never finished.

STEP 2 — CLASSIFY. Pick exactly one, honestly:
- expected — the error is the system working as designed: a validation or policy refusal, a legitimately absent target, a transient that was retried until it was supposed to stop, a message already handled. This is NOT a bug; "fixing" it would mean silencing a signal.
- bug — the error should not have happened. Find the ROOT CAUSE in the code, not the symptom.
- unfixable-here — the root cause is in the user's world (their data, credentials, outward-facing effects, a third-party service) or bigger than a focused repair.

STEP 3 — ACT per class:
- expected → touch nothing.
- bug → fix the root cause only. A workaround, a swallowed exception, a retry band-aid is NEVER the end state. The fix must be small, reversible and platform-internal, and you must verify it: re-run the thing that failed, or read a counter that only moves when the work happened. A 200 or a green status line is not proof. If the real fix is bigger than this turn's budget, classify unfixable-here instead of shipping a band-aid.
- unfixable-here → do not attempt anything.

HARD LIMITS: never touch credentials, the vault, data/, anything outward-facing, or how the user is contacted. When in doubt between bug and unfixable-here, pick unfixable-here — an 'escalated' verdict always beats a 'fixed' that is not really fixed.

STEP 4 — reply with ONLY this JSON, no prose around it:
{{
  "verdict": "expected" | "fixed" | "escalated",
  "reason": "one sentence: what this error is and why you classified it that way",
  "fix": "what you changed and how you verified it (verdict=fixed only)",
  "root_cause": "your analysis and what should happen next (verdict=escalated only)",
  "tier": "autonomous" or "proposal" (verdict=escalated only — autonomous if the follow-up touches only the platform itself, proposal if it reaches the user's world)
}}"""


def _repair_prompt(rid: int, attempts: int, kind: str, target_id: int,
                   error: str) -> str:
    return REPAIR_PROMPT.format(
        rid=rid, attempts=attempts, max=REPAIR_MAX_ATTEMPTS, kind=kind,
        target_id=target_id, error=error or "(none recorded)")


def _parse_verdict(result: Any) -> Optional[dict]:
    """The repair turn's contract is JSON, possibly inside prose."""
    if not result:
        return None
    text = result if isinstance(result, str) else json.dumps(result)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def _repair_activity(kind: str, rid: int, target: Any, verdict: str,
                           detail: str) -> None:
    """Full record of every repair outcome — never raises."""
    await activity.log(
        "self-evolve", "repair",
        f"Repair #{rid} ({kind} {target}): {verdict}",
        detail=(detail or "")[:1000])


async def _upsert_request(kind: str, target_id: int, error: str,
                          fingerprint: str) -> int:
    """Deduped intake. Returns 1 when a row was created, counted, or a
    regression re-opened it — 0 when the signature is already 'expected'
    or 'escalated' (those verdicts are not re-litigated automatically)."""
    now = _now()
    row = await db.fetchone(
        """INSERT INTO selfevolve_repair_requests
             (kind, target_id, fingerprint, error, state, count, attempts,
              first_seen, last_seen, updated_at)
           VALUES ($1, $2, $3, $4, 'requested', 1, 0, $5, $5, $5)
           ON CONFLICT (kind, target_id, fingerprint) DO UPDATE
             SET count = selfevolve_repair_requests.count + 1,
                 last_seen = $5, updated_at = $5,
                 error = $4,
                 state = CASE WHEN selfevolve_repair_requests.state
                              = 'resolved' THEN 'requested'
                              ELSE selfevolve_repair_requests.state END
           WHERE selfevolve_repair_requests.state
                 IN ('requested', 'in-flight', 'resolved')
           RETURNING id""",
        kind, target_id, fingerprint, (error or "failed")[:800], now)
    return 1 if row else 0


async def _scan_terminal() -> int:
    """Phase 1: every terminal failure row becomes (or bumps) a request.

    The sources are a declarative registry read with per-source fault
    isolation: a missing table (events package absent, scheduler empty)
    skips that source and logs at debug — it never wedges the tick."""
    created = 0
    # Dead event deliveries: N failures of the same subscriber are one
    # request, so a dead endpoint cannot flood the loop.
    try:
        rows = await db.fetchall(
            """SELECT app_name, endpoint, COUNT(*) AS n
                 FROM events_deliveries WHERE status = 'dead'
                GROUP BY app_name, endpoint""")
        for r in rows:
            created += await _upsert_request(
                "event_delivery", 0,
                f"{r['n']} dead deliveries for {r['app_name']} -> "
                f"{r['endpoint']}",
                f"event:{r['app_name']}:{r['endpoint']}")
    except Exception as exc:  # noqa: BLE001 — source missing, keep going
        log.debug("repair source event_delivery unavailable: %s", exc)
    # Failing scheduled jobs: enabled jobs with a nonzero fail_count.
    try:
        rows = await db.fetchall(
            """SELECT id, app, name, fail_count
                 FROM schedule_jobs
                WHERE enabled = 1 AND fail_count > 0""")
        for r in rows:
            last_error = None
            try:
                erow = await db.fetchone(
                    """SELECT error FROM job_runs
                        WHERE job_id = $1 AND status = 'failed'
                        ORDER BY id DESC LIMIT 1""", int(r["id"]))
                last_error = (erow or {}).get("error")
            except Exception:  # noqa: BLE001
                pass
            created += await _upsert_request(
                "schedule_job", int(r["id"]),
                f"job {r['app']}/{r['name']} failing "
                f"({r['fail_count']} consecutive) — last error: "
                f"{last_error or 'none recorded'}",
                f"job:{r['app']}:{r['name']}")
    except Exception as exc:  # noqa: BLE001 — source missing, keep going
        log.debug("repair source schedule_job unavailable: %s", exc)
    return created


async def _scan_abandoned_runs() -> int:
    """Phase 1b: a run row left 'running' long after its turn settled is
    an abandoned record — the turn ended without calling finish (e.g. it
    was waiting on a background sub-agent notification that never arrives
    in a single-shot queue turn). The repair loop is the backstop for that
    failure class. 2h matches the skill's own abandon threshold."""
    created = 0
    now = datetime.now(timezone.utc)
    for r in await db.fetchall(
            """SELECT id, started_at FROM selfevolve_runs
                WHERE status = 'running' AND started_at < $1""",
            _ago(hours=ABANDONED_RUN_HOURS)):
        try:
            age_h = int((now - datetime.fromisoformat(r["started_at"])).total_seconds() // 3600)
        except (TypeError, ValueError):
            age_h = ABANDONED_RUN_HOURS
        created += await _upsert_request(
            "selfevolve_run", int(r["id"]),
            f"run {r['id']} left 'running' for ~{age_h}h without finish — "
            f"reclaim it: read what its turn actually did, then "
            f"POST /api/self-evolve/runs/{r['id']}/finish recording it "
            f"(done if the work landed, failed otherwise)",
            f"selfevolve_run:{r['id']}")
    return created


async def _escalate(req: dict, attempts: int, root_cause: str,
                    parsed: Optional[dict] = None) -> dict:
    """A repair that cannot be closed becomes backlog work, with the
    analysis and its autonomy-line tier attached."""
    parsed = parsed or {}
    tier = parsed.get("tier") if parsed.get("tier") in (
        "autonomous", "proposal") else "autonomous"
    title = f"Repair #{req['id']}: {req['kind']} {req['target_id']}"
    existing = await db.fetchone(
        "SELECT id FROM selfevolve_backlog WHERE lower(title) = lower($1) "
        "LIMIT 1", title)
    if existing:
        backlog_id = int(existing["id"])
    else:
        why = (f"The repair loop could not close this: {root_cause}")[:4000]
        now = _now()
        row = await db.fetchone(
            """INSERT INTO selfevolve_backlog
                 (title, why, tier, evidence, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $5) RETURNING id""",
            title, why, tier,
            (f"attempts: {attempts} | original error: {req['error'] or ''}")[:500],
            now)
        backlog_id = int(row["id"])
    now = _now()
    await db.execute(
        """UPDATE selfevolve_repair_requests
               SET state = 'escalated', result = $2, lease_until = NULL,
                   backlog_id = $3, updated_at = $4
             WHERE id = $1""",
        req["id"],
        json.dumps(parsed or {"verdict": "escalated", "reason": root_cause}),
        backlog_id, now)
    detail = (parsed.get("reason") or root_cause) or ""
    await _repair_activity(req["kind"], req["id"], req["target_id"],
                           f"escalated (backlog #{backlog_id}, {tier})",
                           detail)
    await _maybe_notify(
        f"🔧 Repair #{req['id']} ({req['kind']} {req['target_id']}): "
        f"ESCALATED → backlog #{backlog_id} (tier {tier}). "
        f"{str(detail)[:250]}")
    return {"id": req["id"], "verdict": "escalated", "backlog_id": backlog_id}


async def _settle_request(req: dict, verdict: str,
                          parsed: Optional[dict], lease: Optional[str]) -> dict:
    """Apply a repair outcome to the request row, with its record.

    `lease` is the lease this turn was claimed with: every update is
    conditioned on it, so a turn that finished after its row was
    reclaimed (or re-claimed) is a no-op, never a resurrection."""
    rid = req["id"]
    now = _now()
    if verdict == "failed_attempt":
        # `attempts` is the number of repair turns already run — the claim
        # increments it. A failed turn under budget hands the row back to
        # the pool so the next tick re-claims it instead of giving up
        # after the first attempt.
        attempts = int(req["attempts"])
        if attempts < REPAIR_MAX_ATTEMPTS:
            await db.execute(
                """UPDATE selfevolve_repair_requests
                       SET state = 'requested', lease_until = NULL,
                           updated_at = $3
                     WHERE id = $1 AND state = 'in-flight'
                       AND ($2 IS NULL OR lease_until = $2)""",
                rid, lease, now)
            log.info("repair %s: turn %s/%s failed — back to requested "
                     "for retry", rid, attempts, REPAIR_MAX_ATTEMPTS)
            return {"id": rid, "verdict": "retry", "attempts": attempts}
        return await _escalate(
            req, attempts,
            f"the repair turn failed {attempts} times without a usable "
            f"verdict")
    if verdict == "expected":
        await db.execute(
            """UPDATE selfevolve_repair_requests
                   SET state = 'expected', result = $2, lease_until = NULL,
                       updated_at = $3
                 WHERE id = $1 AND state = 'in-flight'
                   AND ($4 IS NULL OR lease_until = $4)""",
            rid, json.dumps(parsed or {"verdict": "expected"}), now, lease)
        detail = (parsed or {}).get("reason") or "classified expected"
        await _repair_activity(req["kind"], rid, req["target_id"], "expected",
                               detail)
        log.info("repair %s: expected — %s", rid, str(detail)[:120])
        return {"id": rid, "verdict": "expected"}
    if verdict == "fixed":
        await db.execute(
            """UPDATE selfevolve_repair_requests
                   SET state = 'resolved', result = $2, lease_until = NULL,
                       updated_at = $3
                 WHERE id = $1 AND state = 'in-flight'
                   AND ($4 IS NULL OR lease_until = $4)""",
            rid, json.dumps(parsed or {"verdict": "fixed"}), now, lease)
        detail = (parsed or {}).get("fix") or ""
        await _repair_activity(req["kind"], rid, req["target_id"], "fixed",
                               detail)
        await _maybe_notify(
            f"🔧 Repair #{rid} ({req['kind']} {req['target_id']}): FIXED. "
            f"{str(detail)[:300]}")
        return {"id": rid, "verdict": "fixed"}
    # escalated
    return await _escalate(
        req, int(req["attempts"]),
        (parsed or {}).get("root_cause") or (parsed or {}).get("reason") or "",
        parsed)


async def _reclaim_stale_repairs() -> List[dict]:
    """Phase 2: a row still in-flight past its lease had its worker killed
    (gateway restart). Hand it back to the pool, or escalate when the
    attempt budget is spent."""
    settled: List[dict] = []
    for req in await db.fetchall(
            """SELECT id, kind, target_id, attempts, error, lease_until
                 FROM selfevolve_repair_requests
                WHERE state = 'in-flight'
                  AND lease_until IS NOT NULL AND lease_until < $1""",
            _now()):
        req = dict(req)
        settled.append(await _settle_request(
            req, "failed_attempt", None, req["lease_until"]))
    return settled


async def _repair_turn(req: dict, lease: str) -> None:
    """One repair turn: ask the agent, parse the verdict, settle the row.

    The row is the lease: a turn that finishes after its row was reclaimed
    (stale or re-claimed) settles nothing — the conditional update is a
    no-op and the row's new owner is the truth."""
    rid = req["id"]
    try:
        settings = await _get_settings()
        timeout = float(settings["implement_timeout_sec"])
        answer = await agent.ask(
            _repair_prompt(rid, int(req["attempts"]), req["kind"],
                           req["target_id"], req["error"]),
            timeout=timeout, priority="normal")
    except Exception as exc:  # noqa: BLE001 — settle as a failed attempt
        log.warning("repair %s: turn failed: %s", rid, exc)
        await _settle_request(req, "failed_attempt", None, lease)
        return
    parsed = _parse_verdict(answer)
    verdict = (parsed.get("verdict") if parsed and
               parsed.get("verdict") in ("expected", "fixed", "escalated")
               else "failed_attempt")
    await _settle_request(req, verdict, parsed, lease)


async def _claim_one() -> Optional[dict]:
    """Phase 3: at most one repair in flight, ever. Most-seen, oldest
    first. The claim is one conditional UPDATE, so two concurrent ticks
    can never both win."""
    row = await db.fetchone(
        """SELECT id, kind, target_id, error, attempts
             FROM selfevolve_repair_requests
            WHERE state = 'requested'
            ORDER BY count DESC, first_seen ASC, id ASC
            LIMIT 1""")
    if not row:
        return None
    row = dict(row)
    rid = row["id"]
    settings = await _get_settings()
    lease = _now()
    lease_until = _ago(seconds=-int(float(
        settings["implement_timeout_sec"])))
    claimed = await db.fetchone(
        """UPDATE selfevolve_repair_requests
               SET state = 'in-flight', attempts = attempts + 1,
                   lease_until = $2, updated_at = $2
             WHERE id = $1 AND state = 'requested'
           RETURNING id, attempts""",
        rid, lease_until)
    if not claimed:
        return None
    row["attempts"] = int(claimed["attempts"])
    _spawn(_repair_turn(row, lease_until))
    log.info("repair %s: turn started (attempt %s)", rid, row["attempts"])
    return {"id": rid, "attempts": row["attempts"]}


@router.post("/repair/tick")
async def repair_tick():
    """One pass: scan terminal failures, reclaim stale repair turns, and
    if nothing is in flight, start the most-urgent repair turn. Fired by
    the scheduler every 15 minutes; returns counters, not a status word."""
    created = await _scan_terminal() + await _scan_abandoned_runs()
    settled = await _reclaim_stale_repairs()
    claimed = await _claim_one()
    summary = {"ok": True, "scanned_new": created,
               "settled": settled, "claimed": claimed}
    log.info("repair tick: %s", summary)
    return summary


@router.get("/repair/requests")
async def list_repair_requests(state: Optional[str] = Query(None),
                               limit: int = Query(50, ge=1, le=200)):
    if state:
        rows = await db.fetchall(
            """SELECT id, kind, target_id, state, count, attempts, error,
                      result, backlog_id, lease_until, first_seen, last_seen,
                      (state IN ('requested', 'in-flight')) AS can_dismiss
                 FROM selfevolve_repair_requests
                WHERE state = $1
                ORDER BY CASE state
                             WHEN 'in-flight' THEN 0 WHEN 'requested' THEN 1
                             ELSE 2 END,
                         last_seen DESC
                LIMIT $2""",
            state, limit)
    else:
        rows = await db.fetchall(
            """SELECT id, kind, target_id, state, count, attempts, error,
                      result, backlog_id, lease_until, first_seen, last_seen,
                      (state IN ('requested', 'in-flight')) AS can_dismiss
                 FROM selfevolve_repair_requests
                ORDER BY CASE state
                             WHEN 'in-flight' THEN 0 WHEN 'requested' THEN 1
                             ELSE 2 END,
                         last_seen DESC
                LIMIT $1""",
            limit)
    out = []
    for r in rows:
        d = dict(r)
        res = d.get("result")
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except (json.JSONDecodeError, TypeError):
                pass
        d["verdict_reason"] = res.get("reason") if isinstance(res, dict) else None
        out.append(d)
    return {"ok": True, "requests": out, "count": len(out)}


class RepairDismiss(BaseModel):
    reason: str = Field("", max_length=4000)


@router.post("/repair/requests/{request_id}/dismiss")
async def dismiss_repair_request(request_id: int,
                                 req: Optional[RepairDismiss] = None):
    """The user's override: 'this is not worth fixing' — treated as an
    expected error and never re-triggered for this signature."""
    reason = (req.reason if req else "") or ""
    result = json.dumps({
        "verdict": "expected",
        "reason": (f"Dismissed: {reason.strip()}" if reason.strip()
                   else "Dismissed")[:800],
    })
    row = await db.fetchone(
        """UPDATE selfevolve_repair_requests
               SET state = 'expected', result = $2, lease_until = NULL,
                   updated_at = $3
             WHERE id = $1 AND state IN ('requested', 'in-flight')
           RETURNING id, state""",
        request_id, result, _now())
    if not row:
        raise HTTPException(
            409, f"request {request_id} is not requested/in-flight")
    item = await db.fetchone(
        "SELECT * FROM selfevolve_repair_requests WHERE id = $1",
        request_id)
    await _repair_activity(
        (item or {}).get("kind") or "?", request_id,
        (item or {}).get("target_id"), "dismissed", reason)
    return {"ok": True, "item": dict(row)}


# ---------------------------------------------------------------------------
# Fitness — the longitudinal landscape the loop evolves against
# ---------------------------------------------------------------------------
#
# Verification at merge time answers "does it work?"; fitness answers
# "is it better?" — a different question, and the one evolution needs.
# A scheduler tick samples every metric into selfevolve_metrics; the
# trends are the landscape. selfevolve_friction is the explicit log of
# moments the user had to step in (a correction, a re-ask, a manual
# fallback) — the highest-density evolutionary signal the system has,
# because every one of them is a measured cost to their day.
#
# Metric names are stable identifiers: reconcile, the dashboard and the
# skill all reference them by name.
#
# The cross-app reads are a declarative, fault-isolated registry: each
# entry is one metric, each guarded by its own try/except — a missing
# table (another package not installed) degrades that one metric, logged
# at debug, and the sampler keeps going. To add a metric for a capability
# this installation has, add its name + SQL here.

METRIC_QUERIES: Dict[str, Tuple[str, Optional[Callable[[], List[Any]]]]] = {
    # Reliability — the system failing visibly
    "events_dead": (
        "SELECT COUNT(*) FROM events_deliveries WHERE status = 'dead'",
        None),
    "jobs_failing": (
        "SELECT COUNT(*) FROM schedule_jobs "
        "WHERE enabled = 1 AND fail_count > 0",
        None),
    "repair_open": (
        "SELECT COUNT(*) FROM selfevolve_repair_requests "
        "WHERE state IN ('requested', 'in-flight')",
        None),
    # Friction — logged moments a human had to step in
    "friction_7d": (
        "SELECT COUNT(*) FROM selfevolve_friction WHERE created_at >= $1",
        lambda: [_ago(days=7)]),
    # Workload regime — not fitness in itself: a variable that moves a
    # fitness metric independently of any code change (more jobs firing).
    # The confounder gate compares it across a change window.
    "jobs_fired_24h": (
        "SELECT COUNT(*) FROM job_runs "
        "WHERE status = 'success' AND started_at >= $1",
        lambda: [_sched_ago(hours=24)]),
}

# Which EXTERNAL signals confound which fitness metric — a list, because
# one metric can be moved by more than one thing at once (a failure-rise
# is confounded by BOTH more work AND a slow model). A metric absent here
# is judged on its own noise, as before. Each listed signal is "the thing
# that would move this metric even if the code change did nothing" — so
# when one moves, blame the external variable, not the change, and hold
# the verdict as 'confounded' until the regime settles. A model-health
# signal (model_probe_ms) is sampled by an active probe, not SQL, so it
# is not in METRIC_QUERIES — but it is a confounder exactly like these.
METRIC_CONFOUNDERS: Dict[str, List[str]] = {
    "jobs_failing": ["jobs_fired_24h", "model_probe_ms"],
}

# Stagnation — the failure shape that no error check can see.
#
# The defect pass looks for failures: errors, dead jobs. A frozen drain
# looks like none of those — the job reports success, the queue just does
# not move. So the landscape samples the stall shape itself: each
# SYSTEM-SIDE queue (one that must drain by itself, by design) is checked
# for work waiting with no evidence of recent completed work. Waiting
# work plus no progress within STALL_WINDOW_HOURS is a stall, and the
# metric only moves when a queue starts or stops moving — a green light
# with no work behind it is what this exists to catch. Queues that
# legitimately wait on a human are deliberately absent: stillness there
# is not a defect.
STALL_QUEUES: Dict[str, Tuple[str, str]] = {
    # queue name -> (work-waiting query, recent-progress query)
    "repair_requests": (
        "SELECT COUNT(*) FROM selfevolve_repair_requests "
        "WHERE state IN ('requested', 'in-flight')",
        "SELECT COUNT(*) FROM selfevolve_repair_requests "
        "WHERE updated_at >= $1"),
}


async def _queues_stalled() -> Tuple[float, Dict[str, Any]]:
    """How many system-side queues are holding work without moving it.

    Returns (count, detail) — the detail names each stalled queue and
    what it is holding, so the nightly run can go straight to the
    culprit."""
    stalled: List[Dict[str, Any]] = []
    for name, (wait_sql, progress_sql) in STALL_QUEUES.items():
        try:
            # A wait query carries a placeholder only when it is
            # window-relative — never bind a param a query does not
            # declare (both dialects reject a surplus binding).
            w_args = [STALL_WINDOW_HOURS] if "$1" in wait_sql else []
            w = await db.fetchone(wait_sql, *w_args)
            p = await db.fetchone(progress_sql, _ago(hours=STALL_WINDOW_HOURS))
        except Exception as exc:  # noqa: BLE001 — one queue is not the sampler
            log.debug("stall check %s failed: %s", name, exc)
            continue
        waiting = float(w and (list(w.values())[0] or 0)) if w else 0.0
        progress = float(p and (list(p.values())[0] or 0)) if p else 0.0
        if waiting > 0 and progress == 0:
            stalled.append({"queue": name, "waiting": waiting,
                            "no_progress_for_hours": STALL_WINDOW_HOURS})
    return float(len(stalled)), {"stalled": stalled}


async def _model_probe_ms() -> Optional[float]:
    """Active probe of the model endpoint — the 'is the LLM busy or slow'
    external signal. Makes one trivial completion and returns its
    round-trip latency in ms. This is what makes a degraded model a
    first-class confounder: the probe latency moves BEFORE any fitness
    metric does, so a change made during a slow period is not blamed for
    it. On error, timeout, or an HTTP failure it returns the budget in ms
    (an unreachable/failing model reads as 'at least this slow'). None
    when the model is not configured — then there is no model signal to
    gate on and the caller skips it."""
    base = os.environ.get("LLM_BASE_URL", "").strip()
    if not base:
        return None
    budget = MODEL_PROBE_TIMEOUT_SEC
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=budget) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization":
                         f"Bearer {os.environ.get('LLM_API_KEY', '')}"},
                json={
                    "model": os.environ.get("LLM_MODEL", ""),
                    "messages": [{"role": "user",
                                   "content": "Reply with the single word: ok"}],
                    "max_tokens": 4, "temperature": 0.0,
                },
            )
        if resp.status_code >= 400:
            return budget * 1000.0  # model erroring — treat as degraded
        return (time.monotonic() - start) * 1000.0
    except Exception:  # noqa: BLE001 — timed out / unreachable
        return budget * 1000.0  # treat as degraded


@router.post("/metrics/sample")
async def metrics_sample():
    """One sampling pass — one row per metric. Fired by the scheduler
    every 30 minutes; returns the values, not a status word."""
    samples: Dict[str, float] = {}
    failed: List[str] = []
    for name, (sql, params_fn) in METRIC_QUERIES.items():
        try:
            row = await db.fetchone(sql, *(params_fn() if params_fn else ()))
            value = (float(list(row.values())[0]) if row
                     and list(row.values())[0] is not None else 0.0)
        except Exception as exc:  # noqa: BLE001 — fault isolation
            log.debug("metric %s failed to sample: %s", name, exc)
            failed.append(name)
            continue
        samples[name] = value
        await db.execute(
            "INSERT INTO selfevolve_metrics (name, value, sampled_at) "
            "VALUES ($1, $2, $3)",
            name, value, _now())
    # Active model probe — the 'is the LLM busy/slow' external signal.
    # Not SQL, so it runs after the query loop. A degraded gateway shows
    # up here first, which is what makes a slow model a confounder rather
    # than a mystery. Absent when the model is not configured (probe
    # returns None).
    try:
        probe_ms = await _model_probe_ms()
        if probe_ms is not None:
            samples["model_probe_ms"] = probe_ms
            await db.execute(
                "INSERT INTO selfevolve_metrics (name, value, sampled_at) "
                "VALUES ($1, $2, $3)",
                "model_probe_ms", probe_ms, _now())
    except Exception as exc:  # noqa: BLE001
        log.debug("model probe failed to sample: %s", exc)
        failed.append("model_probe_ms")
    # Stagnation — the shape that no failure check can see: a queue with
    # work in it that has not moved. 0 = every waiting queue is moving.
    try:
        n_stalled, detail = await _queues_stalled()
        samples["queues_stalled"] = n_stalled
        await db.execute(
            """INSERT INTO selfevolve_metrics
                 (name, value, detail, sampled_at)
               VALUES ($1, $2, $3, $4)""",
            "queues_stalled", n_stalled, json.dumps(detail), _now())
    except Exception as exc:  # noqa: BLE001
        log.debug("stall check failed to sample: %s", exc)
        failed.append("queues_stalled")
    return {"ok": True, "sampled": len(samples), "failed": failed,
            "metrics": samples}


@router.get("/metrics/latest")
async def metrics_latest(hours: int = Query(24, ge=1, le=720)):
    """Latest value per metric plus the value ~N hours ago, so the
    dashboard can show a trend arrow instead of a bare number."""
    rows = await db.fetchall(
        """SELECT m.name, m.value, m.sampled_at
             FROM selfevolve_metrics m
             JOIN (SELECT name, MAX(sampled_at) AS max_at
                     FROM selfevolve_metrics GROUP BY name) g
               ON g.name = m.name AND g.max_at = m.sampled_at
            ORDER BY m.name, m.id DESC""")
    # A tie on the max timestamp would duplicate a name — keep the first.
    latest: Dict[str, dict] = {}
    for r in rows:
        latest.setdefault(r["name"], dict(r))
    out = []
    for name in sorted(latest):
        m = latest[name]
        prev = await db.fetchone(
            """SELECT value FROM selfevolve_metrics
                WHERE name = $1 AND sampled_at <= $2
                ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            name, _shift(m["sampled_at"], hours=hours))
        m["value_prev"] = float(prev["value"]) if prev else None
        out.append(m)
    return {"ok": True, "window_hours": hours, "metrics": out}


@router.get("/fitness")
async def fitness_trend(name: Optional[str] = Query(None),
                        hours: int = Query(168, ge=1, le=720)):
    """Trend series for one metric (or all) — for the meta-run's review
    of the landscape."""
    if name:
        rows = await db.fetchall(
            """SELECT name, value, sampled_at FROM selfevolve_metrics
                WHERE sampled_at >= $1 AND name = $2
                ORDER BY name, sampled_at""",
            _ago(hours=hours), name)
    else:
        rows = await db.fetchall(
            """SELECT name, value, sampled_at FROM selfevolve_metrics
                WHERE sampled_at >= $1
                ORDER BY name, sampled_at""",
            _ago(hours=hours))
    return {"ok": True, "window_hours": hours, "metrics": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Friction — the explicit log of moments the user had to step in
# ---------------------------------------------------------------------------
#
# The sampler cannot see a correction happen; only the agent can, in the
# moment. So the skill logs friction explicitly (kind, context, source)
# whenever it notices one, and the sampler turns the table into the
# friction_7d metric. A cluster of the same kind is a hypothesis for the
# meta-run: recurring friction is what new capabilities grow from.

class FrictionAdd(BaseModel):
    kind: str = Field(..., pattern="^(correction|re-ask|manual|rejected|other)$")
    context: str = Field(..., min_length=5, max_length=1000)
    source: Optional[str] = Field(None, max_length=200)


@router.post("/friction", status_code=201)
async def add_friction(req: FrictionAdd):
    row = await db.fetchone(
        """INSERT INTO selfevolve_friction (kind, context, source, created_at)
           VALUES ($1, $2, $3, $4)
           RETURNING id, kind, context, source, created_at""",
        req.kind, req.context, req.source, _now())
    return {"ok": True, "event": dict(row)}


@router.get("/friction")
async def list_friction(limit: int = Query(50, ge=1, le=200)):
    rows = await db.fetchall(
        """SELECT id, kind, context, source, created_at
             FROM selfevolve_friction
            ORDER BY created_at DESC, id DESC
            LIMIT $1""",
        limit)
    return {"ok": True, "events": [dict(r) for r in rows], "count": len(rows)}


# ---------------------------------------------------------------------------
# Selection over time — changes are kept by evidence, not by trust
# ---------------------------------------------------------------------------
#
# A run that made a change declares, in the finish payload, which
# fitness metric it expects to move and which way (with the pre-change
# baseline). After outcome_measure_days, the reconcile judges the delta,
# gated on the workload: past the tolerance in the wrong direction with a
# stable regime → 'regressed' (the nightly run fixes it forward — it
# never auto-reverts); past it while the workload moved → 'confounded'
# (the move is blamed on the regime, not the change, and is re-judged
# once the regime settles); otherwise 'kept'. 'kept' changes are the
# loop's accumulated adaptations.

@router.get("/metrics/outcomes")
async def list_outcomes(verdict: Optional[str] = Query(None),
                        limit: int = Query(50, ge=1, le=200)):
    if verdict:
        rows = await db.fetchall(
            """SELECT id, run_id, genome_commit, change_summary, metric_name,
                      direction, baseline_value, measured_value, verdict,
                      confounders, decided_at, note, created_at
                 FROM selfevolve_change_outcomes
                WHERE verdict = $1
                ORDER BY CASE verdict
                             WHEN 'regressed' THEN 0 WHEN 'pending' THEN 1
                             WHEN 'confounded' THEN 2 ELSE 3 END,
                         created_at DESC
                LIMIT $2""",
            verdict, limit)
    else:
        rows = await db.fetchall(
            """SELECT id, run_id, genome_commit, change_summary, metric_name,
                      direction, baseline_value, measured_value, verdict,
                      confounders, decided_at, note, created_at
                 FROM selfevolve_change_outcomes
                ORDER BY CASE verdict
                             WHEN 'regressed' THEN 0 WHEN 'pending' THEN 1
                             WHEN 'confounded' THEN 2 ELSE 3 END,
                         created_at DESC
                LIMIT $1""",
            limit)
    return {"ok": True, "outcomes": [dict(r) for r in rows], "count": len(rows)}


class OutcomeReverted(BaseModel):
    commit: str = Field(..., min_length=4, max_length=64)
    note: Optional[str] = Field(None, max_length=1000)


@router.post("/metrics/outcomes/{outcome_id}/reverted")
async def mark_outcome_reverted(outcome_id: int, req: OutcomeReverted):
    """Called by the run that reverted a regressed change — the only
    legal revert path, for explicitly instructed reverts. Closes the
    selection loop with the proof of the revert."""
    existing = await db.fetchone(
        "SELECT note FROM selfevolve_change_outcomes WHERE id = $1",
        outcome_id)
    note = ((existing or {}).get("note") or "") + \
        f" — reverted: {req.note or ''}"
    row = await db.fetchone(
        """UPDATE selfevolve_change_outcomes
               SET verdict = 'reverted', reverted_commit = $2,
                   decided_at = $3, note = $4
             WHERE id = $1 AND verdict = 'regressed'
           RETURNING id, verdict, reverted_commit""",
        outcome_id, req.commit, _now(), note[:1000])
    if not row:
        raise HTTPException(409, f"outcome {outcome_id} is not regressed")
    return {"ok": True, "outcome": dict(row)}


class RebaselineReq(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


@router.post("/metrics/outcomes/{outcome_id}/rebaseline")
async def rebaseline_outcome(outcome_id: int, req: RebaselineReq):
    """Re-anchor a 'confounded' outcome to the current workload regime.

    Called by the nightly run when the workload shift that confounded the
    outcome is PERMANENT (new jobs, a new account, a standing load): the
    old baseline is no longer comparable. Sets the baseline to the
    metric's current level and re-anchors created_at to now, so BOTH the
    measurement window and the confounder gate start fresh, and returns
    the outcome to 'pending' for a full outcome_measure_days window
    against the new regime. A transient spike needs no call — it clears
    itself on the next reconcile."""
    row = await db.fetchone(
        "SELECT * FROM selfevolve_change_outcomes WHERE id = $1",
        outcome_id)
    if not row:
        raise HTTPException(404, f"outcome {outcome_id} not found")
    if row["verdict"] != "confounded":
        raise HTTPException(
            409, f"outcome {outcome_id} is {row['verdict']}, not confounded")
    cur = await db.fetchone(
        """SELECT value FROM selfevolve_metrics
            WHERE name = $1 ORDER BY sampled_at DESC, id DESC LIMIT 1""",
        row["metric_name"])
    if cur is None:
        raise HTTPException(
            409, f"metric {row['metric_name']} has no sample to re-baseline "
                 f"to")
    new_base = float(cur["value"])
    suffix = (f" — re-baselined to new workload regime: "
              f"{float(row['baseline_value']):g} -> {new_base:g}")
    if req.note:
        suffix += f" ({req.note})"
    updated = await db.fetchone(
        """UPDATE selfevolve_change_outcomes
               SET baseline_value = $2, created_at = $3, verdict = 'pending',
                   measured_value = NULL, decided_at = NULL,
                   confounders = NULL, note = $4
             WHERE id = $1
           RETURNING *""",
        outcome_id, new_base, _now(),
        ((row["note"] or "") + suffix)[:2000])
    return {"ok": True, "outcome": dict(updated)}


# ── the judgement ─────────────────────────────────────────────────────

def _sample_stdev(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


async def _noise_band(metric: str, before: str) -> Optional[float]:
    """2x the stdev of a metric's samples in the 28 days before `before`
    — the pre-change noise of the landscape. None when fewer than 5
    samples exist (the caller then falls back to the relative band).

    The stdev is computed in Python over the fetched values — no
    dialect-specific aggregate in SQL."""
    rows = await db.fetchall(
        """SELECT value FROM selfevolve_metrics
            WHERE name = $1 AND sampled_at < $2 AND sampled_at >= $3
            ORDER BY sampled_at, id""",
        metric, before, _shift(before, days=28))
    values = [float(r["value"]) for r in rows]
    if len(values) >= 5:
        return _sample_stdev(values) * 2.0
    return None


def _regression_band(baseline: float, tol: float,
                     noise: Optional[float]) -> float:
    """How far past the baseline counts as a regression. A relative-only
    band misjudges both ways: it flags good changes on naturally spiky
    metrics, and it cannot tell a 14→16 wobble from a real drift on a
    small count. The band is the greatest of the relative tolerance, the
    measured pre-change noise, and an absolute floor of 2 while the
    baseline is still small (where 10% is less than one unit and
    meaningless)."""
    relative = abs(baseline) * max(tol - 1.0, 0.0)
    floor = 2.0 if abs(baseline) < 5 else 0.0
    return max(relative, noise or 0.0, floor)


def _is_regression(baseline: float, measured: float, direction: str,
                   band: float) -> bool:
    if direction == "down":
        return measured > baseline + band
    return measured < baseline - band


async def _confounded(metric: str, at: str,
                      tol: float) -> Optional[List[dict]]:
    """Did the external regime move, independently of the change?

    Checks every external signal the metric declares (METRIC_CONFOUNDERS
    — workload proxies and the model-health probe) and returns the LIST
    of those that moved outside their OWN noise between the change time
    (`at`) and now. Any mover means the metric could have moved for a
    reason that has nothing to do with the change, and the move is not
    cleanly attributable to it. None when no declared signal moved, when
    the metric has no declared signals, or when there is not enough
    history for a signal to judge (never over-gate on thin data).
    Direction-agnostic: a rise OR a fall in any signal confounds (more
    work, or a slow/degraded model all count)."""
    proxies = METRIC_CONFOUNDERS.get(metric)
    if not proxies:
        return None
    moved: List[dict] = []
    for proxy in proxies:
        now_row = await db.fetchone(
            """SELECT value FROM selfevolve_metrics
                WHERE name = $1 ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            proxy)
        at_row = await db.fetchone(
            """SELECT value FROM selfevolve_metrics
                WHERE name = $1 AND sampled_at <= $2
                ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            proxy, at)
        if now_row is None or at_row is None:
            continue  # not enough history for this signal — skip, don't gate
        from_v, to_v = float(at_row["value"]), float(now_row["value"])
        noise = await _noise_band(proxy, at)
        band = _regression_band(from_v, tol, noise)
        if abs(to_v - from_v) > band:
            moved.append({"proxy": proxy, "from": from_v, "to": to_v,
                          "band": band})
    return moved or None


# Metrics that watch EVERY change, declared or not. A change can help
# its declared metric and still clog the system; the guardrail set is
# where that shows up. All are lower-is-better.
GUARDRAIL_METRICS: List[str] = [
    "jobs_failing", "repair_open", "events_dead", "queues_stalled",
]


@router.post("/metrics/reconcile")
async def reconcile_outcomes():
    """Selection over time: noise-aware verdicts, a workload confounder
    gate, and a guardrail watch.

    1. JUDGE pending changes older than outcome_measure_days: regressed
       only when the measured value is past the baseline by MORE than
       the metric's own pre-change noise and the relative tolerance.
       Before committing, GATE on the workload: if a declared external
       signal of the metric moved outside its own noise across the
       window, the move is not cleanly attributable to the change, so
       the verdict is 'confounded' (neither success nor failure) — not
       a regression, so no alarm — and it is re-judged once the regime
       settles. A transient spike clears itself; a permanent regime
       shift is re-baselined by the nightly run.
    2. RE-JUDGE 'confounded' outcomes on every pass until the regime
       settles, then finalise kept/regressed against the baseline.
    3. WATCH the guardrail set across the same window: a core
       reliability metric moving the wrong way raises a system-level
       alert naming the changes made in that period — a signal to
       investigate, not an automatic revert (attribution of a shared
       metric to one commit is a hypothesis, not a fact). Deduped per
       metric per 72h.
    Returns the verdicts and alerts, not a status word."""
    settings = await _get_settings()
    days = float(settings.get("outcome_measure_days", 3))
    tol = float(settings.get("regression_tolerance", 1.0))
    pending = await db.fetchall(
        """SELECT * FROM selfevolve_change_outcomes
            WHERE (verdict = 'pending' AND created_at < $1)
               OR verdict = 'confounded'""",
        _ago(days=days))
    results: List[dict] = []
    for o in pending:
        o = dict(o)
        cur = await db.fetchone(
            """SELECT value FROM selfevolve_metrics
                WHERE name = $1 ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            o["metric_name"])
        if cur is None:
            continue  # metric not sampled yet — judged on a later pass
        measured = float(cur["value"])
        baseline = float(o["baseline_value"])
        noise = await _noise_band(o["metric_name"], o["created_at"])
        band = _regression_band(baseline, tol, noise)
        raw = "regressed" if _is_regression(
            baseline, measured, o["direction"], band) else "kept"

        # Workload confounder gate: if an external signal that drives
        # this metric moved outside its own noise across the window, the
        # move is not cleanly attributable to the change. Hold it as
        # 'confounded' — neither success nor failure, and NOT a
        # regression (so no alarm, no fix-forward pressure on a change
        # that is probably fine).
        confound = await _confounded(o["metric_name"], o["created_at"], tol)
        if confound:
            verdict = "confounded"
            confounders = ", ".join(
                f"{m['proxy']} {m['from']:g} -> {m['to']:g} "
                f"(band {m['band']:g})" for m in confound)
            note = (f"external regime shifted, move not attributable to the "
                    f"change: {confounders}. raw {raw}: measured {measured:g} "
                    f"vs baseline {baseline:g} (direction {o['direction']}, "
                    f"band {band:g}, noise "
                    f"{f'{noise:g}' if noise is not None else 'n/a'}). "
                    f"Re-judged once the regime settles; a permanent shift "
                    f"is re-baselined by the nightly run.")
            await db.execute(
                """UPDATE selfevolve_change_outcomes
                       SET measured_value = $2, verdict = 'confounded',
                           confounders = $3, decided_at = NULL, note = $4
                     WHERE id = $1""",
                o["id"], measured, confounders, note)
            results.append({"id": o["id"], "metric": o["metric_name"],
                            "verdict": verdict, "measured": measured,
                            "baseline": baseline, "band": band,
                            "confounders": confounders})
            continue

        # Regime stable — the move is attributable to the change.
        note = (f"measured {measured:g} vs baseline {baseline:g} "
                f"(direction {o['direction']}, band {band:g}, "
                f"noise {f'{noise:g}' if noise is not None else 'n/a'})")
        await db.execute(
            """UPDATE selfevolve_change_outcomes
                   SET measured_value = $2, verdict = $3, confounders = NULL,
                       decided_at = $4, note = $5
                 WHERE id = $1""",
            o["id"], measured, raw, _now(), note)
        results.append({"id": o["id"], "metric": o["metric_name"],
                        "verdict": raw, "measured": measured,
                        "baseline": baseline, "band": band})
        if raw == "regressed":
            await _maybe_notify(
                f"⚠️ Self-evolve — a change regressed\n"
                f"{(o['change_summary'] or '')[:200]}\n"
                f"Metric {o['metric_name']}: {baseline:g} → {measured:g} "
                f"(expected {o['direction']}, band {band:g}). The next run "
                f"diagnoses the root cause and fixes it forward "
                f"(fix-forward, never auto-revert).")
    alerts: List[dict] = []
    for metric in GUARDRAIL_METRICS:
        latest = await db.fetchone(
            """SELECT value, sampled_at FROM selfevolve_metrics
                WHERE name = $1 ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            metric)
        if latest is None:
            continue
        prev = await db.fetchone(
            """SELECT value FROM selfevolve_metrics
                WHERE name = $1 AND sampled_at <= $2
                ORDER BY sampled_at DESC, id DESC LIMIT 1""",
            metric, _shift(latest["sampled_at"], days=int(days)))
        if prev is None:
            continue  # landscape younger than the window — nothing to compare
        prev_v, cur_v = float(prev["value"]), float(latest["value"])
        noise = await _noise_band(metric, latest["sampled_at"])
        band = _regression_band(prev_v, tol, noise)
        if not _is_regression(prev_v, cur_v, "down", band):
            continue
        open_alert = await db.fetchone(
            """SELECT id FROM selfevolve_guardrail_alerts
                WHERE metric_name = $1 AND raised_at > $2
                  AND acknowledged_at IS NULL""",
            metric, _ago(hours=GUARDRAIL_ALERT_COOLDOWN_HOURS))
        if open_alert:
            continue
        changes = await db.fetchall(
            """SELECT change_summary FROM selfevolve_change_outcomes
                WHERE created_at > $1
                ORDER BY created_at DESC LIMIT 5""",
            _shift(latest["sampled_at"], days=int(days)))
        names = "; ".join(
            (c["change_summary"] or "")[:80] for c in changes) \
            or "(no change recorded in the period)"
        alert = await db.fetchone(
            """INSERT INTO selfevolve_guardrail_alerts
                 (metric_name, window_days, from_value, to_value,
                  threshold, changes_in_window, raised_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING id, metric_name, from_value, to_value""",
            metric, int(days), prev_v, cur_v, band, names, _now())
        await _maybe_notify(
            f"⚠️ Self-evolve — guardrail {metric} regressed\n"
            f"{prev_v:g} → {cur_v:g} over the last {int(days)} days "
            f"(band +{band:g}).\n"
            f"Changes in the period: {names[:200]}")
        alerts.append(dict(alert))
    log.info("outcome reconcile: judged %s, guardrail alerts %s",
             len(results), len(alerts))
    return {"ok": True, "judged": len(results), "results": results,
            "guardrail_alerts": alerts}


@router.get("/metrics/outcomes/summary")
async def outcomes_summary():
    """Calibration data for the meta-run: what the loop has kept,
    reverted or is still judging — by verdict and by metric. A loop
    whose changes never regress may be too conservative; one that
    regresses often needs a stricter trash test. The trends decide."""
    by_verdict = await db.fetchall(
        """SELECT verdict, COUNT(*) AS n
             FROM selfevolve_change_outcomes
            GROUP BY verdict ORDER BY n DESC""")
    by_metric = await db.fetchall(
        """SELECT metric_name, verdict, COUNT(*) AS n
             FROM selfevolve_change_outcomes
            GROUP BY metric_name, verdict
            ORDER BY metric_name, verdict""")
    last = await db.fetchone(
        "SELECT MAX(decided_at) AS t FROM selfevolve_change_outcomes")
    return {"ok": True, "by_verdict": [dict(r) for r in by_verdict],
            "by_metric": [dict(r) for r in by_metric],
            "last_judged": last["t"] if last else None}


# ---------------------------------------------------------------------------
# Guardrails — system-level watch on the core reliability metrics
# ---------------------------------------------------------------------------

@router.get("/guardrail/alerts")
async def list_guardrail_alerts(limit: int = Query(20, ge=1, le=100)):
    rows = await db.fetchall(
        """SELECT id, metric_name, window_days, from_value, to_value,
                  threshold, changes_in_window, raised_at, acknowledged_at,
                  (acknowledged_at IS NULL) AS open
             FROM selfevolve_guardrail_alerts
            ORDER BY raised_at DESC, id DESC
            LIMIT $1""",
        limit)
    return {"ok": True, "alerts": [dict(r) for r in rows], "count": len(rows)}


@router.post("/guardrail/alerts/{alert_id}/acknowledge")
async def acknowledge_guardrail_alert(alert_id: int):
    row = await db.fetchone(
        """UPDATE selfevolve_guardrail_alerts
               SET acknowledged_at = $2
             WHERE id = $1 AND acknowledged_at IS NULL
           RETURNING id, metric_name, acknowledged_at""",
        alert_id, _now())
    if not row:
        raise HTTPException(409, f"alert {alert_id} is not open")
    return {"ok": True, "alert": dict(row)}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
