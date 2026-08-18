/**
 * Data layer — mirror of agentboom_sdk.db.
 *
 * SQLite by default (zero setup, file at $DATA_DIR/agent.db via
 * better-sqlite3); PostgreSQL when DATABASE_URI is set (pg pool).
 * The same code runs on both: `?` and `$n` placeholders are accepted
 * anywhere and rewritten per backend; migrations may ship a
 * `NNN_name.pg.sql` variant used on PostgreSQL.
 */
import fs from "node:fs";
import path from "node:path";

import Database from "better-sqlite3";
import pg from "pg";

import { env } from "./config.js";

export type Row = Record<string, unknown>;
export type Param = string | number | boolean | null | Uint8Array;

const DATABASE_URI = env("DATABASE_URI");
const DATA_DIR = env("DATA_DIR", "data");
const DB_FILENAME = env("DB_FILENAME", "agent.db");

let sqlite: Database.Database | null = null;
let pool: pg.Pool | null = null;

export function isPostgres(): boolean {
  return DATABASE_URI !== "";
}

// ── placeholder interop ────────────────────────────────────────────

const DOLLAR_RE = /\$(\d+)/g;

function countOutsideLiterals(query: string, char: string): number {
  let count = 0;
  let inStr = false;
  for (let i = 0; i < query.length; i += 1) {
    const ch = query[i];
    if (inStr) {
      if (ch === "'") {
        if (query[i + 1] === "'") i += 1;
        else inStr = false;
      }
    } else if (ch === "'") inStr = true;
    else if (ch === char) count += 1;
  }
  return count;
}

export function placeholderCount(query: string): number {
  if (query.includes("$")) {
    let max = 0;
    for (const match of query.matchAll(DOLLAR_RE)) {
      max = Math.max(max, Number.parseInt(match[1], 10));
    }
    return max;
  }
  return countOutsideLiterals(query, "?");
}

function toSqlitePlaceholders(query: string, args: Param[]): [string, Param[]] {
  if (query.includes("$") && query.includes("?")) {
    throw new Error("Mixing $n and ? placeholders in one query is not supported");
  }
  if (!query.includes("$")) return [query, args];
  const order: number[] = [];
  const rewritten = query.replace(DOLLAR_RE, (_m, n: string) => {
    order.push(Number.parseInt(n, 10));
    return "?";
  });
  return [rewritten, order.map((n) => args[n - 1])];
}

function toPostgresPlaceholders(query: string, args: Param[]): [string, Param[]] {
  if (!query.includes("?") || query.includes("$")) return [query, args];
  let out = "";
  let n = 0;
  let inStr = false;
  for (let i = 0; i < query.length; i += 1) {
    const ch = query[i];
    if (inStr) {
      out += ch;
      if (ch === "'") {
        if (query[i + 1] === "'") {
          out += "'";
          i += 1;
        } else inStr = false;
      }
    } else if (ch === "'") {
      inStr = true;
      out += ch;
    } else if (ch === "?") {
      n += 1;
      out += `$${n}`;
    } else out += ch;
  }
  return [out, args];
}

// ── backends ───────────────────────────────────────────────────────

function getSqlite(): Database.Database {
  if (sqlite) return sqlite;
  fs.mkdirSync(DATA_DIR, { recursive: true });
  sqlite = new Database(path.join(DATA_DIR, DB_FILENAME));
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("synchronous = NORMAL");
  sqlite.pragma("foreign_keys = ON");
  return sqlite;
}

function getPool(): pg.Pool {
  if (!pool) pool = new pg.Pool({ connectionString: DATABASE_URI });
  return pool;
}

// ── unified API ────────────────────────────────────────────────────

export interface ExecResult {
  rowCount: number;
  lastInsertRowid?: number | bigint;
}

/** Run a statement; returns affected rows (+ last id on SQLite). */
export async function execute(query: string, ...args: Param[]): Promise<ExecResult> {
  if (isPostgres()) {
    const [sql, params] = toPostgresPlaceholders(query, args);
    const result = await getPool().query(sql, params);
    return { rowCount: result.rowCount ?? 0 };
  }
  const [sql, params] = toSqlitePlaceholders(query, args);
  const info = getSqlite().prepare(sql).run(...(params as unknown[]));
  return { rowCount: info.changes, lastInsertRowid: info.lastInsertRowid };
}

