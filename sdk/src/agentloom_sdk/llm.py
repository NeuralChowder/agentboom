"""One-shot LLM completions (OpenAI-compatible gateway).

For bounded tasks: extract fields, categorize, summarize, draft.
NOT for multi-step tool-using work — use sdk.agent for that.

All traffic is serialized through the shared task queue (parallelism 1 by
default), so completions never burst the model gateway — they wait their
turn. Transient congestion is retried with backoff.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from typing import Dict, List, Optional

import httpx

from agentloom_sdk.task_queue import TaskPriority, queue as task_queue

log = logging.getLogger("agentloom_sdk.llm")

BASE_URL = os.environ.get("LLM_BASE_URL")
API_KEY = os.environ.get("LLM_API_KEY", "")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")
# The gateway queues bursts; a single request may wait a while before the
# model answers, so timeouts must be generous.
DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SEC", "180"))

_JSON_REMINDER = (
    "\n\nReply with the JSON object only — no preamble, no markdown fence."
)


class LLMError(RuntimeError):
    pass


async def _complete_once(
    prompt: str,
    *,
    system: Optional[str],
    model: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    if not BASE_URL:
        raise LLMError("LLM_BASE_URL not set — configure .env")
    if not API_KEY:
        raise LLMError("LLM_API_KEY not set")

    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": model or DEFAULT_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
    except httpx.HTTPError as exc:
        raise LLMError(f"model gateway unreachable: {exc}") from exc

    if resp.status_code >= 400:
        raise LLMError(f"model gateway HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        payload = resp.json()
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"unexpected response: {resp.text[:300]}") from exc


async def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: Optional[float] = None,
    retries: int = 2,
) -> str:
    """One completion, serialized through the shared task queue."""
    budget = timeout or DEFAULT_TIMEOUT
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return await task_queue.run_with_queue(
                _complete_once(
                    prompt, system=system, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=budget,
                ),
                f"llm-{uuid.uuid4().hex[:8]}",
                priority=TaskPriority.NORMAL,
                timeout=budget + 10,
            )
        except (LLMError, asyncio.TimeoutError, TimeoutError) as exc:
            last_error = exc
            if "not set" in str(exc):
                raise
            log.warning(
                "LLM attempt %d/%d failed (%s); waiting before retry",
                attempt + 1, retries + 1, exc,
            )
            if attempt < retries:
                await asyncio.sleep(5 * (attempt + 1))
        except RuntimeError as exc:
            # Queue full — run directly rather than dropping the work.
            log.warning("LLM queue rejected (%s), running direct", exc)
            return await _complete_once(
                prompt, system=system, model=model,
                temperature=temperature, max_tokens=max_tokens,
                timeout=budget,
            )
    raise last_error or LLMError("LLM completion failed")


async def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    retries: int = 1,
) -> Optional[dict]:
    """Complete and parse a JSON object answer."""
    for attempt in range(retries + 1):
        text = await complete(
            prompt if attempt == 0 else prompt + _JSON_REMINDER,
            system=system, model=model, temperature=temperature,
            max_tokens=max_tokens,
        )
        parsed = extract_json(text)
        if parsed is not None:
            return parsed
        log.warning(
            "Non-parseable JSON (attempt %d/%d): %r",
            attempt + 1, retries + 1, text[:200],
        )
    return None


def extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from a model answer."""
    if not text:
        return None
    candidates: List[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    depth, start = 0, None
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:i + 1])
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None
