"""
Data Scheduler Module
Uses APScheduler to poll NOAA and cache latest solar wind + aurora data every 60 seconds.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
from typing import Dict, Any

from solar_wind import get_solar_wind_data
from ovation_parser import get_aurora_grid
from aurora_alerts import evaluate_alerts

# Global cache for latest fetched data
_cache: Dict[str, Any] = {
    "solar_wind": None,
    "aurora_grid": None,
    "alerts": None,
    "last_updated": None,
}

_scheduler: BackgroundScheduler = None


def get_cache() -> Dict[str, Any]:
    """Return a reference to the current data cache."""
    return _cache


def refresh_data():
    """Fetch all data sources and update the cache."""
    try:
        solar_wind = get_solar_wind_data()
        _cache["solar_wind"] = solar_wind
    except Exception:
        pass

    try:
        aurora_grid = get_aurora_grid()
        _cache["aurora_grid"] = aurora_grid
    except Exception:
        pass

    try:
        if _cache["solar_wind"] is not None:
            alerts = evaluate_alerts(_cache["solar_wind"])
            _cache["alerts"] = alerts
    except Exception:
        pass

    _cache["last_updated"] = datetime.now(timezone.utc).isoformat()


def start_scheduler():
    """Start the background scheduler that polls data every 60 seconds."""
    global _scheduler
    if _scheduler is not None:
        return  # Already running

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(refresh_data, "interval", seconds=60, id="refresh_all", max_instances=1)
    _scheduler.start()

    # Run an immediate first fetch
    refresh_data()


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
