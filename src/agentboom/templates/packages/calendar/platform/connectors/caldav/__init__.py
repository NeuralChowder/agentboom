"""CalDAV connector — read events from any CalDAV server
(agentboom package: calendar).

httpx transport + stdlib XML/iCalendar parsing: no extra dependencies.
Works with Fastmail, iCloud (app-specific passwords), Nextcloud, and
any server speaking CalDAV calendar-query REPORTs. Google Calendar is
NOT covered — it requires OAuth, not CalDAV passwords.

Mini-app usage:

    from connectors.caldav import fetch_events

    events = await fetch_events("https://caldav.fastmail.com/dav/calendars/user/you@x/Default/",
                                "you@x", app_password, days=30)

Env: none.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

log = logging.getLogger("connectors.caldav")

_TIMEOUT = float(os.environ.get("CALDAV_TIMEOUT_SEC", "30"))

# Provider presets — principal-less direct calendar URLs where known.
# 'caldav' means bring your own caldav_url.
PROVIDERS: Dict[str, dict] = {
    "fastmail": {
        "base": "https://caldav.fastmail.com",
        "note": "app password from Fastmail settings; URL of one calendar "
                "ends in /dav/calendars/user/<email>/<CalendarName>/",
    },
    "icloud": {
        "base": "https://caldav.icloud.com",
        "note": "app-specific password (appleid.apple.com); calendar path "
                "discovery varies — supply caldav_url when you have it",
    },
    "nextcloud": {
        "base": "",
        "note": "your server: https://cloud.example.com/remote.php/dav/"
                "calendars/<user>/<calendar>/",
    },
    "caldav": {
        "base": "",
        "note": "any CalDAV server — provide the full calendar URL",
    },
}

_REPORT_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start}" end="{end}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""


class CalDavError(RuntimeError):
    """The CalDAV operation failed."""


def _unfold(text: str) -> str:
    """RFC5545 unfolding: continuation lines start with a space/tab."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_ics_datetime(value: str, params: str) -> Optional[datetime]:
    """Parse DTSTART/DTEND values: date, local, or UTC forms."""
    value = value.strip()
    try:
        if len(value) == 8:                       # 20260818 (all-day)
            return datetime.strptime(value, "%Y%m%d")
        if value.endswith("Z"):                   # 20260818T090000Z
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = re.search(r'TZID=([^:;"]+)', params or "")
        if tzid:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(tzid.group(1)))
            except Exception:  # noqa: BLE001 — unknown TZ: keep naive-UTC
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_vevents(ics_text: str) -> List[dict]:
    """Minimal VEVENT extraction — enough for agendas, not a full RFC."""
    events = []
    for block in re.findall(
            r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold(ics_text), re.DOTALL):
        fields: Dict[str, tuple] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            head, _, value = line.partition(":")
            name = head.split(";", 1)[0].strip().upper()
            params = head if ";" in head else ""
            if name not in fields:  # first occurrence wins (EXDATE-safe enough)
                fields[name] = (value.strip(), params)
        uid = fields.get("UID", ("", ""))[0]
        summary = fields.get("SUMMARY", ("", ""))[0]
        if not uid and not summary:
            continue
        start_raw, start_params = fields.get("DTSTART", ("", ""))
        end_raw, end_params = fields.get("DTEND", ("", ""))
        start = _parse_ics_datetime(start_raw, start_params)
        end = _parse_ics_datetime(end_raw, end_params)
        all_day = len(start_raw) == 8
        events.append({
            "uid": uid or summary,
            "summary": summary or "(untitled)",
            "start": start.strftime("%Y-%m-%d %H:%M:%S") if start else None,
            "end": end.strftime("%Y-%m-%d %H:%M:%S") if end else None,
            "all_day": 1 if all_day else 0,
            "location": fields.get("LOCATION", ("", ""))[0][:200],
            "description": fields.get("DESCRIPTION", ("", ""))[0][:500],
        })
    return events


def _extract_calendar_data(xml_bytes: bytes) -> List[str]:
    """Pull every calendar-data payload out of a multistatus response."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise CalDavError(f"unparseable CalDAV response: {exc}") from exc
    out = []
    for elem in root.iter():
        if elem.tag.endswith("}calendar-data") or elem.tag == "calendar-data":
            if elem.text and "BEGIN:VCALENDAR" in elem.text:
                out.append(elem.text)
    return out


async def fetch_events(caldav_url: str, username: str, password: str,
                       days: int = 30) -> List[dict]:
    """Events overlapping [now, now+days] from one calendar collection."""
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=days + 1)
    body = _REPORT_BODY.format(start=start.strftime("%Y%m%dT%H%M%SZ"),
                               end=end.strftime("%Y%m%dT%H%M%SZ"))
    auth = (username, password)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(
                "REPORT", caldav_url, content=body.encode("utf-8"),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                auth=auth)
    except httpx.HTTPError as exc:
        raise CalDavError(f"CalDAV unreachable at {caldav_url}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise CalDavError("CalDAV sign-in rejected (check user/app password)")
    if resp.status_code >= 400:
        raise CalDavError(
            f"CalDAV REPORT HTTP {resp.status_code}: {resp.text[:200]}")
    events: List[dict] = []
    seen = set()
    for ics in _extract_calendar_data(resp.content):
        for event in parse_vevents(ics):
            if event["uid"] in seen:
                continue
            seen.add(event["uid"])
            events.append(event)
    return sorted(events, key=lambda e: e["start"] or "")
