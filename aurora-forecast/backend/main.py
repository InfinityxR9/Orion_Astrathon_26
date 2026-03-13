"""
Aurora Forecast Platform — FastAPI Backend
Main application file exposing REST API endpoints and serving the frontend.
"""

import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from scheduler import start_scheduler, stop_scheduler, get_cache
from solar_wind import get_solar_wind_data
from ovation_parser import get_aurora_grid
from visibility_engine import compute_visibility
from aurora_alerts import evaluate_alerts


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the data scheduler on app startup, stop on shutdown."""
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Aurora Forecast Platform",
    description="Real-time aurora visibility forecasting with hyper-local scoring",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend directory as static files
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ─── API Endpoints ──────────────────────────────────────────────────────────


@app.get("/")
async def root():
    """Serve the frontend index.html."""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Aurora Forecast API is running. Frontend not found."}


@app.get("/solar-wind")
async def solar_wind():
    """Get current solar wind conditions (cached, updated every 60s)."""
    cache = get_cache()
    data = cache.get("solar_wind")
    if data is None:
        data = get_solar_wind_data()
    return JSONResponse(content=data)


@app.get("/aurora-grid")
async def aurora_grid():
    """Get current OVATION aurora probability grid (cached)."""
    cache = get_cache()
    data = cache.get("aurora_grid")
    if data is None:
        data = get_aurora_grid()
    return JSONResponse(content=data)


@app.get("/visibility-score")
async def visibility_score(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
):
    """
    Compute hyper-local aurora visibility score.
    Combines aurora probability, darkness, and cloud cover.
    """
    cache = get_cache()
    aurora_grid_data = cache.get("aurora_grid")
    result = compute_visibility(lat, lon, aurora_grid=aurora_grid_data)
    return JSONResponse(content=result)


@app.get("/alerts")
async def alerts():
    """Get current aurora alerts based on solar wind conditions."""
    cache = get_cache()
    data = cache.get("alerts")
    if data is None:
        sw = cache.get("solar_wind")
        if sw is None:
            sw = get_solar_wind_data()
        data = evaluate_alerts(sw)
    return JSONResponse(content=data)


@app.get("/health")
async def health():
    """Health check endpoint."""
    cache = get_cache()
    return {
        "status": "ok",
        "last_updated": cache.get("last_updated"),
        "has_solar_wind": cache.get("solar_wind") is not None,
        "has_aurora_grid": cache.get("aurora_grid") is not None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
