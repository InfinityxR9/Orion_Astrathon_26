"""
Weather Data Module
Fetches current cloud cover, visibility, and temperature from Open-Meteo API.
Uses the current_weather + current hourly slot (matching the actual UTC hour).
"""

import requests
from datetime import datetime, timezone
from typing import Dict, Any

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10


def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetch current weather conditions from Open-Meteo.
    Returns cloud_cover, cloud_score, temperature, humidity, visibility_km.
    """
    try:
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,weather_code",
            "hourly": "cloud_cover,visibility",
            "forecast_days": 1,
            "timezone": "auto",
        }
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return _parse_weather(data)
    except Exception:
        return _fallback_weather()


def _parse_weather(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse Open-Meteo response — prefer 'current' block (v1 API)."""
    current = data.get("current", {})

    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    cloud_cover = current.get("cloud_cover")
    wind_speed = current.get("wind_speed_10m")
    weather_code = current.get("weather_code")

    # Fallback: 'current_weather' block (older API responses)
    if cloud_cover is None:
        cw = data.get("current_weather", {})
        temperature = temperature or cw.get("temperature")
        # Try hourly at current UTC hour index
        hourly = data.get("hourly", {})
        cc_list = hourly.get("cloud_cover", [])
        now_hour = datetime.now(timezone.utc).hour
        cloud_cover = cc_list[now_hour] if now_hour < len(cc_list) else (cc_list[0] if cc_list else 50.0)

    if cloud_cover is None:
        cloud_cover = 50.0

    # Atmospheric visibility from hourly block
    hourly = data.get("hourly", {})
    vis_list = hourly.get("visibility", [])
    now_hour = datetime.now(timezone.utc).hour
    vis_m = vis_list[now_hour] if now_hour < len(vis_list) else None
    visibility_km = round(vis_m / 1000, 1) if vis_m is not None else None

    # Cloud score: 1 = perfectly clear, 0 = fully overcast
    cloud_fraction = cloud_cover / 100.0
    cloud_score = 1.0 - cloud_fraction

    # Haze penalty for humidity > 80%
    if humidity is not None:
        haze_penalty = max(0, (humidity / 100.0 - 0.8)) * 0.5
        cloud_score = max(0.0, cloud_score - haze_penalty)

    # Low-visibility penalty (fog, mist)
    if visibility_km is not None and visibility_km < 10:
        vis_penalty = (10 - visibility_km) / 10.0 * 0.3
        cloud_score = max(0.0, cloud_score - vis_penalty)

    return {
        "cloud_cover_pct": cloud_cover,
        "cloud_score": round(cloud_score, 3),
        "temperature_c": temperature,
        "humidity_pct": humidity,
        "wind_speed_kmh": wind_speed,
        "visibility_km": visibility_km,
        "weather_code": weather_code,
    }


def _fallback_weather() -> Dict[str, Any]:
    """Return default weather when API is unavailable."""
    return {
        "cloud_cover_pct": 50.0,
        "cloud_score": 0.5,
        "temperature_c": None,
        "humidity_pct": None,
        "wind_speed_kmh": None,
        "visibility_km": None,
        "weather_code": None,
    }
