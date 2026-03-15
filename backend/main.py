"""
Aurora Forecast Platform - FastAPI Backend
Endpoints: solar-wind, aurora-grid, visibility, alerts, terminator, kp-timeline,
photo-settings, community sightings, WebSocket push.
"""

import os
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from scheduler import start_scheduler, stop_scheduler, get_cache, get_kp_history
from solar_wind import get_solar_wind_data, get_bz_history
from ovation_parser import get_aurora_grid
from visibility_engine import (
    build_aurora_overlay_grid,
    compute_visibility,
    find_better_viewing_spot,
)
from aurora_alerts import evaluate_alerts

# ─── WebSocket clients ──────────────────────────────────────────────────────
_ws_clients: List[WebSocket] = []

# ─── In-memory community sightings (hackathon-scope) ────────────────────────
_sightings: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    # Background task to push updates over WebSocket
    task = asyncio.create_task(_ws_broadcast_loop())
    yield
    task.cancel()
    stop_scheduler()


app = FastAPI(
    title="Aurora Forecast Platform",
    description="Real-time aurora visibility forecasting with hyper-local scoring",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ═══════════════════════════════════════════════════════════════════════════
# Page Routes
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Aurora Forecast API running. Frontend not found."}


@app.get("/sw.js")
async def service_worker():
    """Serve the PWA service worker from the root path so it has full-app scope."""
    sw_path = os.path.join(FRONTEND_DIR, "sw.js")
    return FileResponse(sw_path, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@app.get("/manifest.json")
async def web_manifest():
    """Serve the PWA web app manifest."""
    manifest_path = os.path.join(FRONTEND_DIR, "manifest.json")
    return FileResponse(manifest_path, media_type="application/manifest+json")


# ═══════════════════════════════════════════════════════════════════════════
# Data API endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    return JSONResponse(content={"status": "healthy"})


@app.head("/health")
async def health_check_head():
    return JSONResponse(content={"status": "healthy"})

@app.get("/solar-wind")
async def solar_wind():
    cache = get_cache()
    data = cache.get("solar_wind") or get_solar_wind_data()
    return JSONResponse(content=data)


@app.get("/aurora-grid")
async def aurora_grid():
    cache = get_cache()
    raw_grid = cache.get("aurora_grid") or get_aurora_grid()
    data = build_aurora_overlay_grid(raw_grid)
    return JSONResponse(content=data)


@app.get("/visibility-score")
async def visibility_score(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    cache = get_cache()
    grid = cache.get("aurora_grid")
    result = compute_visibility(lat, lon, aurora_grid=grid)
    return JSONResponse(content=result)


@app.get("/alerts")
async def alerts(
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    threshold: float = Query(50.0, ge=0.0, le=100.0),
):
    """
    Alert endpoint with optional user-configured visibility monitor.

    If lat/lon are provided, visibility at that saved location is evaluated and
    threshold triggers are included in the response.
    """
    cache = get_cache()
    sw = cache.get("solar_wind") or get_solar_wind_data()

    visibility_score = None
    visibility_payload = None
    if lat is not None and lon is not None:
        grid = cache.get("aurora_grid")
        visibility_payload = compute_visibility(lat, lon, aurora_grid=grid)
        visibility_score = visibility_payload.get("visibility_score")

    data = evaluate_alerts(
        sw,
        visibility_score=visibility_score,
        user_threshold=threshold,
    )

    data["visibility_monitor"] = {
        "enabled": lat is not None and lon is not None,
        "lat": lat,
        "lon": lon,
        "threshold": threshold,
        "current_score": visibility_score,
        "triggered": visibility_score is not None and visibility_score >= threshold,
        "rating": visibility_payload.get("rating") if visibility_payload else None,
    }

    return JSONResponse(content=data)


@app.get("/terminator")
async def terminator():
    """Day/night terminator line for the map overlay."""
    from visibility_engine import compute_terminator_with_sun
    data = compute_terminator_with_sun()
    return JSONResponse(content=data)


@app.get("/kp-timeline")
async def kp_timeline():
    """Return time-series of estimated Kp values for charting."""
    return JSONResponse(content={"history": get_kp_history()})


@app.get("/bz-history")
async def bz_history():
    """Return recent Bz ring-buffer for substorm rate analysis."""
    return JSONResponse(content={"history": get_bz_history()})


@app.get("/photo-settings")
async def photo_settings(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """Return photography recommendations for a location."""
    cache = get_cache()
    grid = cache.get("aurora_grid")
    vis = compute_visibility(lat, lon, aurora_grid=grid)
    return JSONResponse(content=vis.get("photo_settings", {}))


@app.get("/better-viewing-spot")
async def better_viewing_spot(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    search_radius_km: float = Query(180.0, ge=30.0, le=400.0),
    min_improvement: float = Query(15.0, ge=5.0, le=40.0),
    max_weather_checks_per_ring: int = Query(4, ge=1, le=8),
):
    """
    On-demand nearby recommendation for a materially better aurora viewing spot.
    """
    cache = get_cache()
    grid = cache.get("aurora_grid")
    result = find_better_viewing_spot(
        lat=lat,
        lon=lon,
        aurora_grid=grid,
        search_radius_km=search_radius_km,
        min_improvement=min_improvement,
        max_weather_checks_per_ring=max_weather_checks_per_ring,
    )
    return JSONResponse(content=result)


# ═══════════════════════════════════════════════════════════════════════════
# Community Sightings
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/sightings")
async def get_sightings():
    return JSONResponse(content={"sightings": _sightings[-100:]})


@app.post("/sightings")
async def post_sighting(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    message: str = Query("Aurora spotted!", max_length=280),
    intensity: int = Query(3, ge=1, le=5),
):
    entry = {
        "lat": lat,
        "lon": lon,
        "message": message,
        "intensity": intensity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _sightings.append(entry)
    return JSONResponse(content=entry, status_code=201)


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket for real-time push
# ═══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive; client can send pings
    except WebSocketDisconnect:
        _ws_clients.remove(ws)
    except Exception:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


async def _ws_broadcast_loop():
    """Push latest cache to all WebSocket clients every 30 s."""
    while True:
        await asyncio.sleep(30)
        cache = get_cache()
        if not _ws_clients or cache.get("solar_wind") is None:
            continue
        alerts_data = cache.get("alerts") or {}
        payload = json.dumps({
            "type": "update",
            "solar_wind": cache["solar_wind"],
            "alerts": alerts_data,
            "kp_latest": alerts_data.get("kp_estimate"),
            "last_updated": cache["last_updated"],
        })
        # Iterate over a snapshot so concurrent removes from websocket_endpoint
        # do not cause skipped entries or a RuntimeError mid-loop.
        dead = []
        for ws in list(_ws_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _ws_clients:
                _ws_clients.remove(ws)


# ═══════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    cache = get_cache()
    return {
        "status": "ok",
        "last_updated": cache.get("last_updated"),
        "has_solar_wind": cache.get("solar_wind") is not None,
        "has_aurora_grid": cache.get("aurora_grid") is not None,
        "data_gap": cache.get("solar_wind", {}).get("data_gap", None) if cache.get("solar_wind") else None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
