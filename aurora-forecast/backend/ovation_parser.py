"""
OVATION Aurora Probability Grid Parser
Fetches the latest OVATION aurora forecast from NOAA and converts it
into a list of (lat, lon, probability) points for map rendering.
"""

import requests
from typing import List, Dict, Any

OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
TIMEOUT = 15


def fetch_ovation_data() -> Dict[str, Any]:
    """Fetch and parse the OVATION aurora probability grid from NOAA."""
    try:
        resp = requests.get(OVATION_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return _parse_ovation(data)
    except Exception:
        return {"observation_time": None, "forecast_time": None, "points": []}


def _parse_ovation(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the OVATION JSON into structured aurora probability data."""
    observation_time = data.get("Observation Time", None)
    forecast_time = data.get("Forecast Time", None)
    coordinates = data.get("coordinates", [])

    points: List[Dict[str, float]] = []
    for entry in coordinates:
        lon = entry[0]
        lat = entry[1]
        prob = entry[2]
        # Only include points with non-zero probability for performance
        if prob > 2:
            # NOAA reports longitude 0-360, convert to -180 to 180
            if lon > 180:
                lon = lon - 360
            points.append({
                "lat": lat,
                "lon": lon,
                "prob": prob,
            })

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
    Get approximate aurora probability at a given lat/lon.
    Uses nearest-neighbor interpolation from the OVATION grid.
    """
    if grid is None:
        grid = fetch_ovation_data()

    points = grid.get("points", [])
    if not points:
        return 0.0

    best_prob = 0.0
    best_dist_sq = float("inf")

    for p in points:
        dlat = p["lat"] - lat
        dlon = p["lon"] - lon
        dist_sq = dlat * dlat + dlon * dlon
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_prob = p["prob"]

    # If the nearest point is more than ~3 degrees away, probability is negligible
    if best_dist_sq > 9.0:
        return 0.0

    return min(best_prob, 100.0)
