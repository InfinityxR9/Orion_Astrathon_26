"""
OVATION Aurora Probability Grid Parser
Fetches the latest OVATION aurora forecast from NOAA and converts it
into a list of (lat, lon, probability) points for map rendering.
Uses numpy for fast nearest-neighbor lookups.
"""

import requests
import numpy as np
from typing import Dict, Any, Optional

OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
TIMEOUT = 15

# Cached numpy arrays for fast lookup
_grid_lats: Optional[np.ndarray] = None
_grid_lons: Optional[np.ndarray] = None
_grid_probs: Optional[np.ndarray] = None


def fetch_ovation_data() -> Dict[str, Any]:
    """Fetch and parse the OVATION aurora probability grid from NOAA."""
    global _grid_lats, _grid_lons, _grid_probs
    try:
        resp = requests.get(OVATION_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        result = _parse_ovation(data)
        # Build fast-lookup arrays from ALL coordinates (not just filtered)
        coords = data.get("coordinates", [])
        if coords:
            arr = np.array(coords, dtype=np.float32)
            raw_lons = arr[:, 0].copy()
            raw_lons[raw_lons > 180] -= 360
            _grid_lats = arr[:, 1]
            _grid_lons = raw_lons
            _grid_probs = arr[:, 2]
        return result
    except Exception:
        return {"observation_time": None, "forecast_time": None, "points": [], "point_count": 0}


def _parse_ovation(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the OVATION JSON into structured aurora probability data."""
    observation_time = data.get("Observation Time", None)
    forecast_time = data.get("Forecast Time", None)
    coordinates = data.get("coordinates", [])

    points = []
    for entry in coordinates:
        lon = entry[0]
        lat = entry[1]
        prob = entry[2]
        if prob < 2:
            continue
        if lon > 180:
            lon -= 360
        points.append({"lat": lat, "lon": lon, "prob": prob})

    return {
        "observation_time": observation_time,
        "forecast_time": forecast_time,
        "point_count": len(points),
        "points": points,
    }


def get_aurora_grid() -> Dict[str, Any]:
    """Public API: get the current aurora probability grid."""
    return fetch_ovation_data()


def get_aurora_probability_at(lat: float, lon: float, grid: Dict[str, Any] = None) -> float:
    """
    Get aurora probability at a given lat/lon using numpy vectorized
    nearest-neighbor search with cosine-corrected longitude distance.
    Falls back to brute-force if numpy arrays haven't been built yet.
    """
    global _grid_lats, _grid_lons, _grid_probs

    # Fast path: numpy arrays available
    if _grid_lats is not None and len(_grid_lats) > 0:
        cos_lat = np.cos(np.radians(lat))
        dlat = _grid_lats - lat
        dlon = (_grid_lons - lon)
        # wrap longitude difference to [-180, 180]
        dlon = (dlon + 180) % 360 - 180
        dist_sq = dlat ** 2 + (dlon * cos_lat) ** 2
        idx = np.argmin(dist_sq)
        if dist_sq[idx] > 9.0:  # >~3 deg away
            return 0.0
        return float(min(_grid_probs[idx], 100.0))

    # Slow fallback: iterate grid dict
    if grid is None:
        grid = fetch_ovation_data()
    points = grid.get("points", [])
    if not points:
        return 0.0

    cos_lat = np.cos(np.radians(lat))
    best_prob = 0.0
    best_dist_sq = float("inf")
    for p in points:
        dlat = p["lat"] - lat
        dlon = ((p["lon"] - lon) + 180) % 360 - 180
        dist_sq = dlat ** 2 + (dlon * cos_lat) ** 2
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_prob = p["prob"]
    if best_dist_sq > 9.0:
        return 0.0
    return min(best_prob, 100.0)
