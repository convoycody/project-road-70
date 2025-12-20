# Runbook

## Service control
- Status: `systemctl status project-road-70.service --no-pager -l`
- Restart: `sudo systemctl restart project-road-70.service`
- Logs: `journalctl -u project-road-70.service -n 200 --no-pager`

## Local checks (on server)
- Port: `ss -ltnp | grep ':8787\b'`
- Health: `curl -sS http://127.0.0.1:8787/v1/health ; echo`

## Public checks
- Health: `curl -sS -i https://app.roadstate.club/v1/health | sed -n '1,20p'`

## Admin auth troubleshooting
If `/admin` returns 401 and the headers aren’t helping:
- That 401 may be from Caddy Basic Auth (browser login), not FastAPI.
- Check `/etc/caddy/Caddyfile` for `basic_auth` and reload Caddy.

