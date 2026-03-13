"""
Weather Data Module
Fetches cloud cover and temperature from Open-Meteo API for aurora visibility assessment.
"""

import requests
from typing import Dict, Any, Optional

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10


def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetch current weather conditions for a location using Open-Meteo.
    Returns cloud_cover (%), temperature (C), and computed cloud_score (0-1).
    """
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "hourly": "cloudcover,temperature_2m,relativehumidity_2m",
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
    """Parse Open-Meteo response into weather data."""
    current = data.get("current_weather", {})
    temperature = current.get("temperature", None)

    # Get the first (current) hourly value for cloud cover
    hourly = data.get("hourly", {})
    cloud_cover_list = hourly.get("cloudcover", [])
    humidity_list = hourly.get("relativehumidity_2m", [])

    cloud_cover = cloud_cover_list[0] if cloud_cover_list else 50.0
    humidity = humidity_list[0] if humidity_list else 50.0

    # Cloud score: 1 = perfectly clear, 0 = fully overcast
    cloud_fraction = cloud_cover / 100.0
    cloud_score = 1.0 - cloud_fraction

    # Humidity penalty — high humidity can cause haze even without clouds
    humidity_fraction = humidity / 100.0
    haze_penalty = max(0, (humidity_fraction - 0.8)) * 0.5  # Only penalizes above 80%

    cloud_score = max(0.0, cloud_score - haze_penalty)

    return {
        "cloud_cover_pct": cloud_cover,
        "cloud_score": round(cloud_score, 3),
        "temperature_c": temperature,
        "humidity_pct": humidity,
    }


def _fallback_weather() -> Dict[str, Any]:
    """Return default weather when API is unavailable."""
    return {
        "cloud_cover_pct": 50.0,
        "cloud_score": 0.5,
        "temperature_c": None,
        "humidity_pct": None,
    }
