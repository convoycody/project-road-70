from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
WEB_DIR = ROOT_DIR / "web"
DB_PATH = ROOT_DIR / "data.sqlite3"

API_KEY = os.environ.get("API_KEY", "")

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def require_key(req: Request) -> None:
    if not API_KEY:
        return
    k = req.headers.get("x-api-key", "")
    if k != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def init_db() -> None:
    con = db()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_aggregates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              received_at TEXT NOT NULL,
              node_id TEXT NOT NULL,
              bucket_start TEXT NOT NULL,
              bucket_seconds INTEGER NOT NULL,
              grid_key TEXT NOT NULL,
              direction TEXT NOT NULL,
              speed_band TEXT NOT NULL,
              road_roughness REAL,
              shock_events INTEGER,
              confidence REAL,
              sample_count INTEGER NOT NULL
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS ix_bucket_start ON metric_aggregates(bucket_start);")
        con.execute("CREATE INDEX IF NOT EXISTS ix_grid_key ON metric_aggregates(grid_key);")
        con.commit()
    finally:
        con.close()

app = FastAPI(title="Project Road 70", version="0.1.0")

@app.on_event("startup")
def _startup():
    init_db()
    if not WEB_DIR.exists():
        raise RuntimeError(f"Missing web folder at {WEB_DIR}")

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

@app.get("/admin", response_class=HTMLResponse)
def admin():
    con = db()
    try:
        rows = con.execute(
            "SELECT bucket_start, grid_key, road_roughness, shock_events, confidence, speed_band, sample_count, node_id "
            "FROM metric_aggregates ORDER BY bucket_start DESC LIMIT 50"
        ).fetchall()
    finally:
        con.close()

    trs = []
    for r in rows:
        bucket_start, grid_key, rough, shocks, conf, speed_band, n, node_id = r
        trs.append(
            f"<tr><td>{bucket_start}</td><td>{grid_key}</td><td>{rough or ''}</td>"
            f"<td>{shocks or ''}</td><td>{conf or ''}</td><td>{speed_band}</td><td>{n}</td><td>{node_id}</td></tr>"
        )

    return HTMLResponse(f"""
    <html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Admin</title>
    <style>
      body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, Arial; margin: 24px; }}
      .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ border-bottom: 1px solid #eee; padding: 10px; text-align: left; font-size: 14px; }}
      th {{ font-size: 12px; opacity: 0.7; text-transform: uppercase; }}
      code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
    </style></head><body>
      <div class="card">
        <h2 style="margin:0 0 6px 0;">Project Road 70 • v0.1.0</h2>
        <div>Web app: <a href="/">/</a></div>
      </div>
      <div class="card">
        <h3 style="margin:0 0 12px 0;">Latest aggregates</h3>
        <table>
          <thead><tr><th>Bucket</th><th>Grid</th><th>Rough</th><th>Shocks</th><th>Conf</th><th>Speed</th><th>N</th><th>Node</th></tr></thead>
          <tbody>{''.join(trs) if trs else '<tr><td colspan="8">No data yet.</td></tr>'}</tbody>
        </table>
      </div>
    </body></html>
    """)

@app.post("/v1/ingest/aggregates")
async def ingest_aggregates(request: Request):
    require_key(request)
    payload = await request.json()

    node_id = str(payload.get("node_id", "")).strip()
    items = payload.get("items", [])
    if not node_id or not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Invalid payload: require node_id and items[]")

    received_at = utc_now()
    con = db()
    try:
        inserted = 0
        for it in items:
            bucket_start = str(it.get("bucket_start", "")).strip()
            bucket_seconds = int(it.get("bucket_seconds", 60))
            grid_key = str(it.get("grid_key", "")).strip()
            direction = str(it.get("direction", "UNK"))[:8]
            speed_band = str(it.get("speed_band", "UNK"))[:16]
            sample_count = int(it.get("sample_count", 0))

            road_roughness = it.get("road_roughness", None)
            shock_events = it.get("shock_events", None)
            confidence = it.get("confidence", None)

            if not bucket_start or not grid_key or sample_count <= 0:
                continue

            con.execute(
                """
                INSERT INTO metric_aggregates
                (received_at, node_id, bucket_start, bucket_seconds, grid_key, direction, speed_band,
                 road_roughness, shock_events, confidence, sample_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (received_at, node_id, bucket_start, bucket_seconds, grid_key, direction, speed_band,
                 road_roughness, shock_events, confidence, sample_count)
            )
            inserted += 1
        con.commit()
    finally:
        con.close()

    return {"ok": True, "inserted": inserted}

@app.get("/v1/latest")
def latest(limit: int = 50):
    limit = max(1, min(200, int(limit)))
    con = db()
    try:
        rows = con.execute(
            "SELECT bucket_start, grid_key, road_roughness, shock_events, confidence, speed_band, sample_count "
            "FROM metric_aggregates ORDER BY bucket_start DESC LIMIT ?",
            (limit,)
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "bucket_start": r[0],
            "grid_key": r[1],
            "road_roughness": r[2],
            "shock_events": r[3],
            "confidence": r[4],
            "speed_band": r[5],
            "sample_count": r[6],
        }
        for r in rows
    ]
