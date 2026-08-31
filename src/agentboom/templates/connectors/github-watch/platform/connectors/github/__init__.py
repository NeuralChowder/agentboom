"""GitHub connector — REST v3 (agentboom package: github-watch).

Small async client for the read endpoints agents actually need. Works
unauthenticated for public repos (60 req/h) — set GITHUB_TOKEN for real
use.

Mini-app usage:

    from connectors.github import open_issues, latest_releases

    issues = await open_issues("agent-boom/agentboom-sdk", since="2026-08-01T00:00:00Z")
    releases = await latest_releases("agent-boom/agentboom-sdk")

Env:
  GITHUB_TOKEN  optional PAT / fine-grained token (read scopes suffice)
  GITHUB_API    API base (default https://api.github.com; GitHub
                Enterprise: https://HOSTNAME/api/v3)
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import httpx

log = logging.getLogger("connectors.github")

API_BASE = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
_TIMEOUT = float(os.environ.get("GITHUB_TIMEOUT_SEC", "20"))


class GitHubError(RuntimeError):
    """The GitHub API call failed."""


def authenticated() -> bool:
    return bool(TOKEN)


def _headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "agentboom-github-watch",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


async def _get(path: str, **params) -> object:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{API_BASE}{path}",
                                    headers=_headers(), params=params)
    except httpx.HTTPError as exc:
        raise GitHubError(f"github api unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise GitHubError(
            f"github api HTTP {resp.status_code} for {path}: {resp.text[:200]}"
        )
    return resp.json()


async def open_issues(repo: str, since: Optional[str] = None) -> List[dict]:
    """Open issues (+ PRs filtered out) of 'org/name', newest first.

    `since` is an ISO-8601 timestamp: only issues CREATED after it.
    """
    params = {"state": "open", "sort": "created", "direction": "desc",
              "per_page": 50}
    if since:
        params["since"] = since
    items = await _get(f"/repos/{repo}/issues", **params)
    return [
        {
            "number": it["number"],
            "title": it.get("title"),
            "url": it.get("html_url"),
            "actor": (it.get("user") or {}).get("login"),
            "created_at": it.get("created_at"),
            "is_pr": "pull_request" in it,
        }
        for it in items
        if "pull_request" not in it  # the issues endpoint includes PRs
    ]


async def latest_releases(repo: str, limit: int = 10) -> List[dict]:
    items = await _get(f"/repos/{repo}/releases", per_page=min(limit, 30))
    return [
        {
            "tag": rel.get("tag_name"),
            "name": rel.get("name") or rel.get("tag_name"),
            "url": rel.get("html_url"),
            "published_at": rel.get("published_at"),
        }
        for rel in items[:limit]
    ]


async def repo_info(repo: str) -> dict:
    data = await _get(f"/repos/{repo}")
    return {
        "repo": data.get("full_name"),
        "description": data.get("description"),
        "stars": data.get("stargazers_count"),
        "open_issues": data.get("open_issues_count"),
        "pushed_at": data.get("pushed_at"),
    }
