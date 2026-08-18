/**
 * Agent turns via the `qwen serve` HTTP API — mirror of agentboom_sdk.agent.
 *
 *   import { ask, askJson } from "@agentboom/sdk";
 *   const answer = await ask("Summarise the last 10 alerts");
 *
 * Env: QWEN_AGENT_URL (default http://127.0.0.1:4170), QWEN_SERVER_TOKEN.
 */
import { env, envFloat } from "./config.js";
import { extractJson } from "./llm.js";

export const QWEN_AGENT_URL = env("QWEN_AGENT_URL", "http://127.0.0.1:4170");
const TOKEN = env("QWEN_SERVER_TOKEN");
const SESSION_LABEL = env("AGENT_SESSION_LABEL", "agentboom-sdk-ts");
const MAX_OPEN_SESSIONS = 1;
const DEFAULT_TIMEOUT_SEC = envFloat("AGENT_TIMEOUT_SEC", 120);

function headers(extra: Record<string, string> = {}): Record<string, string> {
  return {
    "content-type": "application/json",
    ...(TOKEN ? { authorization: `Bearer ${TOKEN}` } : {}),
    ...extra,
  };
}

// conversation name -> session id (LRU-ish, capped at MAX_OPEN_SESSIONS)
const sessions = new Map<string, string>();

async function createSession(): Promise<string | null> {
  try {
    const resp = await fetch(`${QWEN_AGENT_URL}/session`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ label: SESSION_LABEL }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as { sessionId?: string; id?: string };
    return data.sessionId ?? data.id ?? null;
  } catch {
    return null;
  }
}

async function closeSession(sessionId: string): Promise<void> {
  try {
    await fetch(`${QWEN_AGENT_URL}/session/${sessionId}`, {
      method: "DELETE",
      headers: headers(),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    // closing is best-effort
  }
}

async function getSession(conversation: string): Promise<string | null> {
  const existing = sessions.get(conversation);
  if (existing) return existing;
  const sessionId = await createSession();
  if (!sessionId) return null;
  if (sessions.size >= MAX_OPEN_SESSIONS) {
    const oldest = sessions.keys().next().value as string | undefined;
    if (oldest) {
      const oldId = sessions.get(oldest);
      sessions.delete(oldest);
      if (oldId) void closeSession(oldId);
    }
  }
  sessions.set(conversation, sessionId);
  return sessionId;
}

/** Read the serve daemon's SSE stream until the prompt's turn completes. */
async function collectAnswer(
  sessionId: string,
  promptId: string,
  lastEventId: number,
  timeoutSec: number,
): Promise<string | null> {
  const chunks: string[] = [];
  try {
    const resp = await fetch(`${QWEN_AGENT_URL}/session/${sessionId}/events`, {
      headers: headers({
        accept: "text/event-stream",
        ...(lastEventId ? { "last-event-id": String(lastEventId) } : {}),
      }),
      signal: AbortSignal.timeout(timeoutSec * 1000),
    });
    if (!resp.ok || !resp.body) return null;
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName = "";
    let done = false;
    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trimEnd();
        buffer = buffer.slice(nl + 1);
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
          continue;
        }
        if (!line.startsWith("data:")) continue;
        let obj: Record<string, unknown>;
        try {
          obj = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        const etype = (obj.type as string) || eventName;
        const data = (obj.data ?? {}) as Record<string, unknown>;
        if (etype === "session_update") {
          const update = (data.update ?? data) as Record<string, unknown>;
          if (update.sessionUpdate === "agent_message_chunk") {
            const content = (update.content ?? {}) as Record<string, unknown>;
            if (content.type === "text" && typeof content.text === "string") {
              chunks.push(content.text);
            }
          }
        } else if (etype === "turn_complete") {
          if (!promptId || (data.promptId ?? promptId) === promptId) done = true;
        } else if (etype === "session_died" || etype === "client_evicted") {
          done = true;
        }
      }
    }
    await reader.cancel().catch(() => undefined);
  } catch {
    // stream error — return whatever was collected
  }
  const answer = chunks.join("").trim();
  return answer || null;
}

/** Run one agent turn. Null on failure (callers carry on). */
export async function ask(
  prompt: string,
  opts: { conversation?: string; timeoutSec?: number } = {},
): Promise<string | null> {
  const timeoutSec = opts.timeoutSec ?? DEFAULT_TIMEOUT_SEC;
  const conversation = opts.conversation ?? "default";
  const sessionId = await getSession(conversation);
  if (!sessionId) return null;
  let resp: Response;
  try {
    resp = await fetch(`${QWEN_AGENT_URL}/session/${sessionId}/prompt`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ prompt: [{ type: "text", text: prompt }] }),
      signal: AbortSignal.timeout(timeoutSec * 1000),
    });
  } catch {
    return null;
  }
  if (resp.ok) {
    const data = (await resp.json()) as { response?: string };
    if (data.response) return data.response;
  }
  if (resp.status !== 202) return null;
  const data = (await resp.json()) as { promptId?: string; lastEventId?: number };
  return collectAnswer(sessionId, data.promptId ?? "", data.lastEventId ?? 0, timeoutSec);
}

/** Ask and parse a JSON object answer (one retry with a reminder). */
export async function askJson(
  prompt: string,
  opts: { conversation?: string; timeoutSec?: number; retries?: number } = {},
): Promise<Record<string, unknown> | null> {
  const retries = opts.retries ?? 1;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const answer = await ask(
      attempt === 0 ? prompt : `${prompt}\n\nReply with JSON only.`,
      opts,
    );
    if (answer) {
      const parsed = extractJson(answer);
      if (parsed) return parsed;
    }
    if (attempt < retries) {
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return null;
}

export async function closeAll(): Promise<void> {
  for (const sessionId of sessions.values()) {
    await closeSession(sessionId);
  }
  sessions.clear();
}
