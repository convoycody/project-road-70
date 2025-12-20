from __future__ import annotations

import os
import hashlib
import secrets
import base64
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import Body, FastAPI, HTTPException, Request
from passlib.context import CryptContext
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.roadscore import (
    ensure_schema as rs_ensure_schema,
    upsert_segment,
    recompute_scores,
    top_roads,
    road_detail,
    roads_near,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT_DIR / "data.sqlite3")))
API_KEY = os.environ.get("ROADSTATE_API_KEY", "")
ADMIN_USER = os.environ.get("ROADSTATE_ADMIN_USER", "")
ADMIN_PASS = os.environ.get("ROADSTATE_ADMIN_PASS", "")


app = FastAPI(title="Project Road 70", version="0.1.0")



### AUTH SYSTEM (users + email verification + sessions)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def _now_utc_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def _db_exec(sql: str, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    con.commit()
    con.close()

def _db_query(sql: str, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    con.close()
    return rows

def _ensure_user_schema():
    _db_exec("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT NOT NULL UNIQUE,
      pass_hash TEXT NOT NULL,
      is_active INTEGER NOT NULL DEFAULT 1,
      is_email_verified INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      email_verified_at TEXT
    );
    """)


def _migrate_users_points_column():
    # Add points_balance if missing (SQLite-safe)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in cur.fetchall()]  # name at index 1
    if "points_balance" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN points_balance INTEGER NOT NULL DEFAULT 0")
        con.commit()
    con.close()

    _db_exec("""
    CREATE TABLE IF NOT EXISTS email_verifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      token TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      used_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    _db_exec("""
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      token TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      revoked_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

def _make_token(nbytes=24):
    return secrets.token_urlsafe(nbytes)

def _require_admin(req: Request):
    au = req.headers.get("x-admin-user","")
    ap = req.headers.get("x-admin-pass","")
    if not (ADMIN_USER and ADMIN_PASS and au == ADMIN_USER and ap == ADMIN_PASS):
      raise HTTPException(status_code=401, detail="Admin auth required")

def _session_user(req: Request):
    tok = req.cookies.get("rs_session","")
    if not tok:
      return None
    rows = _db_query("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.revoked_at IS NULL", (tok,))
    return dict(rows[0]) if rows else None

@app.on_event("startup")
async def _startup_users_schema():
    _ensure_user_schema()


# --- DEBUG: echo endpoint (enabled only when ROADSTATE_DEBUG=1) ---
if os.environ.get("ROADSTATE_DEBUG", "").strip() == "1":
    @app.api_route("/v1/debug/echo", methods=["GET","POST","OPTIONS"])
    async def debug_echo(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        return {"ok": True, "method": request.method, "body": body}




def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def require_key(req: Request) -> None:
    if not API_KEY:
        return
    k = (req.headers.get("x-api-key") or "").strip()
    if k != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def require_admin(req: Request) -> None:
    # If ADMIN_USER/PASS unset, admin is open (use env to lock it down).
    if not ADMIN_USER or not ADMIN_PASS:
        return

    # 1) Accept browser Basic Auth (Authorization: Basic ...)
    auth = (req.headers.get("authorization") or "").strip()
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", "replace")
            u, p = raw.split(":", 1)
        except Exception:
            u, p = "", ""
        if (u or "").strip() == ADMIN_USER and (p or "").strip() == ADMIN_PASS:
            return
        # if basic provided but wrong, still challenge
        raise HTTPException(
            status_code=401,
            detail="Admin auth required",
            headers={"WWW-Authenticate": 'Basic realm="RoadState Admin"'},
        )

    # 2) Accept existing header auth for curl/scripts
    u = (req.headers.get("x-admin-user") or "").strip()
    p = (req.headers.get("x-admin-pass") or "").strip()
    if u == ADMIN_USER and p == ADMIN_PASS:
        return

    # 3) Otherwise challenge (this makes browsers prompt)
    raise HTTPException(
        status_code=401,
        detail="Admin auth required",
        headers={"WWW-Authenticate": 'Basic realm="RoadState Admin"'},
    )

def _f(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None

def sanitize_lat_lon(d: Dict[str, Any]) -> None:
    lat = _f(d.get("lat"))
    lon = _f(d.get("lon"))
    speed = _f(d.get("speed_mps"))
    notes = []

    if lat is not None and abs(lat) > 90:
        notes.append("sanity:lat_out_of_range")
        lat = None
    if lon is not None and abs(lon) > 180:
        notes.append("sanity:lon_out_of_range")
        lon = None

    # your earlier symptom: lon accidentally became speed-ish
    if lon is not None and speed is not None and abs(lon) <= 60 and abs(speed) <= 60:
        notes.append("sanity:lon_looks_like_speed")

    if notes:
        qn = (d.get("quality_note") or "").strip()
        d["quality_note"] = (qn + (" | " if qn else "") + " ".join(notes)).strip()

    d["lat"] = lat
    d["lon"] = lon

def ensure_tables(con: sqlite3.Connection) -> None:
    con.execute("""
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
      sample_count INTEGER,

      lat REAL,
      lon REAL,
      speed_mps REAL,
      heading_deg REAL,

      mount_state TEXT DEFAULT '',
      device_posture TEXT DEFAULT '',
      moving INTEGER DEFAULT 0,
      analyzable INTEGER DEFAULT 1,
      points_eligible INTEGER DEFAULT 0,

      road_name TEXT DEFAULT '',
      short_location TEXT DEFAULT '',
      quality_note TEXT DEFAULT ''
    );
    """)
    con.commit()

def table_cols(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}

def named_insert_metric(con: sqlite3.Connection, data: Dict[str, Any]) -> int:
    cols = table_cols(con, "metric_aggregates")
    aliases = {
        "heading": "heading_deg",
        "speed": "speed_mps",
        "lat_deg": "lat",
        "lon_deg": "lon",

        # common mobile/web naming
        "latitude": "lat",
        "longitude": "lon",
        "lng": "lon",
        "long": "lon",
        "gps_lat": "lat",
        "gps_lon": "lon",
        "gps_lng": "lon",

        "speed_ms": "speed_mps",
        "speed_m_s": "speed_mps",
        "speedMps": "speed_mps",
        "headingDeg": "heading_deg",

        # if client sends confidence under different keys
        "conf": "confidence",
        "confidence_score": "confidence",
        "confidenceScore": "confidence",

        # if client uses device identifiers under different keys
        "deviceId": "node_id",
        "device_id": "node_id",
    }

    mapped: Dict[str, Any] = {}
    for k, v in (data or {}).items():
        kk = aliases.get(k, k)
        if kk in cols:
            mapped[kk] = v

    d = mapped
    # _ROAD70_DEFAULTS_APPLIED: keep ingest resilient against missing fields
    if d.get('sample_count') in (None, '', 'null'):
        d['sample_count'] = 1
    if d.get('bucket_seconds') in (None, '', 'null'):
        d['bucket_seconds'] = d.get('window_seconds') or 5
    if not d.get('node_id'):
        d['node_id'] = d.get('device_id') or d.get('id') or 'unknown'

    d.setdefault("received_at", utc_now())
    d.setdefault("node_id", "unknown")
    d.setdefault("bucket_start", d["received_at"])
    d.setdefault("bucket_seconds", 5)
    d.setdefault("grid_key", d.get("grid_key") or "seg:unknown")
    d.setdefault("direction", d.get("direction") or "unknown")
    d.setdefault("speed_band", d.get("speed_band") or "unknown")
    d.setdefault("quality_note", d.get("quality_note") or "")

    sanitize_lat_lon(d)

    keys = [k for k in d.keys() if k in cols]
    if not keys:
        raise HTTPException(status_code=400, detail="No writable fields")
    sql = f"INSERT INTO metric_aggregates ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
    cur = con.execute(sql, [d[k] for k in keys])
    return int(cur.lastrowid)

@app.on_event("startup")
def _startup() -> None:
    con = db()
    try:
        ensure_tables(con)
    finally:
        con.close()

@app.post("/v1/ingest/aggregates")
async def ingest_aggregates(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    """
    Accepts either:
      (A) a single aggregate object (flat dict), OR
      (B) a batch wrapper: { node_id: "...", items: [ {..row..}, {..row..} ] }
    For (B), each item becomes one row, inheriting top-level fields (node_id, etc).
    """
    require_key(request)
    con = db()
    try:
        ensure_tables(con)

        data = dict(payload or {})
        items = data.get("items")

        # Batch insert: {items:[...]}
        if isinstance(items, list):
            base = {k: v for k, v in data.items() if k != "items"}
            ids = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                row = dict(base)
                row.update(dict(it))
                row_id = named_insert_metric(con, row)
                ids.append(row_id)
            con.commit()
            return JSONResponse({"ok": True, "ids": ids, "count": len(ids)})

        # Single insert: {...}
        row_id = named_insert_metric(con, data)
        con.commit()
        return JSONResponse({"ok": True, "id": row_id})
    finally:
        con.close()

@app.get("/v1/latest")
async def latest() -> JSONResponse:
    con = db()
    try:
        rows = con.execute("""
          SELECT id, received_at, node_id, lat, lon, speed_mps, heading_deg, confidence,
                 road_name, short_location, quality_note
          FROM metric_aggregates
          ORDER BY id DESC
          LIMIT 200
        """).fetchall()
        return JSONResponse({"rows": [dict(r) for r in rows]})
    finally:
        con.close()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    f = WEB_DIR / "admin.html"
    if not f.exists():
        raise HTTPException(status_code=500, detail="admin.html missing")
    return HTMLResponse(f.read_text())

@app.get("/admin/api/rows")
async def admin_rows(request: Request, limit: int = 150, node: str = "") -> JSONResponse:
    require_admin(request)
    limit = max(10, min(1000, int(limit)))
    node = (node or "").strip()

    con = db()
    try:
        ensure_tables(con)
        if node:
            rows = con.execute("""
              SELECT id, received_at, node_id, lat, lon, speed_mps, heading_deg, confidence,
                     road_name, short_location, quality_note
              FROM metric_aggregates
              WHERE node_id = ?
              ORDER BY id DESC
              LIMIT ?
            """, (node, limit)).fetchall()
        else:
            rows = con.execute("""
              SELECT id, received_at, node_id, lat, lon, speed_mps, heading_deg, confidence,
                     road_name, short_location, quality_note
              FROM metric_aggregates
              ORDER BY id DESC
              LIMIT ?
            """, (limit,)).fetchall()
        return JSONResponse({"rows": [dict(r) for r in rows]})
    finally:
        con.close()

@app.patch("/admin/api/rows/{row_id}")
async def admin_patch_row(request: Request, row_id: int, patch: Dict[str, Any] = Body(...)) -> JSONResponse:
    require_admin(request)
    con = db()
    try:
        ensure_tables(con)
        cols = table_cols(con, "metric_aggregates")
        allowed = {"lat","lon","speed_mps","heading_deg","confidence","road_name","short_location","quality_note"}
        d: Dict[str, Any] = {}
        for k in allowed:
            if k in (patch or {}) and k in cols:
                v = patch.get(k)
                if k in ("lat","lon","speed_mps","heading_deg","confidence"):
                    v = _f(v)
                else:
                    v = (v or "").strip()
                d[k] = v
        if not d:
            raise HTTPException(status_code=400, detail="No editable fields provided")

        tmp = dict(d)
        if "lat" in tmp or "lon" in tmp:
            sanitize_lat_lon(tmp)
            d["lat"] = tmp.get("lat", d.get("lat"))
            d["lon"] = tmp.get("lon", d.get("lon"))
            d["quality_note"] = tmp.get("quality_note", d.get("quality_note",""))

        sets = ", ".join([f"{k} = ?" for k in d.keys()])
        con.execute(f"UPDATE metric_aggregates SET {sets} WHERE id = ?", [d[k] for k in d.keys()] + [int(row_id)])
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()

@app.delete("/admin/api/rows/{row_id}")
async def admin_delete_row(request: Request, row_id: int) -> JSONResponse:
    require_admin(request)
    con = db()
    try:
        ensure_tables(con)
        con.execute("DELETE FROM metric_aggregates WHERE id = ?", (int(row_id),))
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()

# IMPORTANT: mount static LAST so it can't swallow /admin or /v1/*
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
@app.get("/v1/health")
def health():
    return {"ok": True}

import json, urllib.parse, urllib.request

def _ensure_geocode_tables(con: sqlite3.Connection) -> None:
    con.execute("""
      CREATE TABLE IF NOT EXISTS geocode_cache(
        key TEXT PRIMARY KEY,
        lat REAL,
        lon REAL,
        payload TEXT,
        road_name TEXT,
        hwy_ref TEXT,
        state TEXT,
        county TEXT,
        city TEXT,
        fetched_at INTEGER
      )
    """)
    con.commit()

def _reverse_geocode_cached(con: sqlite3.Connection, lat: float, lon: float) -> dict:
    # round to reduce unique lookups (good cache hit rate)
    key = f"{lat:.5f},{lon:.5f}"
    _ensure_geocode_tables(con)

    row = con.execute("SELECT payload FROM geocode_cache WHERE key=?", (key,)).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            pass

    # Nominatim (OpenStreetMap) reverse geocode
    # Keep it gentle: 1 request per new rounded coordinate, 3s timeout.
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode({
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "zoom": 18,
        "addressdetails": 1
    })
    req = urllib.request.Request(url, headers={"User-Agent": "project-road-70/0.0.2 (admin@local)"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    j = json.loads(payload)

    addr = (j.get("address") or {})
    road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or addr.get("footway") or addr.get("cycleway")
    hwy = addr.get("highway")  # sometimes present
    # Nominatim sometimes returns "ref" in extra tags; not always present in jsonv2.
    # We'll attempt a few common keys.
    ref = addr.get("ref") or addr.get("route") or None

    state = addr.get("state")
    county = addr.get("county")
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet")

    con.execute("""
      INSERT INTO geocode_cache(key, lat, lon, payload, road_name, hwy_ref, state, county, city, fetched_at)
      VALUES(?,?,?,?,?,?,?,?,?,strftime('%s','now'))
      ON CONFLICT(key) DO UPDATE SET
        payload=excluded.payload,
        road_name=excluded.road_name,
        hwy_ref=excluded.hwy_ref,
        state=excluded.state,
        county=excluded.county,
        city=excluded.city,
        fetched_at=excluded.fetched_at
    """, (key, lat, lon, payload, road, (ref or hwy), state, county, city))
    con.commit()
    return j

def get_db() -> sqlite3.Connection:
    con = sqlite3.connect("./data.sqlite3", check_same_thread=False)
    con.row_factory = sqlite3.Row
    rs_ensure_schema(con)
    return con

@app.api_route("/admin/api/backfill_geocode", methods=["GET","POST"])
def admin_backfill_geocode(limit: int = 200):
    con = get_db()
    rs_ensure_schema(con)

    # pick rows missing geocode fields but having coords
    rows = con.execute("""
      SELECT id, lat, lon FROM metric_aggregates
      WHERE (road_name IS NULL OR road_name = '')
        AND lat IS NOT NULL AND lon IS NOT NULL
      ORDER BY id DESC
      LIMIT ?
    """, (int(limit),)).fetchall()

    updated = 0
    queued = len(rows)

    for r in rows:
        rid = int(r["id"])
        lat = float(r["lat"])
        lon = float(r["lon"])

        try:
            j = _reverse_geocode_cached(con, lat, lon)
            addr = (j.get("address") or {})
            road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or addr.get("footway") or addr.get("cycleway")
            ref = addr.get("ref") or addr.get("route") or addr.get("highway") or None
            state = addr.get("state")
            county = addr.get("county")
            city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet")

            # compute segment_id + upsert segment
            d = {"lat": lat, "lon": lon, "road_name": road, "hwy_ref": ref, "state": state, "county": county, "city": city}
            seg = upsert_segment(con, d)

            con.execute("""
              UPDATE metric_aggregates
              SET road_name=?, hwy_ref=?, state=?, county=?, city=?,
                  geocode_src='nominatim', geocoded_at=strftime('%s','now'),
                  segment_id=?
              WHERE id=?
            """, (road, ref, state, county, city, seg, rid))
            updated += 1
        except Exception:
            # keep going; this is a best-effort batch
            continue

    con.commit()
    return {"ok": True, "updated": updated, "queued": queued}

@app.api_route("/admin/api/recompute_scores", methods=["GET","POST"])
def admin_recompute_scores():
    con = get_db()
    rs_ensure_schema(con)
    r = recompute_scores(con, window_days=7)
    return {"ok": True, **r}

@app.get("/v1/roads/top")
def v1_roads_top(limit: int = 50, state: str | None = None):
    con = get_db()
    items = top_roads(con, limit=int(limit), state=state)
    return {"items": items}

@app.get("/v1/roads/near")
def v1_roads_near(lat: float, lon: float, limit: int = 25):
    con = get_db()
    items = roads_near(con, float(lat), float(lon), int(limit))
    return {"items": items}

@app.get("/v1/road/{segment_id}")
def v1_road(segment_id: str):
    con = get_db()
    return road_detail(con, segment_id)


# Root static mount MUST be last so API POST routes are not shadowed.

@app.post("/v1/verify/ingest")
async def verify_ingest(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    # Verification ingest: accepts ONE aggregate bucket generated client-side.
    # This must remain aggregate-only.
    con = db()
    try:
        ensure_tables(con)
        row_id = named_insert_metric(con, dict(payload or {}))
        con.commit()
        return JSONResponse({"ok": True, "id": row_id})
    finally:
        con.close()



@app.get("/verify", response_class=HTMLResponse)
async def verify_page() -> HTMLResponse:
    f = WEB_DIR / "verify.html"
    if not f.exists():
        raise HTTPException(status_code=500, detail="verify.html missing")
    return HTMLResponse(f.read_text())


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return (WEB_DIR / "login.html").read_text()

@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return (WEB_DIR / "signup.html").read_text()

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page():
    return (WEB_DIR / "admin_users.html").read_text()

@app.post("/v1/auth/signup")
async def auth_signup(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    _ensure_user_schema()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password) > 256:
        raise HTTPException(status_code=400, detail="Password must be at most 256 characters")

    pass_hash = pwd_context.hash(password)
    created_at = _now_utc_iso()

    try:
        _db_exec("INSERT INTO users (email, pass_hash, is_active, is_email_verified, created_at) VALUES (?,?,?,?,?)",
                 (email, pass_hash, 1, 0, created_at))
    except Exception:
        raise HTTPException(status_code=409, detail="Email already registered")

    uid = _db_query("SELECT id FROM users WHERE email=?", (email,))[0]["id"]
    token = _make_token(24)
    _db_exec("INSERT INTO email_verifications (user_id, token, created_at) VALUES (?,?,?)",
             (uid, token, _now_utc_iso()))

    # Industry standard behavior: verification link (email delivery wired up later)
    verify_url = f"/v1/auth/verify?token={token}"
    return JSONResponse({"ok": True, "user_id": uid, "verify_url": verify_url})

@app.get("/v1/auth/verify")
async def auth_verify(token: str) -> JSONResponse:
    _ensure_user_schema()
    rows = _db_query("SELECT * FROM email_verifications WHERE token=? AND used_at IS NULL", (token,))
    if not rows:
        raise HTTPException(status_code=400, detail="Invalid or used token")
    ev = rows[0]
    uid = ev["user_id"]

    _db_exec("UPDATE email_verifications SET used_at=? WHERE id=?", (_now_utc_iso(), ev["id"]))
    _db_exec("UPDATE users SET is_email_verified=1, email_verified_at=? WHERE id=?",
             (_now_utc_iso(), uid))
    return JSONResponse({"ok": True})

@app.post("/v1/auth/login")
async def auth_login(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    _ensure_user_schema()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    rows = _db_query("SELECT * FROM users WHERE email=?", (email,))
    if not rows:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    u = rows[0]
    if int(u["is_active"]) != 1:
        raise HTTPException(status_code=403, detail="Account disabled")
    if not pwd_context.verify(password, u["pass_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if int(u["is_email_verified"]) != 1:
        raise HTTPException(status_code=403, detail="Email not verified")

    tok = _make_token(24)
    _db_exec("INSERT INTO sessions (user_id, token, created_at) VALUES (?,?,?)",
             (u["id"], tok, _now_utc_iso()))

    resp = JSONResponse({"ok": True})
    # httpOnly cookie session (simple + standard for web apps)
    resp.set_cookie(
        key="rs_session",
        value=tok,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
        max_age=60*60*24*14
    )
    return resp

@app.post("/v1/auth/logout")
async def auth_logout(req: Request) -> JSONResponse:
    tok = req.cookies.get("rs_session","")
    if tok:
        _db_exec("UPDATE sessions SET revoked_at=? WHERE token=? AND revoked_at IS NULL", (_now_utc_iso(), tok))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("rs_session", path="/")
    return resp

@app.get("/v1/auth/me")
async def auth_me(req: Request) -> JSONResponse:
    u = _session_user(req)
    if not u:
        raise HTTPException(status_code=401, detail="Not logged in")
    return JSONResponse({
        "ok": True,
        "user": {
            "id": u["id"],
            "email": u["email"],
            "is_email_verified": int(u["is_email_verified"]),
            "created_at": u["created_at"]
        }
    })

@app.get("/v1/admin/users")
async def admin_list_users(req: Request) -> JSONResponse:
    _require_admin(req)
    _ensure_user_schema()
    rows = _db_query("SELECT id,email,is_active,is_email_verified,created_at,email_verified_at,points_balance FROM users ORDER BY id DESC LIMIT 500")
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "email": r["email"],
            "is_active": int(r["is_active"]),
            "is_email_verified": int(r["is_email_verified"]),
            "created_at": r["created_at"],
            "email_verified_at": r["email_verified_at"],
            "points_balance": int(r["points_balance"] if r["points_balance"] is not None else 0),
        })
    return JSONResponse({"ok": True, "users": users})



@app.post("/v1/admin/users/verify")
async def admin_verify_user(req: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    _require_admin(req)
    _ensure_user_schema()
    _migrate_users_points_column()
    user_id = int(payload.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    _db_exec("UPDATE users SET is_email_verified=1, email_verified_at=? WHERE id=?", (_now_utc_iso(), user_id))
    return JSONResponse({"ok": True})

@app.post("/v1/admin/users/delete")
async def admin_delete_user(req: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    _require_admin(req)
    _ensure_user_schema()
    user_id = int(payload.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    # revoke sessions first
    _db_exec("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (_now_utc_iso(), user_id))
    # delete verification tokens
    _db_exec("DELETE FROM email_verifications WHERE user_id=?", (user_id,))
    # delete user
    _db_exec("DELETE FROM users WHERE id=?", (user_id,))
    return JSONResponse({"ok": True})

@app.post("/v1/admin/users/set_points")
async def admin_set_points(req: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    _require_admin(req)
    _ensure_user_schema()
    _migrate_users_points_column()
    user_id = int(payload.get("user_id") or 0)
    points = int(payload.get("points_balance") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    if points < 0:
        raise HTTPException(status_code=400, detail="points_balance must be >= 0")
    _db_exec("UPDATE users SET points_balance=? WHERE id=?", (points, user_id))
    return JSONResponse({"ok": True})


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="root")


