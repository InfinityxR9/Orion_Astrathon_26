"""
Solar Wind Data Ingestion Module
Fetches real-time solar wind magnetic field (Bz) and plasma (speed, density) data
from NOAA SWPC with DSCOVR primary / ACE failover.
Maintains a Bz history ring-buffer for substorm dBz/dt detection.
"""

import requests
import math
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# ─── NOAA SWPC endpoints ────────────────────────────────────────────────────
DSCOVR_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
DSCOVR_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"
ACE_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-2-hour.json"
ACE_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json"

TIMEOUT = 12

# Ring-buffer: keep last 30 minutes of Bz samples (one per ~60 s ≈ 30 entries)
_bz_history: deque = deque(maxlen=60)


def get_bz_history() -> List[Dict[str, Any]]:
    """Return the Bz ring-buffer as a list for substorm-rate analysis."""
    return list(_bz_history)


def get_solar_wind_data() -> Dict[str, Any]:
    """
    Combined solar wind data: magnetic field + plasma.
    Tries DSCOVR 1-day first, falls back to ACE 2-hour on failure.
    """
    mag, mag_source = _fetch_mag_with_failover()
    plasma, plasma_source = _fetch_plasma_with_failover()

    # Compute data-gap flag
    data_gap = _detect_data_gap(mag, plasma)

    # Push Bz into ring-buffer for substorm dBz/dt
    bz = mag.get("bz_gsm")
    if bz is not None:
        _bz_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "bz": bz,
        })

    # Compute dBz/dt (nT per minute) from ring buffer
    dbz_dt = _compute_dbz_dt()

    return {
        "magnetic_field": mag,
        "plasma": plasma,
        "source": {"mag": mag_source, "plasma": plasma_source},
        "data_gap": data_gap,
        "dbz_dt": dbz_dt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Magnetic field ─────────────────────────────────────────────────────────

def _fetch_mag_with_failover():
    """Try DSCOVR mag, fall back to ACE."""
    mag = _fetch_mag_data(DSCOVR_MAG_URL)
    if mag["bz_gsm"] is not None:
        return mag, "DSCOVR"
    mag = _fetch_mag_data(ACE_MAG_URL)
    if mag["bz_gsm"] is not None:
        return mag, "ACE"
    return _empty_mag(), "unavailable"


def _fetch_mag_data(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        # Header row then data rows
        for row in reversed(rows[1:]):
            bz = _safe_float(row[3])
            if bz is not None:
                return {
                    "time_tag": row[0],
                    "bx_gsm": _safe_float(row[1]),
                    "by_gsm": _safe_float(row[2]),
                    "bz_gsm": bz,
                    "bt": _safe_float(row[6]),
                }
        return _empty_mag()
    except Exception:
        return _empty_mag()


# ─── Plasma ─────────────────────────────────────────────────────────────────

def _fetch_plasma_with_failover():
    """Try DSCOVR plasma, fall back to ACE."""
    plasma = _fetch_plasma_data(DSCOVR_PLASMA_URL)
    if plasma["speed"] is not None:
        return plasma, "DSCOVR"
    plasma = _fetch_plasma_data(ACE_PLASMA_URL)
    if plasma["speed"] is not None:
        return plasma, "ACE"
    return _empty_plasma(), "unavailable"


def _fetch_plasma_data(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        for row in reversed(rows[1:]):
            speed = _safe_float(row[2])
            if speed is not None:
                return {
                    "time_tag": row[0],
                    "density": _safe_float(row[1]),
                    "speed": speed,
                    "temperature": _safe_float(row[3]),
                }
        return _empty_plasma()
    except Exception:
        return _empty_plasma()


# ─── Substorm dBz/dt computation ────────────────────────────────────────────

def _compute_dbz_dt() -> Optional[float]:
    """
    Compute rate of change of Bz (nT/min) over last 10 samples using
    actual timestamp deltas from the ring buffer.
    Negative = Bz turning more southward = substorm precursor.
    """
    if len(_bz_history) < 5:
        return None
    recent = list(_bz_history)[-10:]
    valid = [(s["time"], s["bz"]) for s in recent if s["bz"] is not None]
    if len(valid) < 3:
        return None
    try:
        t0 = datetime.fromisoformat(valid[0][0])
        t1 = datetime.fromisoformat(valid[-1][0])
        elapsed_min = (t1 - t0).total_seconds() / 60.0
        if elapsed_min < 0.1:
            return None
        rate = (valid[-1][1] - valid[0][1]) / elapsed_min
        return round(rate, 3)
    except Exception:
        return None


# ─── Data-gap detection ─────────────────────────────────────────────────────

_NOAA_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",  # DSCOVR 1-day feed (has microseconds)
    "%Y-%m-%d %H:%M:%S",     # ACE 2-hour feed (no microseconds)
)


def _detect_data_gap(mag: dict, plasma: dict) -> bool:
    """Flag if either source has stale or missing data (>5 min old)."""
    now = datetime.now(timezone.utc)
    for src in [mag, plasma]:
        tag = src.get("time_tag")
        if tag is None:
            return True
        parsed = None
        for fmt in _NOAA_TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(tag, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        # Treat an unparseable timestamp the same as missing data
        if parsed is None or (now - parsed).total_seconds() > 300:
            return True
    return False


# ─── Helpers ────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _empty_mag() -> dict:
    return {"time_tag": None, "bx_gsm": None, "by_gsm": None, "bz_gsm": None, "bt": None}


def _empty_plasma() -> dict:
    return {"time_tag": None, "density": None, "speed": None, "temperature": None}
