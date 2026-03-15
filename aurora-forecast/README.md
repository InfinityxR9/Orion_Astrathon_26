# Aurora Forecast Platform

Real-time aurora visibility forecasting with hyper-local scoring. Built for the Orion Computational Astrophysics Hackathon.

## What It Does

Answers: **"Can I see the aurora from my exact location right now?"**

Combines **space weather** + **weather conditions** + **sky darkness** into a single visibility score (0-100).

## Architecture

```
aurora-forecast/
├── backend/
│   ├── main.py               # FastAPI app, REST + WebSocket endpoints
│   ├── solar_wind.py          # DSCOVR/ACE solar wind ingestion with failover
│   ├── ovation_parser.py      # OVATION aurora probability grid (numpy-accelerated)
│   ├── weather.py             # Open-Meteo cloud cover, visibility, humidity
│   ├── visibility_engine.py   # Composite engine: Bortle, geomag lat, terminator, photo advisor
│   ├── aurora_alerts.py       # Storm alerts + substorm early warning (dBz/dt)
│   └── scheduler.py           # APScheduler (30s solar wind, 120s OVATION)
├── frontend/
│   ├── index.html             # Map + panels + sightings modal
│   ├── app.js                 # Leaflet heatmap + terminator + Kp chart + WebSocket
│   └── style.css              # Dark theme
├── requirements.txt
└── README.md
```

## Quick Start

```bash
cd aurora-forecast
pip install -r requirements.txt
cd backend
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Frontend UI |
| `/solar-wind` | GET | Solar wind Bz, speed, density, dBz/dt |
| `/aurora-grid` | GET | OVATION probability grid |
| `/visibility-score?lat=&lon=` | GET | Hyper-local visibility score |
| `/alerts` | GET | Active storm / substorm alerts |
| `/terminator` | GET | Day/night boundary polyline |
| `/kp-timeline` | GET | Kp time-series for charting |
| `/bz-history` | GET | Bz ring-buffer for substorm analysis |
| `/photo-settings?lat=&lon=` | GET | Camera exposure recommendations |
| `/sightings` | GET | Community aurora sightings |
| `/sightings?lat=&lon=&intensity=&message=` | POST | Report a sighting |
| `/ws` | WebSocket | Real-time push (solar wind + alerts) |
| `/health` | GET | System health check |

## Visibility Score Formula

```text
visibility_score = 100 * A^1.8 * (0.65 + 0.20 * D + 0.15 * C)
```

Where:
- `A = aurora_probability / 100`
- `D = sky_darkness / 100`
- `C = cloud_clarity / 100`

## Deliverables Implemented

### D1 - Live Data Pipeline
- DSCOVR primary, ACE 2-hour failover
- Data-gap detection (>5 min stale)
- Bz ring-buffer for dBz/dt substorm rate

### D2 - Interactive Aurora Map
- OVATION heatmap overlay (green → red)
- Day/night terminator layer (analytic)
- Night-side polygon shading
- 60s auto-refresh + WebSocket push

### D3 - Visibility Score Engine
- `100 * A^1.8 * (0.65 + 0.20 * D + 0.15 * C)`
- `A = aurora_probability / 100`, `D = sky_darkness / 100`, `C = cloud_clarity / 100`
- Bortle-class light pollution (city table + latitude proxy)
- Moon illumination (synodic phase)
- Solar elevation (Spencer formula)
- Geomagnetic latitude (dipole model)
- Atmospheric visibility + humidity haze penalty

### D4 - Alert System
- Bz < −7 nT, speed > 500 km/s, Bt > 15 nT
- Substorm early warning: dBz/dt < −1.5 nT/min
- User-configurable visibility threshold alerts
- Newell coupling Kp estimate

### D5 - Working Demo
- Single `uvicorn main:app` command
- All data from free public APIs (no keys needed)

### Stretch Goals
- **Substorm Early Warning**: Bz deflection rate monitoring (dBz/dt)
- **Photography Settings Advisor**: ISO, aperture, shutter, white balance
- **Community Sighting Layer**: POST/GET sightings with map markers
- **Kp Time-series Chart**: Canvas mini-chart overlaid on map

## Data Sources

- [NOAA SWPC MAG 1-day](https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json) (DSCOVR)
- [NOAA SWPC Plasma 1-day](https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json) (DSCOVR)
- [NOAA SWPC MAG 2-hour](https://services.swpc.noaa.gov/products/solar-wind/mag-2-hour.json) (ACE failover)
- [NOAA OVATION Aurora](https://services.swpc.noaa.gov/json/ovation_aurora_latest.json)
- [Open-Meteo Forecast API](https://api.open-meteo.com/v1/forecast) (no key)

## Tech Stack

Python 3.10+ / FastAPI / APScheduler / NumPy / Leaflet.js / WebSocket
