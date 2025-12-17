#!/usr/bin/env bash
set -euo pipefail

BUMP="${1:-patch}"

die(){ echo "❌ $*" >&2; exit 1; }
log(){ echo "== $* =="; }

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Not a git repo."
cd "$repo_root"
current_branch="$(git rev-parse --abbrev-ref HEAD)"

read_version() {
  local v=""

  if [[ -f pyproject.toml ]]; then
    v="$(python3 - <<'PY2'
import re, pathlib
p=pathlib.Path("pyproject.toml")
s=p.read_text()
m=re.search(r'(?m)^\s*version\s*=\s*"(\d+\.\d+\.\d+)"\s*$', s)
print(m.group(1) if m else "")
PY2
)"
    [[ -n "$v" ]] && { echo "$v"; return 0; }
  fi

  if [[ -f package.json ]]; then
    v="$(python3 - <<'PY2'
import json, pathlib
p=pathlib.Path("package.json")
try:
  j=json.loads(p.read_text())
  print(j.get("version",""))
except Exception:
  print("")
PY2
)"
    [[ -n "$v" ]] && { echo "$v"; return 0; }
  fi

  if [[ -f VERSION ]]; then
    v="$(tr -d ' \t\r\n' < VERSION)"
    [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && { echo "$v"; return 0; }
  fi

  echo ""
}

write_version_everywhere() {
  local new="$1"

  if [[ -f pyproject.toml ]]; then
    python3 - <<PY2
import re, pathlib
p=pathlib.Path("pyproject.toml")
s=p.read_text()
s2=re.sub(r'(?m)^(\s*version\s*=\s*")(\d+\.\d+\.\d+)("\s*)$',
          r'\g<1>' + "$new" + r'\g<3>', s)
p.write_text(s2)
PY2
  fi

  if [[ -f package.json ]]; then
    python3 - <<PY2
import json, pathlib
p=pathlib.Path("package.json")
j=json.loads(p.read_text())
j["version"]="$new"
p.write_text(json.dumps(j, indent=2) + "\n")
PY2
  fi

  echo "$new" > VERSION
}

bump_semver() {
  local v="$1" mode="$2"
  IFS='.' read -r a b c <<<"$v"
  case "$mode" in
    patch) c=$((c+1));;
    minor) b=$((b+1)); c=0;;
    major) a=$((a+1)); b=0; c=0;;
    *) die "Unknown bump mode: $mode";;
  esac
  echo "${a}.${b}.${c}"
}

old="$(read_version)"
if [[ -z "$old" ]]; then
  old="0.1.0"
fi

if [[ "$BUMP" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  new="$BUMP"
else
  new="$(bump_semver "$old" "$BUMP")"
fi

log "Repo: $repo_root"
log "Branch: $current_branch"
log "Current version: $old"
log "Next version: $new"

# Make sure you didn't accidentally stage ignored junk
git add .gitignore >/dev/null 2>&1 || true

write_version_everywhere "$new"

git add -A

if git diff --cached --quiet; then
  die "Nothing to commit after version bump."
fi

git commit -m "Release v${new}"

tag="v${new}"
if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  die "Tag ${tag} already exists."
fi
git tag -a "${tag}" -m "Release ${tag}"

log "Pushing branch + tag"
git push origin "$current_branch"
git push origin "$tag"

log "Done. Released ${tag}"
