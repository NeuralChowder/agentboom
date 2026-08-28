"""Movie Night — weekly movie/series sessions, researched from what is
actually available on the user's configured streaming platforms.

Once a week the app web-researches what is CURRENTLY available on the
configured platforms and picks 3-5 titles around one coherent theme.
That is the producer half: POST /generate returns the session as
structured content; the `digestos` hub owns the schedule and delivery.

The learning half stays here and has its own screen (the dashboard's
Movie Night tab): the user's reactions — tapped on a title, filed via
POST /taste, or told in chat and recorded through this API — land on
the title rows or in `taste_notes`, and both are embedded in the next
week's research prompt, so each session is sharper than the last.
The catalog is permanent: one row per distinct title, ever.

Exports get_router(); the gateway mounts it at /api/movienight/.

Two things are worth knowing before editing this file.

**Everything in a session came from the open web.** The agent searched,
and a page it read wrote the titles, synopses and poster URLs.
Poster URLs must be https (`_poster_url`) — a broken poster is a
first-class case; the renderer falls back to a styled initial-letter
block, so a dead URL never breaks a card.

**`ask_json()` is in-process, not a queue row.** A research turn is an
`agentboom_sdk.agent.ask_json()` call that runs directly in the agent
process. Firing /generate again while one is in flight therefore adds
no value — it would only retry the same work or produce a duplicate.
`_generation_lock` is what stops that.
"""
import asyncio
import json
import logging
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentboom_sdk import db
from agentboom_sdk.agent import ask_json

log = logging.getLogger("movienight")

# How long a research turn may stay SILENT before it is abandoned.
RESEARCH_IDLE_TIMEOUT = 600

# Module-level in-flight guard. A gateway restart clears the guard (and
# the in-flight state with it).
_generation_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    """ISO-8601 UTC string, microseconds stripped (portable)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fold(text: Optional[str]) -> str:
    """Case- and accent-folded title for dedup/match.
    Pure Python — no Postgres unaccent() or ILIKE.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


def _poster_url(value: object) -> str:
    """A direct https image URL, or '' — a poster that is not https
    is no poster. `javascript:` and `data:` are URLs too, and so is
    `http:`; any of them in a shared page is a stranger's script or a
    leaked address, and a broken poster has a fallback to stand in for
    it."""
    text = ("" if value is None else str(value)).strip()
    if not text or text.upper() == "N/A":
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return text


def _coerce_bool(
    value: Union[bool, str, int, None], default: Optional[bool] = None
) -> Optional[bool]:
    """Booleans arrive three ways — real booleans, form strings, and
    empty. Only English forms are accepted; anything else is a 400."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if not text:
        return default
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise HTTPException(400, f"not a boolean: {value!r}")


# ---------------------------------------------------------------------------
# Pure settings validators
# ---------------------------------------------------------------------------


def _parse_platforms(value: Any) -> dict:
    """Validate and parse a platforms value.

    Accepts a pre-parsed dict or a JSON string. Returns a dict of
    string -> string. Raises ValueError on invalid input.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("platforms is not valid JSON")
    if not isinstance(value, dict) or not value:
        raise ValueError("platforms must be a non-empty dict of string -> string")
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("platforms must be a non-empty dict of string -> string")
    return value


def _validate_country(value: str) -> str:
    """Validate a country string. Max 40 chars, stripped."""
    c = (value or "").strip()
    if len(c) > 40:
        raise ValueError("country must be at most 40 characters")
    return c


# ---------------------------------------------------------------------------
# Settings (key/value table — same pattern as mfa-relay)
# ---------------------------------------------------------------------------


async def _get_settings() -> Dict[str, Any]:
    """Current settings, seeded with defaults if the row is absent."""
    defaults: Dict[str, Any] = {
        "platforms": {"netflix": "Netflix", "prime": "Prime Video"},
        "country": "",
    }
    out = dict(defaults)
    for row in await db.fetchall(
        "SELECT key, value FROM movienight_settings"
    ):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            out[row["key"]] = row["value"]
    return out


# ---------------------------------------------------------------------------
# Research helpers (used in the prompt text)
# ---------------------------------------------------------------------------


def _watched_line(t: dict) -> str:
    liked = t["liked"]
    if t["status"] == "watched":
        if liked:
            verdict = "watched and liked"
        elif liked is not None:
            verdict = "watched, but did NOT like it"
        else:
            verdict = "watched (no opinion)"
    else:
        verdict = "suggested, not seen yet (may be revisited, but prefer new)"
    comment = f' — "{t["comment"]}"' if t.get("comment") else ""
    return f'- "{t["title"]}" — {verdict}{comment}'


