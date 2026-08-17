"""Cron expression parsing and next-fire computation.

One implementation, used by every scheduler in the platform. Before this
existed the reminders mini-app had its own parser and the platform scheduler
had a ``pass  # TODO`` where cron support should have been — which meant a
cron-scheduled job never advanced ``next_fire_at`` and re-fired on every tick.

Format: standard 5-field cron ``minute hour day-of-month month day-of-week``
with ``*``, ``,`` lists, ``a-b`` ranges, ``*/n`` and ``a/n`` steps.
Day-of-week is 0-6 with 0 = Sunday (Unix convention); 7 is also accepted as
Sunday. Both day-of-month and day-of-week restricted means "either matches",
matching Vixie cron.

Usage:
    from agentboom_sdk.cron import next_cron_time, is_valid_cron

    when = next_cron_time("0 9 * * 1-5", after=now, tz_name="Europe/Lisbon")
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

log = logging.getLogger("agentboom_sdk.cron")

__all__ = ["parse_cron", "is_valid_cron", "next_cron_time", "CronError"]

# Search horizon. A valid 5-field expression always matches within ~4 years
# (Feb 29 on a leap year is the worst case), so this bound only trips on
# impossible expressions like "0 0 30 2 *".
_MAX_SEARCH_DAYS = 366 * 4

_FIELD_RANGES = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),
)


class CronError(ValueError):
    """Raised when a cron expression cannot be parsed."""


def _parse_field(field: str, name: str, min_val: int, max_val: int) -> List[int]:
    """Parse one cron field into the sorted list of values it matches."""
    values: set[int] = set()
    # Day-of-week accepts 7 = Sunday. Expand in the 0..7 space and wrap
    # afterwards — normalising 7→0 before expansion would turn "5-7" into
    # "5-0" and silently drop Sunday from the range.
    expand_max = 7 if name == "weekday" else max_val

    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{name}: empty element in {field!r}")

        # Split off an optional /step suffix.
        step = 1
        if "/" in part:
            part, _, step_str = part.partition("/")
            try:
                step = int(step_str)
            except ValueError:
                raise CronError(f"{name}: step must be an integer in {field!r}")
            if step <= 0:
                raise CronError(f"{name}: step must be positive in {field!r}")

        if part == "*":
            lo, hi = min_val, expand_max
        elif "-" in part.lstrip("-"):
            lo_str, _, hi_str = part.partition("-")
            try:
                lo, hi = int(lo_str), int(hi_str)
            except ValueError:
                raise CronError(f"{name}: bad range {part!r} in {field!r}")
        else:
            try:
                lo = int(part)
            except ValueError:
                raise CronError(f"{name}: bad value {part!r} in {field!r}")
            # A bare number with a step means "from here to the end of range",
            # e.g. "5/10" in the minute field is 5,15,25,35,45,55.
            hi = expand_max if step > 1 else lo

        if lo < min_val or hi > expand_max or hi < lo:
            raise CronError(
                f"{name}: {part!r} out of range {min_val}-{max_val} in {field!r}"
            )

        values.update(range(lo, hi + 1, step))

    if name == "weekday":
        values = {0 if v == 7 else v for v in values}

    if not values:
        raise CronError(f"{name}: {field!r} matches nothing")
    return sorted(values)


def parse_cron(expr: str) -> Dict[str, List[int]]:
    """Parse a 5-field cron expression into per-field value lists.

    Raises CronError if the expression is malformed.
    """
    if not expr or not expr.strip():
        raise CronError("empty cron expression")

    parts = expr.strip().split()
    if len(parts) != 5:
        raise CronError(
            f"expected 5 fields (minute hour day month weekday), got {len(parts)}: {expr!r}"
        )

    return {
        name: _parse_field(parts[i], name, lo, hi)
        for i, (name, lo, hi) in enumerate(_FIELD_RANGES)
    }


def is_valid_cron(expr: str) -> bool:
    """Return True if the expression parses. Never raises."""
    try:
        parse_cron(expr)
        return True
    except CronError:
        return False


def _resolve_tz(tz_name: str):
    """Return a tzinfo for tz_name, falling back to UTC if unavailable."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        # A typo in SCHEDULER_TZ would otherwise silently reschedule
        # everything in UTC — loud enough to notice, never fatal.
        log.warning("Unknown timezone %r — falling back to UTC", tz_name)
        return timezone.utc


def next_cron_time(
    expr: str,
    after: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> Optional[datetime]:
    """Return the first datetime strictly after ``after`` that matches ``expr``.

    The returned value is timezone-aware and normalised to UTC, so it can be
    written straight into a ``timestamptz`` column. Matching is done in
    ``tz_name`` so "every day at 09:00" means 09:00 local, and follows DST.

    Returns None if the expression is invalid or matches no time within four
    years (e.g. "0 0 30 2 *" — February 30th never happens).
    """
    try:
        fields = parse_cron(expr)
    except CronError:
        return None

    tz = _resolve_tz(tz_name)
    base = after or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # Start from the next whole minute so we never return `after` itself.
    cursor = base.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)

    minutes = fields["minute"]
    hours = fields["hour"]
    days = fields["day"]
    months = fields["month"]
    weekdays = fields["weekday"]

    # Vixie cron semantics: when BOTH day-of-month and day-of-week are
    # restricted, a day matches if EITHER matches. If only one is restricted,
    # only that one applies.
    dom_restricted = len(days) < 31
    dow_restricted = len(weekdays) < 7

    deadline = cursor + timedelta(days=_MAX_SEARCH_DAYS)

    while cursor < deadline:
        if cursor.month not in months:
            # Jump to the 1st of the next month — skips up to 31 days at once.
            cursor = _start_of_next_month(cursor)
            continue

        dom_ok = cursor.day in days
        # Python: Monday=0..Sunday=6. Cron: Sunday=0..Saturday=6.
        dow_ok = ((cursor.weekday() + 1) % 7) in weekdays

        if dom_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok
        elif dom_restricted:
            day_ok = dom_ok
        elif dow_restricted:
            day_ok = dow_ok
        else:
            day_ok = True

        if not day_ok:
            cursor = _start_of_next_day(cursor)
            continue

        if cursor.hour not in hours:
            cursor = _start_of_next_hour(cursor)
            continue

        if cursor.minute in minutes:
            return cursor.astimezone(timezone.utc)

        cursor += timedelta(minutes=1)

    return None


def _start_of_next_day(dt: datetime) -> datetime:
    """Midnight of the following day, DST-safe."""
    naive_next = (dt.replace(tzinfo=None) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return naive_next.replace(tzinfo=dt.tzinfo)


def _start_of_next_hour(dt: datetime) -> datetime:
    return (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def _start_of_next_month(dt: datetime) -> datetime:
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    naive = datetime(year, month, 1)
    return naive.replace(tzinfo=dt.tzinfo)
