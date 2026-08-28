"""Weather connector — Open-Meteo (agentboom package: weather).

Free, no API key, no account: geocoding + current conditions + daily
forecast by place name.

Mini-app usage:

    from connectors.weather import current_for, forecast_for

    now = await current_for("Lisbon")
    days = await forecast_for("Porto", days=3)

Env: none. (Optional WEATHER_TIMEOUT_SEC.)
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import httpx

log = logging.getLogger("connectors.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = float(os.environ.get("WEATHER_TIMEOUT_SEC", "15"))

# WMO interpretation codes used by Open-Meteo.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


class WeatherError(RuntimeError):
    """Geocoding or forecast lookup failed."""


def describe(code: Optional[int]) -> str:
    return _WMO.get(code if code is not None else -1, f"unknown ({code})")


async def geocode(place: str) -> dict:
    """Resolve a place name to {'name','country','latitude','longitude'}."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(GEOCODE_URL, params={
                "name": place, "count": 1, "language": "en", "format": "json",
            })
    except httpx.HTTPError as exc:
        raise WeatherError(f"geocoding unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise WeatherError(f"geocoding HTTP {resp.status_code}: {resp.text[:200]}")
    results = resp.json().get("results") or []
    if not results:
        raise WeatherError(f"no place found for '{place}'")
    hit = results[0]
    return {
        "name": hit.get("name"),
        "country": hit.get("country"),
        "latitude": hit["latitude"],
        "longitude": hit["longitude"],
    }


async def current_for(place: str) -> dict:
    """Current conditions for a place name."""
    where = await geocode(place)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(FORECAST_URL, params={
                "latitude": where["latitude"], "longitude": where["longitude"],
                "current": ("temperature_2m,apparent_temperature,"
                            "relative_humidity_2m,weather_code,"
                            "wind_speed_10m,precipitation"),
                "timezone": "auto",
            })
    except httpx.HTTPError as exc:
        raise WeatherError(f"forecast unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise WeatherError(f"forecast HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    cur = data.get("current") or {}
    code = cur.get("weather_code")
    return {
        "place": f"{where['name']}, {where['country']}",
        "coordinates": {"lat": where["latitude"], "lon": where["longitude"]},
        "time": cur.get("time"),
        "timezone": data.get("timezone"),
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "humidity_pct": cur.get("relative_humidity_2m"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "precipitation_mm": cur.get("precipitation"),
        "condition": describe(code),
        "weather_code": code,
    }


async def forecast_for(place: str, days: int = 3) -> List[dict]:
    """Daily forecast (up to 16 days) for a place name."""
    days = max(1, min(int(days), 16))
    where = await geocode(place)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(FORECAST_URL, params={
                "latitude": where["latitude"], "longitude": where["longitude"],
                "daily": ("temperature_2m_max,temperature_2m_min,"
                          "precipitation_probability_max,weather_code"),
                "timezone": "auto", "forecast_days": days,
            })
    except httpx.HTTPError as exc:
        raise WeatherError(f"forecast unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise WeatherError(f"forecast HTTP {resp.status_code}: {resp.text[:200]}")
    daily = resp.json().get("daily") or {}
    out = []
    for i, date in enumerate(daily.get("time") or []):
        code = (daily.get("weather_code") or [None])[i] \
            if i < len(daily.get("weather_code") or []) else None
        out.append({
            "date": date,
            "max_c": (daily.get("temperature_2m_max") or [None])[i],
            "min_c": (daily.get("temperature_2m_min") or [None])[i],
            "precipitation_probability_pct":
                (daily.get("precipitation_probability_max") or [None])[i],
            "condition": describe(code),
        })
    return out
