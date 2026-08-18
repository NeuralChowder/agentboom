/**
 * Agent turns — a BRIDGE to the gateway's single agent layer.
 *
 * Session management and SSE collection live once in agentboom_sdk.agent
 * inside the gateway. This client forwards to POST /api/agent/ask and gets
 * back the final answer — Node never touches sessions or SSE.
 */
import { env } from "./config.js";

export const PLATFORM_INTERNAL_URL = env(
  "PLATFORM_INTERNAL_URL",
  "http://127.0.0.1:8000",
);
const ASK_URL = `${PLATFORM_INTERNAL_URL}/api/agent/ask`;

async function post(body: Record<string, unknown>): Promise<Record<string, unknown> | null> {
  let resp: Response;
  try {
    resp = await fetch(ASK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return null; // callers carry on, mirroring the Python SDK
  }
  if (!resp.ok) return null;
  return (await resp.json()) as Record<string, unknown>;
}

/** Run one agent turn. Null on failure. */
export async function ask(
  prompt: string,
  opts: { conversation?: string; timeoutSec?: number } = {},
): Promise<string | null> {
  const r = await post({
    prompt,
    conversation: opts.conversation,
    timeout: opts.timeoutSec,
  });
  return r && r.ok ? ((r.text as string) ?? null) : null;
}

/** Ask and get a parsed JSON object answer. Null on failure. */
export async function askJson(
  prompt: string,
  opts: { conversation?: string; timeoutSec?: number } = {},
): Promise<Record<string, unknown> | null> {
  const r = await post({
    prompt,
    json: true,
    conversation: opts.conversation,
    timeout: opts.timeoutSec,
  });
  return r && r.ok ? ((r.json as Record<string, unknown>) ?? null) : null;
}
