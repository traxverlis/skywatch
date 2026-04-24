# ⬡ SkyWatch — Real-time Geospatial Intelligence Dashboard

A Palantir-inspired global monitoring platform built with CesiumJS and FastAPI. Track flights, satellites, ships, earthquakes, wildfires, and more — all on a single 3D globe.

![SkyWatch](https://img.shields.io/badge/version-2.0-00d4ff?style=flat-square) ![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

## 🎯 Features

### Air
- **~10,000 live flights** via ADSB.lol + adsb.fi (crowd-sourced ADS-B)
- **564+ satellites** with SGP4 orbital propagation (ISS, Starlink, GPS, military, weather, science)
- Aircraft details on click (type, airline, speed, altitude, registration)
- Aircraft photos from Planespotters.net
- Emergency squawk alerts (7700/7600/7500)
- Altitude color bands (blue → red by flight level)

### Sea
- **AIS maritime tracking** via aisstream.io WebSocket
- Real-time ship positions, speed, heading
- Marine weather buoys (Open-Meteo)

### Land
- **Finland trains** real-time (digitraffic.fi)
- **782 bike-sharing networks** worldwide (citybik.es)
- Universal location search with geocoding (Nominatim/OpenStreetMap)

### Environment
- **Earthquakes** — USGS real-time feed (magnitude, depth, tsunami alerts)
- **Wildfires** — NASA FIRMS active fire detection (5,000+ hotspots)
- **Air quality** — 30 major cities AQI (Open-Meteo)
- **Weather overlay** — cloud cover layer
- Day/night mode toggle

### Intelligence
- **GPS jamming zones** — 10 known EW hotspots worldwide
- **Finance overlay** — 12 world stock indices (S&P500, DAX, Nikkei, CAC40...)
- **Country risk index** — composite score (seismic + EW + fire + AQI)
- **AI news synthesis** — real-time intelligence briefing via Perplexity AI
- **Internet outage detection** — IODA/Georgia Tech

### Cameras
- **866 webcams** worldwide with video overlay
- 60 live streams (YouTube embed, SkylineWebcams)
- 786 London traffic cameras (TfL, auto-refresh JPEG)
- Categories: landmarks, airports, beaches, volcanoes, wildlife, ports, ISS
- Click to view → opens overlay directly on the globe

### Visual & UX
- **3 GLSL post-processing filters**: Night Vision, Thermal (FLIR), CRT scanlines
- **Military-style boot sequence** with typewriter animation
- **Lock-on tracking** — camera follows selected entity in real-time
- **Crosshair/reticle** tactical overlay
- Keyboard shortcuts: `1` Night Vision, `2` Thermal, `3` CRT, `0` Normal, `F` Fullscreen, `ESC` Reset
- Starfield skybox + atmospheric halo
- Responsive side panel with layer toggles

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| **Backend** | Python 3.12, FastAPI, Uvicorn |
| **Frontend** | CesiumJS 1.125, vanilla JavaScript |
| **Orbital mechanics** | SGP4 (sgp4 Python library) |
| **Data transport** | httpx (async), WebSocket (AIS) |
| **TLE source** | CelesTrak + AMSAT fallback |

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- [Cesium Ion token](https://ion.cesium.com/) (free)

### Install & Run

```bash
# Clone
git clone https://github.com/traxverlis/skywatch.git
cd skywatch

# Virtual environment
python3 -m venv venv
source venv/bin/activate

# Dependencies
pip install fastapi uvicorn httpx sgp4 websockets

# Optional: AIS maritime data (get free key at aisstream.io)
export AIS_API_KEY="your_key_here"

# Optional: AI news (get key at perplexity.ai)
export PERPLEXITY_API_KEY="your_key_here"

# Run
uvicorn server:app --host 0.0.0.0 --port 8090
```

Open **http://localhost:8090** in your browser.

### Cesium Ion Token

Edit `static/index.html` and replace the Cesium Ion token in the `initCesium()` function, or set it via:

```javascript
Cesium.Ion.defaultAccessToken = 'YOUR_TOKEN';
```

## 📡 Data Sources (all free, no API keys required unless noted)

| Source | Data | Key? |
|--------|------|------|
| [ADSB.lol](https://api.adsb.lol) | Flight tracking | No |
| [adsb.fi](https://api.adsb.fi) | Flight tracking (backup) | No |
| [CelesTrak](https://celestrak.org) | Satellite TLE data | No |
| [AMSAT](https://amsat.org) | Amateur satellite TLE | No |
| [USGS](https://earthquake.usgs.gov) | Earthquakes | No |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov) | Wildfires | No |
| [Open-Meteo](https://open-meteo.com) | Weather, AQI, marine | No |
| [citybik.es](https://api.citybik.es) | Bike sharing | No |
| [Planespotters.net](https://api.planespotters.net) | Aircraft photos | No |
| [Nominatim](https://nominatim.openstreetmap.org) | Geocoding | No |
| [hexdb.io](https://hexdb.io) | Aircraft database | No |
| [Yahoo Finance](https://finance.yahoo.com) | Stock indices | No |
| [TfL](https://api.tfl.gov.uk) | London traffic cams | No |
| [aisstream.io](https://aisstream.io) | AIS ship tracking | **Yes** (free) |
| [Perplexity AI](https://perplexity.ai) | News synthesis | **Yes** (paid) |

## ⌨️ Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` | Night Vision filter |
| `2` | Thermal (FLIR) filter |
| `3` | CRT scanlines filter |
| `0` | Normal view |
| `F` | Toggle fullscreen |
| `ESC` | Stop tracking, close overlays, reset filters |

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────────┐
│  CesiumJS   │────▶│  FastAPI Server   │
│  Frontend   │◀────│  (async workers)  │
└─────────────┘     └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ ADSB.lol │  │CelesTrak │  │  USGS    │
        │ adsb.fi  │  │  AMSAT   │  │  FIRMS   │
        └──────────┘  └──────────┘  │ Open-Met │
                                    └──────────┘
```

Background workers fetch data every 15-30s and cache results. API endpoints serve from cache → **sub-20ms response times**.

## 📄 License

MIT

## 🙏 Credits

Built with [CesiumJS](https://cesium.com/), inspired by [Palantir](https://palantir.com/) and the open-source intelligence community.
