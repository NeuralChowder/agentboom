"""Saying what happened, for a person to read later.

    from agentboom_sdk import activity

    await activity.log(
        "invoices", "invoice.filed",
        "Acme — INV 2026/1183 filed under July",
        detail="€48.30. Tax id found on page 1.",
        subject="email:19c3f2ab",
        link="/api/storage/download/invoices/2026-07/acme-inv-1183.pdf")

Two rules, and they are what make the feed worth opening:

**Write the outcome, not the attempt.** "Processed 14 emails" tells the user
nothing they can act on. "Refused a PDF from Supplier X — it carries the
wrong tax id" tells them a supplier has the wrong billing details on file.
One row per thing that happened *to their things*, in language they would use.

**Never let this fail the work.** Every call swallows its own errors. A
document that was filed correctly must not be reported as a failure because
the row describing it could not be written — that inverts the truth, which is
worse than saying nothing.

Failures belong here too. A pipeline that only records its successes produces
a feed that looks healthy no matter what is happening.

The table self-bootstraps and works on both SQLite and PostgreSQL (no
schema qualification, plain placeholders, portable CURRENT_TIMESTAMP).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from agentboom_sdk import db

_log = logging.getLogger("agentboom_sdk.activity")

#: The statuses a dashboard knows how to colour. Anything else is stored as
#: given and rendered neutral.
STATUSES = ("ok", "pending", "warn", "failed")

_bootstrapped = False


async def _ensure_table() -> None:
    global _bootstrapped
    if _bootstrapped:
        return
    try:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_entries (
                id INTEGER PRIMARY KEY,
                app TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                status TEXT NOT NULL DEFAULT 'ok',
                subject TEXT,
                link TEXT,
                meta TEXT,
                occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _bootstrapped = True
    except Exception:  # noqa: BLE001 — never let bookkeeping break the work
        _log.warning("activity: could not create table", exc_info=True)


async def log_activity(
    app: str,
    kind: str,
    title: str,
    *,
    detail: Optional[str] = None,
    status: str = "ok",
    subject: Optional[str] = None,
    link: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Record one thing that happened. Returns the row id, or None if it could
    not be written — never raises.

    (Named `log_activity` so it does not shadow the module's logger; it is
    also exported as `log` for the natural call style.)
    """
    try:
        await _ensure_table()
        row_id = await db.fetchval(
            """
            INSERT INTO activity_entries
                (app, kind, title, detail, status, subject, link, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            app, kind, title[:500], detail, status, subject, link,
            json.dumps(meta or {}, default=str),
        )
        return row_id
    except Exception:  # noqa: BLE001 — deliberately not re-raised
        _log.warning("Could not record activity %s/%s", app, kind, exc_info=True)
        return None


# The natural call style: activity.log(...)
log = log_activity  # noqa: A001 — intentional alias, logger is _log


async def recent(limit: int = 50, app: Optional[str] = None) -> list:
    """Most recent entries, newest first."""
    try:
        await _ensure_table()
        if app:
            return await db.fetchall(
                "SELECT * FROM activity_entries WHERE app = ? "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                app, int(limit))
        return await db.fetchall(
            "SELECT * FROM activity_entries "
            "ORDER BY occurred_at DESC, id DESC LIMIT ?",
            int(limit))
    except Exception:  # noqa: BLE001
        return []


async def prune(days: int = 180) -> int:
    """Drop entries older than `days`. Returns how many went (portable)."""
    try:
        await _ensure_table()
        # Compute the cutoff in Python so it works on SQLite and Postgres.
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
                  ).strftime("%Y-%m-%d %H:%M:%S")
        removed = await db.execute(
            "DELETE FROM activity_entries WHERE occurred_at < ?", cutoff)
        return int(removed or 0)
    except Exception:  # noqa: BLE001
        return 0
