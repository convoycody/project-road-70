from __future__ import annotations
import os, sqlite3
from typing import Optional
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(ROOT_DIR, "data.sqlite3")

router = APIRouter()

jinja = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates_admin")),
    autoescape=select_autoescape(["html"])
)

def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def _is_authed(req: Request) -> bool:
    return bool(req.session.get("admin_authed"))

def _require_authed(req: Request):
    if not _is_authed(req):
        return RedirectResponse("/admin/login", status_code=HTTP_303_SEE_OTHER)
    return None

@router.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    tpl = jinja.get_template("login.html")
    return tpl.render(error=None)

@router.post("/admin/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    u = os.environ.get("ROADSTATE_ADMIN_USER", "admin")
    p = os.environ.get("ROADSTATE_ADMIN_PASS", "")
    if username == u and p and password == p:
        request.session["admin_authed"] = True
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
    tpl = jinja.get_template("login.html")
    return HTMLResponse(tpl.render(error="Invalid login"))

@router.post("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=HTTP_303_SEE_OTHER)

@router.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", limit: int = 200):
    r = _require_authed(request)
    if r: return r

    con = _db()
    try:
        sql = """
        SELECT id, received_at, node_id,
               lat, lon, speed_mps, heading_deg,
               confidence, analyzable, points_eligible,
               mount_state, moving,
               road_name, short_location, quality_note
        FROM metric_aggregates
        """
        params = []
        if q:
            sql += " WHERE node_id LIKE ? OR road_name LIKE ? OR short_location LIKE ? OR quality_note LIKE ?"
            like = f"%{q}%"
            params = [like, like, like, like]
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))

        rows = con.execute(sql, params).fetchall()
        tpl = jinja.get_template("dashboard.html")
        return tpl.render(rows=rows, q=q, limit=limit)
    finally:
        con.close()

@router.post("/admin/row/{row_id}/update")
def row_update(
    request: Request,
    row_id: int,
    analyzable: int = Form(...),
    points_eligible: int = Form(...),
    road_name: str = Form(""),
    short_location: str = Form(""),
    quality_note: str = Form(""),
):
    r = _require_authed(request)
    if r: return r
    con = _db()
    try:
        con.execute(
            """
            UPDATE metric_aggregates
            SET analyzable = ?,
                points_eligible = ?,
                road_name = ?,
                short_location = ?,
                quality_note = ?
            WHERE id = ?
            """,
            (
                int(analyzable),
                int(points_eligible),
                (road_name.strip() or None),
                (short_location.strip() or None),
                (quality_note.strip() or None),
                int(row_id),
            ),
        )
        con.commit()
    finally:
        con.close()
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)

@router.post("/admin/row/{row_id}/delete")
def row_delete(request: Request, row_id: int):
    r = _require_authed(request)
    if r: return r
    con = _db()
    try:
        con.execute("DELETE FROM metric_aggregates WHERE id = ?", (int(row_id),))
        con.commit()
    finally:
        con.close()
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
