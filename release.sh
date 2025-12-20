#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./release.sh             # bump patch
#   ./release.sh minor       # bump minor
#   ./release.sh major       # bump major
#   ./release.sh 0.1.1       # set explicit version

BUMP="${1:-patch}"

die(){ echo "❌ $*" >&2; exit 1; }
log(){ echo "== $* =="; }

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Not a git repo."
cd "$repo_root"
branch="$(git rev-parse --abbrev-ref HEAD)"

read_version() {
  if [[ -f VERSION ]]; then
    tr -d ' \t\r\n' < VERSION
    return 0
  fi
  echo "0.0.0"
}

write_version() {
  local v="$1"
  echo "$v" > VERSION
}

bump() {
  local v="$1"
  local mode="$2"
  IFS='.' read -r a b c <<<"$v"
  a="${a:-0}"; b="${b:-0}"; c="${c:-0}"
  case "$mode" in
    major) a=$((a+1)); b=0; c=0 ;;
    minor) b=$((b+1)); c=0 ;;
    patch) c=$((c+1)) ;;
    *) die "Unknown bump: $mode" ;;
  esac
  echo "${a}.${b}.${c}"
}

current="$(read_version)"
if [[ "$BUMP" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  new="$BUMP"
else
  new="$(bump "$current" "$BUMP")"
fi

log "Version: $current -> $new"
write_version "$new"

git add VERSION
git add -A

if git diff --cached --quiet; then
  die "Nothing staged to commit."
fi

git commit -m "Release v${new}"

tag="v${new}"
if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  die "Tag ${tag} already exists."
fi

git tag -a "${tag}" -m "Release ${tag}"

log "Pushing branch + tag"
git push origin "$branch"
git push origin "$tag"

log "Done: ${tag}"
