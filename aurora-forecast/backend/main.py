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
from typing import List

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from scheduler import start_scheduler, stop_scheduler, get_cache, get_kp_history
from solar_wind import get_solar_wind_data, get_bz_history
from ovation_parser import get_aurora_grid
from visibility_engine import compute_visibility, compute_terminator
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


# ═══════════════════════════════════════════════════════════════════════════
# Data API endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/solar-wind")
async def solar_wind():
    cache = get_cache()
    data = cache.get("solar_wind") or get_solar_wind_data()
    return JSONResponse(content=data)


@app.get("/aurora-grid")
async def aurora_grid():
    cache = get_cache()
    data = cache.get("aurora_grid") or get_aurora_grid()
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
async def alerts():
    cache = get_cache()
    data = cache.get("alerts")
    if data is None:
        sw = cache.get("solar_wind") or get_solar_wind_data()
        data = evaluate_alerts(sw)
    return JSONResponse(content=data)


@app.get("/terminator")
async def terminator():
    """Day/night terminator line for the map overlay."""
    pts = compute_terminator()
    return JSONResponse(content={"points": pts})


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
        payload = json.dumps({
            "type": "update",
            "solar_wind": cache["solar_wind"],
            "alerts": cache["alerts"],
            "kp_latest": cache["alerts"]["kp_estimate"] if cache["alerts"] else None,
            "last_updated": cache["last_updated"],
        })
        dead = []
        for ws in _ws_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
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