def _taste_line(n: dict) -> str:
    if n["seen"]:
        liked = n["liked"]
        if liked:
            verdict = "watched and liked"
        elif liked is not None:
            verdict = "watched, but did NOT like it"
        else:
            verdict = "watched (no opinion)"
    else:
        verdict = "mentioned, but not watched yet"
    comment = f' — "{n["comment"]}"' if n.get("comment") else ""
    return f'- "{n["title"]}" — {verdict}{comment}'


def _build_research_prompt(
    watched: List[dict],
    taste_notes: List[dict],
    today: date,
    config: Dict[str, Any],
) -> str:
    """The research brief for this week's session.

    `today` is passed in rather than read here, so the prompt and the
    session row can never disagree about which day is being covered.
    """
    has_feedback = bool(watched or taste_notes)
    platforms = config.get("platforms", {})
    country = config.get("country", "")

    if country:
        catalog_line = f"the {country} catalog"
    else:
        catalog_line = "the user's local catalog"

    platforms_text = "\n".join(f"- {v}" for v in platforms.values())

    sources_text = (
        "- JustWatch for " + (country or "the user's region")
        + " (platform catalogs, popular and new titles)\n"
        "- Official platform lists and reliable local press"
    )

    if has_feedback:
        feedback = "WHAT THE USER HAS SEEN:\n"
        if watched:
            feedback += "Previous sessions:\n"
            feedback += "\n".join(_watched_line(t) for t in watched)
        if taste_notes:
            feedback += "Outside sessions (told via chat):\n"
            feedback += "\n".join(_taste_line(n) for n in taste_notes)
        rules = (
            "PERSONALIZATION RULES (mandatory):\n"
            "- NEVER re-suggest a title the user HAS WATCHED (in any "
            "previous session or note), regardless of opinion.\n"
            "- If the user DID NOT like something, avoid the same "
            "formula (same genre, same tone, same pacing) — or choose "
            "it anyway, but explain in the 'why' what makes it different "
            "from what was rejected.\n"
            "- Lean on what the user LIKED: the theme and selections "
            "should align with their recorded tastes."
        )
    else:
        feedback = (
            "No feedback has been registered yet — "
            "this is the first session."
        )
        rules = (
            "PERSONALIZATION RULES (first session, no feedback):\n"
            "- Choose widely acclaimed, safe-quality titles.\n"
            "- State in the 'theme' that feedback will refine future "
            "choices."
        )

    synopsis_note = (
        "In the user\'s language (not fixed to any one language)."
    )

    return (
        f"You are a streaming researcher preparing a weekly 'Movie Night' "
        f"for the user. Today is {today.strftime('%A, %B %d, %Y')}.\n\n"
        f"Research the web for what is CURRENTLY available in "
        f"{catalog_line} on:\n"
        f"{platforms_text}\n\n"
        f"Suggested sources — use whichever you can verify, do not guess:\n"
        f"{sources_text}\n\n"
        f"ONLY titles you can confirm are currently available on the "
        f"specified platform make it into the session. If you cannot "
        f"verify availability, the title does not ship.\n\n"
        f"THE SELECTION — one cohesive session of 3 to 5 titles:\n"
        f"- One unifying theme (a decade, a shared genre, a mood, a "
        f"setting).\n"
        f"- Mix movies and series when possible.\n"
        f"- If both platforms have strong candidates, include at least "
        f"one title from each.\n\n"
        f"{feedback}\n\n"
        f"{rules}\n\n"
        f"For each title:\n"
        f"- title: exact name\n"
        f"- type: 'movie' or 'series'\n"
        f"- year: release year (number, sanity 1900..current+1)\n"
        f"- platform: the platform key where you verified availability\n"
        f"- synopsis: 2-3 sentences ({synopsis_note})\n"
        f"- why: one line explaining why it fits them, backed by the "
        f"feedback above\n"
        f"- poster_url: direct https URL to a poster image, or null\n\n"
        f"Reply WITH ONLY a JSON object in this exact shape:\n"
        f"{{\n"
        f'  "theme": "short evocative name for the session",\n'
        f'  "summary": "2-3 line intro",\n'
        f'  "titles": [\n'
        f"    {{\n"
        f'      "title": "...",\n'
        f'      "type": "movie",\n'
        f'      "year": 2020,\n'
        f'      "platform": "netflix",\n'
        f'      "synopsis": "2-3 sentences",\n'
        f'      "why": "one personal line",\n'
        f'      "poster_url": "https://... or null"\n'
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"Do all the research in this single session — do not launch "
        f"sub-agents. Work strictly sequentially: one search or page "
        f"read at a time, wait for the result, only then choose the "
        f"next step. Never bundle multiple tool calls into one "
        f"response — bundled calls are cut off in this environment. "
        f"Do not end the response before the research is complete; "
        f"an announcement of what you intend to do next is not an "
        f"answer. Your final message must be the JSON object itself — "
        f"no narration before, no status report after."
    )


