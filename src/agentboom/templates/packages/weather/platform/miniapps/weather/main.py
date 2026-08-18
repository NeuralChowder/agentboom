"""Weather mini-app — conditions by place name (agentboom package: weather).

Zero configuration: Open-Meteo needs no API key. Thin HTTP facade over
the weather connector so agents, dashboards, and other mini-apps can
look weather up over HTTP too.

Endpoints (mounted at /api/weather/):
  GET /health                 connector sanity
  GET /current?place=Lisbon   current conditions
  GET /forecast?place=&days=  daily forecast (1-16, default 3)
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from connectors.weather import WeatherError, current_for, forecast_for

log = logging.getLogger("miniapps.weather")

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "app": "weather", "provider": "open-meteo"}


@router.get("/current")
async def current(place: str = ""):
    if not place.strip():
        return JSONResponse({"error": "place is required"}, status_code=400)
    try:
        return await current_for(place.strip())
    except WeatherError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.get("/forecast")
async def forecast(place: str = "", days: int = 3):
    if not place.strip():
        return JSONResponse({"error": "place is required"}, status_code=400)
    try:
        return {
            "forecast": await forecast_for(place.strip(), days),
        }
    except WeatherError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
