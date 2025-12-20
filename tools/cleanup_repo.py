#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = REPO_ROOT / "archive" / time.strftime("%Y%m%d_%H%M%S")

@dataclass
class CmdResult:
    code: int
    out: str
    err: str

def run(cmd: list[str], cwd: Path | None = None, check: bool = False) -> CmdResult:
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = p.communicate()
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{err}")
    return CmdResult(p.returncode, out, err)

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_text(p: Path, content: str) -> None:
    ensure_dir(p.parent)
    p.write_text(content, encoding="utf-8")

def move_to_archive(paths: Iterable[Path]) -> int:
    moved = 0
    for p in paths:
        if not p.exists():
            continue
        rel = p.relative_to(REPO_ROOT)
        dest = ARCHIVE_DIR / rel
        ensure_dir(dest.parent)
        shutil.move(str(p), str(dest))
        moved += 1
    return moved

def collect_junk() -> list[Path]:
    junk = []
    junk += list(REPO_ROOT.rglob("*.bak.*"))
    junk += list(REPO_ROOT.rglob("*.bak"))
    junk += [REPO_ROOT / "static" / "app"]  # accidental prior path-app
    return sorted(set([p for p in junk if p.exists()]))

def normalize_marketing_pages(app_url: str) -> None:
    home = REPO_ROOT / "static" / "index.html"
    about = REPO_ROOT / "static" / "about" / "index.html"
    if not home.exists():
        raise RuntimeError(f"Missing {home}")
    if not about.exists():
        raise RuntimeError(f"Missing {about}")

    def clean_toolbar(html: str) -> str:
        return re.sub(r'(?is)<div[^>]*data-roadstate-nav[^>]*>.*?</div>\s*', '', html)

    def remove_admin_anchors(html: str) -> str:
        html = re.sub(r'(?is)<a[^>]+href="[^"]*/admin[^"]*"[^>]*>.*?</a>\s*', '', html)
        html = re.sub(r'(?is)<a[^>]+href="\/admin[^"]*"[^>]*>.*?</a>\s*', '', html)
        return html

    def ensure_styles(html: str) -> str:
        if ".big{" in html or ".big {" in html:
            return html
        style_block = """
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial;background:#0b1220;color:#eaf0ff;margin:0}
    .wrap{max-width:980px;margin:0 auto;padding:44px 18px}
    .big{display:inline-flex;gap:10px;align-items:center;margin:18px 0;padding:16px 18px;border-radius:18px;
         border:1px solid rgba(255,255,255,.22);text-decoration:none;color:#eaf0ff;font-weight:800}
    .big:hover{background:rgba(255,255,255,.06)}
    .muted{opacity:.8}
  </style>
"""
        return html.replace("</head>", style_block + "\n</head>") if "</head>" in html else html

    def force_cta(html: str) -> str:
        # Replace any CTA-ish link to /app or app.roadstate.club with the correct destination + label
        html = re.sub(r'(?is)<a[^>]+href="[^"]*(/app/|https?://app\.roadstate\.club/?)"[^>]*>.*?</a>',
                      f'<a href="{app_url}" class="big">Feed Your Trip <span>→</span></a>', html, count=1)
        if "Feed Your Trip" not in html:
            html = re.sub(r'(?is)<body[^>]*>',
                          lambda m: m.group(0) + f'\n<div class="wrap">\n<a href="{app_url}" class="big">Feed Your Trip <span>→</span></a>\n</div>\n',
                          html, count=1)
        return html

    for p in (home, about):
        html = p.read_text(encoding="utf-8")
        html = clean_toolbar(html)
        html = remove_admin_anchors(html)
        html = ensure_styles(html)
        html = force_cta(html)
        write_text(p, html)

def main() -> int:
    app_url = os.environ.get("ROADSTATE_APP_URL", "https://app.roadstate.club/")
    print(f"Repo root: {REPO_ROOT}")
    print(f"App URL:   {app_url}")

    junk = collect_junk()
    if junk:
        ensure_dir(ARCHIVE_DIR)
        moved = move_to_archive(junk)
        print(f"Archived junk/backups: {moved} -> {ARCHIVE_DIR}")
    else:
        print("No junk/backups found to archive.")

    normalize_marketing_pages(app_url)
    print("Normalized marketing pages.")

    print("Done.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
