"""Build every distributable edition in one command.

Editions (both built on the Dioxus frontend):
    web     -> dist/md-sync-web   (FastAPI server + wasm UI, http://127.0.0.1:8580)
    desktop -> dist/md-sync-ui    (Dioxus native window, embedded backend)

This is a thin orchestration layer over ``build_web.py`` / ``build_desktop.py``:
each script keeps its own concerns, and both share the fingerprint cache in
``_pkg.py`` (stale bundles are rebuilt automatically, unchanged ones skipped).

Usage:
    python scripts/build_all.py             # web + desktop
    python scripts/build_all.py --web       # web edition only
    python scripts/build_all.py --desktop   # desktop edition only
    python scripts/build_all.py --force     # force re-package backends
    python scripts/build_all.py --clean     # clean PyInstaller caches
    python scripts/build_all.py --no-build  # skip dx frontend build (web only)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

EDITION_SCRIPT = {
    "web": "build_web.py",
    "desktop": "build_desktop.py",
}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="build_all.py",
        description="Build the web and/or desktop distributables (Dioxus frontend).",
    )
    parser.add_argument("--web", action="store_true", help="build web edition only")
    parser.add_argument("--desktop", action="store_true", help="build desktop edition only")
    parser.add_argument("--force", action="store_true", help="force re-package backends")
    parser.add_argument("--clean", action="store_true", help="clean PyInstaller caches")
    parser.add_argument(
        "--no-build", action="store_true", help="skip the dx frontend build (web only)"
    )
    opts = parser.parse_args()

    flags = [
        flag
        for flag, attr in (("--force", "force"), ("--clean", "clean"), ("--no-build", "no_build"))
        if getattr(opts, attr)
    ]

    targets: list[str] = []
    if opts.web:
        targets.append("web")
    if opts.desktop:
        targets.append("desktop")
    if not targets:
        targets = ["web", "desktop"]

    for edition in targets:
        print(f"\n=== [{edition}] {EDITION_SCRIPT[edition]} ===")
        subprocess.run(
            [sys.executable, str(SCRIPTS / EDITION_SCRIPT[edition]), *flags],
            cwd=ROOT,
            check=True,
        )

    print("\nAll done. Artifacts in dist/:")
    for name in ("md-sync-web", "md-sync-ui"):
        exe = ROOT / "dist" / name
        if exe.exists():
            print(f"  {exe} ({exe.stat().st_size / 1024 / 1024:.0f} MB)")


if __name__ == "__main__":
    main()
