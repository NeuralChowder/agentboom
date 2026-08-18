"""GitHub-watch mini-app (agentboom package: github-watch).

Watches configured repos for NEW open issues and releases. A manifest
job checks every 15 minutes; novelties land in github_events and publish
the `github.new_event` event for other mini-apps to act on.

First check after adding a repo seeds the current state silently — you
get events for things that happen from then on, not a dump of history.

Endpoints (mounted at /api/github-watch/):
  GET    /health
  GET    /repos                    watched repos + last status
  POST   /repos    {repo}          add 'org/name' (+ seed check)
  DELETE /repos/{repo_id}
  GET    /events?repo=&limit=      newest first
  POST   /check                    check all enabled repos now
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db, events
from connectors.github import GitHubError, authenticated, latest_releases, open_issues

log = logging.getLogger("miniapps.github-watch")

router = APIRouter()


@router.get("/health")
async def health():
    count = await db.fetchval(
        "SELECT count(*) FROM watched_repos WHERE enabled = 1")
    return {"status": "ok", "app": "github-watch",
            "authenticated": authenticated(), "repos": count}


@router.get("/repos")
async def list_repos():
    rows = await db.fetchall(
        "SELECT id, repo, enabled, last_checked_at, last_error, created_at "
        "FROM watched_repos ORDER BY id")
    return {"repos": rows}


@router.post("/repos")
async def add_repo(payload: dict):
    repo = (payload.get("repo") or "").strip().strip("/")
    if repo.count("/") != 1 or not all(repo.split("/")):
        return JSONResponse(
            {"error": "repo must look like 'org/name'"}, status_code=400)
    existing = await db.fetchone(
        "SELECT id FROM watched_repos WHERE repo = ?", repo)
    if existing:
        return JSONResponse({"error": "already watched",
                             "id": existing["id"]}, status_code=409)
    await db.execute("INSERT INTO watched_repos (repo) VALUES (?)", (repo,))
    seeded = await _check_repo(repo)  # silent seed; errors surface in /repos
    row = await db.fetchone("SELECT id FROM watched_repos WHERE repo = ?", repo)
    return {"ok": True, "id": row["id"] if row else None,
            "repo": repo, "seeded": seeded}


@router.delete("/repos/{repo_id}")
async def remove_repo(repo_id: int):
    removed = await db.execute(
        "DELETE FROM watched_repos WHERE id = ?", repo_id)
    if not removed:
        return JSONResponse({"error": "no such repo"}, status_code=404)
    return {"deleted": True}


@router.get("/events")
async def list_events(repo: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 200))
    where, params = "1=1", []
    if repo:
        where = "repo = ?"
        params = [repo]
    rows = await db.fetchall(
        f"SELECT id, repo, kind, ref, title, url, actor, github_created_at, seen_at "
        f"FROM github_events WHERE {where} "
        f"ORDER BY seen_at DESC, id DESC LIMIT ?",
        (*params, limit),
    )
    return {"events": rows}


@router.post("/check")
async def check_all():
    """Manifest job target: check every enabled repo."""
    repos = await db.fetchall(
        "SELECT repo FROM watched_repos WHERE enabled = 1")
    total_new, errors = 0, []
    for row in repos:
        try:
            total_new += await _check_repo(row["repo"], notify=True)
        except GitHubError as exc:
            errors.append({"repo": row["repo"], "error": str(exc)[:200]})
    log.info("github-watch check: %d repo(s), %d new, %d error(s)",
             len(repos), total_new, len(errors))
    return {"ok": True, "repos": len(repos), "new_events": total_new,
            "errors": errors}


async def _check_repo(repo: str, notify: bool = False) -> int:
    """Store unseen issues/releases; returns how many were new.

    With notify=False (first seed) nothing is published.
    """
    last = await db.fetchone(
        "SELECT last_checked_at FROM watched_repos WHERE repo = ?", repo)
    since = (last or {}).get("last_checked_at")
    new_count = 0
    try:
        issues = await open_issues(repo, since=since)
        releases = await latest_releases(repo, limit=10)
    except GitHubError as exc:
        await db.execute(
            "UPDATE watched_repos SET last_checked_at = CURRENT_TIMESTAMP, "
            "last_error = ? WHERE repo = ?",
            (str(exc)[:200], repo),
        )
        raise
    for issue in issues:
        inserted = await db.execute(
            "INSERT OR IGNORE INTO github_events "
            "(repo, kind, ref, title, url, actor, github_created_at) "
            "VALUES (?, 'issue', ?, ?, ?, ?, ?)",
            (repo, f"issue-{issue['number']}", issue["title"],
             issue["url"], issue["actor"], issue["created_at"]),
        )
        if inserted:
            new_count += 1
            if notify and since:  # skip the silent first seed
                await events.publish("github.new_event", {
                    "repo": repo, "kind": "issue",
                    "title": issue["title"], "url": issue["url"],
                    "actor": issue["actor"],
                })
    for rel in releases:
        inserted = await db.execute(
            "INSERT OR IGNORE INTO github_events "
            "(repo, kind, ref, title, url, github_created_at) "
            "VALUES (?, 'release', ?, ?, ?, ?)",
            (repo, f"release-{rel['tag']}", rel["name"], rel["url"],
             rel["published_at"]),
        )
        if inserted:
            new_count += 1
            if notify and since:
                await events.publish("github.new_event", {
                    "repo": repo, "kind": "release",
                    "title": rel["name"], "url": rel["url"],
                })
    await db.execute(
        "UPDATE watched_repos SET last_checked_at = CURRENT_TIMESTAMP, "
        "last_error = NULL WHERE repo = ?",
        (repo,),
    )
    return new_count


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
