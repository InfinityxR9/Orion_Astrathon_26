"""
Solar Wind Data Ingestion Module
Fetches real-time solar wind magnetic field (Bz) and plasma (speed, density) data from NOAA SWPC.
"""

import requests
from datetime import datetime
from typing import Optional

MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"

TIMEOUT = 10


def fetch_magnetic_field() -> dict:
    """Fetch solar wind magnetic field data. Returns latest Bz, Bt, Bx, By."""
    try:
        resp = requests.get(MAG_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        # rows[0] is a header: ["time_tag","bx_gsm","by_gsm","bz_gsm","lon_gsm","lat_gsm","bt"]
        # Iterate from the end to find the latest row with valid Bz
        for row in reversed(rows[1:]):
            time_tag = row[0]
            bx = _safe_float(row[1])
            by = _safe_float(row[2])
            bz = _safe_float(row[3])
            bt = _safe_float(row[6])
            if bz is not None:
                return {
                    "time_tag": time_tag,
                    "bx_gsm": bx,
                    "by_gsm": by,
                    "bz_gsm": bz,
                    "bt": bt,
                }
        return _empty_mag()
    except Exception:
        return _empty_mag()


def fetch_plasma() -> dict:
    """Fetch solar wind plasma data. Returns latest speed, density, temperature."""
    try:
        resp = requests.get(PLASMA_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        # header: ["time_tag","density","speed","temperature"]
        for row in reversed(rows[1:]):
            time_tag = row[0]
            density = _safe_float(row[1])
            speed = _safe_float(row[2])
            temperature = _safe_float(row[3])
            if speed is not None:
                return {
                    "time_tag": time_tag,
                    "density": density,
                    "speed": speed,
                    "temperature": temperature,
                }
        return _empty_plasma()
    except Exception:
        return _empty_plasma()


def get_solar_wind_data() -> dict:
    """Combined solar wind data: magnetic field + plasma."""
    mag = fetch_magnetic_field()
    plasma = fetch_plasma()
    return {
        "magnetic_field": mag,
        "plasma": plasma,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _safe_float(val) -> Optional[float]:
    try:
        f = float(val)
        return f
    except (TypeError, ValueError):
        return None


def _empty_mag() -> dict:
    return {
        "time_tag": None,
        "bx_gsm": None,
        "by_gsm": None,
        "bz_gsm": None,
        "bt": None,
    }


def _empty_plasma() -> dict:
    return {
        "time_tag": None,
        "density": None,
        "speed": None,
        "temperature": None,
    }
