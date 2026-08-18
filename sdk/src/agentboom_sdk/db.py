"""Data layer: SQLite by default, PostgreSQL when DATABASE_URI is set.

Backend selection happens once, at import time:
  - DATABASE_URI set  -> PostgreSQL via a shared asyncpg pool
  - otherwise         -> SQLite file at DATA_DIR/DB_FILENAME (WAL-tuned)

Quick reference (identical on both backends):

    from agentboom_sdk.db import execute, fetchrow, fetchone, fetchall, fetchval

    rows = await fetchall("SELECT * FROM reminders WHERE status = $1", "pending")
    row  = await fetchrow("SELECT * FROM users WHERE id = $1", 42)
    await execute("UPDATE users SET name = $1 WHERE id = $2", "Alice", 42)

    async with transaction() as conn:   # same connection, atomic block
        ...

Placeholder styles are interchangeable: `$1..$n` queries are rewritten for
SQLite and `?` queries are numbered for PostgreSQL, so code written against
one backend runs on the other.

Passing arguments
-----------------
Both call styles work: ``fetchall(sql, a, b)`` and ``fetchall(sql, [a, b])``.
A single list argument is treated as a parameter list ONLY when the query
has a number of placeholders that makes that the sensible reading; a list
passed to a query with one ``$1`` used in array position (``= ANY($1)``,
``$1::text[]``) is kept intact so PostgreSQL array parameters behave.

PostgreSQL specifics
--------------------
``execute`` returns the status tag ('UPDATE 1'). json/jsonb columns decode
to Python objects automatically (forgetting to json.loads a jsonb column
once cost a dead scheduler watchdog — the codec removes the bug class).
Pool sizing: DB_POOL_MIN / DB_POOL_MAX / DB_POOL_IDLE_SEC. ``acquire()``
exists because consecutive module-level calls may land on DIFFERENT pooled
connections; advisory locks, temp tables and SET LOCAL need one connection.

SQLite specifics
----------------
``execute`` returns the rowcount (int). WAL + busy_timeout=30000 +
synchronous=NORMAL; data must live on a real POSIX filesystem (a Docker
named volume) — host bind mounts corrupt WAL databases.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional

try:
    import asyncpg  # present on PostgreSQL agents
except ImportError:  # SQLite-only agents never need it
    asyncpg = None  # type: ignore[assignment]

log = logging.getLogger("agentboom_sdk.db")

DATABASE_URI = os.environ.get("DATABASE_URI", "")


def _use_postgres() -> bool:
    return bool(DATABASE_URI)


# ── placeholder interop ───────────────────────────────────────────

_DOLLAR_RE = re.compile(r"\$(\d+)")


def _count_question_marks(query: str) -> int:
    """Count `?` placeholders, ignoring `?` inside single-quoted literals."""
    count, in_str, i = 0, False, 0
    while i < len(query):
        ch = query[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(query) and query[i + 1] == "'":
                    i += 1  # '' escape stays inside the literal
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "?":
            count += 1
        i += 1
    return count


def _placeholder_count(query: str) -> int:
    """Highest positional placeholder index used (0 if none), either style."""
    if "$" in query:
        found = _DOLLAR_RE.findall(query)
        return max((int(n) for n in found), default=0)
    return _count_question_marks(query)


# `$1::text[]`, `= ANY($1)`, `ANY($1::uuid[])` — places where the single
# parameter is unambiguously an array rather than a scalar.
_ARRAY_PARAM_RE = re.compile(
    r"\$1\s*::\s*\w+\s*\[\]|(?:ANY|ALL)\s*\(\s*\$1", re.IGNORECASE
)


def _expects_array(query: str) -> bool:
    return bool(_ARRAY_PARAM_RE.search(query))


def _unwrap(query: str, args: tuple) -> tuple:
    """Normalise the two accepted argument styles into a flat tuple.

    Callers historically wrote both ``fetchrow(sql, [a, b])`` and
    ``fetchrow(sql, a, b)``, so a lone list is usually a parameter list.
    The exception that used to silently corrupt queries: a query with a
    single ``$1`` that takes a PostgreSQL array, e.g.

        await fetchall("SELECT ... WHERE id = ANY($1)", [1, 2, 3])

    Blindly exploding that list produced three arguments for one
    placeholder. So a lone list is only exploded when the query actually
    has that many placeholders — otherwise it is passed through as a
    single array value.

    The genuinely ambiguous case is a ONE-element list against a single
    placeholder: settled by looking at how ``$1`` is used — ``= ANY($1)``
    or ``$1::text[]`` means array — because guessing wrong turns a working
    query into a runtime type error.
    """
    if len(args) != 1 or not isinstance(args[0], (list, tuple)):
        return args

    params = args[0]
    if not params:
        return ()

    n_placeholders = _placeholder_count(query)

    if n_placeholders <= 1 and _expects_array(query):
        return args
    if n_placeholders == len(params):
        return tuple(params)
    if n_placeholders <= 1 and len(params) == 1:
        return tuple(params)
    if n_placeholders > 1:
        return tuple(params)  # count mismatch — let the driver raise it
    return args


def _to_sqlite_placeholders(query: str, args: tuple) -> tuple:
    """Rewrite `$n` placeholders to `?`, reordering args accordingly."""
    if "$" in query and "?" in query:
        # Used to pass through silently and die deep in the driver.
        raise ValueError(
            "Mixing $n and ? placeholders in one query is not supported"
        )
    if "$" not in query:
        return query, args
    order: List[int] = []

    def _sub(m):
        order.append(int(m.group(1)))
        return "?"

    rewritten = _DOLLAR_RE.sub(_sub, query)
    if not order:
        return query, args
    try:
        reordered = tuple(args[n - 1] for n in order)
    except IndexError:
        return query, args  # let the driver raise the clear error
    return rewritten, reordered


def _to_postgres_placeholders(query: str, args: tuple) -> tuple:
    """Number sequential `?` placeholders as `$1..$n` for asyncpg.

    `?` inside single-quoted literals is left alone (naive rewriting once
    corrupted queries containing quoted question marks). Note the jsonb
    operators `?` / `?|` / `?&` still collide with positional placeholders
    by design of this interop layer — write those queries with `$n`.
    """
    if "?" not in query or "$" in query:
        return query, args
    out, n, in_str, i = [], 0, False, 0
    while i < len(query):
        ch = query[i]
        if in_str:
            out.append(ch)
            if ch == "'":
                if i + 1 < len(query) and query[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            out.append(ch)
        elif ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
        i += 1
    return "".join(out), args


# ── SQLite backend ────────────────────────────────────────────────

import aiosqlite  # noqa: E402  (resolved at import time for both backends)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR.parent / "data")))
DB_PATH = DATA_DIR / os.environ.get("DB_FILENAME", "agent.db")

_connection_lock = asyncio.Lock()
_db: Optional[aiosqlite.Connection] = None
_db_open = False  # aiosqlite/sqlite3 lack a reliable .closed in Py3.12


class _TaskReentrantLock:
    """Async lock the owning task may re-enter.

    The SQLite backend multiplexes everything through ONE connection, so
    every db operation is serialized behind this lock, and transaction()
    holds it for the whole block — no other task can commit (or roll
    back) its partial work mid-transaction. The same task may still nest
    db calls inside its own transaction, hence reentrance.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None
        self._depth = 0

    async def __aenter__(self) -> "_TaskReentrantLock":
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()
        return False


