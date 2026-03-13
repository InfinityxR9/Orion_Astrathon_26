"""
Data Scheduler Module
Uses APScheduler to poll NOAA/Open-Meteo and cache latest data.
Different cadences: solar wind every 30s, OVATION every 120s, Kp history every 60s.
Maintains a Kp time-series for the frontend chart.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List

from solar_wind import get_solar_wind_data
from ovation_parser import get_aurora_grid
from aurora_alerts import evaluate_alerts, estimate_kp

# ─── Global cache ───────────────────────────────────────────────────────────
_cache: Dict[str, Any] = {
    "solar_wind": None,
    "aurora_grid": None,
    "alerts": None,
    "last_updated": None,
}

# Kp time-series: keep last 4 hours (one sample per minute = 240)
_kp_history: deque = deque(maxlen=240)

_scheduler: BackgroundScheduler = None


def get_cache() -> Dict[str, Any]:
    return _cache


def get_kp_history() -> List[Dict[str, Any]]:
    return list(_kp_history)


# ─── Refresh jobs ───────────────────────────────────────────────────────────

def _refresh_solar_wind():
    """Fetch solar wind data every 30 seconds."""
    try:
        sw = get_solar_wind_data()
        _cache["solar_wind"] = sw

        # Update alerts
        alerts = evaluate_alerts(sw)
        _cache["alerts"] = alerts

        # Push Kp to time-series
        _kp_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "kp": alerts["kp_estimate"],
            "bz": sw["magnetic_field"].get("bz_gsm"),
            "speed": sw["plasma"].get("speed"),
        })

        _cache["last_updated"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass


def _refresh_aurora_grid():
    """Fetch OVATION grid every 120 seconds (it updates less frequently)."""
    try:
        grid = get_aurora_grid()
        _cache["aurora_grid"] = grid
    except Exception:
        pass


def refresh_data():
    """Full refresh for initial load."""
    _refresh_solar_wind()
    _refresh_aurora_grid()


def start_scheduler():
    """Start separate jobs at different cadences."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_refresh_solar_wind, "interval", seconds=30, id="sw",  max_instances=1)
    _scheduler.add_job(_refresh_aurora_grid, "interval", seconds=120, id="ov", max_instances=1)
    _scheduler.start()

    # Immediate first fetch
    refresh_data()


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
