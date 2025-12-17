import sqlite3
from typing import Any, Dict

SQL_INSERT_METRIC = """
INSERT INTO metric_aggregates (
  received_at, node_id, bucket_start, bucket_seconds, grid_key,
  direction, speed_band,
  road_roughness, shock_events, confidence, sample_count,
  lat, lon,
  analyzable, points_eligible, quality_note,
  mount_state, moving,
  speed_mps, heading_deg,
  motion_g, motion_rms, device_posture,
  short_location, road_name
) VALUES (
  :received_at, :node_id, :bucket_start, :bucket_seconds, :grid_key,
  :direction, :speed_band,
  :road_roughness, :shock_events, :confidence, :sample_count,
  :lat, :lon,
  :analyzable, :points_eligible, :quality_note,
  :mount_state, :moving,
  :speed_mps, :heading_deg,
  :motion_g, :motion_rms, :device_posture,
  :short_location, :road_name
)
"""

def _f(x: Any):
    try:
        return float(x)
    except Exception:
        return None

def normalize_metric(row: Dict[str, Any]) -> Dict[str, Any]:
    # allow alias keys from older clients
    speed = row.get("speed_mps", row.get("speed"))
    heading = row.get("heading_deg", row.get("heading"))

    d = {
        "received_at": row.get("received_at"),
        "node_id": row.get("node_id", "unknown"),
        "bucket_start": row.get("bucket_start") or row.get("received_at"),
        "bucket_seconds": int(row.get("bucket_seconds") or 5),
        "grid_key": row.get("grid_key") or "",
        "direction": row.get("direction") or "unknown",
        "speed_band": row.get("speed_band") or "unknown",
        "road_roughness": row.get("road_roughness"),
        "shock_events": row.get("shock_events"),
        "confidence": row.get("confidence"),
        "sample_count": int(row.get("sample_count") or 0),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "analyzable": int(row.get("analyzable") if row.get("analyzable") is not None else 1),
        "points_eligible": int(row.get("points_eligible") if row.get("points_eligible") is not None else 1),
        "quality_note": row.get("quality_note"),
        "mount_state": row.get("mount_state"),
        "moving": int(row.get("moving") or 0),
        "speed_mps": speed,
        "heading_deg": heading,
        "motion_g": row.get("motion_g"),
        "motion_rms": row.get("motion_rms"),
        "device_posture": row.get("device_posture"),
        "short_location": row.get("short_location"),
        "road_name": row.get("road_name"),
    }

    # sanitize lat/lon
    lat = _f(d["lat"])
    lon = _f(d["lon"])
    if lat is None or abs(lat) > 90: lat = None
    if lon is None or abs(lon) > 180: lon = None
    d["lat"], d["lon"] = lat, lon

    # sanitize speed/heading
    sp = _f(d["speed_mps"])
    if sp is not None and sp < 0: sp = None
    d["speed_mps"] = sp

    hd = _f(d["heading_deg"])
    if hd is not None and (hd < 0 or hd >= 360): hd = None
    d["heading_deg"] = hd

    return d

def insert_metric_row(con: sqlite3.Connection, row: Dict[str, Any]) -> None:
    con.execute(SQL_INSERT_METRIC, normalize_metric(row))