def _normalize_titles(
    raw: object, platforms: Dict[str, str]
) -> List[dict]:
    """Every title with every expected field, whatever the agent returned.
    Drops any title whose platform key is not in the configured set."""
    if not isinstance(raw, list):
        return []
    current_year = datetime.now(timezone.utc).year
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        # No verifiable platform = no verified availability = dropped.
        platform = str(item.get("platform") or "").strip().lower()
        if platform not in platforms:
            continue
        type_ = str(item.get("type") or "movie").strip().lower()
        if type_ not in ("movie", "series"):
            type_ = "movie"
        try:
            year = int(str(item.get("year") or "").strip())
        except (TypeError, ValueError):
            year = None
        if year is not None and not (1900 <= year <= current_year + 1):
            year = None
        result.append({
            "title": title,
            "type": type_,
            "year": year,
            "platform": platform,
            "synopsis": str(item.get("synopsis") or "").strip(),
            "why": str(item.get("why") or "").strip(),
            "poster_url": _poster_url(item.get("poster_url")),
        })
    return result


# ---------------------------------------------------------------------------
# Session generation
# ---------------------------------------------------------------------------


async def _gather_taste() -> Tuple[List[dict], List[dict]]:
    """What the research prompt personalises on: reactions to
    recommended titles (last 15) plus reactions to titles never
    recommended (last 20)."""
    watched = [dict(r) for r in await db.fetchall(
        """SELECT title, status, liked, comment FROM movienight_titles
           WHERE status <> 'suggested'
           ORDER BY updated_at DESC LIMIT 15""")]
    notes = [dict(r) for r in await db.fetchall(
        """SELECT title, seen, liked, comment FROM movienight_taste_notes
           ORDER BY noted_at DESC LIMIT 20""")]
    return watched, notes


async def _store_titles(
    titles: List[dict], session_id: int
) -> List[int]:
    """Store this week's titles, keeping ONE row per distinct title, ever.

    Matching is on title_fold (case- and accent-folded) so a stored
    title keeps its accents while a plain-spelled lookup still finds
    it. A match refreshes the pitch and the poster, but never the
    feedback already given.
    """
    ids = []
    for t in titles:
        folded = _fold(t["title"])
        existing = await db.fetchone(
            "SELECT id FROM movienight_titles "
            "WHERE title_fold = $1 LIMIT 1",
            folded)
        if existing:
            await db.execute(
                """UPDATE movienight_titles
                     SET why = $2,
                         poster_url = CASE WHEN $3 = '' THEN poster_url ELSE $3 END,
                         updated_at = $4
                   WHERE id = $1""",
                existing["id"], t["why"], t["poster_url"], _now())
            ids.append(existing["id"])
        else:
            row = await db.fetchone(
                """INSERT INTO movienight_titles
                     (title, title_fold, type, year, platform, synopsis,
                      poster_url, why, session_id, added_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                   RETURNING id""",
                t["title"], folded, t["type"], t["year"],
                t["platform"], t["synopsis"],
                t["poster_url"] or None, t["why"], session_id,
                _now(), _now())
            ids.append(row["id"])
    return ids


