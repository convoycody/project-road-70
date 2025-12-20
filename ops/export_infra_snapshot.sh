#!/usr/bin/env bash
set -euo pipefail
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="snapshots"
mkdir -p "$OUT"

sudo systemctl cat project-road-70.service > "$OUT/systemd.project-road-70.service.${TS}.txt" || true
sudo cp -a /etc/caddy/Caddyfile "$OUT/Caddyfile.${TS}" || true

{
  echo "time_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "== systemctl is-active =="
  systemctl is-active project-road-70.service caddy.service 2>/dev/null || true
  echo
  echo "== status project-road-70 =="
  systemctl status project-road-70.service --no-pager -l 2>/dev/null || true
  echo
  echo "== listeners =="
  ss -ltnp | egrep ':(8787|80|443)\b' || true
  echo
  echo "== versions =="
  python3 -V || true
  /opt/project-road-70/.venv/bin/python -c "import fastapi,uvicorn; print('fastapi',fastapi.__version__,'uvicorn',uvicorn.__version__)" || true
  caddy version || true
} > "$OUT/runtime.${TS}.txt"

for f in "$OUT"/*"${TS}"*; do
  sed -E \
    -e 's/\$2[aby]\$[0-9]{2}\$[A-Za-z0-9.\/]{53}/<REDACTED_BCRYPT_HASH>/g' \
    -e 's/([A-Za-z0-9_]*PASS[A-Za-z0-9_]*=).*/\1<REDACTED>/gI' \
    -e 's/([A-Za-z0-9_]*KEY[A-Za-z0-9_]*=).*/\1<REDACTED>/gI' \
    -e 's/([A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*=).*/\1<REDACTED>/gI' \
    "$f" > "${f}.redacted"
  mv -f "${f}.redacted" "$f"
done

echo "Wrote redacted snapshots with TS=${TS}"
