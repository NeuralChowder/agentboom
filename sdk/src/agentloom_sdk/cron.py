"""Standard 5-field cron expression parser and next-fire computation.

Fields: minute hour day-of-month month day-of-week (0 = Sunday).
Supports: *   */n   n-m   n,m,...   and combinations (1-5,10-15,20).

Day semantics follow standard cron: when BOTH day-of-month and day-of-week
are restricted, a time matches if EITHER matches (OR); otherwise the
restricted field must match.
"""
import datetime
import logging
from typing import List, Optional, Set

log = logging.getLogger("agentloom_sdk.cron")


class CronError(ValueError):
    """Raised when a cron expression is invalid."""


FIELD_RANGES = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 6),  # 0 = Sunday
]


def is_valid_cron(expr: str) -> bool:
    try:
        parse_cron(expr)
        return True
    except CronError:
        return False


def parse_cron(expr: str) -> List[Set[int]]:
    """Parse a 5-field cron expression into [minutes, hours, doms, months, dows]."""
    if not expr or not expr.strip():
        raise CronError("Empty cron expression")

    parts = expr.strip().split()
    if len(parts) != 5:
        raise CronError(f"Cron expression must have 5 fields, got {len(parts)}: {expr}")

    result: List[Set[int]] = []
    for i, part in enumerate(parts):
        field_name, min_val, max_val = FIELD_RANGES[i]
        result.append(_parse_field(part, min_val, max_val, field_name))
    return result


def _parse_field(field: str, min_val: int, max_val: int, field_name: str) -> Set[int]:
    values: Set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"Empty sub-expression in {field_name}")

        step = None
        if "/" in part:
            base, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError:
                raise CronError(f"Invalid step value '{step_str}' in {field_name}")
            if step <= 0:
                raise CronError(f"Step value must be > 0 in {field_name}, got {step}")
        else:
            base = part

        if base == "*":
            range_start, range_end = min_val, max_val
        elif "-" in base:
            range_parts = base.split("-", 1)
            try:
                range_start = int(range_parts[0])
                range_end = int(range_parts[1])
            except ValueError:
                raise CronError(f"Invalid range '{base}' in {field_name}")
            if range_start > range_end:
                raise CronError(f"Range start > end in {field_name}: {base}")
            if range_start < min_val or range_end > max_val:
                raise CronError(
                    f"Range {base} out of bounds [{min_val}-{max_val}] in {field_name}"
                )
        else:
            try:
                val = int(base)
            except ValueError:
                raise CronError(f"Invalid value '{base}' in {field_name}")
            if val < min_val or val > max_val:
                raise CronError(f"Value {val} out of bounds [{min_val}-{max_val}] in {field_name}")
            if step is not None:
                range_start, range_end = val, max_val
            else:
                values.add(val)
                continue

        for v in range(range_start, range_end + 1, step if step else 1):
            values.add(v)

    if not values:
        raise CronError(f"No valid values parsed for {field_name}")
    return values


def next_cron_time(
    expr: str,
    after: Optional[datetime.datetime] = None,
    tz: Optional[datetime.tzinfo] = None,
) -> datetime.datetime:
    """Next datetime (exclusive) after which the cron expression matches.

    Defaults: after=now, tz=UTC. Raises CronError if no match within 1 year.
    """
    fields = parse_cron(expr)
    minutes, hours, doms, months, dowss = fields

    if tz is None:
        tz = datetime.timezone.utc
    if after is None:
        after = datetime.datetime.now(tz)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=tz)

    candidate = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    deadline = after + datetime.timedelta(days=366)

    while candidate < deadline:
        if candidate.month not in months:
            candidate = _advance_to_next_month(candidate, months)
            continue

        # Cron semantics: both dom and dow restricted -> match either (OR).
        dom_restricted = doms != set(range(1, 32))
        dow_restricted = dowss != set(range(0, 7))
        candidate_dow = candidate.isoweekday() % 7  # 0 = Sunday

        if dom_restricted and dow_restricted:
            if candidate.day not in doms and candidate_dow not in dowss:
                candidate = (candidate + datetime.timedelta(days=1)).replace(hour=0, minute=0)
                continue
        elif dom_restricted:
            if candidate.day not in doms:
                candidate = (candidate + datetime.timedelta(days=1)).replace(hour=0, minute=0)
                continue
        elif dow_restricted:
            if candidate_dow not in dowss:
                candidate = (candidate + datetime.timedelta(days=1)).replace(hour=0, minute=0)
                continue

        if candidate.hour not in hours:
            candidate = _advance_to_next_hour(candidate, hours)
            continue
        if candidate.minute not in minutes:
            candidate = _advance_to_next_minute(candidate, minutes)
            continue
        return candidate

    raise CronError(f"No match found for cron expression '{expr}' within 1 year of {after}")


def _advance_to_next_month(dt: datetime.datetime, months: Set[int]) -> datetime.datetime:
    year, month = dt.year, dt.month
    for _ in range(13):
        month += 1
        if month > 12:
            month = 1
            year += 1
        if month in months:
            return dt.replace(year=year, month=month, day=1, hour=0, minute=0,
                              second=0, microsecond=0)
    return dt.replace(year=year + 1, month=1, day=1, hour=0, minute=0)


def _advance_to_next_hour(dt: datetime.datetime, hours: Set[int]) -> datetime.datetime:
    for h in range(dt.hour + 1, 24):
        if h in hours:
            return dt.replace(hour=h, minute=0, second=0, microsecond=0)
    next_day = (dt + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for h in sorted(hours):
        return next_day.replace(hour=h)
    return next_day + datetime.timedelta(days=1)


def _advance_to_next_minute(dt: datetime.datetime, minutes: Set[int]) -> datetime.datetime:
    for m in range(dt.minute + 1, 60):
        if m in minutes:
            return dt.replace(minute=m, second=0, microsecond=0)
    return dt.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
