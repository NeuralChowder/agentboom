/**
 * Capability calls between mini-apps — mirror of agentboom_sdk.capabilities.
 *
 * Mini-apps expose capabilities via their manifest `provides` and consume
 * them by name; the gateway keeps one registry (GET /api/capabilities).
 * Callers never hard-code another app's URL.
 *
 *   import { call } from "@agentboom/sdk";
 *   const result = await call("contacts.lookup", { text: "Maria" });
 */
import { env, envFloat } from "./config.js";

export const PLATFORM_INTERNAL_URL = env(
  "PLATFORM_INTERNAL_URL",
  "http://127.0.0.1:8000",
);
const CACHE_TTL_SEC = envFloat("CAPABILITIES_CACHE_SEC", 60);
const TIMEOUT_MS = envFloat("CAPABILITY_TIMEOUT_SEC", 30) * 1000;

export interface CapabilityRecord {
  app: string;
  method: string;
  path: string;
  description: string;
}

export class CapabilityError extends Error {}

let cache: { at: number; capabilities: Record<string, CapabilityRecord> } = {
  at: 0,
  capabilities: {},
};

export async function registry(refresh = false): Promise<Record<string, CapabilityRecord>> {
  const now = Date.now() / 1000;
  if (!refresh && Object.keys(cache.capabilities).length > 0 && now - cache.at < CACHE_TTL_SEC) {
    return cache.capabilities;
  }
  let resp: Response;
  try {
    resp = await fetch(`${PLATFORM_INTERNAL_URL}/api/capabilities`, {
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err) {
    if (Object.keys(cache.capabilities).length > 0) return cache.capabilities; // stale beats dead
    throw new CapabilityError(
      `capability registry unreachable at ${PLATFORM_INTERNAL_URL}: ${err}`,
    );
  }
  if (!resp.ok) {
    throw new CapabilityError(`capability registry returned HTTP ${resp.status}`);
  }
  const body = (await resp.json()) as { capabilities?: Record<string, CapabilityRecord> };
  cache = { at: now, capabilities: body.capabilities ?? {} };
  return cache.capabilities;
}

export async function resolve(name: string, refresh = false): Promise<CapabilityRecord> {
  const caps = await registry(refresh);
  const record = caps[name];
  if (!record) {
    const available = Object.keys(caps).sort().join(", ") || "(none loaded)";
    throw new CapabilityError(
      `capability '${name}' is not provided by any loaded mini-app. ` +
        `Install the package that provides it (agentboom add package ...). ` +
        `Loaded capabilities: ${available}`,
    );
  }
  return record;
}

/** Call a capability and return its decoded response. */
export async function call(
  name: string,
  payload?: Record<string, unknown>,
  opts: { timeoutMs?: number; refresh?: boolean } = {},
): Promise<unknown> {
  const record = await resolve(name, opts.refresh);
  const url = `${PLATFORM_INTERNAL_URL}/api/${record.app}${record.path}`;
  const method = (record.method || "POST").toUpperCase();
  let resp: Response;
  try {
    if (method === "GET") {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(payload ?? {})) params.set(k, String(v));
      resp = await fetch(`${url}?${params}`, {
        signal: AbortSignal.timeout(opts.timeoutMs ?? TIMEOUT_MS),
      });
    } else {
      resp = await fetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload ?? {}),
        signal: AbortSignal.timeout(opts.timeoutMs ?? TIMEOUT_MS),
      });
    }
  } catch (err) {
    throw new CapabilityError(`capability '${name}' (${record.app}) unreachable: ${err}`);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new CapabilityError(
      `capability '${name}' (${record.app}) failed: HTTP ${resp.status}: ${text.slice(0, 200)}`,
    );
  }
  const text = await resp.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function invalidateCache(): void {
  cache = { at: 0, capabilities: {} };
}
