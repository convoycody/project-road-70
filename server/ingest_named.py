import sqlite3
from typing import Any, Dict, Tuple

def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}

def _sanitize_latlon(d: Dict[str, Any]) -> None:
    if "lat" in d and d["lat"] is not None:
        try:
            lat = float(d["lat"])
            if abs(lat) > 90: d["lat"] = None
            else: d["lat"] = lat
        except Exception:
            d["lat"] = None
    if "lon" in d and d["lon"] is not None:
        try:
            lon = float(d["lon"])
            if abs(lon) > 180: d["lon"] = None
            else: d["lon"] = lon
        except Exception:
            d["lon"] = None

def insert_metric_aggregate(con: sqlite3.Connection, payload: Dict[str, Any]) -> int:
    """
    Schema-driven insert using explicit column names.
    Prevents mis-ordered values (your current 0.97/0.0 issue).
    """
    table = "metric_aggregates"
    cols = _cols(con, table)

    aliases = {
        "speed": "speed_mps",
        "heading": "heading_deg",
        "lat_deg": "lat",
        "lon_deg": "lon",
    }

    d: Dict[str, Any] = {}
    for k, v in (payload or {}).items():
        kk = aliases.get(k, k)
        if kk in cols:
            d[kk] = v

    # Minimum required columns (match your table)
    required_defaults = {
        "received_at": None,
        "node_id": "unknown",
        "bucket_start": None,
        "bucket_seconds": 5,
        "grid_key": "unknown",
        "direction": "unknown",
        "speed_band": "unknown",
        "sample_count": 1,
    }
    for k, dv in required_defaults.items():
        if k in cols and (k not in d or d[k] in (None, "")):
            d[k] = dv

    # Ensure ints where expected if present
    for k in ("bucket_seconds", "shock_events", "sample_count", "moving", "analyzable", "points_eligible"):
        if k in d and d[k] is not None:
            try:
                d[k] = int(d[k])
            except Exception:
                d[k] = 0 if k in ("shock_events", "moving") else 1

    _sanitize_latlon(d)

    # Only insert keys that are real columns
    keys = [k for k in d.keys() if k in cols]
    if not keys:
        raise ValueError("No writable fields")

    # Deterministic, explicit column list
    col_list = ", ".join(keys)
    ph_list = ", ".join([f":{k}" for k in keys])
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({ph_list})"

    cur = con.execute(sql, d)
    return int(cur.lastrowid)
