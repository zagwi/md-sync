"""Build the DESKTOP edition in one command -> ``dist/md-sync-ui``.

Steps:
    1. if ``dist/md-sync`` is missing, package the backend first (PyInstaller)
    2. cargo build --release --features desktop (Dioxus native window)
       — build.rs embeds the backend into the binary at build time
    3. copy the binary to ``dist/md-sync-ui``

The result is a self-contained native app: copy ``md-sync-ui`` anywhere and run.

The backend is only re-packaged when its inputs changed (fingerprint cache in
``dist/.backend-md-sync.fp``) — stale bundles are never reused.

Usage:
    python scripts/build_desktop.py          # full: backend + desktop binary
    python scripts/build_desktop.py --force  # force re-package the backend
    python scripts/build_desktop.py --clean  # also clean PyInstaller caches
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import _pkg

ROOT = Path(__file__).resolve().parent.parent
DIOXUS = ROOT / "dioxus"
EXE_NAME = "md-sync.exe" if sys.platform == "win32" else "md-sync"
BACKEND = ROOT / "dist" / EXE_NAME
CARGO_OUT = DIOXUS / "target" / "release" / "md-sync-ui"
DIST_OUT = ROOT / "dist" / "md-sync-ui"


def main() -> None:
    clean = "--clean" in sys.argv
    force = "--force" in sys.argv
    if force or _pkg.needs_rebuild(web_only=False):
        print(f"[desktop] backend stale or missing — packaging backend ...")
        _pkg.package(web_only=False, clean=clean)
    else:
        print(f"[desktop] backend up to date: {BACKEND}")

    # 覆盖旧 UI 产物前先删除，避免复制后留下旧的（例如跨架构/旧版本）二进制。
    if DIST_OUT.exists():
        DIST_OUT.unlink()
        print(f"[desktop] removed stale artifact: {DIST_OUT}")

    print("[desktop] cargo build --release --features desktop ...")
    subprocess.run(
        ["cargo", "build", "--release", "--features", "desktop"],
        cwd=DIOXUS,
        check=True,
    )
    if not CARGO_OUT.exists():
        sys.exit(f"[desktop] build failed: {CARGO_OUT} not found")

    DIST_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CARGO_OUT, DIST_OUT)
    print(f"[desktop] done: {DIST_OUT} ({DIST_OUT.stat().st_size / 1024 / 1024:.0f} MB)")


if __name__ == "__main__":
    main()
