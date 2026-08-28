"""Feeds mini-app — RSS/Atom watching (agentboom package: rss-feeds).

Feeds are added over HTTP; a manifest job polls every 30 minutes; new
items land in feed_items and publish the `feeds.new_items` event, so
other mini-apps can react (summarize, notify, file...) by subscribing.

Endpoints (mounted at /api/feeds/):
  GET    /health
  GET    /feeds                      list configured feeds + last status
  POST   /feeds     {url}            add + fetch once immediately
  DELETE /feeds/{feed_id}
  GET    /items?feed_id=&since=&limit=   newest first
  POST   /poll                       poll all enabled feeds now
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db, events
from connectors.rss import RssError, fetch_feed

log = logging.getLogger("miniapps.feeds")

router = APIRouter()


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM feeds WHERE enabled = 1")
    return {"status": "ok", "app": "feeds", "enabled_feeds": count}


@router.get("/feeds")
async def list_feeds():
    rows = await db.fetchall(
        "SELECT id, url, title, enabled, last_fetched_at, last_error, created_at "
        "FROM feeds ORDER BY id"
    )
    return {"feeds": rows}


@router.post("/feeds")
async def add_feed(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "url must be http(s)"}, status_code=400)
    existing = await db.fetchone("SELECT id FROM feeds WHERE url = ?", url)
    if existing:
        return JSONResponse({"error": "feed already exists",
                             "id": existing["id"]}, status_code=409)
    # Fetch once up front: rejects dead/malformed URLs at add-time, and
    # seeds the title. First-seen items are stored silently (no event —
    # everything is "new" on an empty table).
    try:
        feed = await fetch_feed(url)
    except RssError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await db.execute(
        "INSERT INTO feeds (url, title, last_fetched_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (url, feed["title"]),
    )
    row = await db.fetchone("SELECT id FROM feeds WHERE url = ?", url)
    feed_id = row["id"] if row else None
    for item in feed["items"]:
        await db.execute(
            "INSERT INTO feed_items "
            "(feed_id, guid, title, link, summary, published) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (feed_id, item["guid"], item["title"], item["link"],
             item["summary"], item["published"]),
        )
    return {"ok": True, "id": feed_id, "title": feed["title"],
            "seeded_items": len(feed["items"])}


@router.delete("/feeds/{feed_id}")
async def remove_feed(feed_id: int):
    removed = await db.execute("DELETE FROM feeds WHERE id = ?", feed_id)
    if not removed:
        return JSONResponse({"error": "no such feed"}, status_code=404)
    return {"deleted": True}


@router.get("/items")
async def list_items(feed_id: int = 0, since: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 200))
    where, params = ["1=1"], []
    if feed_id:
        where.append("i.feed_id = ?")
        params.append(feed_id)
    if since:
        where.append("i.seen_at > ?")
        params.append(since)
    rows = await db.fetchall(
        f"""
        SELECT i.id, i.feed_id, f.title AS feed_title, i.title, i.link,
               i.summary, i.published, i.seen_at
        FROM feed_items i JOIN feeds f ON f.id = i.feed_id
        WHERE {' AND '.join(where)}
        ORDER BY i.seen_at DESC, i.id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    return {"items": rows}


@router.post("/poll")
async def poll_all():
    """Manifest job target: fetch every enabled feed, store new items."""
    feeds = await db.fetchall("SELECT id, url FROM feeds WHERE enabled = 1")
    total_new = 0
    errors = []
    for feed in feeds:
        try:
            parsed = await fetch_feed(feed["url"])
        except RssError as exc:
            errors.append({"feed_id": feed["id"], "error": str(exc)[:200]})
            await db.execute(
                "UPDATE feeds SET last_fetched_at = CURRENT_TIMESTAMP, "
                "last_error = ? WHERE id = ?",
                (str(exc)[:200], feed["id"]),
            )
            continue
        new_here = 0
        for item in parsed["items"]:
            inserted = await db.execute(
                "INSERT INTO feed_items "
                "(feed_id, guid, title, link, summary, published) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (feed["id"], item["guid"], item["title"], item["link"],
                 item["summary"], item["published"]),
            )
            new_here += 1 if inserted else 0
        await db.execute(
            "UPDATE feeds SET last_fetched_at = CURRENT_TIMESTAMP, "
            "last_error = NULL, title = COALESCE(NULLIF(?, ''), title) "
            "WHERE id = ?",
            (parsed["title"], feed["id"]),
        )
        if new_here:
            total_new += new_here
            await events.publish("feeds.new_items", {
                "feed_id": feed["id"], "feed_url": feed["url"],
                "new_items": new_here,
            })
    log.info("feeds poll: %d feed(s), %d new item(s), %d error(s)",
             len(feeds), total_new, len(errors))
    return {"ok": True, "feeds": len(feeds), "new_items": total_new,
            "errors": errors}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
