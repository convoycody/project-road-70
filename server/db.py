from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT_DIR / "data.sqlite3")))

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def table_cols(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}

def init_db() -> None:
    con = db()
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS metric_aggregates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          received_at TEXT NOT NULL,
          node_id TEXT NOT NULL,
          bucket_start TEXT NOT NULL,
          bucket_seconds INTEGER NOT NULL,
          grid_key TEXT NOT NULL,
          lat REAL,
          lon REAL,
          direction TEXT NOT NULL,
          speed_band TEXT NOT NULL,
          road_roughness REAL,
          shock_events INTEGER,
          confidence REAL,
          sample_count INTEGER,
          analyzable INTEGER DEFAULT 1,
          points_eligible INTEGER DEFAULT 0,
          quality_note TEXT DEFAULT '',
          mount_state TEXT DEFAULT '',
          moving INTEGER DEFAULT 0,
          speed_mps REAL,
          heading_deg REAL,
          motion_g REAL,
          motion_rms REAL,
          device_posture TEXT DEFAULT '',
          short_location TEXT DEFAULT '',
          road_name TEXT DEFAULT ''
        );
        """)
        # rollups
        con.execute("""
        CREATE TABLE IF NOT EXISTS segment_hourly (
          segment_key TEXT NOT NULL,
          hour_bucket TEXT NOT NULL,
          n_samples INTEGER NOT NULL,
          avg_roughness REAL,
          p50_roughness REAL,
          p95_roughness REAL,
          avg_quality REAL,
          score REAL,
          score_confidence REAL,
          anomaly_flag INTEGER DEFAULT 0,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(segment_key, hour_bucket)
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS segment_latest (
          segment_key TEXT PRIMARY KEY,
          score REAL,
          score_confidence REAL,
          n_7d INTEGER DEFAULT 0,
          n_30d INTEGER DEFAULT 0,
          last_seen TEXT,
          road_name_display TEXT DEFAULT '',
          short_location_display TEXT DEFAULT '',
          updated_at TEXT NOT NULL
        );
        """)
        con.commit()
    finally:
        con.close()

def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1

def compute_score(r50: float, r95: float) -> float:
    # Tune later. This is stable + explainable.
    a = 45.0
    b = 30.0
    penalty = a * r50 + b * max(0.0, (r95 - r50))
    score = 100.0 - max(0.0, min(100.0, penalty))
    return float(score)

def compute_confidence(n: int, avg_quality: float) -> float:
    # saturating curve on sample count
    import math
    n_term = 1.0 / (1.0 + math.exp(-0.25 * (n - 8)))  # ~0.5 at n=8
    return float(max(0.0, min(1.0, n_term * max(0.0, min(1.0, avg_quality)))))

def rollup_hour(con: sqlite3.Connection, segment_key: str, hour_bucket: str, now_iso: str) -> None:
    rows = con.execute("""
      SELECT road_roughness, confidence
      FROM metric_aggregates
      WHERE grid_key = ?
        AND substr(bucket_start,1,13) = substr(?,1,13)
        AND analyzable = 1
        AND road_roughness IS NOT NULL
    """, (segment_key, hour_bucket)).fetchall()

    rough = [float(r["road_roughness"]) for r in rows if r["road_roughness"] is not None]
    qual = [float(r["confidence"]) for r in rows if r["confidence"] is not None]

    n = len(rough)
    if n == 0:
        return

    avg_rough = sum(rough) / n
    r50 = percentile(rough, 0.50) or avg_rough
    r95 = percentile(rough, 0.95) or max(rough)
    avg_q = (sum(qual) / len(qual)) if qual else 0.7

    score = compute_score(r50, r95)
    conf = compute_confidence(n, avg_q)

    con.execute("""
      INSERT OR REPLACE INTO segment_hourly(
        segment_key, hour_bucket, n_samples,
        avg_roughness, p50_roughness, p95_roughness,
        avg_quality, score, score_confidence, anomaly_flag, updated_at
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        segment_key, hour_bucket, int(n),
        float(avg_rough), float(r50), float(r95),
        float(avg_q), float(score), float(conf), 0, now_iso
    ))

def update_latest(con: sqlite3.Connection, segment_key: str, now_iso: str) -> None:
    # latest = weighted by recency via hourly buckets (simple: just take most recent hour)
    row = con.execute("""
      SELECT score, score_confidence, hour_bucket
      FROM segment_hourly
      WHERE segment_key = ?
      ORDER BY hour_bucket DESC
      LIMIT 1
    """, (segment_key,)).fetchone()
    if not row:
        return

    # pick a display road/short from newest aggregate with geo strings
    disp = con.execute("""
      SELECT road_name, short_location, bucket_start
      FROM metric_aggregates
      WHERE grid_key = ?
      ORDER BY id DESC
      LIMIT 1
    """, (segment_key,)).fetchone()

    con.execute("""
      INSERT OR REPLACE INTO segment_latest(
        segment_key, score, score_confidence, n_7d, n_30d, last_seen,
        road_name_display, short_location_display, updated_at
      ) VALUES (?,?,?,?,?,?,?,?,?)
    """, (
      segment_key,
      float(row["score"]) if row["score"] is not None else None,
      float(row["score_confidence"]) if row["score_confidence"] is not None else None,
      0, 0,
      disp["bucket_start"] if disp else now_iso,
      (disp["road_name"] or "") if disp else "",
      (disp["short_location"] or "") if disp else "",
      now_iso
    ))
