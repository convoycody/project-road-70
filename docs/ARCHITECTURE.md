# Architecture

## Request flow (prod)
Client (Safari web app) ->
  https://app.roadstate.club ->
    Caddy reverse_proxy ->
      FastAPI on 127.0.0.1:8787 ->
        SQLite (data.sqlite3)

## Auth layers
1) API key (optional)
- If ROADSTATE_API_KEY is set, requests must include header: `x-api-key`

2) Admin auth (app layer)
- If ROADSTATE_ADMIN_USER and ROADSTATE_ADMIN_PASS are set, admin endpoints require:
  - `x-admin-user`
  - `x-admin-pass`
- NOTE: Avoid putting passwords in the repo. Store in `/etc/project-road-70/admin.env` or systemd drop-in Environment= lines.

## Geocoding
- Reverse geocode uses Nominatim and caches results in `geocode_cache`
- Backfill endpoint updates missing road fields for recent rows