_op_lock = _TaskReentrantLock()


def _is_closed(conn: Optional[aiosqlite.Connection]) -> bool:
    return conn is None or not _db_open


async def get_connection() -> aiosqlite.Connection:
    """Get or create the singleton SQLite connection."""
    global _db, _db_open
    if _is_closed(_db):
        async with _connection_lock:
            if _is_closed(_db):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _db = await aiosqlite.connect(str(DB_PATH))
                _db.row_factory = aiosqlite.Row
                await _db.execute("PRAGMA journal_mode=WAL")
                await _db.execute("PRAGMA foreign_keys=ON")
                # Wait for locks instead of erroring when another writer
                # (a maintenance exec, a second process) holds the lock.
                await _db.execute("PRAGMA busy_timeout=30000")
                # NORMAL is durable enough for WAL and keeps checkpoints cheap.
                await _db.execute("PRAGMA synchronous=NORMAL")
                await _db.commit()
                _db_open = True
                log.info("SQLite opened at %s", DB_PATH)
    return _db


async def _sqlite_close() -> None:
    global _db, _db_open
    if not _is_closed(_db):
        await _db.close()
        _db = None
        _db_open = False


# ── PostgreSQL backend ────────────────────────────────────────────
# asyncpg is imported lazily so SQLite-only agents never need it installed.

_POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN", "0"))
_POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX", "10"))
_POOL_IDLE_LIFETIME = float(os.environ.get("DB_POOL_IDLE_SEC", "300"))

_pool = None
_pool_lock: Optional[asyncio.Lock] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


