# Aurora Forecast Platform

Real-time aurora visibility forecasting with hyper-local scoring. Built for the Computational Astrophysics Hackathon.

## What It Does

Answers the question: **"Can I see the aurora from my exact location right now?"**

The system combines:
- **Space weather data** — real-time solar wind (Bz, speed) from NOAA SWPC
- **Aurora probability** — OVATION model aurora oval forecast
- **Weather conditions** — cloud cover from Open-Meteo
- **Sky darkness** — solar elevation, moon phase, light pollution estimate

Into a single **visibility score (0-100)** for any location on Earth.

## Architecture

```
aurora-forecast/
├── backend/
│   ├── main.py               # FastAPI app + endpoints
│   ├── solar_wind.py          # NOAA solar wind ingestion (Bz, speed, density)
│   ├── ovation_parser.py      # OVATION aurora probability grid parser
│   ├── weather.py             # Open-Meteo cloud cover + temperature
│   ├── visibility_engine.py   # Composite visibility score engine
│   ├── aurora_alerts.py       # Alert generation (storm conditions)
│   └── scheduler.py           # APScheduler polling (60s intervals)
├── frontend/
│   ├── index.html             # Map UI with side panel
│   ├── app.js                 # Leaflet map + heatmap + data polling
│   └── style.css              # Dark theme for night observers
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Install dependencies

```bash
cd aurora-forecast
pip install -r requirements.txt
```

### 2. Run the server

```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 3. Open the UI

Navigate to [http://localhost:8000](http://localhost:8000)

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Frontend UI |
| `GET /solar-wind` | Current solar wind conditions |
| `GET /aurora-grid` | OVATION aurora probability grid |
| `GET /visibility-score?lat=64&lon=-21` | Visibility score for a location |
| `GET /alerts` | Active aurora alerts |
| `GET /health` | System health check |

## Visibility Score Formula

```
visibility = 0.5 * aurora_probability
           + 0.3 * darkness_score
           + 0.2 * cloud_clarity_score
```

### Darkness Score Components
- **Solar elevation** — astronomical/nautical/civil twilight thresholds
- **Moon illumination** — synodic month phase calculation
- **Light pollution** — latitude-based population density proxy

### Alert Triggers
- Bz < -7 nT (southward interplanetary magnetic field)
- Solar wind speed > 500 km/s (high-speed stream)
- Bt > 15 nT (strong total field)

## Data Sources

- **Solar wind magnetic field**: [NOAA SWPC MAG 1-day](https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json)
- **Solar wind plasma**: [NOAA SWPC Plasma 1-day](https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json)
- **Aurora probability**: [NOAA OVATION Aurora Latest](https://services.swpc.noaa.gov/json/ovation_aurora_latest.json)
- **Weather**: [Open-Meteo Forecast API](https://api.open-meteo.com/v1/forecast)

## Tech Stack

- **Backend**: Python, FastAPI, APScheduler, NumPy, Pandas
- **Frontend**: Leaflet.js, Leaflet.heat, Vanilla JS
- **APIs**: NOAA SWPC (no key required), Open-Meteo (free)
