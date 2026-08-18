"""ntfy connector — push notifications (agentboom package: ntfy).

ntfy (https://ntfy.sh) is pub/sub over HTTP: publishing a message is one
POST, no accounts, no API keys on the public server. The TOPIC is the
channel — treat it like a password (anyone who knows it can read/post).

Mini-app usage:

    from connectors.ntfy import send, enabled

    if enabled():
        await send("Backup finished: 42 files", title="backups",
                   priority=3, tags=["floppy_disk"])

Skill/agent usage: a plain curl works too — see the `ntfy` skill.

Env:
  NTFY_TOPIC     default topic for send() (required)
  NTFY_BASE_URL  server base URL (default https://ntfy.sh)
  NTFY_TOKEN     optional bearer token (self-hosted/protected topics)
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import httpx

log = logging.getLogger("connectors.ntfy")

BASE_URL = os.environ.get("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
DEFAULT_TOPIC = os.environ.get("NTFY_TOPIC", "")
TOKEN = os.environ.get("NTFY_TOKEN", "")

_TIMEOUT = float(os.environ.get("NTFY_TIMEOUT_SEC", "15"))


def enabled() -> bool:
    """True when send() has a topic to talk about."""
    return bool(DEFAULT_TOPIC)


async def send(
    message: str,
    *,
    title: Optional[str] = None,
    topic: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[Iterable[str]] = None,
    click: Optional[str] = None,
    attach: Optional[str] = None,
    delay: Optional[str] = None,
) -> dict:
    """Publish one notification. Returns {'ok': True, 'topic': ...}.

    priority: 1 min … 5 max (4/5 may need a self-hosted server).
    tags: emoji short-codes shown beside the message ('warning', ...).
    click: URL opened when the notification is tapped.
    attach: URL of a file attached to the notification.
    delay: ntfy delay string ('30m', '9am', ...) for scheduled sends.
    Raises NtfyError on misconfiguration or transport failure.
    """
    target = topic or DEFAULT_TOPIC
    if not target:
        raise NtfyError(
            "NTFY_TOPIC is not set — add it to .env and pass it through "
            "in docker-compose.yml"
        )
    headers = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if title:
        headers["Title"] = title
    if priority is not None:
        if not 1 <= int(priority) <= 5:
            raise NtfyError(f"priority must be 1-5, got {priority}")
        headers["Priority"] = str(int(priority))
    if tags:
        headers["Tags"] = ",".join(tags)
    if click:
        headers["Click"] = click
    if attach:
        headers["Attach"] = attach
    if delay:
        headers["At"] = delay

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{BASE_URL}/{target}",
                content=message.encode("utf-8"),
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise NtfyError(f"ntfy unreachable at {BASE_URL}: {exc}") from exc
    if resp.status_code >= 400:
        raise NtfyError(
            f"ntfy returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    log.info("ntfy: published to %s (%d chars)", target, len(message))
    return {"ok": True, "topic": target}


class NtfyError(RuntimeError):
    """The notification could not be published."""
