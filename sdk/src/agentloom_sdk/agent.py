"""Ask the Qwen Code agent (`qwen serve` HTTP API) for judgement tasks.

Use for multi-step work needing tools/judgement. For simple one-shot
completions (classify/extract/draft), use sdk.llm instead.

Protocol (Qwen Code daemon):
  POST   /session                     -> {"sessionId": ...}
  POST   /session/{id}/prompt         -> 200 {response} or 202 {promptId, lastEventId}
  GET    /session/{id}/events  (SSE)  -> stream until `turn_complete`
  DELETE /session/{id}
Auth: Bearer token shared via QWEN_SERVER_TOKEN on both sides.

All asks are serialized through sdk.task_queue so LLM-bound work never
bursts the model gateway in parallel.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from agentloom_sdk.task_queue import TaskPriority, queue as task_queue

log = logging.getLogger("agentloom_sdk.agent")

QWEN_AGENT_URL = os.environ.get("QWEN_AGENT_URL", "http://127.0.0.1:4170")
QWEN_SERVER_TOKEN = os.environ.get("QWEN_SERVER_TOKEN", "")
SESSION_LABEL = os.environ.get("AGENT_SESSION_LABEL", "platform")
MAX_OPEN_SESSIONS = 1

_conversations: Dict[str, dict] = {}
_conversation_lock = asyncio.Lock()


async def start():
    """Start the internal task queue. Call once at app startup."""
    await task_queue.start()


async def stop():
    """Stop the internal task queue. Call at shutdown."""
    await close_all()
    await task_queue.stop()


def stats() -> Dict[str, Any]:
    return task_queue.stats()


async def ask(
    prompt: str,
    *,
    conversation: Optional[str] = None,
    timeout: float = 120,
    priority: str = "normal",
) -> Optional[str]:
    """Ask the agent a question (queued to prevent LLM bursting).

    Returns the assistant's final text, or None on failure.
    """
    pri_map = {
        "critical": TaskPriority.CRITICAL,
        "high": TaskPriority.HIGH,
        "normal": TaskPriority.NORMAL,
        "low": TaskPriority.LOW,
    }
    prio = pri_map.get(priority.lower(), TaskPriority.NORMAL)
    task_id = f"ask-{conversation or 'adhoc'}-{uuid.uuid4().hex[:8]}"

    async def _do_ask() -> Optional[str]:
        if conversation:
            session_id = await _get_session(conversation)
        else:
            session_id = await _create_session()

        if not session_id:
            log.error("No available agent session")
            return None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{QWEN_AGENT_URL}/session/{session_id}/prompt",
                    headers=_headers(),
                    json={"prompt": [{"type": "text", "text": prompt}]},
                )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("response"):
                    return data["response"]
            if resp.status_code != 202:
                log.error("Agent prompt failed: %d %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            return await _collect_answer(
                session_id, data.get("promptId", ""), data.get("lastEventId", 0), timeout
            )
        except Exception as e:
            log.error("Agent call failed: %s", e)
            return None

    try:
        return await task_queue.run_with_queue(
            _do_ask(), task_id, priority=prio, timeout=timeout + 10
        )
    except RuntimeError as e:
        # Queue full — still attempt directly as fallback rather than dropping.
        log.warning("Queue rejected '%s', attempting direct: %s", task_id, e)
        return await _do_ask()
    except Exception as e:
        log.error("Queued ask failed for '%s': %s", task_id, e)
        return None


async def _collect_answer(
    session_id: str, prompt_id: str, last_event_id: int, timeout: float
) -> Optional[str]:
    """Read the serve daemon's SSE stream until the prompt's turn completes.

    Replays from last_event_id so frames emitted between enqueue and stream
    open are not lost. Heartbeats keep the stream alive until the agent
    answers.
    """
    chunks: List[str] = []
    headers = _headers()
    headers["Accept"] = "text/event-stream"
    if last_event_id:
        headers["Last-Event-ID"] = str(last_event_id)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10, read=timeout)
        ) as client:
            async with client.stream(
                "GET",
                f"{QWEN_AGENT_URL}/session/{session_id}/events",
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    log.error("Events stream failed: %d", resp.status_code)
                    return None
                event_name = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except (ValueError, TypeError):
                        continue
                    etype = obj.get("type", event_name)
                    data = obj.get("data", {}) or {}
                    if etype == "session_update":
                        update = data.get("update", data)
                        if update.get("sessionUpdate") == "agent_message_chunk":
                            content = update.get("content") or {}
                            if content.get("type") == "text" and content.get("text"):
                                chunks.append(content["text"])
                    elif etype == "turn_complete":
                        if not prompt_id or data.get("promptId", prompt_id) == prompt_id:
                            break
                    elif etype in ("session_died", "client_evicted"):
                        log.error("Session terminated while waiting: %s", etype)
                        break
    except Exception as e:
        log.error("Event stream error: %s", e)
    answer = "".join(chunks).strip()
    return answer or None


async def ask_json(
    prompt: str,
    *,
    conversation: Optional[str] = None,
    timeout: float = 120,
    retries: int = 1,
) -> Optional[dict]:
    """Ask and parse a JSON object answer."""
    from agentloom_sdk.llm import extract_json

    _JSON_REMINDER = "\n\nReply with JSON only."
    for attempt in range(retries + 1):
        answer = await ask(
            prompt if attempt == 0 else prompt + _JSON_REMINDER,
            conversation=conversation, timeout=timeout,
        )
        if answer:
            parsed = extract_json(answer)
            if parsed is not None:
                return parsed
        if attempt < retries:
            await asyncio.sleep(2)
    return None


async def close_all() -> None:
    async with _conversation_lock:
        entries = list(_conversations.values())
        _conversations.clear()
    for entry in entries:
        await _close_session(entry["session_id"])


async def _get_session(name: str) -> Optional[str]:
    # Hold the lock across the entire get-or-create to prevent duplicates.
    async with _conversation_lock:
        entry = _conversations.get(name)
        if entry:
            entry["last_used"] = time.time()
            return entry["session_id"]

        session_id = await _create_session()
        if session_id:
            if len(_conversations) >= MAX_OPEN_SESSIONS:
                oldest = min(_conversations.items(), key=lambda e: e[1]["last_used"])
                await _close_session(oldest[1]["session_id"])
                _conversations.pop(oldest[0])
            _conversations[name] = {"session_id": session_id, "last_used": time.time()}
    return session_id


async def _create_session() -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{QWEN_AGENT_URL}/session",
                headers=_headers(),
                json={"label": SESSION_LABEL},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("sessionId") or data.get("id")
    except Exception as e:
        log.error("Failed to create session: %s", e)
    return None


async def _close_session(session_id: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                f"{QWEN_AGENT_URL}/session/{session_id}",
                headers=_headers(),
            )
    except Exception:
        pass


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if QWEN_SERVER_TOKEN:
        h["Authorization"] = f"Bearer {QWEN_SERVER_TOKEN}"
    return h
