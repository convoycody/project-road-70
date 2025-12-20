from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

def _now() -> int:
    return int(time.time())

def _col_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)

def ensure_schema(con: sqlite3.Connection) -> None:
    # add columns to metric_aggregates if missing
    # (safe: only adds if not present)
    if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metric_aggregates'").fetchone() is None:
        # if somehow missing, do nothing here (main.py should create base)
        return

    for col, typ in [
        ("road_name", "TEXT"),
        ("hwy_ref", "TEXT"),
        ("state", "TEXT"),
        ("county", "TEXT"),
        ("city", "TEXT"),
        ("geocode_src", "TEXT"),
        ("geocoded_at", "INTEGER"),
        ("segment_id", "TEXT"),
    ]:
        if not _col_exists(con, "metric_aggregates", col):
            con.execute(f"ALTER TABLE metric_aggregates ADD COLUMN {col} {typ}")

    con.execute("""
    CREATE TABLE IF NOT EXISTS road_segments (
      segment_id TEXT PRIMARY KEY,
      hwy_ref TEXT,
      road_name TEXT,
      state TEXT,
      county TEXT,
      city TEXT,
      centroid_lat REAL,
      centroid_lon REAL,
      created_at INTEGER,
      updated_at INTEGER
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS road_scores (
      segment_id TEXT PRIMARY KEY,
      window_days INTEGER NOT NULL,
      rows_used INTEGER NOT NULL,
      score REAL NOT NULL,
      roughness_mean REAL,
      shock_p95 REAL,
      confidence_mean REAL,
      updated_at INTEGER NOT NULL,
      FOREIGN KEY(segment_id) REFERENCES road_segments(segment_id)
    )
    """)
    con.commit()

def make_segment_id(hwy_ref: Optional[str], road_name: Optional[str], state: Optional[str]) -> str:
    base = "|".join([
        (hwy_ref or "").strip().upper(),
        (road_name or "").strip().upper(),
        (state or "").strip().upper(),
    ])
    if base == "||":
        base = "UNKNOWN"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

def upsert_segment(con: sqlite3.Connection, d: Dict[str, Any]) -> str:
    seg = make_segment_id(d.get("hwy_ref"), d.get("road_name"), d.get("state"))
    lat = d.get("lat")
    lon = d.get("lon")
    now = _now()

    con.execute("""
      INSERT INTO road_segments(segment_id, hwy_ref, road_name, state, county, city, centroid_lat, centroid_lon, created_at, updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(segment_id) DO UPDATE SET
        hwy_ref=excluded.hwy_ref,
        road_name=excluded.road_name,
        state=excluded.state,
        county=excluded.county,
        city=excluded.city,
        centroid_lat=COALESCE(excluded.centroid_lat, road_segments.centroid_lat),
        centroid_lon=COALESCE(excluded.centroid_lon, road_segments.centroid_lon),
        updated_at=excluded.updated_at
    """, (
        seg,
        d.get("hwy_ref"),
        d.get("road_name"),
        d.get("state"),
        d.get("county"),
        d.get("city"),
        float(lat) if isinstance(lat, (int,float)) else None,
        float(lon) if isinstance(lon, (int,float)) else None,
        now,
        now
    ))
    con.commit()
    return seg

def recompute_scores(con: sqlite3.Connection, window_days: int = 7) -> Dict[str, Any]:
    """
    Computes a simple, stable score per road segment based on metric_aggregates.
    score: lower is better (smoother). Higher = rougher.

    weighted roughness mean + shock p95 blended, lightly normalized.
    """
    ensure_schema(con)
    now = _now()
    since_ts = now - window_days * 86400

    # We use geocoded+segmented rows where analyzable=1 (if present)
    rows = con.execute("""
      SELECT
        COALESCE(segment_id,'') AS segment_id,
        AVG(CASE WHEN road_roughness IS NOT NULL THEN road_roughness END) AS rough_mean,
        AVG(CASE WHEN confidence IS NOT NULL THEN confidence END) AS conf_mean,
        COUNT(*) AS n
      FROM metric_aggregates
      WHERE (geocoded_at IS NOT NULL AND geocoded_at >= ?)
        AND (segment_id IS NOT NULL AND segment_id != '')
        AND (analyzable IS NULL OR analyzable=1)
      GROUP BY segment_id
      HAVING n >= 5
    """, (since_ts,)).fetchall()

    def p95_shocks(seg: str) -> Optional[float]:
        # percentile via ordering; SQLite lacks percentile_*
        xs = con.execute("""
          SELECT shock_events FROM metric_aggregates
          WHERE segment_id=? AND shock_events IS NOT NULL
            AND (geocoded_at IS NOT NULL AND geocoded_at >= ?)
            AND (analyzable IS NULL OR analyzable=1)
          ORDER BY shock_events
        """, (seg, since_ts)).fetchall()
        if not xs:
            return None
        vals = [float(x[0]) for x in xs]
        k = int(round(0.95*(len(vals)-1)))
        return vals[max(0, min(k, len(vals)-1))]

    roads_scored = 0
    rows_used = 0

    for seg, rough_mean, conf_mean, n in rows:
        seg = str(seg)
        shock95 = p95_shocks(seg)

        # base score: roughness dominates; shocks contribute secondarily
        rm = float(rough_mean) if rough_mean is not None else 0.0
        s95 = float(shock95) if shock95 is not None else 0.0
        cm = float(conf_mean) if conf_mean is not None else 1.0

        # normalize shocks a bit (so a few jolts doesn't explode)
        score = (rm * 100.0) + (min(s95, 20.0) * 2.5)

        # penalize low confidence slightly
        score = score * (1.0 + max(0.0, (0.9 - cm)) * 0.25)

        con.execute("""
          INSERT INTO road_scores(segment_id, window_days, rows_used, score, roughness_mean, shock_p95, confidence_mean, updated_at)
          VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(segment_id) DO UPDATE SET
            window_days=excluded.window_days,
            rows_used=excluded.rows_used,
            score=excluded.score,
            roughness_mean=excluded.roughness_mean,
            shock_p95=excluded.shock_p95,
            confidence_mean=excluded.confidence_mean,
            updated_at=excluded.updated_at
        """, (seg, window_days, int(n), float(score), rm, s95, cm, now))
        roads_scored += 1
        rows_used += int(n)

    con.commit()
    return {"roads_scored": roads_scored, "rows_used": rows_used, "window_days": window_days}

