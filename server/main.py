from __future__ import annotations



def _sanitize_lat_lon(d: dict) -> None:
    # Convert and validate lat/lon; if suspicious, null them and note why.
    def _f(x):
        if x is None or x == "": return None
        try: return float(x)
        except Exception: return None

    lat = _f(d.get("lat"))
    lon = _f(d.get("lon"))

    bad = []
    if lat is not None and abs(lat) > 90: bad.append("lat_out_of_range")
    if lon is not None and abs(lon) > 180: bad.append("lon_out_of_range")

    # patterns we've seen in your DB
    if lat is not None and lon is not None:
        if abs(lon) < 1e-6 and 0 < abs(lat) < 2:
            bad.append("lon_zero_lat_tiny")
        # lon looks like speed, lat looks like a normalized value
        if 0 <= abs(lon) <= 80 and 0 < abs(lat) < 2 and d.get("speed_mps") is None:
            bad.append("lon_looks_like_speed")

    if bad:
        d["lat"] = None
        d["lon"] = None
        q = (d.get("quality_note") or "").strip()
        tag = "sanity_check:" + ",".join(bad)
        d["quality_note"] = (q + (" | " if q else "") + tag).strip()
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from .ingest_named import insert_metric_aggregate

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

# 

@app.get("/admin")
def admin_page():
    # Serve dedicated admin HTML (dark themed)
    return HTMLResponse((WEB_DIR / "admin.html").read_text())

@app.get("/admin/data")
def admin_data(limit: int = 200):
    limit = max(10, min(int(limit or 200), 2000))
    con = db()
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
          SELECT id, received_at, node_id, lat, lon, speed_mps, heading_deg,
                 confidence, moving, mount_state, road_name, short_location, quality_note
          FROM metric_aggregates
          ORDER BY id DESC
          LIMIT ?
        """, (limit,)).fetchall()
        return {"rows": [dict(r) for r in rows]}
    finally:
        con.close()

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

async def ingest_aggregates(payload: dict = Body(...)):
    con = db()
    try:
        rid = insert_metric_aggregate(con, payload)
        con.commit()
        return {"ok": True, "id": rid}
    except Exception as e:
        con.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()

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


@app.get("/health")
async def health():
    return {"ok": True}


def _table_cols(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}

def _normalize_row(d: dict, cols: set[str]) -> dict:
    # Accept a few aliases from client
    aliases = {
        "speed": "speed_mps",
        "heading": "heading_deg",
        "lat_deg": "lat",
        "lon_deg": "lon",
    }
    out = {}
    for k,v in (d or {}).items():
        kk = aliases.get(k, k)
        if kk in cols:
            out[kk] = v

    # Light sanity guards (prevents confidence->lat type disasters)
    try:
        if "lat" in out and out["lat"] is not None:
            lat = float(out["lat"])
            if abs(lat) > 90:
                out["lat"] = None
    except Exception:
        out["lat"] = None

    try:
        if "lon" in out and out["lon"] is not None:
            lon = float(out["lon"])
            if abs(lon) > 180:
                out["lon"] = None
    except Exception:
        out["lon"] = None

    # If it looks like confidence got shoved into lat/lon, flag it
    # (lat between 0..1, lon == 0, confidence exists) => likely wrong
    try:
        lat = float(out.get("lat")) if out.get("lat") is not None else None
        lon = float(out.get("lon")) if out.get("lon") is not None else None
        conf = float(out.get("confidence")) if out.get("confidence") is not None else None
        if lat is not None and lon is not None and conf is not None:
            if 0 <= lat <= 1.2 and lon == 0.0 and 0 <= conf <= 1.0:
                out["analyzable"] = 0
                q = (out.get("quality_note") or "")
                out["quality_note"] = (q + " | sanity_check:lat_lon_suspected_from_conf").strip(" |")
    except Exception:
        pass

    return out

@app.post("/v1/ingest/aggregates")
async def ingest_aggregates(payload: dict = Body(...), request: Request = None):
    require_key(request)  # your existing API key gate (no-op if API_KEY empty)
    init_db()

    # Accept either {"rows":[...]} or {"aggregates":[...]} or a single row dict
    rows = None
    for key in ("rows","aggregates","data"):
        if isinstance(payload.get(key), list):
            rows = payload[key]
            break
    if rows is None:
        rows = [payload]

    con = db()
    try:
        cols = _table_cols(con, "metric_aggregates")
        inserted = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            d = _normalize_row(r, cols)

            # Required-ish defaults for older clients
            d.setdefault("received_at", utc_now())
            d.setdefault("node_id", "unknown")
            d.setdefault("bucket_start", d["received_at"])
            d.setdefault("bucket_seconds", 5)
            d.setdefault("grid_key", "unknown")
            d.setdefault("direction", "unknown")
            d.setdefault("speed_band", "unknown")
            d.setdefault("sample_count", 1)

            # Build INSERT deterministically by column names present
            keys = [k for k in d.keys() if k in cols]
            if not keys:
                continue
            cols_sql = ", ".join(keys)
            vals_sql = ", ".join([f":{k}" for k in keys])
            sql = f"INSERT INTO metric_aggregates ({cols_sql}) VALUES ({vals_sql})"
            con.execute(sql, d)
            inserted += 1

        con.commit()
        return {"ok": True, "inserted": inserted}
    finally:
        con.close()


## MOVED_STATIC_MOUNT (after API routes)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
