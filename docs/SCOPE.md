# Project Road 70 (RoadState) – Scope

## What exists now (as of this snapshot)
- FastAPI service: `Project Road 70` (uvicorn) listening on `127.0.0.1:8787`
- Reverse proxy: Caddy on `app.roadstate.club` -> `127.0.0.1:8787`
- Primary ingestion endpoint:
  - `POST /v1/ingest/aggregates`
- Health:
  - `GET /v1/health` returns `{"ok": true}`
- Admin UI:
  - `GET /admin` serves `web/admin.html`
  - Admin data endpoints (require admin auth at app layer):
    - `GET /admin/api/rows`
    - `PATCH /admin/api/rows/{row_id}`
    - `DELETE /admin/api/rows/{row_id}`
    - `GET|POST /admin/api/backfill_geocode`
    - `GET|POST /admin/api/recompute_scores`
  - Event and deploy endpoints:
    - `GET /v1/events/latest`
    - `POST /v1/admin/webhook/github`

## Core rule constraints (must remain true)
- iPhone Safari web app (no App Store)
- User explicitly taps Start/Stop
- Collects: accelerometer + gyro + GPS speed (client-side)
- Aggregates into 60-second buckets (client-side)
- Computes confidence score locally (client-side)
- Upload only aggregates (no raw sensor streams)

## Data model (server-side)
- SQLite DB (default `data.sqlite3`)
- Table: `metric_aggregates`
  - ingestion accepts flexible field names via alias mapping
  - `sanitize_lat_lon()` enforces lat/lon sanity + annotates `quality_note`
- Table: `road_events` (detected issues + analysis payloads)

## Operational goals for maintainers
- Predictable deployment (systemd unit + venv)
- Redacted infra snapshots committed for reproducibility
- Clear runbook for onboarding and incident response
