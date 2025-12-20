from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple

import requests

# Nominatim policy wants a real User-Agent with contact.
UA = os.environ.get("GEOCODE_UA", "RoadScoreNetwork/0.1 (admin@roadscore.local)")
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse")
TIMEOUT_S = float(os.environ.get("GEOCODE_TIMEOUT_S", "6.0"))
SLEEP_BETWEEN_S = float(os.environ.get("GEOCODE_SLEEP_S", "0.9"))

def _round_key(lat: float, lon: float, precision: int = 5) -> str:
    return f"{lat:.{precision}f},{lon:.{precision}f}"

def ensure_cache(con: sqlite3.Connection) -> None:
    con.execute("""
      CREATE TABLE IF NOT EXISTS geocode_cache (
        key TEXT PRIMARY KEY,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL
      )
    """)

def cache_get(con: sqlite3.Connection, lat: float, lon: float) -> Optional[Dict[str, Any]]:
    ensure_cache(con)
    k = _round_key(lat, lon)
    row = con.execute("SELECT payload FROM geocode_cache WHERE key = ?", (k,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None

def cache_put(con: sqlite3.Connection, lat: float, lon: float, created_at: str, payload: Dict[str, Any]) -> None:
    ensure_cache(con)
    k = _round_key(lat, lon)
    con.execute(
        "INSERT OR REPLACE INTO geocode_cache(key, lat, lon, created_at, payload) VALUES(?,?,?,?,?)",
        (k, float(lat), float(lon), created_at, json.dumps(payload, ensure_ascii=False)),
    )

def reverse_geocode_short(lat: float, lon: float) -> Optional[Dict[str, str]]:
    """
    Returns:
      { "road": "...", "short_location": "..." }
    """
    params = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "zoom": 18,
        "addressdetails": 1,
    }
    headers = {"User-Agent": UA}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=TIMEOUT_S)
    r.raise_for_status()
    j = r.json()
    addr = j.get("address", {}) or {}

    road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or addr.get("footway")
    # Prefer a readable shorthand: "Road • Cross St"
    cross = addr.get("intersection") or addr.get("neighbourhood") or addr.get("suburb") or addr.get("hamlet")
    city = addr.get("city") or addr.get("town") or addr.get("village")
    state = addr.get("state")
    if road and cross:
        short = f"{road} • {cross}"
    elif road and city:
        short = f"{road} • {city}"
    elif road and state:
        short = f"{road} • {state}"
    else:
        short = road or (j.get("display_name") or "")

    return {"road": road or "", "short_location": short or ""}

def geocode_with_cache(con: sqlite3.Connection, lat: float, lon: float, now_iso: str) -> Optional[Dict[str, str]]:
    cached = cache_get(con, lat, lon)
    if cached:
        return cached

    # Be nice to the upstream.
    time.sleep(SLEEP_BETWEEN_S)

    out = reverse_geocode_short(lat, lon)
    if out:
        cache_put(con, lat, lon, now_iso, out)
    return out