export async function fetchOne(query: string, ...args: Param[]): Promise<Row | null> {
  if (isPostgres()) {
    const [sql, params] = toPostgresPlaceholders(query, args);
    const result = await getPool().query(sql, params);
    return (result.rows[0] as Row | undefined) ?? null;
  }
  const [sql, params] = toSqlitePlaceholders(query, args);
  const row = getSqlite().prepare(sql).get(...(params as unknown[]));
  return (row as Row | undefined) ?? null;
}

/** asyncpg-named alias — both call styles work on both backends. */
export const fetchRow = fetchOne;

export async function fetchAll(query: string, ...args: Param[]): Promise<Row[]> {
  if (isPostgres()) {
    const [sql, params] = toPostgresPlaceholders(query, args);
    const result = await getPool().query(sql, params);
    return result.rows as Row[];
  }
  const [sql, params] = toSqlitePlaceholders(query, args);
  return getSqlite().prepare(sql).all(...(params as unknown[])) as Row[];
}

export async function fetchVal(query: string, ...args: Param[]): Promise<unknown> {
  const row = await fetchOne(query, ...args);
  if (row === null) return null;
  const keys = Object.keys(row);
  return keys.length ? row[keys[0]] : null;
}

/**
 * Run `fn` inside a transaction. SQLite serializes through the single
 * connection; PostgreSQL uses a dedicated client with BEGIN/COMMIT.
 */
export async function transaction<T>(fn: (run: (q: string, ...a: Param[]) => Promise<ExecResult>) => Promise<T>): Promise<T> {
  if (isPostgres()) {
    const client = await getPool().connect();
    try {
      await client.query("BEGIN");
      const run = async (q: string, ...a: Param[]) => {
        const [sql, params] = toPostgresPlaceholders(q, a);
        const result = await client.query(sql, params);
        return { rowCount: result.rowCount ?? 0 };
      };
      const out = await fn(run);
      await client.query("COMMIT");
      return out;
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }
  const db = getSqlite();
  db.exec("BEGIN");
  try {
    const run = async (q: string, ...a: Param[]) => {
      const [sql, params] = toSqlitePlaceholders(q, a);
      const info = db.prepare(sql).run(...(params as unknown[]));
      return { rowCount: info.changes, lastInsertRowid: info.lastInsertRowid };
    };
    const out = await fn(run);
    db.exec("COMMIT");
    return out;
  } catch (err) {
    db.exec("ROLLBACK");
    throw err;
  }
}

// ── migrations ─────────────────────────────────────────────────────

/**
 * Apply pending numbered .sql migrations.
 * `NNN_name.pg.sql` replaces `NNN_name.sql` on PostgreSQL agents; the
 * canonical name recorded is the base one either way.
 */
export async function runMigrations(migrationsDir?: string): Promise<void> {
  const dir = migrationsDir
    ?? env("MIGRATIONS_DIR")
    ?? (fs.existsSync(path.join("migrations")) ? "migrations" : "");
  if (!dir || !fs.existsSync(dir)) return;

  const selected = new Map<string, string>();
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    if (file.endsWith(".pg.sql")) {
      if (isPostgres()) selected.set(file.slice(0, -".pg.sql".length), path.join(dir, file));
      continue;
    }
    if (!selected.has(file)) selected.set(file, path.join(dir, file));
  }

  if (isPostgres()) {
    const client = await getPool().connect();
    try {
      await client.query(
        "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())",
      );
      const applied = new Set(
        (await client.query("SELECT name FROM _migrations")).rows.map((r: Row) => r.name as string),
      );
      for (const [name, file] of [...selected.entries()].sort()) {
        if (applied.has(name)) continue;
        await client.query(fs.readFileSync(file, "utf-8"));
        await client.query(
          "INSERT INTO _migrations (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
          [name],
        );
      }
    } finally {
      client.release();
    }
    return;
  }

  const db = getSqlite();
  db.exec("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)");
  const applied = new Set(
    db.prepare("SELECT name FROM _migrations").all().map((r) => (r as Row).name as string),
  );
  for (const [name, file] of [...selected.entries()].sort()) {
    if (applied.has(name)) continue;
    db.exec(fs.readFileSync(file, "utf-8"));
    db.prepare("INSERT OR IGNORE INTO _migrations (name) VALUES (?)").run(name);
  }
}

export async function close(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
  if (sqlite) {
    sqlite.close();
    sqlite = null;
  }
}
