"""Build the WEB edition in one command -> ``dist/md-sync-web``.

Steps:
    1. dx build (Dioxus wasm frontend) -> deploy into ``md_sync/web/static/``
    2. PyInstaller bundle the FastAPI backend + assets -> ``dist/md-sync-web``

The resulting binary starts the :8580 web server on launch:
    ./dist/md-sync-web   ->  http://127.0.0.1:8580

Usage:
    python scripts/build_web.py              # full: frontend + package
    python scripts/build_web.py --no-build   # re-package existing static/ only
    python scripts/build_web.py --force      # force re-package the backend
    python scripts/build_web.py --clean      # clean PyInstaller caches
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import _pkg

ROOT = Path(__file__).resolve().parent.parent
DIOXUS = ROOT / "dioxus"
PUBLIC = DIOXUS / "target" / "dx" / "md-sync-ui" / "release" / "web" / "public"
STATIC = ROOT / "md_sync" / "web" / "static"
TITLE = "md-sync Web"


def build_frontend() -> None:
    print("[web] dx build --release --features web --platform web ...")
    subprocess.run(
        ["dx", "build", "--release", "--features", "web", "--platform", "web"],
        cwd=DIOXUS,
        check=True,
    )
    if not PUBLIC.exists():
        sys.exit(f"[web] build output not found: {PUBLIC}")


def prune_assets(static: Path) -> None:
    """Drop hashed assets not referenced by the entry js/css.

    dx keeps every historical hashed build in ``assets/``; only the files
    referenced by ``index.html`` (and transitively by the entry js) are needed.
    """
    index = (static / "index.html").read_text(encoding="utf-8")
    keep_js = set(re.findall(r"assets/([\w.-]+\.js)", index))
    keep_wasm: set[str] = set()
    for js_name in keep_js:
        body = (static / "assets" / js_name).read_text(encoding="utf-8")
        keep_wasm |= set(re.findall(r"([\w.-]+_bg[\w.-]*\.wasm)", body))

    assets_dir = static / "assets"
    if not assets_dir.exists():
        return
    for f in assets_dir.iterdir():
        if not f.is_file():
            continue
        if f.name in keep_js or f.name in keep_wasm or f.suffix == ".css":
            continue
        f.unlink()
        print(f"[web] pruned stale asset: {f.name}")


def fix_title(index: Path) -> None:
    text = index.read_text(encoding="utf-8")
    fixed = re.sub(r"<title>.*?</title>", f"<title>{TITLE}</title>", text, count=1)
    if fixed != text:
        index.write_text(fixed, encoding="utf-8")
        print(f"[web] title fixed -> {TITLE}")


def deploy() -> None:
    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(PUBLIC, STATIC)
    prune_assets(STATIC)
    fix_title(STATIC / "index.html")
    print(f"[web] frontend deployed to {STATIC}")


def main() -> None:
    clean = "--clean" in sys.argv
    force = "--force" in sys.argv
    if "--no-build" not in sys.argv:
        build_frontend()
    deploy()
    if force or _pkg.needs_rebuild(web_only=True):
        _pkg.package(web_only=True, clean=clean)
    else:
        print("[web] backend up to date — PyInstaller skipped")
    print("[web] done. Run `./dist/md-sync-web` -> http://127.0.0.1:8580")


if __name__ == "__main__":
    main()