async def _generate_session(session_date: Optional[date] = None) -> dict:
    """Research -> store -> structured content."""
    if session_date is None:
        session_date = datetime.now(timezone.utc).date()

    log.info("Researching Movie Night session for %s", session_date)
    config = await _get_settings()
    watched, taste_notes = await _gather_taste()
    prompt = _build_research_prompt(
        watched, taste_notes, session_date, config)

    data = await ask_json(
        prompt, timeout=RESEARCH_IDLE_TIMEOUT, retries=2)

    if not data:
        log.error("Research returned nothing usable (turn failed or "
                  "the answer was not parseable JSON)")
        raise HTTPException(502, "Research returned nothing usable")

    theme = str(data.get("theme") or "").strip()
    if not theme:
        raise HTTPException(502, "Research returned no theme")
    titles = _normalize_titles(data.get("titles"), config.get("platforms", {}))
    if not titles:
        raise HTTPException(502, "Research returned no usable titles")
    summary = str(data.get("summary") or "").strip()

    # Store a session row so we have a session_id for FK.
    now = _now()
    row = await db.fetchone(
        """INSERT INTO movienight_sessions
             (session_date, theme, created_at)
           VALUES ($1, $2, $3)
           ON CONFLICT (session_date) DO UPDATE
             SET theme = $2, created_at = $3
           RETURNING id""",
        str(session_date), theme, now)
    session_id = row["id"]

    title_ids = await _store_titles(titles, session_id)

    # Fetch stored rows — dynamic IN list works on both backends.
    if title_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(title_ids)))
        rows = await db.fetchall(
            f"""SELECT id, title, type, year, platform, synopsis,
                        poster_url, why, status, liked, comment,
                        added_at, updated_at
                   FROM movienight_titles
                  WHERE id IN ({placeholders})""",
            *title_ids)
    else:
        rows = []
    by_id = {r["id"]: r for r in rows}
    session_titles = [by_id[i] for i in title_ids if i in by_id]

    log.info("Stored Movie Night session %s (%s) with %d titles",
             session_date, theme, len(session_titles))

    return {
        "ok": True,
        "title": theme,
        "summary": summary,
        "content": {
            "theme": theme,
            "titles": session_titles,
            "session_date": str(session_date),
        },
        "sources": None,
    }


# ---------------------------------------------------------------------------
# Pydantic models (outside get_router — FastAPI hot-reload safety)
# ---------------------------------------------------------------------------


class TasteRequest(BaseModel):
    title: str = Field(min_length=1)
    seen: Union[bool, str, int, None] = True
    liked: Union[bool, str, int, None] = None
    comment: Optional[str] = None
    source: Optional[str] = None


class TitleFeedbackRequest(BaseModel):
    watched: Union[bool, str, int, None] = None
    liked: Union[bool, str, int, None] = None
    comment: Optional[str] = None


class GenerateBody(BaseModel):
    """The digestos hub posts {**params, 'date'}; the research itself
    covers 'what is available now' — `date` only pins the session's day."""
    date: Optional[str] = None


class SettingsUpdate(BaseModel):
    platforms: Optional[Any] = None
    country: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(tags=["movienight"])


@router.get("/health")
async def movienight_health():
    """Liveness, plus how much the catalog holds."""
    return {
        "ok": True,
        "schema": "movienight",
        "title_count": await db.fetchval(
            "SELECT COUNT(*) FROM movienight_titles") or 0,
        "taste_count": await db.fetchval(
            "SELECT COUNT(*) FROM movienight_taste_notes") or 0,
    }


@router.get("/titles")
async def list_titles(
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = Query(None),
):
    """The permanent catalog, most recent first. One row per distinct
    title, ever."""
    if status is not None and status not in (
        "suggested", "watched", "not_watched"
    ):
        raise HTTPException(400,
            "status must be one of: suggested, watched, not_watched")
    if status is not None:
        rows = await db.fetchall(
            """SELECT id, title, type, year, platform, synopsis,
                      poster_url, why, status, liked, comment,
                      added_at, updated_at
                 FROM movienight_titles
                WHERE status = $1
                ORDER BY added_at DESC, id DESC
                LIMIT $2""",
            status, limit)
    else:
        rows = await db.fetchall(
            """SELECT id, title, type, year, platform, synopsis,
                      poster_url, why, status, liked, comment,
                      added_at, updated_at
                 FROM movienight_titles
                ORDER BY added_at DESC, id DESC
                LIMIT $1""",
            limit)
    return {
        "titles": [{
            "id": r["id"],
            "title": r["title"],
            "type": r["type"],
            "year": r["year"],
            "platform": r["platform"],
            "synopsis": r["synopsis"],
            "poster_url": r["poster_url"],
            "why": r["why"],
            "status": r["status"],
            "liked": r["liked"],
            "comment": r["comment"],
            "added_at": r["added_at"],
            "updated_at": r["updated_at"],
        } for r in rows],
        "count": len(rows),
    }