def top_roads(con: sqlite3.Connection, limit: int = 50, state: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_schema(con)
    q = """
      SELECT s.segment_id, s.hwy_ref, s.road_name, s.state, s.county, s.city,
             rs.score, rs.rows_used, rs.roughness_mean, rs.shock_p95, rs.confidence_mean, rs.updated_at
      FROM road_scores rs
      JOIN road_segments s ON s.segment_id = rs.segment_id
      WHERE rs.window_days=7
    """
    args: List[Any] = []
    if state:
        q += " AND UPPER(COALESCE(s.state,'')) = UPPER(?)"
        args.append(state)
    q += " ORDER BY rs.score DESC LIMIT ?"
    args.append(int(limit))

    out = []
    for r in con.execute(q, args).fetchall():
        out.append({
            "segment_id": r[0],
            "hwy_ref": r[1],
            "road_name": r[2],
            "state": r[3],
            "county": r[4],
            "city": r[5],
            "score": r[6],
            "rows_used": r[7],
            "roughness_mean": r[8],
            "shock_p95": r[9],
            "confidence_mean": r[10],
            "updated_at": r[11],
        })
    return out

def road_detail(con: sqlite3.Connection, segment_id: str) -> Dict[str, Any]:
    ensure_schema(con)
    seg = con.execute("SELECT segment_id, hwy_ref, road_name, state, county, city, centroid_lat, centroid_lon FROM road_segments WHERE segment_id=?",
                      (segment_id,)).fetchone()
    if not seg:
        return {"segment_id": segment_id, "found": False}
    scores = con.execute("SELECT window_days, score, rows_used, roughness_mean, shock_p95, confidence_mean, updated_at FROM road_scores WHERE segment_id=? ORDER BY window_days",
                         (segment_id,)).fetchall()
    return {
        "found": True,
        "segment": {
            "segment_id": seg[0],
            "hwy_ref": seg[1],
            "road_name": seg[2],
            "state": seg[3],
            "county": seg[4],
            "city": seg[5],
            "centroid_lat": seg[6],
            "centroid_lon": seg[7],
        },
        "scores": [
            {"window_days": s[0], "score": s[1], "rows_used": s[2], "roughness_mean": s[3], "shock_p95": s[4], "confidence_mean": s[5], "updated_at": s[6]}
            for s in scores
        ]
    }

def roads_near(con: sqlite3.Connection, lat: float, lon: float, limit: int = 25) -> List[Dict[str, Any]]:
    ensure_schema(con)
    # simple distance proxy (not haversine) good enough for "near-ish"
    out = []
    for r in con.execute("""
      SELECT segment_id, hwy_ref, road_name, state, county, city, centroid_lat, centroid_lon
      FROM road_segments
      WHERE centroid_lat IS NOT NULL AND centroid_lon IS NOT NULL
      ORDER BY ((centroid_lat-?)*(centroid_lat-?) + (centroid_lon-?)*(centroid_lon-?)) ASC
      LIMIT ?
    """, (lat, lat, lon, lon, int(limit))).fetchall():
        out.append({
            "segment_id": r[0], "hwy_ref": r[1], "road_name": r[2],
            "state": r[3], "county": r[4], "city": r[5],
            "centroid_lat": r[6], "centroid_lon": r[7],
        })
    return out