def _encode_json(value: Any) -> str:
    """Serialise a value for a json/jsonb column.

    Accepts a dict/list and encodes it, or an already-serialised string and
    passes it through. json.dumps() on an existing JSON string would store a
    quoted string literal instead of an object — silently turning
    `{"a": 1}` into `"{\\"a\\": 1}"`.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


async def _configure_connection(conn) -> None:
    """Decode json/jsonb columns into Python objects.

    By default asyncpg hands back jsonb as a raw str, so every read site
    would have to remember json.loads(); forgetting is silent until runtime.
    It cost the codebase this comes from a dead scheduler watchdog
    (comparing a condition dict against a string). Decoding once here
    removes the whole class of bug.
    """
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=_encode_json,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def get_pool():
    """Return the shared asyncpg pool, creating it on first use."""
    if not DATABASE_URI:
        raise RuntimeError(
            "DATABASE_URI is not set. Configure it in .env / docker-compose.yml."
        )
    if asyncpg is None:
        raise RuntimeError(
            "asyncpg is not installed — install agentboom-sdk[postgres]."
        )

    global _pool, _pool_loop
    if _pool is not None and not getattr(_pool, "_closed", False):
        return _pool

    async with _get_lock():
        if _pool is not None and not getattr(_pool, "_closed", False):
            return _pool
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URI,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            max_inactive_connection_lifetime=_POOL_IDLE_LIFETIME,
            command_timeout=30,
            timeout=10,
            init=_configure_connection,
        )
        _pool_loop = asyncio.get_running_loop()
        log.info(
            "PostgreSQL pool created (min=%d max=%d idle_lifetime=%.0fs)",
            _POOL_MIN_SIZE, _POOL_MAX_SIZE, _POOL_IDLE_LIFETIME,
        )
    return _pool


async def close_pool() -> None:
    """Close the shared pool. Safe to call more than once."""
    global _pool, _pool_loop
    pool, _pool, _pool_loop = _pool, None, None
    if pool is not None and not getattr(pool, "_closed", False):
        await pool.close()


async def copy_from_table(conn, table_name: str, source, columns: tuple):
    """Bulk insert via the PostgreSQL COPY protocol (needs acquire())."""
    return await conn.copy_records_to_table(table_name, source, column=columns)


def _close_pool_on_exit() -> None:
    if _pool is None or getattr(_pool, "_closed", False):
        return
    try:
        loop = _pool_loop
        if loop is None or loop.is_closed():
            return
        if loop.is_running():
            loop.create_task(close_pool())
        else:
            loop.run_until_complete(close_pool())
    except Exception:
        pass  # shutting down; nothing useful to do here


def _close_sqlite_on_exit() -> None:
    # The aiosqlite worker thread is non-daemon: a connection left open at
    # interpreter shutdown hangs process exit. Agents that forget close()
    # must still terminate cleanly.
    if _db is None or not _db_open:
        return
    try:
        asyncio.run(_sqlite_close())
    except Exception:
        pass  # shutting down; nothing useful to do here


def _cleanup_on_exit() -> None:
    _close_pool_on_exit()
    _close_sqlite_on_exit()


atexit.register(_cleanup_on_exit)


# ── unified API ───────────────────────────────────────────────────

async def execute(query: str, *args: Any):
    """Run a statement.

    Returns the rowcount (int) on SQLite, the PostgreSQL status tag
    ('UPDATE 1') on PostgreSQL.
    """
    args = _unwrap(query, args)
    if _use_postgres():
        query, args = _to_postgres_placeholders(query, args)
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)
    query, args = _to_sqlite_placeholders(query, args)
    async with _op_lock:
        db = await get_connection()
        async with db.execute(query, args) as cursor:
            await db.commit()
            return cursor.rowcount


async def fetchone(query: str, *args: Any) -> Optional[dict]:
    """Fetch one row as a dict, or None."""
    args = _unwrap(query, args)
    if _use_postgres():
        query, args = _to_postgres_placeholders(query, args)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None
    query, args = _to_sqlite_placeholders(query, args)
    async with _op_lock:
        db = await get_connection()
        async with db.execute(query, args) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


# asyncpg naming, aliased so both call styles work on both backends.
fetchrow = fetchone


async def fetchall(query: str, *args: Any) -> List[dict]:
    """Fetch all rows as a list of dicts."""
    args = _unwrap(query, args)
    if _use_postgres():
        query, args = _to_postgres_placeholders(query, args)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]
    query, args = _to_sqlite_placeholders(query, args)
    async with _op_lock:
        db = await get_connection()
        async with db.execute(query, args) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def fetchval(query: str, *args: Any) -> Any:
    """Fetch the first column of the first row, or None."""
    args = _unwrap(query, args)
    if _use_postgres():
        query, args = _to_postgres_placeholders(query, args)
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    query, args = _to_sqlite_placeholders(query, args)
    async with _op_lock:
        db = await get_connection()
        async with db.execute(query, args) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return (dict(row) or {}).get(next(iter(dict(row)), None))


async def executemany(sql: str, params_list: List[tuple]) -> int:
    """Execute a statement with multiple parameter sets."""
    if _use_postgres():
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(sql, [tuple(p) for p in params_list])
        return len(params_list)
    async with _op_lock:
        db = await get_connection()
        await db.executemany(sql, params_list)
        await db.commit()
    return len(params_list)


@contextlib.asynccontextmanager
async def acquire() -> AsyncIterator[Any]:
    """Borrow one connection for the duration of the block.

    Use whenever consecutive statements must run on the SAME backend —
    advisory locks, temp tables, SET LOCAL, cursors. On SQLite this yields
    the singleton connection.
    """
    if _use_postgres():
        pool = await get_pool()
        async with pool.acquire() as conn:
            yield conn
    else:
        async with _op_lock:
            yield await get_connection()


@contextlib.asynccontextmanager
async def transaction() -> AsyncIterator[Any]:
    """Borrow a connection and wrap the block in a transaction.

    On SQLite the block also holds the backend-wide op lock, so no other
    task can commit on the shared connection mid-block — the block is
    actually atomic. Keep blocks short: other tasks' db calls wait.
    Nested db calls from the SAME task are fine (the lock is reentrant).
    """
    if _use_postgres():
        async with acquire() as conn:
            async with conn.transaction():
                yield conn
    else:
        async with _op_lock:
            conn = await get_connection()
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


def is_postgres() -> bool:
    """True when the agent runs on PostgreSQL (DATABASE_URI is set).

    The default is SQLite on the data volume — nothing requires
    Postgres. Mini-apps that must vary SQL by backend branch on this.
    """
    return _use_postgres()


def _select_migration_files(migrations_dir: Path) -> Dict[str, Path]:
    """One effective file per migration, keyed by its canonical name.

    `NNN_name.sql` is the base (SQLite-first — the zero-setup default).
    `NNN_name.pg.sql`, when present, REPLACES it on PostgreSQL agents —
    the escape hatch for the few things the dialects genuinely disagree
    on (identity columns, etc.). The canonical name recorded in
    _migrations is the base name either way.
    """
    selected: Dict[str, Path] = {}
    for mf in sorted(migrations_dir.glob("*.sql")):
        if mf.name.endswith(".pg.sql"):
            base = mf.name[: -len(".pg.sql")] + ".sql"
            if _use_postgres():
                selected[base] = mf
            continue
        selected.setdefault(mf.name, mf)
    return selected


async def run_migrations(migrations_dir: Optional[Path] = None) -> None:
    """Apply pending numbered .sql migrations.

    Directory resolution: explicit arg -> MIGRATIONS_DIR env ->
    <package parent>/migrations (vendored layout) -> <cwd>/migrations
    (normal container layout, where the SDK is pip-installed).

    Migrations are immutable once applied anywhere. SQLite is the
    default backend and needs no setup; a migration may ship a
    `<name>.pg.sql` variant used instead on PostgreSQL agents.
    """
    if migrations_dir is None:
        env_dir = os.environ.get("MIGRATIONS_DIR")
        if env_dir:
            migrations_dir = Path(env_dir)
        else:
            vendored = BASE_DIR / "migrations"
            migrations_dir = vendored if vendored.is_dir() else Path.cwd() / "migrations"
    if not migrations_dir.is_dir():
        log.warning("No migrations directory found at %s", migrations_dir)
        return

    selected = _select_migration_files(migrations_dir)

    if _use_postgres():
        async with acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS _migrations ("
                " name TEXT PRIMARY KEY,"
                " applied_at TIMESTAMPTZ DEFAULT NOW())"
            )
            rows = await conn.fetch("SELECT name FROM _migrations")
            applied = {r["name"] for r in rows}
            for name in sorted(selected):
                if name in applied:
                    continue
                mf = selected[name]
                log.info("Applying migration %s", mf.name)
                # No parameters: asyncpg uses the simple protocol, which
                # accepts multi-statement scripts (dollar-quoted bodies ok).
                await conn.execute(mf.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO _migrations (name) VALUES ($1) "
                    "ON CONFLICT (name) DO NOTHING", name,
                )
        return

    await execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        " name TEXT PRIMARY KEY,"
        " applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = set(r["name"] for r in await fetchall("SELECT name FROM _migrations"))
    for name in sorted(selected):
        if name in applied:
            continue
        mf = selected[name]
        log.info("Applying migration %s", mf.name)
        # executescript() handles multi-statement SQL correctly, including
        # strings containing semicolons. It auto-commits per statement —
        # acceptable for migrations.
        async with _op_lock:
            conn = await get_connection()
            await conn.executescript(mf.read_text(encoding="utf-8"))
        await execute(
            "INSERT OR IGNORE INTO _migrations (name) VALUES (?)", (name,)
        )
        log.info("Migration %s applied", mf.name)


async def close() -> None:
    """Close whichever backend is active."""
    if _use_postgres():
        await close_pool()
    else:
        await _sqlite_close()
