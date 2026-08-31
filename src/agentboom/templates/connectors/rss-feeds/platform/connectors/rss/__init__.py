"""RSS/Atom connector (agentboom package: rss-feeds).

Fetch + parse a feed into plain dicts; tolerant of the usual real-world
sloppiness (wrong content-types, missing guids).

Mini-app usage:

    from connectors.rss import fetch_feed

    feed = await fetch_feed("https://example.com/feed.xml")
    for item in feed["items"]:
        ...  # guid, title, link, summary, published

Env: none. (Optional RSS_TIMEOUT_SEC, RSS_MAX_ITEMS.)
"""
from __future__ import annotations

import logging
import os
from typing import List

import feedparser
import httpx

log = logging.getLogger("connectors.rss")

_TIMEOUT = float(os.environ.get("RSS_TIMEOUT_SEC", "30"))
_MAX_ITEMS = int(os.environ.get("RSS_MAX_ITEMS", "50"))
_USER_AGENT = "agentboom-feeds/0.1 (+https://github.com/agent-boom/agentboom)"


class RssError(RuntimeError):
    """The feed could not be fetched or parsed."""


def _guid(entry) -> str:
    """Stable identity for dedupe: id > link > title (first one present)."""
    return (getattr(entry, "id", "") or getattr(entry, "link", "")
            or getattr(entry, "title", "") or "").strip()


async def fetch_feed(url: str) -> dict:
    """Fetch and parse one feed.

    Returns {'url', 'title', 'items': [{guid, title, link, summary,
    published}]} — newest entries first, capped at RSS_MAX_ITEMS.
    Raises RssError on transport failures or unparseable content.
    """
    try:
        async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except httpx.HTTPError as exc:
        raise RssError(f"fetch failed for {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise RssError(f"HTTP {resp.status_code} for {url}")

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        cause = getattr(parsed, "bozo_exception", "unknown parse error")
        raise RssError(f"unparseable feed at {url}: {str(cause)[:120]}")

    items: List[dict] = []
    for entry in parsed.entries[:_MAX_ITEMS]:
        summary = (getattr(entry, "summary", "") or "").strip()
        items.append({
            "guid": _guid(entry),
            "title": (getattr(entry, "title", "") or "").strip() or "(untitled)",
            "link": (getattr(entry, "link", "") or "").strip(),
            "summary": summary[:500],
            "published": (getattr(entry, "published", "")
                          or getattr(entry, "updated", "") or "").strip(),
        })
    # Drop entries with no identity at all — they cannot be deduped.
    items = [it for it in items if it["guid"]]
    title = (parsed.feed.get("title") or "").strip() if parsed.feed else ""
    return {"url": url, "title": title, "items": items}
