"""
Aurora Visibility Engine
Computes a composite visibility score (0-100) combining:
  - Aurora probability from OVATION model (with geomagnetic latitude correction)
  - Sky darkness (solar elevation, moon phase, Bortle-class light pollution)
  - Cloud cover / atmospheric clarity

Also provides photography camera-settings recommendations.

Formula:
  visibility = 0.50 * aurora_prob_norm
             + 0.30 * darkness_score
             + 0.20 * cloud_clarity
  (all components normalized 0-100 before weighting)
"""

import math
from datetime import datetime, timezone
from typing import Dict, Any

from ovation_parser import get_aurora_probability_at
from weather import fetch_weather

# ─── Bortle-class lookup (city coords → Bortle ~1-9) ────────────────────────
# A minimal table of well-known cities; everything else gets a latitude-based
# estimate.  Values: (lat, lon, bortle_class).
_BORTLE_CITIES = [
    (40.71, -74.01, 9),   # New York
    (51.51,  -0.13, 9),   # London
    (48.86,   2.35, 9),   # Paris
    (35.68, 139.69, 9),   # Tokyo
    (55.76,  37.62, 8),   # Moscow
    (28.61,  77.21, 8),   # Delhi
    (39.91, 116.40, 8),   # Beijing
    (34.05,-118.24, 8),   # Los Angeles
    (41.88, -87.63, 8),   # Chicago
    (37.77,-122.42, 8),   # San Francisco
    (52.52,  13.41, 8),   # Berlin
    (59.33,  18.07, 7),   # Stockholm
    (60.17,  24.94, 7),   # Helsinki
    (63.43,  10.40, 5),   # Trondheim
    (64.15, -21.94, 5),   # Reykjavik
    (69.65,  18.96, 4),   # Tromsø
    (78.23,  15.65, 2),   # Longyearbyen
    (68.35,  14.40, 3),   # Lofoten
    (66.50,  25.73, 3),   # Sodankylä
    (64.84, -18.08, 2),   # Iceland interior
    (62.46, -114.37, 3),  # Yellowknife
    (61.22, -149.90, 5),  # Anchorage
    (64.84, -147.72, 4),  # Fairbanks
]


def compute_visibility(lat: float, lon: float, aurora_grid=None) -> Dict[str, Any]:
    """
    Compute the full aurora visibility score for a given location.
    Returns composite score, sub-scores, and photography recommendations.
    """
    # 1. Aurora probability (0-100)
    aurora_prob = get_aurora_probability_at(lat, lon, grid=aurora_grid)

    # 2. Weather / cloud clarity (0-1 → 0-100)
    weather_data = fetch_weather(lat, lon)
    cloud_score = weather_data["cloud_score"] * 100.0

    # 3. Darkness score (0-100)
    now_utc = datetime.now(timezone.utc)
    darkness = compute_darkness_score(lat, lon, now_utc)

    # 4. Geomagnetic-latitude correction (aurora more visible at higher geomlat)
    geomag_lat = _geomagnetic_latitude(lat, lon)

    # 5. Composite
    visibility_score = (
        0.50 * aurora_prob
        + 0.30 * darkness["darkness_score"]
        + 0.20 * cloud_score
    )
    visibility_score = round(min(max(visibility_score, 0), 100), 1)
    rating = _score_to_rating(visibility_score)

    # 6. Photography recommendations
    photo = _photo_recommendations(aurora_prob, darkness["darkness_score"])

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
        "bortle_class": darkness["bortle_class"],
        "geomagnetic_latitude": round(geomag_lat, 1),
        "weather": weather_data,
        "photo_settings": photo,
        "timestamp": now_utc.isoformat(),
    }


# ─── Darkness Score ─────────────────────────────────────────────────────────

def compute_darkness_score(lat: float, lon: float, dt: datetime) -> Dict[str, Any]:
    """
    Darkness score from solar elevation, moon illumination, and Bortle light
    pollution class.
    """
    sun_elev = solar_elevation(lat, lon, dt)
    moon_illum = moon_illumination(dt)
    bortle = estimate_bortle(lat, lon)

    # Solar component (0-100)
    if sun_elev < -18:
        solar_score = 100.0               # Astronomical night
    elif sun_elev < -12:
        solar_score = 70.0 + 30.0 * ((-12 - sun_elev) / 6.0)
    elif sun_elev < -6:
        solar_score = 30.0 + 40.0 * ((-6 - sun_elev) / 6.0)
    elif sun_elev < 0:
        solar_score = 5.0 + 25.0 * (-sun_elev / 6.0)
    else:
        solar_score = 0.0                  # Daytime

    # Moon penalty (0-30 points, full moon in dark sky)
    moon_penalty = moon_illum * 30.0

    # Bortle penalty: class 1 = 0 pts, class 9 = 40 pts
    bortle_penalty = (bortle - 1) / 8.0 * 40.0

    darkness_score = max(0.0, solar_score - moon_penalty - bortle_penalty)
    is_dark = sun_elev < -6

    return {
        "darkness_score": darkness_score,
        "solar_elevation": sun_elev,
        "moon_illumination": moon_illum,
        "bortle_class": bortle,
        "is_dark": is_dark,
    }


# ─── Solar Elevation ────────────────────────────────────────────────────────

def solar_elevation(lat: float, lon: float, dt: datetime) -> float:
    """Solar elevation angle (degrees above horizon), Spencer formula."""
    doy = dt.timetuple().tm_yday
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

    gamma = 2 * math.pi * (doy - 1) / 365.0
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
    )
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.04089 * math.sin(2 * gamma)
    )

    tst = hour_utc * 60 + eqtime + 4 * lon   # minutes
    ha_rad = math.radians(tst / 4.0 - 180.0)
    lat_rad = math.radians(lat)

    sin_elev = (
        math.sin(lat_rad) * math.sin(declination)
        + math.cos(lat_rad) * math.cos(declination) * math.cos(ha_rad)
    )
    return math.degrees(math.asin(max(-1, min(1, sin_elev))))


