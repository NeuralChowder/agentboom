/**
 * One-shot LLM completions — a BRIDGE to the gateway's single LLM layer.
 *
 * Model endpoint, key, and tags are configured ONCE in the platform's
 * .env (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL). This client forwards to
 * POST /api/llm/complete, so Node mini-apps share the exact same LLM
 * wiring as everything else — and JSON extraction happens in the gateway,
 * once, not per-language.
 */
import { env } from "./config.js";

export const PLATFORM_INTERNAL_URL = env(
  "PLATFORM_INTERNAL_URL",
  "http://127.0.0.1:8000",
);
const COMPLETE_URL = `${PLATFORM_INTERNAL_URL}/api/llm/complete`;

export class LLMError extends Error {}

export interface CompleteOptions {
  system?: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
  timeoutSec?: number;
}

async function post(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  let resp: Response;
  try {
    resp = await fetch(COMPLETE_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new LLMError(`llm bridge unreachable at ${COMPLETE_URL}: ${err}`);
  }
  const text = await resp.text();
  if (!resp.ok) throw new LLMError(`llm bridge error: ${text.slice(0, 300)}`);
  return JSON.parse(text) as Record<string, unknown>;
}

/** One completion. Throws LLMError when the gateway/LLM fails. */
export async function complete(prompt: string, opts: CompleteOptions = {}): Promise<string> {
  const r = await post({
    prompt,
    system: opts.system,
    model: opts.model,
    temperature: opts.temperature,
    max_tokens: opts.maxTokens,
    timeout: opts.timeoutSec,
  });
  return (r.text as string) ?? "";
}

/** Ask for JSON; parsed in the gateway. Null when unparseable. */
export async function completeJson(
  prompt: string,
  opts: CompleteOptions = {},
): Promise<Record<string, unknown> | null> {
  const r = await post({
    prompt,
    json: true,
    system: opts.system,
    model: opts.model,
    temperature: opts.temperature,
    max_tokens: opts.maxTokens,
    timeout: opts.timeoutSec,
  });
  return r.ok ? ((r.json as Record<string, unknown>) ?? null) : null;
}
