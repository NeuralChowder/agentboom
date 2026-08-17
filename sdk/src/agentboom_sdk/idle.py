"""Letting a scheduled job prove there is nothing to do.

    from agentboom_sdk.idle import unchanged_since, mark_done

    async def promote():
        fingerprint = await input_fingerprint(
            "SELECT count(*), max(seen_at) FROM brain.promotion_candidates")
        if await unchanged_since("brain-promote", fingerprint):
            return {"ok": True, "skipped": "no new candidates"}

        ...do the work...

        await mark_done("brain-promote", fingerprint)   # only on success

The shape matters more than the helper. Two rules, both because the failure is
silent:

**Record on success, never on start.** A job that dies halfway must run again.
Stamping the fingerprint when the work begins leaves state saying "already
done" after a crash, and the work is then never completed and never reported
missing.

**Derive the fingerprint from data, never from a clock.** "Nothing changed in
the last 10 minutes" is a guess. "The newest row is the same row and there are
the same number of them" is a fact. A time-based skip is wrong the moment a
row arrives dated earlier than the last run — a backfill, a re-import, a
message that took a week to sync — which is exactly when the job most needs to
run.

When in doubt, run. A wasted pass costs one model call. A wrongly skipped pass
costs a fact that is never learned, and nothing reports that it was missed.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional, Sequence

from agentboom_sdk.db import execute, fetchrow

log = logging.getLogger("agentboom_sdk.idle")

# The module bootstraps its own table: no migration anywhere creates it,
# and requiring one would break every adopting job on first use. The name
# is unqualified on purpose — `scheduler.job_input_state` is PostgreSQL
# schema syntax and never existed on SQLite.
_TABLE = "job_input_state"
_ensured = False


async def _ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    await execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            job_key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            skips INTEGER NOT NULL DEFAULT 0,
            last_ran_at TIMESTAMP,
            last_skipped_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    _ensured = True


async def input_fingerprint(query: str, *args: Any) -> str:
    """Reduce a job's inputs to a short string.

    The query should return one row of cheap aggregates — a count and a
    max(timestamp) over an indexed column is almost always both sufficient and
    free. It must NOT return the rows themselves; the point is to decide
    whether to look, without looking.

    A query that raises produces a fingerprint that never matches, so a broken
    check degrades to running every time rather than to skipping every time.
    """
    try:
        row = await fetchrow(query, *args)
    except Exception as exc:
        log.warning("Fingerprint query failed, will not skip: %s", exc)
        return "unknown"
    if row is None:
        return "empty"
    return hashlib.sha256(
        "|".join(str(v) for v in dict(row).values()).encode("utf-8")
    ).hexdigest()[:32]


async def unchanged_since(job_key: str, fingerprint: str) -> bool:
    """True when this job last *completed* with exactly these inputs.

    An "unknown" fingerprint never matches, so a job whose own check is broken
    keeps running. That asymmetry is deliberate: the cost of running is a model
    call, the cost of skipping wrongly is silence.
    """
    if fingerprint in ("unknown", ""):
        return False

    row = await fetchrow(
        "SELECT fingerprint FROM scheduler.job_input_state WHERE job_key = $1",
        job_key)
    if not row or row["fingerprint"] != fingerprint:
        return False

    await execute(
        """UPDATE scheduler.job_input_state
              SET skips = skips + 1, last_skipped_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE job_key = $1""", job_key)
    return True


async def mark_done(job_key: str, fingerprint: str) -> None:
    """Record the inputs this job has now finished processing.

    Call this only after the work has actually succeeded. Calling it earlier is
    the one way to turn this optimisation into lost work.
    """
    if fingerprint in ("unknown", ""):
        return
    await execute(
        """INSERT INTO scheduler.job_input_state (job_key, fingerprint, last_ran_at, updated_at)
           VALUES ($1, $2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
           ON CONFLICT (job_key) DO UPDATE
              SET fingerprint = EXCLUDED.fingerprint,
                  last_ran_at = CURRENT_TIMESTAMP,
                  updated_at = CURRENT_TIMESTAMP""",
        job_key, fingerprint)


async def state(job_key: str) -> Optional[dict]:
    """What this job last saw — for a status screen or a puzzled human."""
    row = await fetchrow(
        "SELECT * FROM scheduler.job_input_state WHERE job_key = $1", job_key)
    return dict(row) if row else None
