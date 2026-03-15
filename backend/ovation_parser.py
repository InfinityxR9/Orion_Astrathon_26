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

# Cached numpy arrays for fast lookup stored as a single tuple for atomic read/write.
# The background scheduler thread writes; the async request handlers read.
# A single name rebind in CPython is atomic under the GIL, preventing partial views.
_grid_data: Optional[tuple] = None  # (lats_array, lons_array, probs_array) | None


def fetch_ovation_data() -> Dict[str, Any]:
    """Fetch and parse the OVATION aurora probability grid from NOAA."""
    global _grid_data
    try:
        resp = requests.get(OVATION_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        result = _parse_ovation(data)
        # Build fast-lookup arrays from ALL coordinates (not just filtered).
        # Assigned as a single tuple so readers always see a consistent snapshot.
        coords = data.get("coordinates", [])
        if coords:
            arr = np.array(coords, dtype=np.float32)
            raw_lons = arr[:, 0].copy()
            raw_lons[raw_lons > 180] -= 360
            _grid_data = (arr[:, 1], raw_lons, arr[:, 2])  # (lats, lons, probs)
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
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        try:
            lon = float(entry[0])
            lat = float(entry[1])
            prob = float(entry[2])
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0):
            continue
        prob = max(0.0, min(prob, 100.0))
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
    return get_aurora_lookup_diagnostics(lat, lon, grid=grid)["probability"]


def get_aurora_lookup_diagnostics(
    lat: float,
    lon: float,
    grid: Dict[str, Any] = None,
) -> Dict[str, float | str]:
    """
    Return nearest-point lookup diagnostics for transparency in scoring payloads.
    """
    distance_cutoff_deg = 3.0

    # Take a single reference so readers see a consistent snapshot.
    snapshot = _grid_data

    if snapshot is not None and len(snapshot[0]) > 0:
        grid_lats, grid_lons, grid_probs = snapshot
        cos_lat = np.cos(np.radians(lat))
        dlat = grid_lats - lat
        dlon = (grid_lons - lon)
        dlon = (dlon + 180) % 360 - 180
        dist_sq = dlat ** 2 + (dlon * cos_lat) ** 2
        idx = int(np.argmin(dist_sq))
        nearest_distance_deg = float(np.sqrt(float(dist_sq[idx])))
        if dist_sq[idx] > distance_cutoff_deg ** 2:
            return {
                "probability": 0.0,
                "nearest_distance_deg": round(nearest_distance_deg, 3),
                "distance_cutoff_deg": distance_cutoff_deg,
                "lookup_method": "nearest_neighbor_numpy",
            }
        return {
            "probability": float(min(grid_probs[idx], 100.0)),
            "nearest_distance_deg": round(nearest_distance_deg, 3),
            "distance_cutoff_deg": distance_cutoff_deg,
            "lookup_method": "nearest_neighbor_numpy",
        }

    if grid is None:
        grid = fetch_ovation_data()
    points = grid.get("points", [])
    if not points:
        return {
            "probability": 0.0,
            "nearest_distance_deg": None,
            "distance_cutoff_deg": distance_cutoff_deg,
            "lookup_method": "nearest_neighbor_grid_fallback",
        }

    cos_lat = np.cos(np.radians(lat))
    best_prob = 0.0
    best_dist_sq = float("inf")
    for point in points:
        dlat = point["lat"] - lat
        dlon = ((point["lon"] - lon) + 180) % 360 - 180
        dist_sq = dlat ** 2 + (dlon * cos_lat) ** 2
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_prob = point["prob"]

    nearest_distance_deg = float(np.sqrt(best_dist_sq))
    if best_dist_sq > distance_cutoff_deg ** 2:
        return {
            "probability": 0.0,
            "nearest_distance_deg": round(nearest_distance_deg, 3),
            "distance_cutoff_deg": distance_cutoff_deg,
            "lookup_method": "nearest_neighbor_grid_fallback",
        }
    return {
        "probability": float(min(best_prob, 100.0)),
        "nearest_distance_deg": round(nearest_distance_deg, 3),
        "distance_cutoff_deg": distance_cutoff_deg,
        "lookup_method": "nearest_neighbor_grid_fallback",
    }
