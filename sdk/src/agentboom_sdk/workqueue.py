"""Leased work queues — the claim/lease/reclaim/terminal-state discipline.

Durable work queues share one shape: a row is taken by an atomic claim, the
taker holds a lease that only it refreshes, a lapsed lease proves the taker
is gone rather than slow, lapsed rows are put back until their attempts are
spent and then failed, and a row is finished only by the taker that claimed
it. Hand-rolling that shape twice is how orphans get clobbered: a terminal
write that does not check ownership can land on a row a re-claim has already
handed to a new worker.

The rule is structural:

1. CLAIM is the only way to get ownership. It stamps the row with a
   ``claim_token`` and hands it back to the claimer.
2. Every write to a claimed row needs that token — the refresh, the release,
   the terminal state. When the reaper gives a lapsed row to a new claimer,
   the old holder's writes match zero rows and are dropped instead of
   clobbering the new owner. A writer that never claimed the row has no
   token and can do nothing at all.
3. A lapsed lease means the holder stopped existing. The row is requeued
   while it has attempts left and failed when it does not.

Portability: timestamps are ISO-8601 UTC strings written by Python (no
``NOW()``, no ``make_interval``), the claim token is a Python ``uuid4``, and
the claim branches per backend — PostgreSQL uses ``FOR UPDATE SKIP LOCKED``,
SQLite takes the write lock for the whole claim (it serialises everything
anyway).

The queue table must carry: ``id, status, attempts, lease_until, started_at,
finished_at, error, claim_token``, plus a ``result`` column (TEXT) when
``complete()`` is given a result, and whatever ``order_by`` references.
Statuses: ``pending``, ``running``, ``done``, ``failed``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import db

log = logging.getLogger("agentboom_sdk.workqueue")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _later(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat()


def _applied(tag) -> bool:
    """True when the statement wrote at least one row.

    An ownership-guarded write that matches nothing is not an error — it is
    the guard working: the row was reclaimed and given to a new holder, and
    this process may no longer touch it.
    """
    if isinstance(tag, int):
        return tag > 0
    try:
        return int((tag or "").rsplit(" ", 1)[-1]) > 0  # PostgreSQL 'UPDATE n'
    except (ValueError, AttributeError):
        return False


class WorkQueue:
    """The lease-based discipline for one queue table."""

    def __init__(self, *, table: str, name: str, lease_sec: float,
                 max_attempts: int, order_by: str = "created_at"):
        self.table = table
        self.name = name
        self.lease_sec = lease_sec
        self.max_attempts = max_attempts
        # Claim order, as a SQL expression over the table's own columns.
        self.order_by = order_by

    # ── claim ──────────────────────────────────────────────────────────

    async def claim(self) -> Optional[dict]:
        """Take the oldest claimable row, or None. Atomic against any other
        worker.

        A pending row is claimable. A running row is claimable only when its
        lease has lapsed — its holder is gone — and only while it has
        attempts left; an exhausted orphan is failed by ``reclaim_orphans``,
        not re-run, because something about it takes its runner down.
        """
        now, lease_until, token = _now(), _later(self.lease_sec), uuid.uuid4().hex
        if db.is_postgres():
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""UPDATE {self.table} q
                           SET status = 'running',
                               started_at = COALESCE(q.started_at, $1),
                               attempts = q.attempts + 1,
                               lease_until = $2,
                               claim_token = $3
                         FROM (SELECT id FROM {self.table}
                                 WHERE status = 'pending'
                                    OR (status = 'running'
                                        AND (lease_until IS NULL
                                             OR lease_until < $1)
                                        AND attempts < $4)
                                 ORDER BY {self.order_by}
                                 FOR UPDATE SKIP LOCKED
                                 LIMIT 1) picked
                        WHERE q.id = picked.id
                       RETURNING q.*""",
                    now, lease_until, token, self.max_attempts)
            return dict(row) if row else None

        # SQLite: select, claim, read — one transaction holds the write lock.
        async with db.transaction() as conn:
            cursor = await conn.execute(
                f"SELECT id FROM {self.table} "
                "WHERE status = 'pending' "
                "OR (status = 'running' "
                "AND (lease_until IS NULL OR lease_until < ?) "
                "AND attempts < ?) "
                f"ORDER BY {self.order_by} LIMIT 1",
                (now, self.max_attempts),
            )
            picked = await cursor.fetchone()
            if picked is None:
                return None
            row_id = picked[0]
            await conn.execute(
                f"UPDATE {self.table} SET status = 'running', "
                "started_at = COALESCE(started_at, ?), attempts = attempts + 1, "
                "lease_until = ?, claim_token = ? WHERE id = ?",
                (now, lease_until, token, row_id),
            )
            cursor = await conn.execute(
                f"SELECT * FROM {self.table} WHERE id = ?", (row_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    # ── lease ──────────────────────────────────────────────────────────

    async def refresh(self, row_id: int, token: str) -> bool:
        """Extend the lease.

        False means the row is no longer ours — its lease lapsed, the row
        was reclaimed and handed to a new claimer, and this process is now a
        stranger to it. The other guarded writes say the same thing.
        """
        tag = await db.execute(
            f"""UPDATE {self.table}
                  SET lease_until = $2
                WHERE id = $1 AND claim_token = $3""",
            row_id, _later(self.lease_sec), token)
        return _applied(tag)

    async def hold_lease(self, row_id: int, token: str,
                         stop: asyncio.Event) -> None:
        """Keep saying "still mine" until `stop` is set or the row is lost.

        Refreshes every third of the lease, so a slow refresh is never
        mistaken for a dead holder, and a dead holder is never mistaken for
        a slow one.
        """
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.lease_sec / 3)
                return
            except asyncio.TimeoutError:
                pass
            try:
                if not await self.refresh(row_id, token):
                    log.error("%s: job %s lost its lease — a re-claim took "
                              "the row; the work here is orphaned",
                              self.name, row_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("%s: could not refresh lease on job %s",
                            self.name, row_id, exc_info=True)

    # ── giving a row back ──────────────────────────────────────────────

    async def release(self, row_id: int, token: str) -> bool:
        """Return a claimed row to the queue as though it had never been
        taken. The turn never reached the work (the agent was unreachable,
        the prompt never started), so the attempt is refunded: a job must
        not be worn down towards its retry limit by outages it had no part
        in."""
        tag = await db.execute(
            f"""UPDATE {self.table}
                  SET status = 'pending',
                      lease_until = NULL,
                      claim_token = NULL,
                      attempts = CASE WHEN attempts > 0 THEN attempts - 1
                                      ELSE 0 END
                WHERE id = $1 AND status = 'running' AND claim_token = $2""",
            row_id, token)
        return _applied(tag)

    # ── terminal states ────────────────────────────────────────────────

    async def complete(self, row_id: int, token: str, result: Any = None,
                       extra: Optional[Dict[str, Any]] = None) -> bool:
        """Mark the row done.

        ``result`` goes to the table's result column; ``extra`` carries the
        queue's own columns — column names come from the queue's own code,
        values are parameterised.
        """
        sets = ["status = 'done'",
                "result = $2",
                "error = NULL",
                "finished_at = $3",
                "lease_until = NULL",
                "claim_token = NULL"]
        args: List[Any] = [row_id, result, _now()]
        for column, value in (extra or {}).items():
            args.append(value)
            sets.append(f"{column} = ${len(args)}")
        args.append(token)
        tag = await db.execute(
            f"UPDATE {self.table} SET {', '.join(sets)} "
            f"WHERE id = $1 AND claim_token = ${len(args)}", *args)
        return _applied(tag)

    async def fail(self, row_id: int, token: str, error: str, *,
                   retryable: bool = True, refund: bool = False) -> Optional[str]:
        """Fail one attempt, and report what the row ended up as.

        retryable=False — the turn ran and died; the row is failed for good,
        because running it again would do the same thing.
        retryable=True  — the row goes back to the queue while it has
        attempts left, and is failed when it does not.
        refund=True     — no attempt was actually made (the agent was
        offline, the prompt never started), so the attempt is given back:
        the retry budget is never spent on the work being unreachable.

        Returns 'pending' or 'failed', or None when the write did not land
        (the row is no longer ours, and its new holder will settle it).
        """
        if not retryable:
            tag = await db.execute(
                f"""UPDATE {self.table}
                      SET status = 'failed', error = $2,
                          finished_at = $3,
                          lease_until = NULL, claim_token = NULL
                    WHERE id = $1 AND claim_token = $4""",
                row_id, error, _now(), token)
            return "failed" if _applied(tag) else None

        attempts_expr = (
            "CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END" if refund
            else "attempts"
        )
        tag = await db.execute(
            f"""UPDATE {self.table}
                  SET status = CASE WHEN {attempts_expr} >= $3
                                    THEN 'failed' ELSE 'pending' END,
                      error = $2,
                      attempts = {attempts_expr},
                      finished_at = CASE WHEN {attempts_expr} >= $3
                                         THEN $4 ELSE NULL END,
                      lease_until = NULL,
                      claim_token = NULL
                WHERE id = $1 AND claim_token = $5""",
            row_id, error, self.max_attempts, _now(), token)
        if not _applied(tag):
            return None
        row = await db.fetchone(
            f"SELECT status FROM {self.table} WHERE id = $1", row_id)
        return row["status"] if row else None

    # ── reaping ────────────────────────────────────────────────────────

    async def reclaim_orphans(self) -> int:
        """Settle rows whose holder is gone.

        A lapsed lease means the process holding the row stopped existing —
        the holder refreshes it every few seconds while it genuinely holds
        it. While the row has attempts left it is put back to pending and
        the next claim takes it; when it does not, it is failed, because
        something about it takes its runner down, and the visible 'failed'
        is what the repair loop looks for.
        """
        stuck = await db.fetchall(
            f"""SELECT id, attempts FROM {self.table}
                WHERE status = 'running'
                  AND (lease_until IS NULL OR lease_until < $1)""",
            _now())
        if not stuck:
            return 0

        retryable = [r["id"] for r in stuck if r["attempts"] < self.max_attempts]
        exhausted = [r["id"] for r in stuck if r["attempts"] >= self.max_attempts]

        if retryable:
            ph = ", ".join(f"${i}" for i in range(1, len(retryable) + 1))
            await db.execute(
                f"UPDATE {self.table} SET status = 'pending', lease_until = NULL, "
                f"claim_token = NULL WHERE id IN ({ph})", *retryable)
        if exhausted:
            # $1 is finished_at; the ids start at $2.
            ph = ", ".join(f"${i}" for i in range(2, len(exhausted) + 2))
            await db.execute(
                f"""UPDATE {self.table}
                      SET status = 'failed', finished_at = $1,
                          lease_until = NULL, claim_token = NULL,
                          error = COALESCE(error,
                            'abandoned: the worker lost its lease after '
                            || attempts || ' attempts')
                    WHERE id IN ({ph})""",
                _now(), *exhausted)

        log.warning("%s: reclaimed %d orphaned row(s), failed %d exhausted",
                    self.name, len(retryable), len(exhausted))
        return len(stuck)
