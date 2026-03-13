"""
Aurora Visibility Engine
Computes a composite visibility score (0-100) combining:
  - Aurora probability from OVATION model
  - Sky darkness (solar elevation, moon phase, light pollution estimate)
  - Cloud cover / weather clarity

Formula:
  visibility_score = 0.5 * aurora_prob + 0.3 * darkness_score + 0.2 * cloud_score
  (all components normalized to 0-100 before weighting)
"""

import math
from datetime import datetime, timezone
from typing import Dict, Any

from ovation_parser import get_aurora_probability_at
from weather import fetch_weather


def compute_visibility(lat: float, lon: float, aurora_grid=None) -> Dict[str, Any]:
    """
    Compute the full aurora visibility score for a given location.
    Returns a dict with the composite score and all sub-scores.
    """
    # 1. Aurora probability (0-100)
    aurora_prob = get_aurora_probability_at(lat, lon, grid=aurora_grid)

    # 2. Weather / cloud score (0-1 → scale to 0-100)
    weather_data = fetch_weather(lat, lon)
    cloud_score_raw = weather_data["cloud_score"]
    cloud_score = cloud_score_raw * 100.0

    # 3. Darkness score (0-100)
    now_utc = datetime.now(timezone.utc)
    darkness = compute_darkness_score(lat, lon, now_utc)

    # Composite weighted score
    visibility_score = (
        0.50 * aurora_prob
        + 0.30 * darkness["darkness_score"]
        + 0.20 * cloud_score
    )
    visibility_score = round(min(max(visibility_score, 0), 100), 1)

    # Determine qualitative rating
    rating = _score_to_rating(visibility_score)

    return {
        "lat": lat,
        "lon": lon,
        "visibility_score": visibility_score,
        "rating": rating,
        "aurora_probability": round(aurora_prob, 1),
        "darkness_score": round(darkness["darkness_score"], 1),
        "cloud_score": round(cloud_score, 1),
        "is_dark": darkness["is_dark"],
        "solar_elevation_deg": round(darkness["solar_elevation"], 1),
        "moon_illumination_pct": round(darkness["moon_illumination"] * 100, 1),
        "weather": weather_data,
        "timestamp": now_utc.isoformat(),
    }


def compute_darkness_score(lat: float, lon: float, dt: datetime) -> Dict[str, Any]:
    """
    Compute darkness score from solar elevation, moon illumination, and light pollution.
    Returns darkness_score (0-100) plus diagnostic values.
    """
    sun_elev = solar_elevation(lat, lon, dt)
    moon_illum = moon_illumination(dt)
    light_pollution = estimate_light_pollution(lat, lon)

    # Solar contribution (0-100): fully dark when sun < -18°, twilight penalty
    if sun_elev < -18:
        solar_score = 100.0
    elif sun_elev < -12:
        # Astronomical twilight
        solar_score = 70.0 + 30.0 * ((-12 - sun_elev) / 6.0)
    elif sun_elev < -6:
        # Nautical twilight
        solar_score = 30.0 + 40.0 * ((-6 - sun_elev) / 6.0)
    elif sun_elev < 0:
        # Civil twilight
        solar_score = 5.0 + 25.0 * ((-sun_elev) / 6.0)
    else:
        # Daytime — aurora basically invisible
        solar_score = 0.0

    # Moon penalty (0-30 point reduction at full moon)
    moon_penalty = moon_illum * 30.0

    # Light pollution penalty (0-25)
    lp_penalty = light_pollution * 25.0

    darkness_score = max(0.0, solar_score - moon_penalty - lp_penalty)
    is_dark = sun_elev < -6

    return {
        "darkness_score": darkness_score,
        "solar_elevation": sun_elev,
        "moon_illumination": moon_illum,
        "light_pollution": light_pollution,
        "is_dark": is_dark,
    }


def solar_elevation(lat: float, lon: float, dt: datetime) -> float:
    """
    Simplified solar elevation angle calculation.
    Returns degrees above/below horizon.
    """
    # Day of year
    doy = dt.timetuple().tm_yday
    # Fractional hour in UTC
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

    # Solar declination (Spencer formula simplified)
    gamma = 2 * math.pi * (doy - 1) / 365.0
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
    )

    # Equation of time (minutes)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.04089 * math.sin(2 * gamma)
    )

    # True solar time
    time_offset = eqtime + 4 * lon  # minutes
    tst = hour_utc * 60 + time_offset
    # Hour angle in degrees
    ha = (tst / 4.0) - 180.0
    ha_rad = math.radians(ha)

    lat_rad = math.radians(lat)

    # Solar elevation
    sin_elev = (
        math.sin(lat_rad) * math.sin(declination)
        + math.cos(lat_rad) * math.cos(declination) * math.cos(ha_rad)
    )
    sin_elev = max(-1, min(1, sin_elev))
    elevation = math.degrees(math.asin(sin_elev))
    return elevation


def moon_illumination(dt: datetime) -> float:
    """
    Simplified moon illumination fraction (0 = new, 1 = full).
    Based on a known new moon reference date.
    """
    # Reference new moon: 2024-01-11 11:57 UTC
    ref_new_moon = datetime(2024, 1, 11, 11, 57, 0, tzinfo=timezone.utc)
    synodic_month = 29.53058867  # days

    diff_days = (dt - ref_new_moon).total_seconds() / 86400.0
    phase = (diff_days % synodic_month) / synodic_month  # 0-1 cycle

    # Illumination: 0 at new moon (phase=0), 1 at full (phase=0.5)
    illumination = 0.5 * (1 - math.cos(2 * math.pi * phase))
    return illumination


def estimate_light_pollution(lat: float, lon: float) -> float:
    """
    Rough light pollution estimate (0 = dark sky, 1 = heavy light pollution).
    Uses latitude as a proxy: mid-latitudes tend to have more populated areas.
    This is a simplification — a real system would use a Bortle scale database.
    """
    # Population density proxy based on latitude bands
    abs_lat = abs(lat)
    if abs_lat > 65:
        # Arctic/Antarctic — generally low light pollution
        return 0.1
    elif abs_lat > 55:
        # Northern regions (Scandinavia, Canada) — moderate
        return 0.2
    elif abs_lat > 40:
        # Mid-latitudes (US, Europe, Japan) — higher
        return 0.4
    elif abs_lat > 25:
        return 0.5
    else:
        # Tropics — variable, moderate default
        return 0.3


def _score_to_rating(score: float) -> str:
    if score >= 80:
        return "Excellent"
    elif score >= 60:
        return "Good"
    elif score >= 40:
        return "Moderate"
    elif score >= 20:
        return "Low"
    else:
        return "Very Low"
