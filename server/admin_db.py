import sqlite3
from typing import Any, Dict, List, Optional, Tuple

DB_DEFAULT = "./data.sqlite3"

def db_path() -> str:
    return DB_DEFAULT

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn

FILTER_KEYS = {
    "node_id", "grid_key", "direction", "speed_band",
    "analyzable", "points_eligible",
    "min_conf", "max_conf",
    "from_ts", "to_ts",
    "has_latlon",
    "quality_note",
}

def build_where(params: Dict[str, Any]) -> Tuple[str, List[Any]]:
    w = []
    a: List[Any] = []

    node_id = params.get("node_id")
    if node_id:
        w.append("node_id = ?"); a.append(node_id)

    grid_key = params.get("grid_key")
    if grid_key:
        w.append("grid_key = ?"); a.append(grid_key)

    direction = params.get("direction")
    if direction:
        w.append("direction = ?"); a.append(direction)

    speed_band = params.get("speed_band")
    if speed_band:
        w.append("speed_band = ?"); a.append(speed_band)

    quality_note = params.get("quality_note")
    if quality_note:
        w.append("quality_note = ?"); a.append(quality_note)

    analyzable = params.get("analyzable")
    if analyzable in ("0", "1"):
        w.append("analyzable = ?"); a.append(int(analyzable))

    points = params.get("points_eligible")
    if points in ("0", "1"):
        w.append("points_eligible = ?"); a.append(int(points))

    min_conf = params.get("min_conf")
    if min_conf not in (None, ""):
        w.append("confidence >= ?"); a.append(float(min_conf))

    max_conf = params.get("max_conf")
    if max_conf not in (None, ""):
        w.append("confidence <= ?"); a.append(float(max_conf))

    from_ts = params.get("from_ts")
    if from_ts:
        w.append("bucket_start >= ?"); a.append(from_ts)

    to_ts = params.get("to_ts")
    if to_ts:
        w.append("bucket_start <= ?"); a.append(to_ts)

    has_latlon = params.get("has_latlon")
    if has_latlon in ("0", "1"):
        if has_latlon == "1":
            w.append("(lat IS NOT NULL AND lon IS NOT NULL)")
        else:
            w.append("(lat IS NULL OR lon IS NULL)")

    if not w:
        return "", []
    return " WHERE " + " AND ".join(w), a

def list_rows(params: Dict[str, Any], limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    where_sql, args = build_where(params)

    with connect() as c:
        total = c.execute(f"SELECT COUNT(*) AS n FROM metric_aggregates{where_sql}", args).fetchone()["n"]
        rows = c.execute(
            f"""SELECT id, received_at, node_id, bucket_start, bucket_seconds, grid_key,
                       direction, speed_band, road_roughness, shock_events, confidence, sample_count,
                       lat, lon, analyzable, points_eligible, quality_note
                FROM metric_aggregates
                {where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?""",
            args + [limit, offset]
        ).fetchall()

    return {"total": total, "rows": [dict(r) for r in rows]}

def get_row(row_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM metric_aggregates WHERE id = ?", [row_id]).fetchone()
        return dict(r) if r else None

EDITABLE = {
    "road_roughness", "shock_events", "confidence", "sample_count",
    "lat", "lon",
    "analyzable", "points_eligible",
    "quality_note",
    "direction", "speed_band", "grid_key", "node_id", "bucket_start", "bucket_seconds",
}

def update_row(row_id: int, patch: Dict[str, Any]) -> None:
    cols = []
    args: List[Any] = []
    for k, v in patch.items():
        if k not in EDITABLE:
            continue
        cols.append(f"{k} = ?")
        args.append(v)
    if not cols:
        return
    args.append(row_id)
    with connect() as c:
        c.execute(f"UPDATE metric_aggregates SET {', '.join(cols)} WHERE id = ?", args)
        c.commit()

def delete_row(row_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM metric_aggregates WHERE id = ?", [row_id])
        c.commit()

def distinct_values(col: str, limit: int = 200) -> List[str]:
    if col not in ("node_id", "grid_key", "direction", "speed_band", "quality_note"):
        return []
    with connect() as c:
        rows = c.execute(f"SELECT DISTINCT {col} AS v FROM metric_aggregates WHERE {col} IS NOT NULL ORDER BY v LIMIT ?", [limit]).fetchall()
        return [r["v"] for r in rows if r["v"] is not None]

def series(params: Dict[str, Any], max_points: int = 300) -> Dict[str, Any]:
    where_sql, args = build_where(params)
    # Aggregate by bucket_start (already a time bucket)
    q = f"""
      SELECT bucket_start AS t,
             AVG(COALESCE(confidence,0)) AS conf_avg,
             SUM(COALESCE(shock_events,0)) AS shocks_sum,
             SUM(COALESCE(sample_count,0)) AS samples_sum,
             SUM(CASE WHEN analyzable=1 THEN 1 ELSE 0 END) AS analyzable_rows
      FROM metric_aggregates
      {where_sql}
      GROUP BY bucket_start
      ORDER BY bucket_start DESC
      LIMIT ?
    """
    with connect() as c:
        rows = c.execute(q, args + [max_points]).fetchall()
    rows = list(reversed(rows))
    return {
        "t": [r["t"] for r in rows],
        "conf_avg": [float(r["conf_avg"] or 0) for r in rows],
        "shocks_sum": [int(r["shocks_sum"] or 0) for r in rows],
        "samples_sum": [int(r["samples_sum"] or 0) for r in rows],
        "analyzable_rows": [int(r["analyzable_rows"] or 0) for r in rows],
    }
