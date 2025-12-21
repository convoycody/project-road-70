# Runbook

## Service control
- Status: `systemctl status project-road-70.service --no-pager -l`
- Restart: `sudo systemctl restart project-road-70.service`
- Logs: `journalctl -u project-road-70.service -n 200 --no-pager`

## Webhook deploy (GitHub)
Configure a webhook to call the server after a push. The API expects:
- `GITHUB_WEBHOOK_SECRET` for HMAC signature verification
- `ROADSTATE_DEPLOY_SCRIPT` pointing at a deploy script (e.g. `/usr/local/bin/project-road-70-deploy`)
- `GITHUB_WEBHOOK_BRANCH` for branch filtering (recommended: `refs/heads/main`)

Webhook URL: `https://app.roadstate.club/v1/admin/webhook/github`

Recommended repo path: `/srv/project-road-70`

Example deploy script (`/usr/local/bin/project-road-70-deploy`):
```
#!/usr/bin/env bash
set -euo pipefail
cd /srv/project-road-70
git pull origin main
sudo systemctl restart project-road-70.service
```

One-time server setup:
```
sudo mkdir -p /srv
sudo git clone https://github.com/convoycody/project-road-70.git /srv/project-road-70
sudo chown -R $USER:$USER /srv/project-road-70
```

## Local checks (on server)
- Port: `ss -ltnp | grep ':8787\b'`
- Health: `curl -sS http://127.0.0.1:8787/v1/health ; echo`

## Public checks
- Health: `curl -sS -i https://app.roadstate.club/v1/health | sed -n '1,20p'`

## Admin auth troubleshooting
If `/admin` returns 401 and the headers aren’t helping:
- That 401 may be from Caddy Basic Auth (browser login), not FastAPI.
- Check `/etc/caddy/Caddyfile` for `basic_auth` and reload Caddy.
