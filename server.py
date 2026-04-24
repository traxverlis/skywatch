"""
SkyWatch — Real-time Geospatial Intelligence Dashboard
Backend API server
"""
import asyncio
import csv
import json
import math
import os
import time
from datetime import datetime, timezone
from io import StringIO

import httpx
import websockets
import websockets.exceptions
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sgp4.api import Satrec, jday
from sgp4 import exporter

app = FastAPI(title="SkyWatch", version="0.1.0")

# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------
_cache: dict[str, dict] = {}
CACHE_TTL = {
    "tle": 3600,        # 1h for TLE data
    "flights": 60,      # 60s — bg worker refreshes every 15s, keeps stale data longer
    "military": 60,
    "earthquakes": 300, # 5 min
    "gps-jamming": 3600,
    "outages": 600,     # 10 min
    "fires": 1800,      # 30 min
    "air-quality": 1800,
    "bikes": 900,       # 15 min
    "aircraft-photo": 86400,
    "news": 1800,       # 30 min
    "finance": 900,     # 15 min
    "country-risk": 3600,
}

def cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL.get(key.split(":")[0], 60):
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ---------------------------------------------------------------------------
# TLE / Satellite data from CelesTrak
# ---------------------------------------------------------------------------
TLE_URLS = {
    "stations": "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
    "starlink": "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
    "active": "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
    "military": "https://celestrak.org/NORAD/elements/gp.php?GROUP=military&FORMAT=tle",
    "weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
    "gps": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=tle",
    "science": "https://celestrak.org/NORAD/elements/gp.php?GROUP=science&FORMAT=tle",
    "geodetic": "https://celestrak.org/NORAD/elements/gp.php?GROUP=geodetic&FORMAT=tle",
    "resource": "https://celestrak.org/NORAD/elements/gp.php?GROUP=resource&FORMAT=tle",
    "sarsat": "https://celestrak.org/NORAD/elements/gp.php?GROUP=sarsat&FORMAT=tle",
    "debris-cosmos": "https://celestrak.org/NORAD/elements/gp.php?GROUP=cosmos-1408-debris&FORMAT=tle",
    "debris-fengyun": "https://celestrak.org/NORAD/elements/gp.php?GROUP=fengyun-1c-debris&FORMAT=tle",
    "debris-iridium": "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-33-debris&FORMAT=tle",
    "last-30-days": "https://celestrak.org/NORAD/elements/gp.php?GROUP=last-30-days&FORMAT=tle",
    "visual": "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle",
    "oneweb": "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle",
    "planet": "https://celestrak.org/NORAD/elements/gp.php?GROUP=planet&FORMAT=tle",
    "spire": "https://celestrak.org/NORAD/elements/gp.php?GROUP=spire&FORMAT=tle",
    "globalstar": "https://celestrak.org/NORAD/elements/gp.php?GROUP=globalstar&FORMAT=tle",
    "iridium-next": "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-NEXT&FORMAT=tle",
}

async def fetch_tle(group: str = "stations") -> list[dict]:
    cache_key = f"tle:{group}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    sats = []
    # Try CelesTrak first (fast timeout — often blocked from VPS)
    url = TLE_URLS.get(group, TLE_URLS["stations"])
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        i = 0
        while i + 2 < len(lines):
            name = lines[i].strip()
            line1 = lines[i + 1].strip()
            line2 = lines[i + 2].strip()
            if line1.startswith("1 ") and line2.startswith("2 "):
                sats.append({"name": name, "line1": line1, "line2": line2})
                i += 3
            else:
                i += 1
    except Exception:
        pass

    # Fallback: use amsat.org TLE file or local cache
    if not sats:
        try:
            # Try amsat.org (usually accessible)
            amsat_url = "https://www.amsat.org/tle/current/nasabare.txt"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(amsat_url)
                if resp.status_code == 200:
                    lines = resp.text.strip().split("\n")
                    i = 0
                    while i + 2 < len(lines):
                        name = lines[i].strip()
                        line1 = lines[i + 1].strip()
                        line2 = lines[i + 2].strip()
                        if line1.startswith("1 ") and line2.startswith("2 "):
                            sats.append({"name": name, "line1": line1, "line2": line2})
                            i += 3
                        else:
                            i += 1
                    # Save locally for next time
                    import pathlib
                    data_dir = pathlib.Path(__file__).parent / "data"
                    data_dir.mkdir(exist_ok=True)
                    (data_dir / f"{group}.tle").write_text(resp.text)
        except Exception:
            pass

    # Last resort: local file
    if not sats:
        try:
            import pathlib
            data_dir = pathlib.Path(__file__).parent / "data"
            for tle_file in [data_dir / f"{group}.tle", data_dir / "amsat.tle"]:
                if tle_file.exists():
                    lines = tle_file.read_text().strip().split("\n")
                    i = 0
                    while i + 2 < len(lines):
                        name = lines[i].strip()
                        line1 = lines[i + 1].strip()
                        line2 = lines[i + 2].strip()
                        if line1.startswith("1 ") and line2.startswith("2 "):
                            sats.append({"name": name, "line1": line1, "line2": line2})
                            i += 3
                        else:
                            i += 1
                    if sats:
                        break
        except Exception:
            pass

    if sats:
        cache_set(cache_key, sats)
    return sats


def propagate_satellite(tle: dict) -> dict | None:
    """Compute current lat/lon/alt from TLE using SGP4."""
    try:
        sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
        now = datetime.now(timezone.utc)
        jd, fr = jday(now.year, now.month, now.day,
                       now.hour, now.minute, now.second + now.microsecond / 1e6)
        e, r, v = sat.sgp4(jd, fr)
        if e != 0:
            return None

        # Convert ECI to lat/lon/alt (simplified)
        x, y, z = r  # km
        alt = math.sqrt(x**2 + y**2 + z**2) - 6371.0  # approx altitude
        lon = math.degrees(math.atan2(y, x))
        lat = math.degrees(math.atan2(z, math.sqrt(x**2 + y**2)))

        # Adjust longitude for Earth rotation (GMST)
        gmst = _gmst(jd, fr)
        lon = (lon - gmst) % 360
        if lon > 180:
            lon -= 360

        return {
            "name": tle["name"],
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "alt": round(alt, 2),
            "velocity": round(math.sqrt(v[0]**2 + v[1]**2 + v[2]**2), 2),
        }
    except Exception:
        return None


def _gmst(jd, fr):
    """Greenwich Mean Sidereal Time in degrees."""
    t = ((jd - 2451545.0) + fr) / 36525.0
    gmst_sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * t \
               + 0.093104 * t**2 - 6.2e-6 * t**3
    gmst_deg = (gmst_sec / 240.0) % 360.0
    return gmst_deg


# ---------------------------------------------------------------------------
# Flight position history for trails
# ---------------------------------------------------------------------------
_flight_history: dict[str, list[dict]] = {}
FLIGHT_HISTORY_MAX = 500  # max positions per flight (more = longer trail)

def _update_flight_history(flights: list[dict]):
    """Store position history for trail rendering."""
    now = time.time()
    for f in flights:
        hex_id = f.get("hex")
        if not hex_id or not f.get("lat") or not f.get("lon"):
            continue
        if hex_id not in _flight_history:
            _flight_history[hex_id] = []
        hist = _flight_history[hex_id]
        # Store every 5 seconds for denser trails
        if not hist or (now - hist[-1]["ts"]) >= 5:
            hist.append({"lat": f["lat"], "lon": f["lon"], "alt": f.get("alt", 0), "ts": now})
            if len(hist) > FLIGHT_HISTORY_MAX:
                hist.pop(0)
    # Prune old flights not seen in 10 min
    stale = [k for k, v in _flight_history.items() if v and (now - v[-1]["ts"]) > 600]
    for k in stale:
        del _flight_history[k]


