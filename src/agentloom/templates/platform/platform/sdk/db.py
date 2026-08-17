# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""SQLite database access layer.

Singleton aiosqlite connection tuned for containers:
- WAL journal mode (readers don't block the writer)
- busy_timeout=30000: wait for locks instead of erroring when another
  writer (a maintenance exec, a second process) holds the write lock
- synchronous=NORMAL: durable enough for WAL, keeps checkpoints cheap

Data must live on a real POSIX filesystem (a Docker named volume).
Host bind mounts (especially network ones) break SQLite WAL locking and
corrupt databases — this was learned the expensive way.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from sdk.config import env

log = logging.getLogger("sdk.db")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR.parent / "data")))
DB_PATH = DATA_DIR / env("DB_FILENAME", "agent.db")

_connection_lock = asyncio.Lock()
_db: Optional[aiosqlite.Connection] = None
_db_open = False  # aiosqlite/sqlite3 lack a reliable .closed in Py3.12


def _is_closed(conn: Optional[aiosqlite.Connection]) -> bool:
    return conn is None or not _db_open


async def get_connection() -> aiosqlite.Connection:
    """Get or create the singleton database connection."""
    global _db, _db_open
    if _is_closed(_db):
        async with _connection_lock:
            if _is_closed(_db):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _db = await aiosqlite.connect(str(DB_PATH))
                _db.row_factory = aiosqlite.Row
                await _db.execute("PRAGMA journal_mode=WAL")
                await _db.execute("PRAGMA foreign_keys=ON")
                await _db.execute("PRAGMA busy_timeout=30000")
                await _db.execute("PRAGMA synchronous=NORMAL")
                await _db.commit()
                _db_open = True
                log.info("Database opened at %s", DB_PATH)
    return _db


async def execute(sql: str, params: Optional[tuple] = None) -> int:
    """Execute a single SQL statement. Returns rows affected."""
    db = await get_connection()
    async with db.execute(sql, params or ()) as cursor:
        await db.commit()
        return cursor.rowcount


async def fetchone(sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dict, or None."""
    db = await get_connection()
    async with db.execute(sql, params or ()) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def fetchall(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Fetch all rows as list of dicts."""
    db = await get_connection()
    async with db.execute(sql, params or ()) as cursor:
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def executemany(sql: str, params_list: List[tuple]) -> int:
    """Execute a statement with multiple parameter sets."""
    db = await get_connection()
    await db.executemany(sql, params_list)
    await db.commit()
    return len(params_list)


async def run_migrations() -> None:
    """Apply pending SQL migrations from platform/migrations/.

    Migrations are numbered .sql files, immutable once applied anywhere.
    """
    migrations_dir = BASE_DIR / "migrations"
    if not migrations_dir.exists():
        return

    await execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    applied = set(
        r["name"] for r in await fetchall("SELECT name FROM _migrations")
    )

    for mf in sorted(migrations_dir.glob("*.sql")):
        if mf.name in applied:
            continue
        log.info("Applying migration %s", mf.name)
        # executescript() handles multi-statement SQL correctly, including
        # strings containing semicolons. It auto-commits per statement —
        # acceptable for migrations.
        conn = await get_connection()
        await conn.executescript(mf.read_text(encoding="utf-8"))
        await execute("INSERT OR IGNORE INTO _migrations (name) VALUES (?)", (mf.name,))
        log.info("Migration %s applied", mf.name)


async def close() -> None:
    """Close the database connection."""
    global _db, _db_open
    if not _is_closed(_db):
        await _db.close()
        _db = None
        _db_open = False