@router.get("/taste")
async def list_taste(limit: int = Query(20, ge=1, le=200)):
    """Recent taste notes — what Friday's prompt personalises on."""
    rows = await db.fetchall(
        """SELECT id, title, seen, liked, comment, source, noted_at
             FROM movienight_taste_notes
            ORDER BY noted_at DESC, id DESC
            LIMIT $1""", limit)
    return {
        "taste": [{
            "id": r["id"],
            "title": r["title"],
            "seen": r["seen"],
            "liked": r["liked"],
            "comment": r["comment"],
            "source": r["source"],
            "noted_at": r["noted_at"],
        } for r in rows],
        "count": len(rows),
    }


@router.post("/taste")
async def record_taste(req: TasteRequest):
    """Record a reaction to a title.

    If the title is already in the catalog (folded match), the reaction
    lands on that row; otherwise it becomes a taste note for a title
    never recommended.
    """
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "title is required")
    seen = _coerce_bool(req.seen, default=True)
    liked = _coerce_bool(req.liked)
    comment = (req.comment or "").strip() or None
    source = (req.source or "chat").strip() or "chat"

    matched = await db.fetchone(
        "SELECT id FROM movienight_titles "
        "WHERE title_fold = $1 LIMIT 1", _fold(title))
    if matched:
        status = "watched" if seen else "not_watched"
        await db.execute(
            """UPDATE movienight_titles
                 SET status = $1, liked = $2, comment = $3,
                     updated_at = $4
               WHERE id = $5""",
            status, liked, comment, _now(), matched["id"])
        return {"matched": matched["id"]}

    await db.execute(
        """INSERT INTO movienight_taste_notes
             (title, seen, liked, comment, source, noted_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        title, seen, liked, comment, source, _now())
    return {"inserted": True}


@router.post("/generate")
async def generate_session(body: Optional[GenerateBody] = None):
    """Research this week's session and return structured content.

    In-flight guard: a gateway restart clears the guard (and the
    in-flight state with it).
    """
    session_date = None
    if body and body.date:
        try:
            session_date = date.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(400, f"Invalid date: {body.date!r}")

    if _generation_lock.locked():
        raise HTTPException(
            409,
            "A Movie Night research turn is already queued or running. "
            "Wait for it rather than enqueuing another.")

    async with _generation_lock:
        return await _generate_session(session_date)


@router.post("/titles/{title_id}/feedback")
async def title_feedback(title_id: int, req: TitleFeedbackRequest):
    """Apply a reaction to one catalog row — only what is present.

    Each dashboard button sends one field; an absent field must leave
    the column alone. Uses COALESCE($N, column) with NULL = absent.
    """
    row = await db.fetchone(
        "SELECT id FROM movienight_titles WHERE id = $1", title_id)
    if row is None:
        raise HTTPException(404, f"Title {title_id} not found")

    status = None
    if req.watched is not None:
        status = "watched" if _coerce_bool(req.watched) else "not_watched"
    liked = _coerce_bool(req.liked) if req.liked is not None else None
    comment = (req.comment or "").strip() or None

    await db.execute(
        """UPDATE movienight_titles
             SET status = COALESCE($1, status),
                 liked = COALESCE($2, liked),
                 comment = COALESCE($3, comment),
                 updated_at = $4
           WHERE id = $5""",
        status, liked, comment, _now(), title_id)
    return {"ok": True, "id": title_id}


@router.get("/settings")
async def get_settings_route():
    """Current Movie Night settings."""
    settings = await _get_settings()
    return settings


@router.put("/settings")
async def update_settings_route(req: SettingsUpdate):
    """Update Movie Night settings."""
    platforms = req.platforms
    country = req.country
    changes: Dict[str, Any] = {}

    if platforms is not None:
        try:
            p = _parse_platforms(platforms)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        changes["platforms"] = json.dumps(p)

    if country is not None:
        try:
            c = _validate_country(country)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        changes["country"] = json.dumps(c)

    if not changes:
        raise HTTPException(400, "no recognised fields to update")

    now = _now()
    for key, value in changes.items():
        await db.execute(
            """INSERT INTO movienight_settings (key, value, updated_at)
               VALUES ($1, $2, $3)
               ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3""",
            key, value, now)

    return {"ok": True, "settings": await get_settings_route()}


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def get_router() -> APIRouter:
    """Return the FastAPI router for the gateway to mount."""
    return router