# ---------------------------------------------------------------------------
# Satellite orbit computation
# ---------------------------------------------------------------------------
def compute_orbit_path(tle: dict, minutes: int = 90, steps: int = 180) -> list[dict]:
    """Compute future orbit path for a satellite."""
    try:
        sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
        now = datetime.now(timezone.utc)
        path = []
        for i in range(steps):
            dt_min = (i / steps) * minutes
            t = now.timestamp() + dt_min * 60
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            jd, fr = jday(dt.year, dt.month, dt.day,
                          dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
            e, r, v = sat.sgp4(jd, fr)
            if e != 0:
                continue
            x, y, z = r
            alt = math.sqrt(x**2 + y**2 + z**2) - 6371.0
            lon = math.degrees(math.atan2(y, x))
            lat = math.degrees(math.atan2(z, math.sqrt(x**2 + y**2)))
            gmst = _gmst(jd, fr)
            lon = (lon - gmst) % 360
            if lon > 180:
                lon -= 360
            path.append({"lat": round(lat, 3), "lon": round(lon, 3), "alt": round(alt, 1)})
        return path
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Flight data from ADSB.lol (free, no API key)
# ---------------------------------------------------------------------------
# Regions to cover for global-ish flight data
# Grid the entire world with overlapping 500nm circles
# Use fewer, larger cells for global coverage (5000nm radius = ~9250km)
# 6 mega-cells cover the entire globe instead of 126 small ones
ADSB_REGIONS = [
    {"lat": 50, "lon": 0, "dist": 5000, "name": "europe_africa"},
    {"lat": 40, "lon": -100, "dist": 5000, "name": "north_america"},
    {"lat": 30, "lon": 100, "dist": 5000, "name": "east_asia"},
    {"lat": 10, "lon": 60, "dist": 5000, "name": "south_asia_mideast"},
    {"lat": -20, "lon": -60, "dist": 5000, "name": "south_america"},
    {"lat": -25, "lon": 140, "dist": 5000, "name": "oceania"},
    {"lat": 65, "lon": -40, "dist": 3000, "name": "north_atlantic"},
    {"lat": 70, "lon": 100, "dist": 3000, "name": "arctic_asia"},
]
print(f"[SkyWatch] ADSB grid: {len(ADSB_REGIONS)} mega-regions for global coverage")

async def _fetch_region(client: httpx.AsyncClient, region: dict) -> list[dict]:
    """Fetch flights for one region from ADSB.lol only (adsb.fi as fallback)."""
    url = f"https://api.adsb.lol/v2/lat/{region['lat']}/lon/{region['lon']}/dist/{region['dist']}"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.json().get("ac", [])
    except Exception:
        pass
    # Fallback to adsb.fi
    try:
        url2 = f"https://api.adsb.fi/v2/lat/{region['lat']}/lon/{region['lon']}/dist/{region['dist']}"
        resp = await client.get(url2)
        if resp.status_code == 200:
            return resp.json().get("ac", [])
    except Exception:
        pass
    return []


async def _bg_fetch_flights():
    """Background worker: fetch all flights and update cache."""
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            results = await asyncio.gather(
                *[_fetch_region(client, r) for r in ADSB_REGIONS],
                return_exceptions=True
            )

        seen_hex = set()
        flights = []
        for r in results:
            if isinstance(r, Exception) or not isinstance(r, list):
                continue
            for ac in r:
                if not ac.get("lat") or not ac.get("lon"):
                    continue
                hex_id = ac.get("hex", "")
                if hex_id in seen_hex:
                    continue
                seen_hex.add(hex_id)
                flights.append({
                    "hex": hex_id,
                    "flight": (ac.get("flight") or "").strip(),
                    "lat": ac["lat"],
                    "lon": ac["lon"],
                    "alt": ac.get("alt_baro", ac.get("alt_geom", 0)),
                    "alt_geom": ac.get("alt_geom", 0),
                    "speed": ac.get("gs", 0),
                    "ias": ac.get("ias", 0),
                    "tas": ac.get("tas", 0),
                    "mach": ac.get("mach", 0),
                    "heading": ac.get("track", 0),
                    "mag_heading": ac.get("mag_heading", 0),
                    "baro_rate": ac.get("baro_rate", 0),
                    "squawk": ac.get("squawk", ""),
                    "type": ac.get("t", ""),
                    "category": ac.get("category", ""),
                    "military": ac.get("dbFlags", 0) == 1,
                    "registration": ac.get("r", ""),
                    "emergency": ac.get("emergency", "none"),
                    "nav_altitude": ac.get("nav_altitude_mcp", 0),
                    "oat": ac.get("oat"),
                    "wind_dir": ac.get("wd"),
                    "wind_speed": ac.get("ws"),
                    "roll": ac.get("roll"),
                })
        # Only update cache if we got results (don't overwrite good data with empty)
        if flights:
            cache_set("flights:all", flights)
            _update_flight_history(flights)
        print(f"[BG] Flights updated: {len(flights)}")
    except Exception as e:
        print(f"[BG] Flight fetch error: {e}")


async def fetch_flights(bounds: dict | None = None) -> list[dict]:
    """Read flights from cache (populated by background worker)."""
    cached = cache_get("flights:all")
    flights = cached if cached else []

    # Filter by bounds if provided
    if bounds:
        lat_min, lat_max = bounds.get("lat_min", -90), bounds.get("lat_max", 90)
        lon_min, lon_max = bounds.get("lon_min", -180), bounds.get("lon_max", 180)
        flights = [f for f in flights
                   if lat_min <= f["lat"] <= lat_max and lon_min <= f["lon"] <= lon_max]

    return flights


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------
@app.get("/api/satellites/{group}")
async def get_satellites(group: str = "stations", limit: int = 200):
    """Get current positions of satellites (from background cache)."""
    cached = cache_get("satellites:positions")
    if cached:
        return {"count": len(cached), "satellites": cached[:limit], "group": "all"}
    # Cache not ready yet — return empty with message
    return {"count": 0, "satellites": [], "group": group, "status": "loading"}


@app.get("/api/satellite-groups")
async def get_satellite_groups():
    """List available satellite groups."""
    return {"groups": list(TLE_URLS.keys()) + ["all"]}


@app.get("/api/flights")
async def get_flights(
    lat_min: float = -90, lat_max: float = 90,
    lon_min: float = -180, lon_max: float = 180,
    military_only: bool = False,
):
    """Get current flights, optionally filtered by bounds."""
    flights = await fetch_flights({
        "lat_min": lat_min, "lat_max": lat_max,
        "lon_min": lon_min, "lon_max": lon_max,
    })
    if military_only:
        flights = [f for f in flights if f["military"]]
    return {"count": len(flights), "flights": flights}


@app.get("/api/stats")
async def get_stats():
    """Quick stats for the dashboard."""
    flights = await fetch_flights()
    military = [f for f in flights if f["military"]]
    return {
        "total_flights": len(flights),
        "military_flights": len(military),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/flight-trail/{hex_id}")
async def get_flight_trail(hex_id: str):
    """Get position history for a specific flight."""
    trail = _flight_history.get(hex_id, [])
    return {"hex": hex_id, "count": len(trail), "trail": trail}


@app.get("/api/aircraft/{hex_id}")
async def get_aircraft_info(hex_id: str):
    """Lookup aircraft details from hexdb.io."""
    cache_key = f"aircraft:{hex_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://hexdb.io/api/v1/aircraft/{hex_id}")
            if resp.status_code == 200:
                data = resp.json()
                result = {
                    "hex": hex_id,
                    "registration": data.get("Registration", ""),
                    "manufacturer": data.get("Manufacturer", ""),
                    "type_code": data.get("ICAOTypeCode", ""),
                    "type_full": data.get("Type", ""),
                    "owner": data.get("RegisteredOwners", ""),
                    "operator_code": data.get("OperatorFlagCode", ""),
                }
                _cache[cache_key] = {"data": result, "ts": time.time()}
                CACHE_TTL["aircraft"] = 86400  # 24h
                return result
    except Exception:
        pass
    return {"hex": hex_id, "error": "not found"}


@app.get("/api/satellite-orbit/{name}")
async def get_satellite_orbit(name: str, minutes: int = 90):
    """Compute orbit path for a named satellite."""
    # Search across all groups
    for group in TLE_URLS:
        tles = await fetch_tle(group)
        for tle in tles:
            if tle["name"].strip().lower() == name.strip().lower():
                path = compute_orbit_path(tle, minutes=minutes)
                return {"name": tle["name"], "count": len(path), "orbit": path, "minutes": minutes}
    return {"name": name, "count": 0, "orbit": [], "error": "Satellite not found"}


# ---------------------------------------------------------------------------
# Marine / Weather / Webcams / Trains APIs
# ---------------------------------------------------------------------------

# --- Marine weather (Open-Meteo, no API key) ---
MARINE_GRID = []
for _mlat in range(-60, 70, 30):
    for _mlon in range(-180, 180, 40):
        MARINE_GRID.append((_mlat, _mlon))


async def _fetch_marine_point(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    try:
        url = (f"https://marine-api.open-meteo.com/v1/marine?"
               f"latitude={lat}&longitude={lon}"
               f"&current=wave_height,wave_direction,wave_period")
        resp = await client.get(url)
        if resp.status_code == 200:
            d = resp.json()
            c = d.get("current", {})
            if c.get("wave_height") is not None:
                return {
                    "lat": lat, "lon": lon,
                    "wave_height": c.get("wave_height"),
                    "wave_direction": c.get("wave_direction"),
                    "wave_period": c.get("wave_period"),
                }
    except Exception:
        pass
    return None


@app.get("/api/marine")
async def get_marine():
    """Get marine weather data points across oceans."""
    cached = cache_get("marine:all")
    if cached:
        return {"count": len(cached), "buoys": cached}

    async with httpx.AsyncClient(timeout=15) as client:
        tasks = [_fetch_marine_point(client, lat, lon) for lat, lon in MARINE_GRID]
        results = await asyncio.gather(*tasks)

    buoys = [r for r in results if r]
    _cache["marine:all"] = {"data": buoys, "ts": time.time()}
    CACHE_TTL["marine"] = 1800  # 30 min
    return {"count": len(buoys), "buoys": buoys}


# --- Weather overlay tiles (OpenWeatherMap free tier if key, or Open-Meteo) ---
@app.get("/api/weather")
async def get_weather(lat: float = 48.8, lon: float = 2.3):
    """Get weather for a point via Open-Meteo (no key needed)."""
    cache_key = f"weather:{lat:.1f}:{lon:.1f}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={lat}&longitude={lon}"
                   f"&current=temperature_2m,wind_speed_10m,wind_direction_10m,"
                   f"weather_code,cloud_cover,relative_humidity_2m,pressure_msl")
            resp = await client.get(url)
            data = resp.json().get("current", {})
            result = {
                "lat": lat, "lon": lon,
                "temp": data.get("temperature_2m"),
                "wind_speed": data.get("wind_speed_10m"),
                "wind_dir": data.get("wind_direction_10m"),
                "weather_code": data.get("weather_code"),
                "clouds": data.get("cloud_cover"),
                "humidity": data.get("relative_humidity_2m"),
                "pressure": data.get("pressure_msl"),
            }
            _cache[cache_key] = {"data": result, "ts": time.time()}
            CACHE_TTL["weather"] = 900  # 15 min
            return result
    except Exception:
        return {"error": "weather unavailable"}


# --- Webcams (Windy API — free tier available) ---
# Public webcams from various DOT and city sources
WEBCAM_SOURCES = [
    # === ICONIC LANDMARKS ===
    {"name": "Times Square NYC", "lat": 40.758, "lon": -73.9855, "url": "https://www.earthcam.com/usa/newyork/timessquare/", "embed": "https://www.youtube.com/embed/AdUw5RdyZxI?autoplay=1&mute=1", "country": "US", "type": "landmark"},
    {"name": "Abbey Road London", "lat": 51.5320, "lon": -0.1778, "url": "https://www.earthcam.com/world/england/london/abbeyroad/", "embed": "https://www.youtube.com/embed/b1UOcKnUPRU?autoplay=1&mute=1", "country": "GB", "type": "landmark"},
    {"name": "Tour Eiffel Paris", "lat": 48.8584, "lon": 2.2945, "url": "https://www.earthcam.com/world/france/paris/", "embed": "https://www.youtube.com/embed/wUVYIJBRKaA?autoplay=1&mute=1", "country": "FR", "type": "landmark"},
    {"name": "Shibuya Crossing Tokyo", "lat": 35.6595, "lon": 139.7004, "url": "https://www.youtube.com/watch?v=DjdUEyjx13s", "embed": "https://www.youtube.com/embed/DjdUEyjx13s?autoplay=1&mute=1", "country": "JP", "type": "landmark"},
    {"name": "Venice Grand Canal", "lat": 45.4408, "lon": 12.3155, "url": "https://www.skylinewebcams.com/en/webcam/italia/veneto/venezia/canal-grande-rialto.html", "embed": "https://www.youtube.com/embed/vPsM9hGKxQo?autoplay=1&mute=1", "country": "IT", "type": "landmark"},
    {"name": "Barcelona La Rambla", "lat": 41.3818, "lon": 2.1732, "url": "https://www.skylinewebcams.com/en/webcam/espana/cataluna/barcelona/las-ramblas.html", "country": "ES", "type": "landmark"},
    {"name": "Sydney Opera House", "lat": -33.8568, "lon": 151.2153, "url": "https://www.youtube.com/watch?v=UrFTWfX3JuI", "embed": "https://www.youtube.com/embed/UrFTWfX3JuI?autoplay=1&mute=1", "country": "AU", "type": "landmark"},
    {"name": "Dubai Burj Khalifa", "lat": 25.1972, "lon": 55.2744, "url": "https://www.earthcam.com/world/unitedarabemirates/dubai/", "country": "AE", "type": "landmark"},
    {"name": "Prague Old Town", "lat": 50.0874, "lon": 14.4213, "url": "https://www.skylinewebcams.com/en/webcam/czech-republic/hlavni-mesto-praha/prague/old-town.html", "country": "CZ", "type": "landmark"},
    {"name": "Rome Trevi Fountain", "lat": 41.9009, "lon": 12.4833, "url": "https://www.skylinewebcams.com/en/webcam/italia/lazio/roma/fontana-di-trevi.html", "country": "IT", "type": "landmark"},
    {"name": "Amsterdam Dam Square", "lat": 52.3731, "lon": 4.8932, "url": "https://www.earthcam.com/world/netherlands/amsterdam/", "country": "NL", "type": "landmark"},
    {"name": "Berlin Brandenburg Gate", "lat": 52.5163, "lon": 13.3777, "url": "https://www.earthcam.com/world/germany/berlin/", "country": "DE", "type": "landmark"},
    {"name": "Brooklyn Bridge NYC", "lat": 40.7061, "lon": -73.9969, "url": "https://www.earthcam.com/usa/newyork/brooklynbridge/", "country": "US", "type": "landmark"},
    {"name": "Hollywood Sign LA", "lat": 34.1341, "lon": -118.3215, "url": "https://www.earthcam.com/usa/california/losangeles/hollywoodsign/", "country": "US", "type": "landmark"},
    {"name": "Rio Christ the Redeemer", "lat": -22.9519, "lon": -43.2105, "url": "https://www.skylinewebcams.com/en/webcam/brasil/rio-de-janeiro/rio-de-janeiro/cristo-redentor.html", "country": "BR", "type": "landmark"},
    {"name": "Istanbul Bosphorus", "lat": 41.0422, "lon": 29.0083, "url": "https://www.skylinewebcams.com/en/webcam/turkiye/istanbul/istanbul/bosphorus.html", "country": "TR", "type": "landmark"},
    {"name": "Moscow Red Square", "lat": 55.7539, "lon": 37.6208, "url": "https://www.earthcam.com/world/russia/moscow/", "country": "RU", "type": "landmark"},
    {"name": "Singapore Marina Bay", "lat": 1.2814, "lon": 103.8636, "url": "https://www.earthcam.com/world/singapore/marinabay/", "country": "SG", "type": "landmark"},
    {"name": "Cape Town Table Mountain", "lat": -33.9628, "lon": 18.4098, "url": "https://www.tablemountain.net/webcam", "country": "ZA", "type": "landmark"},
    {"name": "Hong Kong Victoria Harbour", "lat": 22.2930, "lon": 114.1694, "url": "https://www.earthcam.com/world/china/hongkong/", "country": "HK", "type": "landmark"},
    # === AIRPORTS ===
    {"name": "St Maarten Airport (SXM)", "lat": 18.0410, "lon": -63.1089, "url": "https://www.sxmairport.com/webcam", "embed": "https://www.youtube.com/embed/JYPpmyKbhRs?autoplay=1&mute=1", "country": "SX", "type": "airport"},
    {"name": "LAX Runway", "lat": 33.9425, "lon": -118.4081, "url": "https://www.lawa.org/en/connectlax/maps-and-information/webcam", "embed": "https://www.youtube.com/embed/mJkVFZNGHOA?autoplay=1&mute=1", "country": "US", "type": "airport"},
    {"name": "Zurich Airport", "lat": 47.4647, "lon": 8.5492, "url": "https://www.flughafen-zuerich.ch/en/passengers/experience/webcam", "embed": "https://www.youtube.com/embed/1OLlFIUmKwU?autoplay=1&mute=1", "country": "CH", "type": "airport"},
    {"name": "Gibraltar Airport", "lat": 36.1512, "lon": -5.3497, "url": "https://www.youtube.com/watch?v=t2R1XkDn3Hk", "embed": "https://www.youtube.com/embed/t2R1XkDn3Hk?autoplay=1&mute=1", "country": "GI", "type": "airport"},
    {"name": "Funchal Madeira Airport", "lat": 32.6942, "lon": -16.7745, "url": "https://www.youtube.com/watch?v=YH_AkBXK2VY", "embed": "https://www.youtube.com/embed/YH_AkBXK2VY?autoplay=1&mute=1", "country": "PT", "type": "airport"},
    # === BEACHES & SURF ===
    {"name": "Bondi Beach Sydney", "lat": -33.8915, "lon": 151.2767, "url": "https://www.youtube.com/watch?v=C-S3fGbGMVQ", "embed": "https://www.youtube.com/embed/C-S3fGbGMVQ?autoplay=1&mute=1", "country": "AU", "type": "beach"},
    {"name": "Waikiki Beach Hawaii", "lat": 21.2766, "lon": -157.8278, "url": "https://www.youtube.com/watch?v=LiKXZPHCn_8", "embed": "https://www.youtube.com/embed/LiKXZPHCn_8?autoplay=1&mute=1", "country": "US", "type": "beach"},
    {"name": "Copacabana Beach Rio", "lat": -22.9711, "lon": -43.1823, "url": "https://www.skylinewebcams.com/en/webcam/brasil/rio-de-janeiro/rio-de-janeiro/praia-de-copacabana.html", "country": "BR", "type": "beach"},
    {"name": "Nazaré Big Waves", "lat": 39.6011, "lon": -9.0706, "url": "https://beachcam.meo.pt/en/livecams/nazare-north-beach/", "embed": "https://www.youtube.com/embed/KCTB3LkWvDY?autoplay=1&mute=1", "country": "PT", "type": "beach"},
    {"name": "Pipeline North Shore", "lat": 21.6640, "lon": -158.0529, "url": "https://www.youtube.com/watch?v=WJVMM2TFVSM", "embed": "https://www.youtube.com/embed/WJVMM2TFVSM?autoplay=1&mute=1", "country": "US", "type": "beach"},
    {"name": "Hossegor France", "lat": 43.6647, "lon": -1.4383, "url": "https://www.youtube.com/watch?v=T9V6lVZfqFY", "embed": "https://www.youtube.com/embed/T9V6lVZfqFY?autoplay=1&mute=1", "country": "FR", "type": "beach"},
    {"name": "Maldives Beach", "lat": 4.1755, "lon": 73.5093, "url": "https://www.skylinewebcams.com/en/webcam/maldives/north-male-atoll/male/maldives.html", "country": "MV", "type": "beach"},
    # === VOLCANOES & NATURE ===
    {"name": "Etna Volcano Sicily", "lat": 37.7510, "lon": 14.9934, "url": "https://www.skylinewebcams.com/en/webcam/italia/sicilia/catania/vulcano-etna.html", "embed": "https://www.youtube.com/embed/L3IN2cKxqQs?autoplay=1&mute=1", "country": "IT", "type": "nature"},
    {"name": "Yellowstone Old Faithful", "lat": 44.4605, "lon": -110.8281, "url": "https://www.nps.gov/yell/learn/photosmultimedia/webcams.htm", "embed": "https://www.youtube.com/embed/ovRhCNjSFfs?autoplay=1&mute=1", "country": "US", "type": "nature"},
    {"name": "Stromboli Volcano", "lat": 38.7890, "lon": 15.2130, "url": "https://www.skylinewebcams.com/en/webcam/italia/sicilia/messina/stromboli.html", "country": "IT", "type": "nature"},
    {"name": "Niagara Falls", "lat": 43.0896, "lon": -79.0849, "url": "https://www.earthcam.com/world/canada/niagarafalls/", "embed": "https://www.youtube.com/embed/6-MKqcVkJZg?autoplay=1&mute=1", "country": "CA", "type": "nature"},
    {"name": "Northern Lights Iceland", "lat": 64.1466, "lon": -21.9426, "url": "https://www.youtube.com/watch?v=ELGb-g1PNOE", "embed": "https://www.youtube.com/embed/ELGb-g1PNOE?autoplay=1&mute=1", "country": "IS", "type": "nature"},
    {"name": "Teide Volcano Tenerife", "lat": 28.2723, "lon": -16.6424, "url": "https://www.skylinewebcams.com/en/webcam/espana/canarias/santa-cruz-de-tenerife/teide.html", "country": "ES", "type": "nature"},
    {"name": "Great Barrier Reef", "lat": -18.2861, "lon": 147.7000, "url": "https://www.youtube.com/watch?v=GhwPJhkQ0fI", "embed": "https://www.youtube.com/embed/GhwPJhkQ0fI?autoplay=1&mute=1", "country": "AU", "type": "nature"},
    # === TRAFFIC / CITY ===
    {"name": "I-95 Miami", "lat": 25.7940, "lon": -80.2100, "url": "https://www.youtube.com/watch?v=_9GJ-n_MTVY", "embed": "https://www.youtube.com/embed/_9GJ-n_MTVY?autoplay=1&mute=1", "country": "US", "type": "traffic"},
    {"name": "Seoul Gangnam", "lat": 37.4979, "lon": 127.0276, "url": "https://www.youtube.com/watch?v=gCNeDWCI0vo", "embed": "https://www.youtube.com/embed/gCNeDWCI0vo?autoplay=1&mute=1", "country": "KR", "type": "city"},
    {"name": "Bangkok Sukhumvit", "lat": 13.7380, "lon": 100.5600, "url": "https://www.youtube.com/watch?v=nCCGkqT3NMI", "embed": "https://www.youtube.com/embed/nCCGkqT3NMI?autoplay=1&mute=1", "country": "TH", "type": "city"},
    {"name": "Place de la Concorde Paris", "lat": 48.8656, "lon": 2.3212, "url": "https://www.youtube.com/watch?v=fq3rJb6l-LI", "embed": "https://www.youtube.com/embed/fq3rJb6l-LI?autoplay=1&mute=1", "country": "FR", "type": "city"},
    {"name": "Piccadilly Circus London", "lat": 51.5100, "lon": -0.1347, "url": "https://www.youtube.com/watch?v=ni-VsPXLPss", "embed": "https://www.youtube.com/embed/ni-VsPXLPss?autoplay=1&mute=1", "country": "GB", "type": "city"},
    {"name": "Mexico City Zocalo", "lat": 19.4326, "lon": -99.1332, "url": "https://www.youtube.com/watch?v=NjCgZbDHmVU", "embed": "https://www.youtube.com/embed/NjCgZbDHmVU?autoplay=1&mute=1", "country": "MX", "type": "city"},
    {"name": "Buenos Aires Obelisco", "lat": -34.6037, "lon": -58.3816, "url": "https://www.youtube.com/watch?v=Pc_hGVnqbQs", "embed": "https://www.youtube.com/embed/Pc_hGVnqbQs?autoplay=1&mute=1", "country": "AR", "type": "city"},
    {"name": "Lisbon Praça do Comércio", "lat": 38.7077, "lon": -9.1365, "url": "https://www.skylinewebcams.com/en/webcam/portugal/lisboa/lisboa/praca-do-comercio.html", "country": "PT", "type": "city"},
    {"name": "Mumbai Marine Drive", "lat": 18.9436, "lon": 72.8234, "url": "https://www.youtube.com/watch?v=WFvFR5PFj6Y", "embed": "https://www.youtube.com/embed/WFvFR5PFj6Y?autoplay=1&mute=1", "country": "IN", "type": "city"},
    {"name": "Havana Malecón", "lat": 23.1417, "lon": -82.3600, "url": "https://www.skylinewebcams.com/en/webcam/cuba/la-habana/la-habana/la-habana.html", "country": "CU", "type": "city"},
    # === HARBORS & PORTS ===
    {"name": "Port of Rotterdam", "lat": 51.9066, "lon": 4.2888, "url": "https://www.youtube.com/watch?v=vJm1wEL4kY8", "embed": "https://www.youtube.com/embed/vJm1wEL4kY8?autoplay=1&mute=1", "country": "NL", "type": "port"},
    {"name": "Port of Hamburg", "lat": 53.5459, "lon": 9.9667, "url": "https://www.youtube.com/watch?v=ckpnDz6UMwg", "embed": "https://www.youtube.com/embed/ckpnDz6UMwg?autoplay=1&mute=1", "country": "DE", "type": "port"},
    {"name": "Marseille Vieux Port", "lat": 43.2951, "lon": 5.3691, "url": "https://www.skylinewebcams.com/en/webcam/france/provence-alpes-cote-d-azur/marseille/vieux-port-de-marseille.html", "country": "FR", "type": "port"},
    # === WILDLIFE ===
    {"name": "African Watering Hole", "lat": -24.3828, "lon": 31.5918, "url": "https://www.youtube.com/watch?v=ydYDqZQpim8", "embed": "https://www.youtube.com/embed/ydYDqZQpim8?autoplay=1&mute=1", "country": "ZA", "type": "wildlife"},
    {"name": "Monterey Bay Aquarium", "lat": 36.6183, "lon": -121.9017, "url": "https://www.montereybayaquarium.org/animals/live-cams", "embed": "https://www.youtube.com/embed/gLc50GtlPJQ?autoplay=1&mute=1", "country": "US", "type": "wildlife"},
    {"name": "Transylvania Bears", "lat": 45.5981, "lon": 24.9668, "url": "https://www.youtube.com/watch?v=b5_lAHHV4Gs", "embed": "https://www.youtube.com/embed/b5_lAHHV4Gs?autoplay=1&mute=1", "country": "RO", "type": "wildlife"},
    {"name": "Decorah Eagles Iowa", "lat": 43.3035, "lon": -91.7856, "url": "https://www.youtube.com/watch?v=WnAYEvMlBWE", "embed": "https://www.youtube.com/embed/WnAYEvMlBWE?autoplay=1&mute=1", "country": "US", "type": "wildlife"},
    # === SPACE / ISS ===
    {"name": "ISS Live Earth View", "lat": 0.0, "lon": 0.0, "url": "https://eol.jsc.nasa.gov/ESRS/HDEV/", "embed": "https://www.youtube.com/embed/xRPjKQtRXR8?autoplay=1&mute=1", "country": "SPACE", "type": "space"},
    # === SKYLINEWEBCAMS EMBEDS (iframe direct) ===
    {"name": "Amalfi Coast", "lat": 40.6340, "lon": 14.6027, "url": "https://www.skylinewebcams.com/en/webcam/italia/campania/amalfi/amalfi.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/campania/amalfi/amalfi.html", "country": "IT", "type": "beach"},
    {"name": "Taormina Sicily", "lat": 37.8516, "lon": 15.2854, "url": "https://www.skylinewebcams.com/en/webcam/italia/sicilia/messina/taormina.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/sicilia/messina/taormina.html", "country": "IT", "type": "beach"},
    {"name": "Santorini Oia", "lat": 36.4618, "lon": 25.3753, "url": "https://www.skylinewebcams.com/en/webcam/ellada/south-aegean/thira/oia-santorini.html", "embed": "https://www.skylinewebcams.com/en/webcam/ellada/south-aegean/thira/oia-santorini.html", "country": "GR", "type": "landmark"},
    {"name": "Naples Vesuvius", "lat": 40.8518, "lon": 14.2681, "url": "https://www.skylinewebcams.com/en/webcam/italia/campania/napoli/napoli-vesuvio.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/campania/napoli/napoli-vesuvio.html", "country": "IT", "type": "nature"},
    {"name": "Dubrovnik Old Town", "lat": 42.6507, "lon": 18.0944, "url": "https://www.skylinewebcams.com/en/webcam/hrvatska/dubrovacko-neretvanska/dubrovnik/dubrovnik.html", "embed": "https://www.skylinewebcams.com/en/webcam/hrvatska/dubrovacko-neretvanska/dubrovnik/dubrovnik.html", "country": "HR", "type": "landmark"},
    {"name": "Mykonos Town", "lat": 37.4467, "lon": 25.3289, "url": "https://www.skylinewebcams.com/en/webcam/ellada/south-aegean/mykonos/mykonos-town.html", "embed": "https://www.skylinewebcams.com/en/webcam/ellada/south-aegean/mykonos/mykonos-town.html", "country": "GR", "type": "landmark"},
    {"name": "Madrid Puerta del Sol", "lat": 40.4168, "lon": -3.7038, "url": "https://www.skylinewebcams.com/en/webcam/espana/comunidad-de-madrid/madrid/puerta-del-sol.html", "embed": "https://www.skylinewebcams.com/en/webcam/espana/comunidad-de-madrid/madrid/puerta-del-sol.html", "country": "ES", "type": "city"},
    {"name": "Florence Ponte Vecchio", "lat": 43.7680, "lon": 11.2531, "url": "https://www.skylinewebcams.com/en/webcam/italia/toscana/firenze/ponte-vecchio.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/toscana/firenze/ponte-vecchio.html", "country": "IT", "type": "landmark"},
    {"name": "Milan Duomo", "lat": 45.4642, "lon": 9.1900, "url": "https://www.skylinewebcams.com/en/webcam/italia/lombardia/milano/duomo-milano.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/lombardia/milano/duomo-milano.html", "country": "IT", "type": "landmark"},
    {"name": "Pisa Leaning Tower", "lat": 43.7228, "lon": 10.3966, "url": "https://www.skylinewebcams.com/en/webcam/italia/toscana/pisa/piazza-dei-miracoli.html", "embed": "https://www.skylinewebcams.com/en/webcam/italia/toscana/pisa/piazza-dei-miracoli.html", "country": "IT", "type": "landmark"},
    {"name": "Playa del Carmen Mexico", "lat": 20.6296, "lon": -87.0739, "url": "https://www.skylinewebcams.com/en/webcam/mexico/quintana-roo/playa-del-carmen/playa-del-carmen.html", "embed": "https://www.skylinewebcams.com/en/webcam/mexico/quintana-roo/playa-del-carmen/playa-del-carmen.html", "country": "MX", "type": "beach"},
    {"name": "Benidorm Beach", "lat": 38.5411, "lon": -0.1225, "url": "https://www.skylinewebcams.com/en/webcam/espana/comunidad-valenciana/alicante/playa-de-benidorm.html", "embed": "https://www.skylinewebcams.com/en/webcam/espana/comunidad-valenciana/alicante/playa-de-benidorm.html", "country": "ES", "type": "beach"},
    # === MORE YOUTUBE LIVE 24/7 ===
    {"name": "Jackson Hole Town Square", "lat": 43.4799, "lon": -110.7624, "url": "https://www.youtube.com/watch?v=psfFJR3vZ78", "embed": "https://www.youtube.com/embed/psfFJR3vZ78?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Miami Beach", "lat": 25.7907, "lon": -80.1300, "url": "https://www.youtube.com/watch?v=VT3hcKmnCcE", "embed": "https://www.youtube.com/embed/VT3hcKmnCcE?autoplay=1&mute=1", "country": "US", "type": "beach"},
    {"name": "Key West Duval Street", "lat": 24.5551, "lon": -81.8018, "url": "https://www.youtube.com/watch?v=hUnKd1a62rY", "embed": "https://www.youtube.com/embed/hUnKd1a62rY?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Nashville Broadway", "lat": 36.1627, "lon": -86.7816, "url": "https://www.youtube.com/watch?v=PJxC9JXj4Fo", "embed": "https://www.youtube.com/embed/PJxC9JXj4Fo?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "New Orleans Bourbon St", "lat": 29.9584, "lon": -90.0651, "url": "https://www.youtube.com/watch?v=lLaFSyiVjIE", "embed": "https://www.youtube.com/embed/lLaFSyiVjIE?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Kitten Rescue Live", "lat": 34.0522, "lon": -118.4437, "url": "https://www.youtube.com/watch?v=clfFBl2CKIY", "embed": "https://www.youtube.com/embed/clfFBl2CKIY?autoplay=1&mute=1", "country": "US", "type": "wildlife"},
    {"name": "Tokyo Tower", "lat": 35.6586, "lon": 139.7454, "url": "https://www.youtube.com/watch?v=a5bnEq41yMk", "embed": "https://www.youtube.com/embed/a5bnEq41yMk?autoplay=1&mute=1", "country": "JP", "type": "landmark"},
    {"name": "Osaka Dotonbori", "lat": 34.6687, "lon": 135.5013, "url": "https://www.youtube.com/watch?v=rT0jfeniBAY", "embed": "https://www.youtube.com/embed/rT0jfeniBAY?autoplay=1&mute=1", "country": "JP", "type": "city"},
    {"name": "San Francisco Fishermans Wharf", "lat": 37.8080, "lon": -122.4177, "url": "https://www.youtube.com/watch?v=Is4aTi0YmcE", "embed": "https://www.youtube.com/embed/Is4aTi0YmcE?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Las Vegas Strip", "lat": 36.1147, "lon": -115.1728, "url": "https://www.youtube.com/watch?v=VJV-H3hZbEU", "embed": "https://www.youtube.com/embed/VJV-H3hZbEU?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Chicago Skyline", "lat": 41.8827, "lon": -87.6233, "url": "https://www.youtube.com/watch?v=JGfvCvBIJlA", "embed": "https://www.youtube.com/embed/JGfvCvBIJlA?autoplay=1&mute=1", "country": "US", "type": "city"},
    {"name": "Pattaya Beach Thailand", "lat": 12.9236, "lon": 100.8825, "url": "https://www.youtube.com/watch?v=gThZ4GVu7Go", "embed": "https://www.youtube.com/embed/gThZ4GVu7Go?autoplay=1&mute=1", "country": "TH", "type": "beach"},
]


@app.get("/api/webcam-snapshot")
async def get_webcam_snapshot(url: str):
    """Proxy to get og:image or live snapshot from a webcam URL."""
    cache_key = f"webcam-snap:{url}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
            if resp.status_code == 200:
                text = resp.text
                # Try og:image first
                import re
                og_match = re.search(r'og:image["\']?\s*content=["\']([^"\']+)["\']', text)
                if og_match:
                    img_url = og_match.group(1)
                    result = {"image": img_url, "source": "og:image"}
                    _cache[cache_key] = {"data": result, "ts": time.time()}
                    CACHE_TTL["webcam-snap"] = 60  # refresh every minute
                    return result
                # Try finding any live jpg
                img_matches = re.findall(r'https?://[^"\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]*)?', text)
                img_matches = [i for i in img_matches if 'live' in i.lower() or 'snap' in i.lower() or 'cam' in i.lower()]
                if img_matches:
                    result = {"image": img_matches[0], "source": "page-scan"}
                    _cache[cache_key] = {"data": result, "ts": time.time()}
                    return result
    except Exception:
        pass
    return {"image": "", "error": "not found"}


@app.get("/api/webcams")
async def get_webcams(country: str | None = None, source: str | None = None):
    """List available public webcams from all sources."""
    all_cams = list(WEBCAM_SOURCES)  # static list

    # Dynamically fetch TfL JamCams (London CCTV)
    tfl_cache = cache_get("webcams:tfl")
    if tfl_cache:
        all_cams.extend(tfl_cache)
    else:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://api.tfl.gov.uk/Place/Type/JamCam")
                if resp.status_code == 200:
                    tfl_data = resp.json()
                    tfl_cams = []
                    for cam in tfl_data:
                        props = {p["key"]: p["value"] for p in cam.get("additionalProperties", [])}
                        if props.get("available") == "true" and props.get("imageUrl"):
                            tfl_cams.append({
                                "name": cam.get("commonName", "TfL Camera"),
                                "lat": cam.get("lat"),
                                "lon": cam.get("lon"),
                                "url": props.get("videoUrl") or props.get("imageUrl"),
                                "image": props.get("imageUrl"),
                                "country": "GB",
                                "source": "tfl",
                                "type": "traffic",
                            })
                    _cache["webcams:tfl"] = {"data": tfl_cams, "ts": time.time()}
                    CACHE_TTL["webcams"] = 300  # 5 min
                    all_cams.extend(tfl_cams)
        except Exception:
            pass

    if country:
        all_cams = [c for c in all_cams if c.get("country", "").lower() == country.lower()]
    if source:
        all_cams = [c for c in all_cams if c.get("source", "curated").lower() == source.lower()]
    return {"count": len(all_cams), "webcams": all_cams, "sources": ["curated", "tfl"]}


# --- Transport en commun France (GTFS-RT via transport.data.gouv.fr) ---
FR_GTFSRT_FEEDS = [
    ("STAR Rennes", "https://proxy.transport.data.gouv.fr/resource/star-rennes-integration-gtfs-rt-vehicle-position"),
    ("Divia Dijon", "https://proxy.transport.data.gouv.fr/resource/divia-dijon-gtfs-rt-vehicle-position"),
    ("Bibus Brest", "https://proxy.transport.data.gouv.fr/resource/bibus-brest-gtfs-rt-vehicle-position"),
    ("Le Met Metz", "https://proxy.transport.data.gouv.fr/resource/lemet-metz-gtfs-rt-vehicle-position"),
    ("Ametis Amiens", "https://proxy.transport.data.gouv.fr/resource/ametis-amiens-gtfs-rt-vehicle-position"),
    ("SETRAM Le Mans", "https://proxy.transport.data.gouv.fr/resource/setram-lemans-gtfs-rt-vehicle-position"),
    ("Atoumod Normandie", "https://proxy.transport.data.gouv.fr/resource/atoumod-gtfs-rt-vehicle-position"),
    ("Aléop Pays de Loire", "https://proxy.transport.data.gouv.fr/resource/aleop-pdl-gtfs-rt-vehicle-position"),
    ("Fluo Grand-Est", "https://proxy.transport.data.gouv.fr/resource/fluo-haut-rhin-68-gtfs-rt-vehicle-position"),
    ("Citéa Valence", "https://proxy.transport.data.gouv.fr/resource/citea-valence-gtfs-rt-vehicle-position"),
    ("Palmbus Cannes", "https://proxy.transport.data.gouv.fr/resource/palmbus-cannes-gtfs-rt-vehicle-position"),
    ("Kicéo Vannes", "https://proxy.transport.data.gouv.fr/resource/kiceo-vannes-gtfs-rt-vehicle-position"),
]


async def _fetch_gtfsrt_feed(client: httpx.AsyncClient, name: str, url: str) -> list[dict]:
    """Fetch one GTFS-RT vehicle position feed."""
    try:
        from google.transit import gtfs_realtime_pb2
        resp = await client.get(url)
        if resp.status_code != 200 or len(resp.content) < 10:
            return []
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        vehicles = []
        for entity in feed.entity:
            vp = entity.vehicle
            lat = vp.position.latitude
            lon = vp.position.longitude
            if lat == 0 and lon == 0:
                continue
            vehicles.append({
                "id": f"FR-{name}-{entity.id}",
                "number": vp.trip.route_id or entity.id,
                "type": "transit",
                "network": name,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "speed": round(vp.position.speed, 1) if vp.position.speed else 0,
                "heading": round(vp.position.bearing, 1) if vp.position.bearing else 0,
                "country": "FR",
            })
        return vehicles
    except Exception:
        return []


async def fetch_trains_france() -> list[dict]:
    """Fetch all French GTFS-RT vehicle position feeds."""
    cached = cache_get("trains:fr")
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=12) as client:
        tasks = [_fetch_gtfsrt_feed(client, name, url) for name, url in FR_GTFSRT_FEEDS]
        results = await asyncio.gather(*tasks)
    all_vehicles = []
    for vlist in results:
        all_vehicles.extend(vlist)
    cache_set("trains:fr", all_vehicles)
    return all_vehicles


@app.get("/api/trains")
async def get_trains():
    """Get real-time transport positions in France."""
    fr_trains = await fetch_trains_france()
    return {"count": len(fr_trains), "trains": fr_trains, "sources": ["FR (transport.data.gouv.fr GTFS-RT)"]}


# --- AIS Ships (aisstream.io WebSocket) ---
AIS_API_KEY = "b4c149e43639ca50c6e0f6014949b005d93cb49c"
_ships: dict[int, dict] = {}  # MMSI -> ship data
_ais_task = None


async def _ais_stream_worker():
    """Background task: connect to AIS stream and accumulate ship positions."""
    while True:
        try:
            uri = "wss://stream.aisstream.io/v0/stream"
            msg = json.dumps({
                "APIKey": AIS_API_KEY,
                "BoundingBoxes": [[[-90, -180], [90, 180]]],
                "FilterMessageTypes": ["PositionReport"],
            })
            async with websockets.connect(uri, ping_interval=30) as ws:
                await ws.send(msg)
                while True:
                    data = await asyncio.wait_for(ws.recv(), timeout=60)
                    d = json.loads(data)
                    meta = d.get("MetaData", {})
                    pos = d.get("Message", {}).get("PositionReport", {})
                    mmsi = meta.get("MMSI")
                    lat = pos.get("Latitude")
                    lon = pos.get("Longitude")
                    if mmsi and lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
                        _ships[mmsi] = {
                            "mmsi": mmsi,
                            "name": (meta.get("ShipName") or "").strip(),
                            "lat": round(lat, 5),
                            "lon": round(lon, 5),
                            "speed": pos.get("Sog", 0),
                            "heading": pos.get("TrueHeading", 0),
                            "course": pos.get("Cog", 0),
                            "status": pos.get("NavigationalStatus", 0),
                            "ship_type": meta.get("ShipType", 0),
                            "ts": time.time(),
                        }
                    # Prune ships not seen in 10 min
                    if len(_ships) > 10000:
                        now = time.time()
                        stale = [k for k, v in _ships.items() if now - v["ts"] > 600]
                        for k in stale:
                            del _ships[k]
        except Exception as e:
            print(f"[AIS] Stream error: {e}, reconnecting in 10s...")
            await asyncio.sleep(10)


@app.on_event("startup")
async def start_background_workers():
    global _ais_task
    _ais_task = asyncio.create_task(_ais_stream_worker())
    print("[AIS] Background stream started")
    # Background flight fetcher — runs every 15s
    async def _flight_worker():
        while True:
            await _bg_fetch_flights()
            await asyncio.sleep(15)
    asyncio.create_task(_flight_worker())
    print("[BG] Flight background worker started")
    # Background satellite position computer — runs every 30s
    async def _sat_worker():
        while True:
            try:
                # Fetch from best available source (amsat works when celestrak is down)
                all_tles = []
                # Try a few key groups with short timeout
                for g in ["stations", "starlink", "gps", "military", "weather", "science"]:
                    try:
                        tles = await fetch_tle(g)
                        max_per = 200 if g == "starlink" else 200
                        for t in tles[:max_per]:
                            t["_group"] = g
                        all_tles.extend(tles[:max_per])
                    except Exception as e:
                        print(f"[BG] TLE {g}: {e}")
                    await asyncio.sleep(0.5)
                # Propagate all positions
                positions = []
                for tle in all_tles:
                    pos = propagate_satellite(tle)
                    if pos:
                        pos["group"] = tle.get("_group", "unknown")
                        pos["is_debris"] = tle.get("_group", "").startswith("debris")
                        positions.append(pos)
                # Only update if we got data
                if positions:
                    cache_set("satellites:positions", positions)
                    CACHE_TTL["satellites"] = 30
                print(f"[BG] Satellites updated: {len(positions)}")
            except Exception as e:
                print(f"[BG] Satellite error: {e}")
            await asyncio.sleep(30)
    asyncio.create_task(_sat_worker())
    print("[BG] Satellite background worker started")
    # Background earthquake/fire pre-fetcher
    async def _env_worker():
        while True:
            try:
                await get_earthquakes()
                await get_fires()
                print("[BG] Env data refreshed")
            except Exception: pass
            await asyncio.sleep(300)
    asyncio.create_task(_env_worker())
    print("[BG] Environment background worker started")


@app.get("/api/ships")
async def get_ships(limit: int = 5000):
    """Get current ship positions from AIS stream."""
    ships = sorted(_ships.values(), key=lambda s: s["ts"], reverse=True)[:limit]
    return {"count": len(ships), "total_tracked": len(_ships), "ships": ships}


# ---------------------------------------------------------------------------
# Earthquakes (USGS)
# ---------------------------------------------------------------------------
@app.get("/api/earthquakes")
async def get_earthquakes():
    cached = cache_get("earthquakes:all")
    if cached:
        return {"count": len(cached), "earthquakes": cached}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
            resp.raise_for_status()
            data = resp.json()
        quakes = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            coords = f.get("geometry", {}).get("coordinates", [0, 0, 0])
            quakes.append({"lon": coords[0], "lat": coords[1], "depth": coords[2] if len(coords) > 2 else 0,
                "mag": props.get("mag", 0), "place": props.get("place", ""), "time": props.get("time", 0),
                "url": props.get("url", ""), "tsunami": props.get("tsunami", 0), "alert": props.get("alert")})
        cache_set("earthquakes:all", quakes)
        return {"count": len(quakes), "earthquakes": quakes}
    except Exception as e:
        return {"count": 0, "earthquakes": [], "error": str(e)}


# ---------------------------------------------------------------------------
# GPS Jamming zones
# ---------------------------------------------------------------------------
GPS_JAMMING_ZONES = [
    {"name": "Eastern Mediterranean", "lat": 35.0, "lon": 33.0, "radius_km": 500, "intensity": "high", "description": "Persistent GPS spoofing/jamming"},
    {"name": "Black Sea", "lat": 43.5, "lon": 34.0, "radius_km": 400, "intensity": "high", "description": "Russian EW zone"},
    {"name": "Baltic Sea", "lat": 57.0, "lon": 20.0, "radius_km": 350, "intensity": "medium", "description": "Kaliningrad interference"},
    {"name": "Middle East", "lat": 34.0, "lon": 42.0, "radius_km": 600, "intensity": "high", "description": "Conflict zone"},
    {"name": "North Korea Border", "lat": 38.0, "lon": 127.0, "radius_km": 200, "intensity": "medium", "description": "DPRK jamming"},
    {"name": "Ukraine", "lat": 48.5, "lon": 37.0, "radius_km": 500, "intensity": "critical", "description": "Active conflict EW"},
    {"name": "Libya", "lat": 32.0, "lon": 15.0, "radius_km": 300, "intensity": "medium", "description": "Intermittent GPS disruption"},
    {"name": "Red Sea / Yemen", "lat": 15.0, "lon": 42.0, "radius_km": 400, "intensity": "high", "description": "Houthi GPS/AIS disruption"},
    {"name": "South China Sea", "lat": 12.0, "lon": 114.0, "radius_km": 300, "intensity": "low", "description": "Sporadic anomalies"},
    {"name": "India-Pakistan Border", "lat": 32.0, "lon": 74.0, "radius_km": 200, "intensity": "medium", "description": "Military jamming"},
]

@app.get("/api/gps-jamming")
async def get_gps_jamming():
    return {"count": len(GPS_JAMMING_ZONES), "zones": GPS_JAMMING_ZONES}


# ---------------------------------------------------------------------------
# Internet Outages (IODA)
# ---------------------------------------------------------------------------
@app.get("/api/internet-outages")
async def get_internet_outages():
    cached = cache_get("outages:all")
    if cached:
        return {"count": len(cached), "outages": cached}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            now = int(time.time())
            resp = await client.get(f"https://api.ioda.inetintel.cc.gatech.edu/v2/signals/raw/country?from={now - 3600}&until={now}")
            if resp.status_code != 200:
                return {"count": 0, "outages": []}
            data = resp.json()
        outages = []
        for entry in data.get("data", []):
            entity = entry.get("entity", {})
            for source in entry.get("dataseries", []):
                values = source.get("values", [])
                if values:
                    outages.append({"country": entity.get("name", ""), "code": entity.get("code", ""),
                        "source": source.get("datasource", ""), "value": values[-1]})
        cache_set("outages:all", outages)
        return {"count": len(outages), "outages": outages}
    except Exception as e:
        return {"count": 0, "outages": [], "error": str(e)}


# ---------------------------------------------------------------------------
# NASA FIRMS Active Fires
# ---------------------------------------------------------------------------
@app.get("/api/fires")
async def get_fires():
    cached = cache_get("fires:all")
    if cached:
        return {"count": len(cached), "fires": cached}
    urls = ["https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv",
            "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv"]
    fires = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200: continue
                    reader = csv.DictReader(StringIO(resp.text))
                    for row in reader:
                        try:
                            fires.append({"lat": float(row.get("latitude", 0)), "lon": float(row.get("longitude", 0)),
                                "brightness": float(row.get("brightness", row.get("bright_ti4", 0))),
                                "confidence": row.get("confidence", ""), "acq_date": row.get("acq_date", ""),
                                "frp": float(row.get("frp", 0))})
                        except (ValueError, TypeError): continue
                    if fires: break
                except Exception: continue
        if len(fires) > 5000:
            import random; fires = random.sample(fires, 5000)
        cache_set("fires:all", fires)
        return {"count": len(fires), "fires": fires}
    except Exception as e:
        return {"count": 0, "fires": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Air Quality (Open-Meteo)
# ---------------------------------------------------------------------------
AQI_CITIES = [
    ("Beijing", 39.9, 116.4), ("Delhi", 28.6, 77.2), ("Tokyo", 35.7, 139.7),
    ("Shanghai", 31.2, 121.5), ("Sao Paulo", -23.5, -46.6), ("Mumbai", 19.1, 72.9),
    ("Cairo", 30.0, 31.2), ("Mexico City", 19.4, -99.1), ("Dhaka", 23.8, 90.4),
    ("Osaka", 34.7, 135.5), ("New York", 40.7, -74.0), ("Karachi", 24.9, 67.0),
    ("Buenos Aires", -34.6, -58.4), ("Istanbul", 41.0, 29.0), ("Lagos", 6.5, 3.4),
    ("Paris", 48.9, 2.3), ("London", 51.5, -0.1), ("Bangkok", 13.8, 100.5),
    ("Lima", -12.0, -77.0), ("Jakarta", -6.2, 106.8), ("Lahore", 31.5, 74.3),
    ("Los Angeles", 34.1, -118.2), ("Moscow", 55.8, 37.6), ("Seoul", 37.6, 127.0),
    ("Nairobi", -1.3, 36.8), ("Berlin", 52.5, 13.4), ("Sydney", -33.9, 151.2),
    ("Dubai", 25.2, 55.3), ("Singapore", 1.3, 103.8), ("Riyadh", 24.7, 46.7),
]

async def _fetch_aqi_point(client, city, lat, lon):
    try:
        resp = await client.get(f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=european_aqi,pm10,pm2_5,nitrogen_dioxide,ozone")
        if resp.status_code == 200:
            d = resp.json().get("current", {})
            return {"city": city, "lat": lat, "lon": lon, "aqi": d.get("european_aqi"), "pm25": d.get("pm2_5"), "pm10": d.get("pm10"), "no2": d.get("nitrogen_dioxide"), "ozone": d.get("ozone")}
    except Exception: pass
    return None

@app.get("/api/air-quality")
async def get_air_quality():
    cached = cache_get("air-quality:all")
    if cached:
        return {"count": len(cached), "air_quality": cached}
    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(*[_fetch_aqi_point(client, c, lat, lon) for c, lat, lon in AQI_CITIES])
    data = [r for r in results if r]
    cache_set("air-quality:all", data)
    return {"count": len(data), "air_quality": data}


# ---------------------------------------------------------------------------
# Bikes (citybik.es)
# ---------------------------------------------------------------------------
@app.get("/api/bikes")
async def get_bikes():
    cached = cache_get("bikes:all")
    if cached:
        return {"count": len(cached), "networks": cached}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.citybik.es/v2/networks")
            resp.raise_for_status()
            data = resp.json()
        networks = [{"id": n.get("id", ""), "name": n.get("name", ""), "city": n.get("location", {}).get("city", ""),
            "country": n.get("location", {}).get("country", ""), "lat": n.get("location", {}).get("latitude"),
            "lon": n.get("location", {}).get("longitude")} for n in data.get("networks", [])]
        cache_set("bikes:all", networks)
        return {"count": len(networks), "networks": networks}
    except Exception as e:
        return {"count": 0, "networks": [], "error": str(e)}

@app.get("/api/bikes/{network_id}")
async def get_bike_stations(network_id: str):
    cache_key = f"bikes:{network_id}"
    cached = cache_get(cache_key)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.citybik.es/v2/networks/{network_id}")
            resp.raise_for_status()
            data = resp.json().get("network", {})
        stations = [{"name": s.get("name", ""), "lat": s.get("latitude"), "lon": s.get("longitude"),
            "free_bikes": s.get("free_bikes", 0), "empty_slots": s.get("empty_slots", 0)} for s in data.get("stations", [])]
        result = {"network": network_id, "count": len(stations), "stations": stations}
        cache_set(cache_key, result)
        return result
    except Exception as e:
        return {"network": network_id, "count": 0, "stations": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Aircraft Photos (planespotters.net)
# ---------------------------------------------------------------------------
@app.get("/api/aircraft-photo/{hex_id}")
async def get_aircraft_photo(hex_id: str):
    cache_key = f"aircraft-photo:{hex_id}"
    cached = cache_get(cache_key)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.planespotters.net/pub/photos/hex/{hex_id}", headers={"User-Agent": "SkyWatch/1.0"})
            if resp.status_code == 200:
                photos = resp.json().get("photos", [])
                if photos:
                    p = photos[0]
                    result = {"hex": hex_id, "photo_url": p.get("thumbnail_large", {}).get("src", ""),
                        "photographer": p.get("photographer", ""), "link": p.get("link", "")}
                    cache_set(cache_key, result)
                    return result
    except Exception: pass
    return {"hex": hex_id, "photo_url": "", "error": "not found"}


# ---------------------------------------------------------------------------
# Squawk Alerts
# ---------------------------------------------------------------------------
@app.get("/api/squawk-alerts")
async def get_squawk_alerts():
    flights = await fetch_flights()
    EMERGENCY_SQUAWKS = {"7700": "General Emergency", "7600": "Radio Failure", "7500": "Hijack"}
    alerts = [{**f, "alert_type": EMERGENCY_SQUAWKS[str(f.get("squawk", ""))], "alert_squawk": str(f.get("squawk", ""))}
              for f in flights if str(f.get("squawk", "")) in EMERGENCY_SQUAWKS]
    return {"count": len(alerts), "alerts": alerts}


# ---------------------------------------------------------------------------
# AI News Synthesis (Perplexity)
# ---------------------------------------------------------------------------
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
if not PERPLEXITY_API_KEY:
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            _cfg = json.load(f)
        PERPLEXITY_API_KEY = _cfg.get("plugins", {}).get("entries", {}).get("perplexity", {}).get("config", {}).get("apiKey", "") or _cfg.get("providers", {}).get("perplexity", {}).get("apiKey", "")
    except Exception: pass
if not PERPLEXITY_API_KEY:
    PERPLEXITY_API_KEY = os.environ.get("SKYWATCH_PERPLEXITY_KEY", "")

@app.get("/api/news")
async def get_news():
    cached = cache_get("news:global")
    if cached: return cached
    if not PERPLEXITY_API_KEY:
        return {"summary": "API key not configured", "error": True}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"},
                json={"model": "sonar", "messages": [{"role": "user", "content":
                    "Give a concise intelligence briefing of the most important global events in the last 24 hours. "
                    "Cover: military/security, natural disasters, geopolitical tensions, major accidents. "
                    "Format as bullet points, max 15 items. Include locations."}]})
            resp.raise_for_status()
            data = resp.json()
        summary = data.get("choices", [{}])[0].get("message", {}).get("content", "No data")
        result = {"summary": summary, "timestamp": datetime.now(timezone.utc).isoformat()}
        cache_set("news:global", result)
        return result
    except Exception as e:
        return {"summary": f"Error: {e}", "error": True}


# ---------------------------------------------------------------------------
# Finance Overlay
# ---------------------------------------------------------------------------
FINANCE_INDICES = [
    {"symbol": "^GSPC", "name": "S&P 500", "country": "US", "lat": 40.71, "lon": -74.01},
    {"symbol": "^IXIC", "name": "NASDAQ", "country": "US", "lat": 40.76, "lon": -73.98},
    {"symbol": "^DJI", "name": "Dow Jones", "country": "US", "lat": 40.71, "lon": -74.05},
    {"symbol": "^GDAXI", "name": "DAX", "country": "DE", "lat": 50.11, "lon": 8.68},
    {"symbol": "^N225", "name": "Nikkei 225", "country": "JP", "lat": 35.68, "lon": 139.69},
    {"symbol": "^FTSE", "name": "FTSE 100", "country": "GB", "lat": 51.51, "lon": -0.09},
    {"symbol": "^FCHI", "name": "CAC 40", "country": "FR", "lat": 48.87, "lon": 2.34},
    {"symbol": "^HSI", "name": "Hang Seng", "country": "HK", "lat": 22.28, "lon": 114.16},
    {"symbol": "^BSESN", "name": "BSE Sensex", "country": "IN", "lat": 19.08, "lon": 72.88},
    {"symbol": "^BVSP", "name": "Bovespa", "country": "BR", "lat": -23.55, "lon": -46.63},
    {"symbol": "^AORD", "name": "ASX All Ord", "country": "AU", "lat": -33.87, "lon": 151.21},
    {"symbol": "^KS11", "name": "KOSPI", "country": "KR", "lat": 37.57, "lon": 126.98},
]

@app.get("/api/finance")
async def get_finance():
    cached = cache_get("finance:all")
    if cached:
        return {"count": len(cached), "indices": cached}
    indices = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for idx in FINANCE_INDICES:
                try:
                    resp = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{idx['symbol']}?range=1d&interval=1d",
                        headers={"User-Agent": "SkyWatch/1.0"})
                    if resp.status_code == 200:
                        meta = resp.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                        price = meta.get("regularMarketPrice", 0)
                        prev = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
                        change_pct = ((price - prev) / prev * 100) if prev else 0
                        indices.append({**idx, "value": round(price, 2), "change_pct": round(change_pct, 2), "currency": meta.get("currency", "")})
                except Exception:
                    indices.append({**idx, "value": 0, "change_pct": 0, "error": True})
        cache_set("finance:all", indices)
        return {"count": len(indices), "indices": indices}
    except Exception as e:
        return {"count": 0, "indices": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Country Risk Index (composite)
# ---------------------------------------------------------------------------
COUNTRY_CENTROIDS = {
    "US": (39.8, -98.6), "CN": (35.0, 105.0), "IN": (22.0, 78.0), "BR": (-10.0, -55.0),
    "RU": (62.0, 100.0), "JP": (36.0, 138.0), "DE": (51.0, 10.0), "GB": (54.0, -2.0),
    "FR": (46.0, 2.0), "IT": (42.0, 12.0), "MX": (23.0, -102.0), "KR": (36.0, 128.0),
    "AU": (-25.0, 135.0), "ES": (40.0, -4.0), "TR": (39.0, 35.0), "ID": (-2.0, 118.0),
    "SA": (24.0, 45.0), "ZA": (-29.0, 25.0), "EG": (27.0, 30.0), "NG": (10.0, 8.0),
    "PK": (30.0, 70.0), "BD": (24.0, 90.0), "UA": (49.0, 32.0), "PL": (52.0, 20.0),
    "TH": (15.0, 101.0), "PH": (13.0, 122.0), "CO": (4.0, -72.0), "AR": (-34.0, -64.0),
    "IR": (32.0, 53.0), "IQ": (33.0, 44.0), "SY": (35.0, 38.0), "AF": (33.0, 66.0),
    "YE": (15.0, 48.0), "LY": (27.0, 17.0), "SD": (16.0, 30.0), "MM": (22.0, 96.0),
}

@app.get("/api/country-risk")
async def get_country_risk():
    cached = cache_get("country-risk:all")
    if cached:
        return {"count": len(cached), "countries": cached}
    risk_scores = {}
    try:
        eq_data = await get_earthquakes()
        for q in eq_data.get("earthquakes", []):
            mag = q.get("mag", 0) or 0
            if mag >= 3:
                for code, (clat, clon) in COUNTRY_CENTROIDS.items():
                    if math.sqrt((q["lat"] - clat)**2 + (q["lon"] - clon)**2) < 20:
                        risk_scores.setdefault(code, {"earthquake": 0, "fire": 0, "jamming": 0, "aqi": 0})
                        risk_scores[code]["earthquake"] += min(mag * 2, 20)
    except Exception: pass
    intensity_map = {"low": 5, "medium": 15, "high": 25, "critical": 40}
    for zone in GPS_JAMMING_ZONES:
        score = intensity_map.get(zone["intensity"], 10)
        for code, (clat, clon) in COUNTRY_CENTROIDS.items():
            if math.sqrt((zone["lat"] - clat)**2 + (zone["lon"] - clon)**2) < zone["radius_km"] / 80:
                risk_scores.setdefault(code, {"earthquake": 0, "fire": 0, "jamming": 0, "aqi": 0})
                risk_scores[code]["jamming"] += score
    countries = [{"country": code, "lat": COUNTRY_CENTROIDS[code][0], "lon": COUNTRY_CENTROIDS[code][1],
        "risk_score": round(min(sum(factors.values()), 100)), "factors": factors}
        for code, factors in risk_scores.items() if sum(factors.values()) > 0]
    countries.sort(key=lambda x: x["risk_score"], reverse=True)
    cache_set("country-risk:all", countries)
    return {"count": len(countries), "countries": countries}


# ---------------------------------------------------------------------------
# WebSocket for live updates
# ---------------------------------------------------------------------------
connected_clients: set[WebSocket] = set()

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    try:
        while True:
            # Send updated data every 10 seconds
            flights = await fetch_flights()
            stations = await fetch_tle("stations")
            sat_positions = [propagate_satellite(s) for s in stations[:50]]
            sat_positions = [s for s in sat_positions if s]

            await ws.send_json({
                "type": "update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "flights": {"count": len(flights), "sample": flights[:500]},
                "satellites": {"count": len(sat_positions), "data": sat_positions},
            })
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        connected_clients.discard(ws)
    except Exception:
        connected_clients.discard(ws)


# ---------------------------------------------------------------------------
# Serve frontend
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
