"""
Aurora Visibility Engine
Computes a composite visibility score (0-100) combining:
  - Aurora probability from OVATION model
  - Sky darkness (solar elevation, moon phase, Bortle-class light pollution)
  - Cloud cover / atmospheric clarity

Also provides photography camera-settings recommendations.

Formula:
    A_norm = min(aurora_probability / 30, 1.0)
    D = sky_darkness / 100
    C = cloud_clarity / 100

    visibility_score = 100 * A_norm * sqrt(D) * sqrt(C)
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

from ovation_parser import get_aurora_probability_at, get_aurora_lookup_diagnostics
from weather import fetch_weather

# Minimal city lookup for rough Bortle estimation.
_BORTLE_CITIES = [
    (40.71, -74.01, 9),   # New York
    (51.51, -0.13, 9),    # London
    (48.86, 2.35, 9),     # Paris
    (35.68, 139.69, 9),   # Tokyo
    (55.76, 37.62, 8),    # Moscow
    (28.61, 77.21, 8),    # Delhi
    (39.91, 116.40, 8),   # Beijing
    (34.05, -118.24, 8),  # Los Angeles
    (41.88, -87.63, 8),   # Chicago
    (37.77, -122.42, 8),  # San Francisco
    (52.52, 13.41, 8),    # Berlin
    (59.33, 18.07, 7),    # Stockholm
    (60.17, 24.94, 7),    # Helsinki
    (63.43, 10.40, 5),    # Trondheim
    (64.15, -21.94, 5),   # Reykjavik
    (69.65, 18.96, 4),    # Tromso
    (78.23, 15.65, 2),    # Longyearbyen
    (68.35, 14.40, 3),    # Lofoten
    (66.50, 25.73, 3),    # Sodankyla
    (64.84, -18.08, 2),   # Iceland interior
    (62.46, -114.37, 3),  # Yellowknife
    (61.22, -149.90, 5),  # Anchorage
    (64.84, -147.72, 4),  # Fairbanks
]

VISIBILITY_MODEL_ID = "v3_linear_aurora_sqrt_darkness_sqrt_cloud"
PREVIOUS_VISIBILITY_MODEL_ID = "v2_linear_aurora_weighted_darkness_cloud"


def compute_visibility(lat: float, lon: float, aurora_grid=None) -> Dict[str, Any]:
    """
    Compute the full aurora visibility score for a given location.
    Returns composite score, sub-scores, and photography recommendations.
    """
    now_utc = datetime.now(timezone.utc)
    lookup = get_aurora_lookup_diagnostics(lat, lon, grid=aurora_grid)
    aurora_prob = lookup["probability"]
    weather_data = fetch_weather(lat, lon)
    darkness = compute_darkness_score(lat, lon, now_utc)
    return _build_visibility_payload(
        lat=lat,
        lon=lon,
        aurora_prob=aurora_prob,
        aurora_lookup=lookup,
        weather_data=weather_data,
        darkness=darkness,
        now_utc=now_utc,
    )


def find_better_viewing_spot(
    lat: float,
    lon: float,
    aurora_grid=None,
    search_radius_km: float = 180.0,
    min_improvement: float = 15.0,
    ring_step_km: float = 30.0,
    bearings_per_ring: int = 12,
    max_weather_checks_per_ring: int = 4,
) -> Dict[str, Any]:
    """
    Search outward in distance rings and return the nearest location whose
    visibility score beats the origin by a meaningful margin.
    """
    origin_lon = _normalize_longitude(lon)
    started = time.perf_counter()
    search_time = datetime.now(timezone.utc)
    origin_visibility = compute_visibility(lat, origin_lon, aurora_grid=aurora_grid)
    target_score = origin_visibility["visibility_score"] + min_improvement

    screened_candidates = 0
    evaluated_candidates = 0
    near_miss = None
    recommendation = None

    for radius_km in _build_search_rings(search_radius_km, ring_step_km):
        ring_matches = []
        weather_queue = []
        for bearing_deg in _build_bearings(bearings_per_ring):
            cand_lat, cand_lon = _destination_point(lat, origin_lon, radius_km, bearing_deg)
            aurora_prob = get_aurora_probability_at(cand_lat, cand_lon, grid=aurora_grid)
            darkness = compute_darkness_score(cand_lat, cand_lon, search_time)
            screened_candidates += 1

            # Skip points that cannot clear the requested gain even under clear sky.
            best_case_score = _compute_visibility_score(
                aurora_prob=aurora_prob,
                sky_darkness=darkness["darkness_score"],
                cloud_clarity=100.0,
            )
            if best_case_score < target_score:
                continue

            weather_queue.append({
                "lat": cand_lat,
                "lon": cand_lon,
                "aurora_prob": aurora_prob,
                "darkness": darkness,
                "best_case_score": best_case_score,
                "bearing_deg": bearing_deg,
            })

        if weather_queue:
            weather_queue.sort(key=lambda item: item["best_case_score"], reverse=True)

        selected_seeds = weather_queue[:max_weather_checks_per_ring]
        weather_results = []
        if selected_seeds:
            with ThreadPoolExecutor(max_workers=min(len(selected_seeds), max_weather_checks_per_ring)) as pool:
                future_map = {
                    pool.submit(fetch_weather, seed["lat"], seed["lon"]): seed
                    for seed in selected_seeds
                }
                for future in as_completed(future_map):
                    seed = future_map[future]
                    try:
                        weather_data = future.result()
                    except Exception:
                        weather_data = fetch_weather(seed["lat"], seed["lon"])
                    weather_results.append((seed, weather_data))

        for seed, weather_data in weather_results:
            candidate_visibility = _build_visibility_payload(
                lat=seed["lat"],
                lon=seed["lon"],
                aurora_prob=seed["aurora_prob"],
                aurora_lookup=None,
                weather_data=weather_data,
                darkness=seed["darkness"],
                now_utc=search_time,
            )
            evaluated_candidates += 1

            improvement = round(
                candidate_visibility["visibility_score"] - origin_visibility["visibility_score"],
                1,
            )
            candidate = _summarize_location(candidate_visibility)
            candidate.update({
                "distance_km": round(radius_km, 1),
                "bearing_deg": round(seed["bearing_deg"], 1),
                "direction": _bearing_to_cardinal(seed["bearing_deg"]),
                "improvement": improvement,
            })

            if improvement >= min_improvement:
                candidate["reason"] = _build_recommendation_reason(
                    origin_visibility,
                    candidate_visibility,
                    candidate["direction"],
                )
                ring_matches.append(candidate)
                continue

            if near_miss is None or improvement > near_miss["improvement"]:
                near_miss = candidate

        if ring_matches:
            recommendation = max(
                ring_matches,
                key=lambda item: (
                    item["improvement"],
                    item["visibility_score"],
                    item["cloud_score"],
                ),
            )
            break

    response = {
        "timestamp": search_time.isoformat(),
        "processing_ms": round((time.perf_counter() - started) * 1000, 1),
        "origin": _summarize_location(origin_visibility),
        "search_radius_km": round(search_radius_km, 1),
        "min_improvement": round(min_improvement, 1),
        "screened_candidates": screened_candidates,
        "evaluated_candidates": evaluated_candidates,
        "screen_rejections": screened_candidates - evaluated_candidates,
        "weather_checks_per_ring_limit": max_weather_checks_per_ring,
        "found_better_spot": recommendation is not None,
        "destination": recommendation,
    }

    if recommendation is None:
        response["message"] = _build_no_recommendation_message(
            origin_visibility=origin_visibility,
            near_miss=near_miss,
            search_radius_km=search_radius_km,
            min_improvement=min_improvement,
        )
        return response

    response["processing_ms"] = round((time.perf_counter() - started) * 1000, 1)
    response["message"] = (
        f"Nearest meaningful improvement is {recommendation['distance_km']:.0f} km "
        f"{recommendation['direction']} with a +{recommendation['improvement']:.0f} score gain."
    )
    return response


def build_aurora_overlay_grid(
    aurora_grid: Dict[str, Any],
    cloud_clarity_baseline: float = 70.0,
) -> Dict[str, Any]:
    """
    Build map-overlay values from the OVATION grid.

    The overlay uses the softer heat formula with per-cell darkness and a
    neutral cloud-clarity baseline so the regional oval stays visible without
    requiring a live weather call for every global grid cell.
    """
    now_utc = datetime.now(timezone.utc)
    points = []

    for point in aurora_grid.get("points", []):
        darkness = compute_darkness_score(point["lat"], point["lon"], now_utc)
        heat_value = _compute_heat_value(
            aurora_prob=point["prob"],
            sky_darkness=darkness["darkness_score"],
            cloud_clarity=cloud_clarity_baseline,
        )
        points.append({
            "lat": point["lat"],
            "lon": point["lon"],
            "prob": point["prob"],
            "heat_value": heat_value,
        })

    return {
        "observation_time": aurora_grid.get("observation_time"),
        "forecast_time": aurora_grid.get("forecast_time"),
        "point_count": len(points),
        "overlay_cloud_clarity_baseline": cloud_clarity_baseline,
        "points": points,
    }


def _compute_visibility_score(
    aurora_prob: float,
    sky_darkness: float,
    cloud_clarity: float,
) -> float:
    """
    Compute the normalized multiplicative visibility score.

    A_norm = min(aurora_probability / 30, 1.0)
    D = sky_darkness / 100
    C = cloud_clarity / 100

    visibility_score = 100 * A_norm * sqrt(D) * sqrt(C)

    This ensures:
    - Aurora probability is still the dominant multiplicative driver.
    - Darkness acts as a low-light requirement through sqrt(D).
    - Cloud clarity stays a strong multiplicative veto through sqrt(C).
    """
    aurora_norm = min(max(aurora_prob, 0.0), 30.0) / 30.0
    darkness_norm = _normalize_score(sky_darkness)
    cloud_norm = _normalize_score(cloud_clarity)

    visibility_score = 100.0 * aurora_norm * math.sqrt(darkness_norm) * math.sqrt(cloud_norm)
    return round(min(max(visibility_score, 0.0), 100.0), 1)


def _compute_previous_visibility_score(
    aurora_prob: float,
    sky_darkness: float,
    cloud_clarity: float,
) -> float:
    """Previous model kept for side-by-side comparison in API responses."""
    aurora_norm = min(max(aurora_prob, 0.0), 30.0) / 30.0
    darkness_norm = _normalize_score(sky_darkness)
    cloud_norm = _normalize_score(cloud_clarity)

    visibility_score = 100.0 * aurora_norm * (
        0.5 * (darkness_norm ** 2) + 0.5 * (cloud_norm ** 3)
    )
    return round(min(max(visibility_score, 0.0), 100.0), 1)


def _compute_heat_value(
    aurora_prob: float,
    sky_darkness: float,
    cloud_clarity: float,
) -> float:
    """
    Compute the softer regional map-rendering metric.

    heat_value = 100 * A^1.25 * (0.75 + 0.15 * D + 0.10 * C)
    """
    aurora_norm = _normalize_score(aurora_prob)
    darkness_norm = _normalize_score(sky_darkness)
    cloud_norm = _normalize_score(cloud_clarity)

    heat_value = 100.0 * (aurora_norm ** 1.25) * (
        0.75 + 0.15 * darkness_norm + 0.10 * cloud_norm
    )
    return round(min(max(heat_value, 0.0), 100.0), 1)


def _build_visibility_payload(
    lat: float,
    lon: float,
    aurora_prob: float,
    aurora_lookup: Dict[str, Any] | None,
    weather_data: Dict[str, Any],
    darkness: Dict[str, Any],
    now_utc: datetime,
) -> Dict[str, Any]:
    """Build the public visibility payload from precomputed components."""
    lookup = aurora_lookup or {
        "lookup_method": "nearest_neighbor_unknown",
        "nearest_distance_deg": None,
        "distance_cutoff_deg": 3.0,
    }
    cloud_score = weather_data["cloud_score"] * 100.0
    geomag_lat = _geomagnetic_latitude(lat, lon)
    aurora_norm = min(max(aurora_prob, 0.0), 30.0) / 30.0
    darkness_norm = _normalize_score(darkness["darkness_score"])
    cloud_norm = _normalize_score(cloud_score)

    visibility_score = _compute_visibility_score(
        aurora_prob=aurora_prob,
        sky_darkness=darkness["darkness_score"],
        cloud_clarity=cloud_score,
    )
    previous_visibility_score = _compute_previous_visibility_score(
        aurora_prob=aurora_prob,
        sky_darkness=darkness["darkness_score"],
        cloud_clarity=cloud_score,
    )
    rating = _score_to_rating(visibility_score)
    photo = _photo_recommendations(aurora_prob, darkness["darkness_score"])

    return {
        "lat": lat,
        "lon": _normalize_longitude(lon),
        "visibility_score": visibility_score,
        "visibility_model": VISIBILITY_MODEL_ID,
        "previous_visibility_model": PREVIOUS_VISIBILITY_MODEL_ID,
        "previous_visibility_score": previous_visibility_score,
        "score_delta_vs_previous": round(visibility_score - previous_visibility_score, 1),
        "score_components": {
            "aurora_norm": round(aurora_norm, 3),
            "darkness_norm": round(darkness_norm, 3),
            "cloud_norm": round(cloud_norm, 3),
        },
        "rating": rating,
        "aurora_probability": round(aurora_prob, 1),
        "aurora_lookup": {
            "lookup_method": lookup["lookup_method"],
            "nearest_distance_deg": lookup["nearest_distance_deg"],
            "distance_cutoff_deg": lookup["distance_cutoff_deg"],
        },
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


def compute_darkness_score(lat: float, lon: float, dt: datetime) -> Dict[str, Any]:
    """
    Darkness score from solar elevation, moon illumination, and Bortle light
    pollution class.
    """
    sun_elev = solar_elevation(lat, lon, dt)
    moon_illum = moon_illumination(dt)
    bortle = estimate_bortle(lat, lon)

    if sun_elev < -18:
        solar_score = 100.0
    elif sun_elev < -12:
        solar_score = 70.0 + 30.0 * ((-12 - sun_elev) / 6.0)
    elif sun_elev < -6:
        solar_score = 30.0 + 40.0 * ((-6 - sun_elev) / 6.0)
    elif sun_elev < 0:
        solar_score = 5.0 + 25.0 * (-sun_elev / 6.0)
    else:
        solar_score = 0.0

    moon_penalty = moon_illum * 30.0
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


def _solar_params(dt: datetime) -> tuple:
    """
    Return (gamma, declination_rad, eqtime_minutes) for a given UTC datetime.
    Shared by solar_elevation() and compute_terminator() to avoid duplication.
    """
    doy = dt.timetuple().tm_yday
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
    return gamma, declination, eqtime


def solar_elevation(lat: float, lon: float, dt: datetime) -> float:
    """Solar elevation angle in degrees above the horizon."""
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    _, declination, eqtime = _solar_params(dt)

    tst = hour_utc * 60 + eqtime + 4 * lon
    ha_rad = math.radians(tst / 4.0 - 180.0)
    lat_rad = math.radians(lat)

    sin_elev = (
        math.sin(lat_rad) * math.sin(declination)
        + math.cos(lat_rad) * math.cos(declination) * math.cos(ha_rad)
    )
    return math.degrees(math.asin(max(-1, min(1, sin_elev))))


def moon_illumination(dt: datetime) -> float:
    """Moon illumination fraction (0=new, 1=full)."""
    ref = datetime(2024, 1, 11, 11, 57, 0, tzinfo=timezone.utc)
    days = (dt - ref).total_seconds() / 86400.0
    phase = (days % 29.53058867) / 29.53058867
    return 0.5 * (1 - math.cos(2 * math.pi * phase))


def estimate_bortle(lat: float, lon: float) -> int:
    """
    Return Bortle class 1-9 for a location.
    Checks the small city table first; otherwise uses latitude bands as proxy.
    """
    best_dist = float("inf")
    best_bortle = None
    for clat, clon, cb in _BORTLE_CITIES:
        dist = (lat - clat) ** 2 + (lon - clon) ** 2
        if dist < best_dist:
            best_dist = dist
            best_bortle = cb
    if best_dist < 2.25:
        return best_bortle

    alat = abs(lat)
    if alat > 70:
        return 2
    if alat > 60:
        return 3
    if alat > 55:
        return 4
    if alat > 45:
        return 5
    if alat > 35:
        return 6
    return 7


def _geomagnetic_latitude(lat: float, lon: float) -> float:
    """
    Convert geographic to geomagnetic latitude using a simple dipole centered at
    (80.65N, -72.68W). Good enough for aurora band estimation.
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


