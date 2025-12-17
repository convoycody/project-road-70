from __future__ import annotations
import time, sqlite3, json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import requests

DB_PATH = "./data.sqlite3"

def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def _key(lat: float, lon: float) -> str:
    # ~11m rounding. Good enough to avoid hammering geocoder.
    return f"{lat:.4f},{lon:.4f}"

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def reverse_geocode_short(lat: float, lon: float, timeout_s: float = 1.8) -> Optional[Dict[str, Any]]:
    """
    Uses OpenStreetMap Nominatim reverse geocoding.
    - Cached by rounded lat/lon.
    - Returns dict with road_name + short_location, etc.
    """
    k = _key(lat, lon)
    con = _db()
    try:
        row = con.execute("SELECT * FROM geocode_cache WHERE key = ?", (k,)).fetchone()
        if row and row["short_location"]:
            return dict(row)

        # Nominatim policy: provide a descriptive User-Agent
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "format": "jsonv2",
            "lat": str(lat),
            "lon": str(lon),
            "zoom": "18",
            "addressdetails": "1",
        }
        headers = {"User-Agent": "RoadState/0.1 (admin@roadstate.club)"}
        r = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        if r.status_code != 200:
            return None

        data = r.json()
        addr = data.get("address", {}) or {}

        road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or addr.get("footway")
        house_number = addr.get("house_number")
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet")
        state = addr.get("state")
        postcode = addr.get("postcode")

        # "Short location" MVP: "Road • City, ST" or "Road • near City, ST"
        parts = []
        if road:
            parts.append(road)
        if city and state:
            parts.append(f"{city}, {state}")
        elif city:
            parts.append(city)
        elif state:
            parts.append(state)

        short_location = " • ".join(parts) if parts else None

        con.execute("""
            INSERT INTO geocode_cache (key, created_at, road, house_number, city, state, postcode, short_location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              created_at=excluded.created_at,
              road=excluded.road,
              house_number=excluded.house_number,
              city=excluded.city,
              state=excluded.state,
              postcode=excluded.postcode,
              short_location=excluded.short_location
        """, (k, _utc_now_z(), road, house_number, city, state, postcode, short_location))
        con.commit()

        return {
            "key": k,
            "created_at": _utc_now_z(),
            "road": road,
            "house_number": house_number,
            "city": city,
            "state": state,
            "postcode": postcode,
            "short_location": short_location,
        }
    finally:
        con.close()
