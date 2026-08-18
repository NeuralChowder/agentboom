/**
 * One-shot LLM completions — mirror of agentboom_sdk.llm.
 *
 * Any OpenAI-compatible endpoint: your own local server with your own
 * model tags (llama.cpp/vLLM/Ollama/LiteLLM) or a hosted API. Config via
 * env, same names as the Python SDK: LLM_BASE_URL / LLM_API_KEY /
 * LLM_MODEL / LLM_TIMEOUT_SEC.
 */
import { env, envFloat } from "./config.js";

export const BASE_URL = env("LLM_BASE_URL").replace(/\/$/, "");
export const API_KEY = env("LLM_API_KEY");
export const DEFAULT_MODEL = env("LLM_MODEL", "qwen-plus");
export const DEFAULT_TIMEOUT_SEC = envFloat("LLM_TIMEOUT_SEC", 120);

export class LLMError extends Error {}

export interface CompleteOptions {
  system?: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
  timeoutSec?: number;
}

/** One completion. Raises LLMError on transport/API failure. */
export async function complete(prompt: string, opts: CompleteOptions = {}): Promise<string> {
  if (!BASE_URL) {
    throw new LLMError("LLM_BASE_URL is not set — see .env.example for setup");
  }
  const messages: Array<{ role: string; content: string }> = [];
  if (opts.system) messages.push({ role: "system", content: opts.system });
  messages.push({ role: "user", content: prompt });

  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}/chat/completions`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(API_KEY ? { authorization: `Bearer ${API_KEY}` } : {}),
      },
      body: JSON.stringify({
        model: opts.model || DEFAULT_MODEL,
        messages,
        temperature: opts.temperature ?? 0.2,
        max_tokens: opts.maxTokens ?? 2048,
      }),
      signal: AbortSignal.timeout((opts.timeoutSec ?? DEFAULT_TIMEOUT_SEC) * 1000),
    });
  } catch (err) {
    throw new LLMError(`LLM unreachable at ${BASE_URL}: ${err}`);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new LLMError(`LLM HTTP ${resp.status}: ${text.slice(0, 300)}`);
  }
  const data = (await resp.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new LLMError("LLM returned no content");
  return content;
}

/**
 * Find a JSON object in model output (models wrap JSON in prose or code
 * fences). String/escape-aware brace scanning — braces inside JSON string
 * values do not close the span early.
 */
export function extractJson(text: string): Record<string, unknown> | null {
  if (!text) return null;
  const candidates: string[] = [];

  const fenced = text.match(/```(?:json)?\s*(\{.*?\})\s*```/s);
  if (fenced) candidates.push(fenced[1]);

  let depth = 0;
  let start = -1;
  let inStr = false;
  let escape = false;
  const spans: string[] = [];
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inStr) {
      if (escape) escape = false;
      else if (ch === "\\") escape = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === "{") {
      if (depth === 0) start = i;
      depth += 1;
    } else if (ch === "}" && depth > 0) {
      depth -= 1;
      if (depth === 0 && start >= 0) spans.push(text.slice(start, i + 1));
    }
  }
  candidates.push(...spans.sort((a, b) => b.length - a.length));

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // keep trying the next candidate
    }
  }
  return null;
}

/** Ask for JSON; one retry with a reminder. Null when unparseable. */
export async function completeJson(
  prompt: string,
  opts: CompleteOptions = {},
): Promise<Record<string, unknown> | null> {
  const reminder =
    "\n\nReminder: reply with a single JSON object, no prose, no code fences.";
  for (const attempt of [0, 1]) {
    const text = await complete(attempt === 0 ? prompt : prompt + reminder, opts);
    const parsed = extractJson(text);
    if (parsed) return parsed;
  }
  return null;
}
