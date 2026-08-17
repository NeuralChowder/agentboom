"""One-shot LLM completions (OpenAI-compatible gateway).

Two different things get confused here, so be explicit about which you want:

  * **A completion** — classify this, draft that, extract these fields. No
    tools, no memory, no session. That is what this module does.
  * **Agent work** — something needing tools, files or judgement across
    steps. That is a scheduler job of type `agent`, not a function call.

    from agentboom_sdk.llm import complete, complete_json

    verdict = await complete_json(prompt)

Serialization
-------------
When the shared task queue has been started (`agentboom_sdk.task_queue`),
completions are serialized through it (bounded parallelism — the usual
production posture, keeps the model gateway from bursting). When the queue
has not been started, calls go straight through. Same code, both regimes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Dict, List, Optional

import httpx

from agentboom_sdk.task_queue import TaskPriority, queue as _task_queue

log = logging.getLogger("agentboom_sdk.llm")

BASE_URL = os.environ.get("LLM_BASE_URL", "")
API_KEY = os.environ.get("LLM_API_KEY", "")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")
# Gateways queue bursts; a single request may wait a while before the model
# answers, so timeouts must be generous.
DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SEC", "120"))


#: Appended when a first answer could not be parsed. Kept short and specific:
#: the model already knows the task, it got the format wrong.
_JSON_REMINDER = (
    "\n\nYour previous answer could not be parsed. Reply with the JSON object "
    "and nothing else — no preamble, no explanation, no code fence, no text "
    "after the closing brace."
)


class LLMError(RuntimeError):
    """The model could not be reached or returned nothing usable."""


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
        raise LLMError(
            "LLM_BASE_URL is not set. Add it to .env and pass it through in "
            "docker-compose.yml so mini-apps can reach the model gateway."
        )
    if not API_KEY:
        raise LLMError(
            "LLM_API_KEY is not set. Add it to .env and pass it through in "
            "docker-compose.yml so mini-apps can reach the model gateway."
        )

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
        raise LLMError(
            f"model gateway returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    try:
        payload = resp.json()
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"unexpected response shape: {resp.text[:300]}") from exc


async def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: Optional[float] = None,
) -> str:
    """One prompt in, text out. Serialized via the task queue when it runs."""
    budget = timeout or DEFAULT_TIMEOUT
    if _task_queue.running():
        try:
            return await _task_queue.run_with_queue(
                _complete_once(
                    prompt, system=system, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=budget,
                ),
                f"llm-{uuid.uuid4().hex[:8]}",
                priority=TaskPriority.NORMAL,
                timeout=budget + 10,
            )
        except RuntimeError as exc:
            # Queue full — run directly rather than dropping the work.
            log.warning("LLM queue rejected (%s), running direct", exc)
    return await _complete_once(
        prompt, system=system, model=model,
        temperature=temperature, max_tokens=max_tokens, timeout=budget,
    )


async def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout: Optional[float] = None,
    retries: int = 1,
) -> Optional[dict]:
    """Ask for JSON and return it parsed, or None if nothing parseable came back.

    Asks again when the first answer will not parse. A model that wraps its
    object in an apology, or stops a token short of the closing brace, is
    flaking rather than refusing — and the caller cannot tell those apart, so
    it would treat a flake as a verdict and drop the item. On a filing
    pipeline that means an invoice quietly not filed.

    Returns None rather than raising once the retries are spent: callers
    generally want to skip that item and carry on, not abort a batch.
    """
    for attempt in range(retries + 1):
        text = await complete(
            prompt if attempt == 0 else prompt + _JSON_REMINDER,
            system=system, model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )
        parsed = extract_json(text)
        if parsed is not None:
            return parsed
        log.warning(
            "Model answer was not parseable JSON (attempt %d/%d, %d chars): %r",
            attempt + 1, retries + 1, len(text or ""), (text or "")[:200])

    return None


def extract_json(text: str) -> Optional[dict]:
    """Find a JSON object in model output.

    Models wrap JSON in prose or code fences often enough that insisting on
    clean output would discard a large share of otherwise good responses.
    """
    if not text:
        return None

    candidates: List[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))

    # Every balanced top-level {...} span, longest first — the outermost
    # object is almost always the intended payload.
    depth, start = 0, None
    spans: List[str] = []
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append(text[start:index + 1])
    candidates.extend(sorted(spans, key=len, reverse=True))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None