def _photo_recommendations(aurora_prob: float, darkness: float) -> Dict[str, Any]:
    """
    Camera-exposure recommendations for aurora photography.
    """
    brightness = aurora_prob * (darkness / 100.0)

    if brightness >= 60:
        return {
            "iso": 800,
            "aperture": "f/2.8",
            "shutter_sec": 4,
            "wb_kelvin": 3500,
            "tip": "Bright aurora - short exposure to capture detail and movement.",
        }
    if brightness >= 30:
        return {
            "iso": 1600,
            "aperture": "f/2.8",
            "shutter_sec": 8,
            "wb_kelvin": 3800,
            "tip": "Moderate aurora - balanced exposure to capture color.",
        }
    if brightness >= 10:
        return {
            "iso": 3200,
            "aperture": "f/2.0",
            "shutter_sec": 15,
            "wb_kelvin": 4000,
            "tip": "Faint aurora - longer exposure, use a tripod and remote shutter.",
        }
    return {
        "iso": 6400,
        "aperture": "f/1.8",
        "shutter_sec": 25,
        "wb_kelvin": 4200,
        "tip": "Very faint or no aurora - maximum sensitivity, may only be visible in photos.",
    }


def compute_terminator_with_sun(dt: datetime = None) -> Dict[str, Any]:
    """
    Return terminator points plus sub-solar position for the frontend
    to determine the night side of the terminator.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    pts = compute_terminator(dt)
    _, decl, eqtime = _solar_params(dt)
    sub_solar_lat = round(math.degrees(decl), 2)
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    sub_solar_lon = round(((180.0 - (hour_utc * 60 + eqtime) / 4.0) % 360) - 180, 2)
    return {
        "points": pts,
        "sub_solar_lat": sub_solar_lat,
        "sub_solar_lon": sub_solar_lon,
    }


def compute_terminator(dt: datetime = None, n_points: int = 180) -> list:
    """
    Return a polyline tracing the solar terminator (sun elevation = 0).

    For each longitude, find the latitude where:
      sin(lat)*sin(decl) + cos(lat)*cos(decl)*cos(ha) = 0
    => tan(lat) = -cos(ha)/tan(decl)

    Uses atan2 for correct quadrant handling across all seasons.
    Also returns the sub-solar latitude so the frontend can determine
    which side of the terminator is night.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    _, decl, eqtime = _solar_params(dt)

    points = []
    for idx in range(n_points + 1):
        lon_deg = -180.0 + (360.0 / n_points) * idx
        tst = hour_utc * 60 + eqtime + 4 * lon_deg
        ha = math.radians(tst / 4.0 - 180.0)

        cos_ha = math.cos(ha)
        sin_decl = math.sin(decl)
        cos_decl = math.cos(decl)

        # Terminator latitude: where solar elevation = 0
        # sin(lat)*sin(decl) + cos(lat)*cos(decl)*cos(ha) = 0
        if abs(cos_decl) < 1e-12:
            lat_deg = 0.0
        else:
            lat_rad = math.atan2(-cos_ha * cos_decl, sin_decl)
            lat_deg = math.degrees(lat_rad)

        lat_deg = max(-90, min(90, lat_deg))
        points.append({"lat": round(lat_deg, 2), "lon": round(lon_deg, 2)})

    return points


