# Security / Credentials Handling

## Rules
- Do not commit secrets to Git (passwords, API keys, tokens, bcrypt hashes).
- Admin/API credentials belong in:
  - `/etc/project-road-70/admin.env` (recommended) and referenced by systemd EnvironmentFile
  - or a systemd drop-in (Environment=...)

## Current known auth behavior
- Admin endpoints are protected at the application layer when env vars are set.
- Do not layer Caddy basic_auth unless you explicitly want browser-level gating.

## How to set admin credentials (server-side)
Use a root-only env file:
- `/etc/project-road-70/admin.env` with:
  ROADSTATE_ADMIN_USER=admin
  ROADSTATE_ADMIN_PASS=<REDACTED>
  ROADSTATE_API_KEY=<REDACTED>

Then:
- `sudo systemctl restart project-road-70.service`