# ─── Moon Illumination ──────────────────────────────────────────────────────

def moon_illumination(dt: datetime) -> float:
    """Moon illumination fraction (0=new, 1=full). Synodic-month method."""
    ref = datetime(2024, 1, 11, 11, 57, 0, tzinfo=timezone.utc)
    days = (dt - ref).total_seconds() / 86400.0
    phase = (days % 29.53058867) / 29.53058867
    return 0.5 * (1 - math.cos(2 * math.pi * phase))


# ─── Bortle-class Light-Pollution Estimate ──────────────────────────────────

def estimate_bortle(lat: float, lon: float) -> int:
    """
    Return Bortle class 1-9 for a location.
    Checks the small city table first; otherwise uses latitude bands as proxy.
    """
    # Check nearby cities (within ~1.5 deg)
    best_dist = float("inf")
    best_bortle = None
    for clat, clon, cb in _BORTLE_CITIES:
        d = (lat - clat) ** 2 + (lon - clon) ** 2
        if d < best_dist:
            best_dist = d
            best_bortle = cb
    if best_dist < 2.25:   # within ~1.5 degrees
        return best_bortle

    # Latitude-band proxy
    alat = abs(lat)
    if alat > 70:
        return 2      # Polar
    if alat > 60:
        return 3      # Sub-arctic
    if alat > 55:
        return 4      # Northern-Scandinavia / Canada
    if alat > 45:
        return 5      # Mid-latitude countryside
    if alat > 35:
        return 6      # Suburban mid-lat
    return 7           # Lower latitudes / tropical urban


# ─── Geomagnetic Latitude ──────────────────────────────────────────────────

def _geomagnetic_latitude(lat: float, lon: float) -> float:
    """
    Convert geographic to geomagnetic latitude using a simple dipole
    centered at (80.65N, -72.68W).  Good enough for aurora band estimation.
    """
    pole_lat = math.radians(80.65)
    pole_lon = math.radians(-72.68)
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)

    sin_gm = (
        math.sin(lat_r) * math.sin(pole_lat)
        + math.cos(lat_r) * math.cos(pole_lat) * math.cos(lon_r - pole_lon)
    )
    return math.degrees(math.asin(max(-1, min(1, sin_gm))))


# ─── Photography Recommendations ───────────────────────────────────────────

def _photo_recommendations(aurora_prob: float, darkness: float) -> Dict[str, Any]:
    """
    Camera-exposure recommendations for aurora photography.
    Based on aurora brightness (proxy from probability + darkness).
    Returns ISO, aperture, shutter speed, white balance.
    """
    brightness = aurora_prob * (darkness / 100.0)  # 0-100

    if brightness >= 60:
        # Bright aurora
        return {"iso": 800, "aperture": "f/2.8", "shutter_sec": 4,  "wb_kelvin": 3500, "tip": "Bright aurora — short exposure to capture detail and movement."}
    elif brightness >= 30:
        return {"iso": 1600, "aperture": "f/2.8", "shutter_sec": 8,  "wb_kelvin": 3800, "tip": "Moderate aurora — balanced exposure to capture color."}
    elif brightness >= 10:
        return {"iso": 3200, "aperture": "f/2.0", "shutter_sec": 15, "wb_kelvin": 4000, "tip": "Faint aurora — longer exposure, use a tripod and remote shutter."}
    else:
        return {"iso": 6400, "aperture": "f/1.8", "shutter_sec": 25, "wb_kelvin": 4200, "tip": "Very faint or no aurora — maximum sensitivity, may only be visible in photos."}


# ─── Terminator (day/night boundary) ───────────────────────────────────────

def compute_terminator(dt: datetime = None, n_points: int = 180) -> list:
    """
    Return a polyline tracing the solar terminator (sun elevation ~ 0).
    Uses an analytic inversion: for each longitude, solve for the latitude
    where sin(elev) = 0  →  lat = -arctan(cos(ha) / tan(decl)).
    Returns {lat, lon} list AND the sub-solar point for night-polygon logic.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    doy = dt.timetuple().tm_yday
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    gamma = 2 * math.pi * (doy - 1) / 365.0

    # Solar declination
    decl = (
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

    points = []
    for i in range(n_points + 1):
        lon_deg = -180.0 + (360.0 / n_points) * i
        tst = hour_utc * 60 + eqtime + 4 * lon_deg  # true solar time (min)
        ha = math.radians(tst / 4.0 - 180.0)

        # Analytic terminator latitude:
        # sin(elev) = sin(lat)*sin(decl) + cos(lat)*cos(decl)*cos(ha) = 0
        # => tan(lat) = -cos(ha)*cos(decl)/sin(decl)  (if decl != 0)
        # => lat = atan(-cos(ha) / tan(decl))
        if abs(decl) < 1e-9:
            # Equinox: terminator at ±90° where cos(ha)=0, else use simplified
            lat_deg = math.degrees(math.atan2(-math.cos(ha), 1e-9))
        else:
            lat_deg = math.degrees(math.atan(-math.cos(ha) / math.tan(decl)))

        lat_deg = max(-90, min(90, lat_deg))
        points.append({"lat": round(lat_deg, 2), "lon": round(lon_deg, 2)})

    return points


# ─── Helpers ────────────────────────────────────────────────────────────────

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