def _score_to_rating(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Moderate"
    if score >= 20:
        return "Low"
    return "Very Low"


def _normalize_score(value: float) -> float:
    """Clamp a 0-100 score into the normalized 0-1 range."""
    return min(max(value, 0.0), 100.0) / 100.0


def _normalize_longitude(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def _summarize_location(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the subset of fields needed in recommendation responses."""
    return {
        "lat": round(payload["lat"], 4),
        "lon": round(_normalize_longitude(payload["lon"]), 4),
        "visibility_score": payload["visibility_score"],
        "rating": payload["rating"],
        "aurora_probability": payload["aurora_probability"],
        "darkness_score": payload["darkness_score"],
        "cloud_score": payload["cloud_score"],
        "timestamp": payload["timestamp"],
    }


def _build_search_rings(search_radius_km: float, ring_step_km: float) -> List[float]:
    rings = []
    radius = max(10.0, ring_step_km)
    while radius < search_radius_km:
        rings.append(radius)
        radius += ring_step_km
    rings.append(search_radius_km)
    return rings


def _build_bearings(bearings_per_ring: int) -> List[float]:
    return [idx * (360.0 / bearings_per_ring) for idx in range(bearings_per_ring)]


def _destination_point(
    lat: float,
    lon: float,
    distance_km: float,
    bearing_deg: float,
) -> tuple[float, float]:
    """Project a point from origin along a bearing and distance on a sphere."""
    earth_radius_km = 6371.0088
    angular_distance = distance_km / earth_radius_km
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    bearing = math.radians(bearing_deg)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), _normalize_longitude(math.degrees(lon2))


def _bearing_to_cardinal(bearing_deg: float) -> str:
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((bearing_deg % 360.0) + 22.5) // 45.0) % len(directions)
    return directions[idx]


def _build_recommendation_reason(
    origin_visibility: Dict[str, Any],
    candidate_visibility: Dict[str, Any],
    direction: str,
) -> str:
    reasons = []
    if candidate_visibility["aurora_probability"] - origin_visibility["aurora_probability"] >= 8:
        reasons.append("higher aurora probability")
    if candidate_visibility["cloud_score"] - origin_visibility["cloud_score"] >= 10:
        reasons.append("clearer sky")
    if candidate_visibility["darkness_score"] - origin_visibility["darkness_score"] >= 10:
        reasons.append("darker sky")

    if not reasons:
        reasons.append("a stronger combined visibility balance")

    if len(reasons) == 1:
        return f"{direction} improves conditions mainly through {reasons[0]}."
    return (
        f"{direction} improves conditions through "
        f"{', '.join(reasons[:-1])} and {reasons[-1]}."
    )


def _build_no_recommendation_message(
    origin_visibility: Dict[str, Any],
    near_miss: Dict[str, Any] | None,
    search_radius_km: float,
    min_improvement: float,
) -> str:
    if near_miss and near_miss["improvement"] > 0:
        return (
            f"No spot within {search_radius_km:.0f} km clears the requested "
            f"+{min_improvement:.0f} score gain. Best nearby option is "
            f"{near_miss['distance_km']:.0f} km {near_miss['direction']} at "
            f"{near_miss['visibility_score']:.0f}, only +{near_miss['improvement']:.0f}."
        )
    if origin_visibility["visibility_score"] >= 60:
        return (
            f"No meaningfully better spot was found within {search_radius_km:.0f} km. "
            "Your current location is already competitive for viewing."
        )
    return (
        f"No meaningfully better spot was found within {search_radius_km:.0f} km. "
        "Nearby conditions appear similarly limited by aurora strength, cloud, or darkness."
    )
