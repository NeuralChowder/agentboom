"""SQLite-backed scheduler for mini-app jobs.

Jobs are declared in mini-app manifests (.miniapp.json `jobs`) and
registered with the scheduler when the gateway loads or reloads apps.

Two job types:
  http  — POST {PLATFORM_INTERNAL_URL}/api/<app>/<target>
  agent — run one Qwen Code agent turn with the job's prompt
          (serialized through sdk.agent's task queue)

Doctrine, distilled from production incidents:
- Every run is recorded in job_runs. A run stuck in 'running' longer than
  STALE_RUNNING_SEC is reaped and marked failed — a wedged run once
  silently disabled alerting for 9 days before anyone noticed.
- Failures back off exponentially instead of hammering a broken endpoint.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from agentloom_sdk import agent as agent_sdk
from agentloom_sdk import cron, db

log = logging.getLogger("services.scheduler")

TICK_SEC = int(os.environ.get("SCHEDULER_TICK_SEC", "10"))
STALE_RUNNING_SEC = int(os.environ.get("STALE_RUNNING_SEC", "600"))
MAX_JOB_PARALLEL = int(os.environ.get("MAX_JOB_PARALLEL", "2"))
FAILURE_BACKOFF_BASE_SEC = 60
FAILURE_BACKOFF_CAP_SEC = 3600
AGENT_JOB_TIMEOUT = float(os.environ.get("AGENT_JOB_TIMEOUT_SEC", "300"))
HTTP_JOB_TIMEOUT = float(os.environ.get("HTTP_JOB_TIMEOUT_SEC", "300"))
INTERNAL_URL = os.environ.get("PLATFORM_INTERNAL_URL", "http://127.0.0.1:8000")

_TS = "%Y-%m-%d %H:%M:%S"  # matches SQLite CURRENT_TIMESTAMP (UTC)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


class Scheduler:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._sem = asyncio.Semaphore(MAX_JOB_PARALLEL)
        self._in_flight: set = set()

    # ── lifecycle ─────────────────────────────────────────────────

    async def start(self):
        await agent_sdk.start()
        self._task = asyncio.create_task(self._loop(), name="scheduler")
        log.info("Scheduler started (tick=%ss, max_parallel=%d)", TICK_SEC, MAX_JOB_PARALLEL)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await agent_sdk.stop()

    # ── registration (called by the gateway on app load/reload) ───

    async def register_jobs(self, app_name: str, jobs: List[dict]) -> None:
        """Upsert an app's manifest jobs; preserve next_run unless the
        schedule definition changed."""
        now = _utcnow()
        for job in jobs:
            name = job.get("name")
            if not name:
                log.warning("App %s: job without a name skipped", app_name)
                continue
            cron_expr = job.get("cron") or None
            interval_min = job.get("interval_min")
            if cron_expr and not cron.is_valid_cron(cron_expr):
                log.error("App %s job %s: invalid cron '%s' — job disabled",
                          app_name, name, cron_expr)
                continue
            next_run = _first_next_run(cron_expr, interval_min, now)
            await db.execute(
                """
                INSERT INTO schedule_jobs
                    (app, name, type, target, prompt, cron_expr, interval_min,
                     enabled, next_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(app, name) DO UPDATE SET
                    type = excluded.type,
                    target = excluded.target,
                    prompt = excluded.prompt,
                    enabled = excluded.enabled,
                    cron_expr = excluded.cron_expr,
                    interval_min = excluded.interval_min,
                    next_run = CASE
                        WHEN schedule_jobs.cron_expr IS NOT excluded.cron_expr
                          OR schedule_jobs.interval_min IS NOT excluded.interval_min
                        THEN excluded.next_run
                        ELSE schedule_jobs.next_run
                    END
                """,
                (
                    app_name, name, job.get("type", "http"), job.get("target"),
                    job.get("prompt"), cron_expr, interval_min,
                    1 if job.get("enabled", True) else 0,
                    _fmt(next_run) if next_run else None,
                ),
            )
        # Drop jobs that vanished from the manifest.
        declared = [j.get("name") for j in jobs if j.get("name")]
        if declared:
            placeholders = ",".join("?" * len(declared))
            await db.execute(
                f"DELETE FROM schedule_jobs WHERE app = ? AND name NOT IN ({placeholders})",
                (app_name, *declared),
            )
        else:
            await db.execute("DELETE FROM schedule_jobs WHERE app = ?", (app_name,))

    async def unregister_app(self, app_name: str) -> None:
        await db.execute("DELETE FROM schedule_jobs WHERE app = ?", (app_name,))

    # ── main loop ─────────────────────────────────────────────────

    async def _loop(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_SEC)

    async def _tick(self):
        now = _utcnow()
        await self._reap_stale_runs(now)
        due = await db.fetchall(
            """
            SELECT * FROM schedule_jobs
            WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?
            """,
            (_fmt(now),),
        )
        for job in due:
            if job["id"] in self._in_flight:
                continue
            self._in_flight.add(job["id"])
            asyncio.create_task(self._run_job(dict(job)))

    async def _run_job(self, job: Dict[str, Any]):
        started = _utcnow()
        await db.execute(
            """
            INSERT INTO job_runs (job_id, job_name, status, started_at)
            VALUES (?, ?, 'running', ?)
            """,
            (job["id"], f"{job['app']}.{job['name']}", _fmt(started)),
        )
        row = await db.fetchone(
            "SELECT id FROM job_runs WHERE job_id = ? AND started_at = ? AND status = 'running'",
            (job["id"], _fmt(started)),
        )
        run_id = row["id"] if row else None

        error: Optional[str] = None
        try:
            async with self._sem:
                if job["type"] == "agent":
                    answer = await agent_sdk.ask(
                        job.get("prompt") or "", timeout=AGENT_JOB_TIMEOUT
                    )
                    if answer is None:
                        error = "agent turn returned no answer"
                else:
                    url = f"{INTERNAL_URL}/api/{job['app']}/{job.get('target') or ''}".rstrip("/")
                    async with httpx.AsyncClient(timeout=HTTP_JOB_TIMEOUT) as client:
                        resp = await client.post(url)
                    if resp.status_code >= 400:
                        error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:  # noqa: BLE001 — recorded, never crashes the loop
            error = str(exc)

        finished = _utcnow()
        status = "failed" if error else "success"
        if run_id:
            await db.execute(
                """
                UPDATE job_runs
                SET finished_at = ?, duration_ms = ?, status = ?, error = ?
                WHERE id = ?
                """,
                (_fmt(finished), int((finished - started).total_seconds() * 1000),
                 status, error, run_id),
            )

        new_fail = (job.get("fail_count") or 0) + (1 if error else 0)
        next_run = _next_run_after(job, finished, failed=bool(error), fail_count=new_fail)
        await db.execute(
            """
            UPDATE schedule_jobs
            SET last_run = ?, last_status = ?, fail_count = ?, next_run = ?
            WHERE id = ?
            """,
            (_fmt(finished), status, 0 if not error else new_fail,
             _fmt(next_run) if next_run else None, job["id"]),
        )
        if error:
            log.warning("Job %s.%s failed (fail_count=%d): %s",
                        job["app"], job["name"], new_fail, error)
        else:
            log.info("Job %s.%s ok in %dms",
                     job["app"], job["name"],
                     int((finished - started).total_seconds() * 1000))
        self._in_flight.discard(job["id"])

    async def _reap_stale_runs(self, now: datetime):
        """Re-arm jobs stuck in 'running' past STALE_RUNNING_SEC.

        If a run is that old, the dispatching process is gone; the job row
        would otherwise stay blocked forever (silent outage).
        """
        cutoff = now - timedelta(seconds=STALE_RUNNING_SEC)
        stale = await db.fetchall(
            "SELECT id, job_id FROM job_runs WHERE status = 'running' AND started_at < ?",
            (_fmt(cutoff),),
        )
        for run in stale:
            await db.execute(
                """
                UPDATE job_runs SET status = 'failed', finished_at = ?,
                    error = 'reaped: exceeded STALE_RUNNING_SEC'
                WHERE id = ?
                """,
                (_fmt(now), run["id"]),
            )
            log.error("Reaped stale run %d (job_id=%d) after %ds",
                      run["id"], run["job_id"], STALE_RUNNING_SEC)

    # ── introspection ─────────────────────────────────────────────

    async def stats(self) -> Dict[str, Any]:
        jobs = await db.fetchall(
            "SELECT app, name, enabled, last_status, fail_count, next_run FROM schedule_jobs"
        )
        recent = await db.fetchall(
            "SELECT job_name, status, started_at, duration_ms FROM job_runs "
            "ORDER BY id DESC LIMIT 20"
        )
        return {
            "tick_sec": TICK_SEC,
            "in_flight": len(self._in_flight),
            "jobs": jobs,
            "recent_runs": recent,
        }


def _first_next_run(cron_expr, interval_min, now: datetime) -> Optional[datetime]:
    if cron_expr:
        return cron.next_cron_time(cron_expr, after=now)
    if interval_min:
        return now + timedelta(minutes=interval_min)
    return None  # manual-only job


def _next_run_after(job: dict, after: datetime, *, failed: bool,
                    fail_count: int) -> Optional[datetime]:
    if failed:
        delay = min(
            FAILURE_BACKOFF_BASE_SEC * (2 ** max(fail_count - 1, 0)),
            FAILURE_BACKOFF_CAP_SEC,
        )
        return after + timedelta(seconds=delay)
    if job.get("cron_expr"):
        return cron.next_cron_time(job["cron_expr"], after=after)
    if job.get("interval_min"):
        return after + timedelta(minutes=job["interval_min"])
    return None


scheduler = Scheduler()
