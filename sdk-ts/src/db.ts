/**
 * Database access — a BRIDGE to the gateway's single db layer.
 *
 * The actual logic (placeholder interop, SQLite/PostgreSQL backend
 * selection, connection handling) lives once in agentboom_sdk.db inside
 * the platform gateway. This client just forwards to POST /api/bridge/db
 * over loopback HTTP — so a Node mini-app uses the SAME db the rest of
 * the platform uses, with zero duplicated logic and no native driver.
 *
 * Migrations are the gateway's job (it runs them at startup); mini-apps
 * only query.
 */
import { env } from "./config.js";

export const PLATFORM_INTERNAL_URL = env(
  "PLATFORM_INTERNAL_URL",
  "http://127.0.0.1:8000",
);
const DB_URL = `${PLATFORM_INTERNAL_URL}/api/bridge/db`;

export type Row = Record<string, unknown>;
export type Param = string | number | boolean | null;

export class DbError extends Error {}

async function post(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  let resp: Response;
  try {
    resp = await fetch(DB_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new DbError(`db bridge unreachable at ${DB_URL}: ${err}`);
  }
  const text = await resp.text();
  if (!resp.ok) throw new DbError(`db bridge error: ${text.slice(0, 300)}`);
  return JSON.parse(text) as Record<string, unknown>;
}

/** Run a statement; returns affected row count. */
export async function execute(sql: string, ...params: Param[]): Promise<{ rowCount: number }> {
  const r = await post({ op: "execute", sql, params });
  return { rowCount: (r.rowcount as number) ?? 0 };
}

export async function fetchOne(sql: string, ...params: Param[]): Promise<Row | null> {
  const r = await post({ op: "fetchone", sql, params });
  return (r.row as Row | null) ?? null;
}

/** asyncpg-named alias — both call styles work. */
export const fetchRow = fetchOne;

export async function fetchAll(sql: string, ...params: Param[]): Promise<Row[]> {
  const r = await post({ op: "fetchall", sql, params });
  return (r.rows as Row[]) ?? [];
}

export async function fetchVal(sql: string, ...params: Param[]): Promise<unknown> {
  const r = await post({ op: "fetchval", sql, params });
  return r.value ?? null;
}

/**
 * Run several statements atomically (one transaction on the gateway).
 * This is the Node-side replacement for a transaction() block.
 */
export async function batch(statements: Array<{ sql: string; params?: Param[] }>): Promise<void> {
  await post({ op: "batch", statements });
}
