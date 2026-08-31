"""Cross-platform build script for md-sync standalone executables.

Produces a single-file executable for the current platform:
  - Windows : dist/md-sync.exe
  - macOS   : dist/md-sync
  - Linux   : dist/md-sync

Run:
    python build_app.py            # build for current platform
    python build_app.py --clean    # clean caches before build

Cross-platform binaries (win/linux/macos) are produced by running this
script on each target OS, or via the GitHub Actions workflow in
.github/workflows/build.yml which runs it on windows/macos/ubuntu runners.
"""

from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent  # repo root (scripts/ -> repo/)
APP_NAME = "md-sync"
ENTRY = ROOT / "md_sync" / "cli.py"


def _sep() -> str:
    import os

    return os.pathsep


def _collect_datas() -> list[tuple[str, str]]:
    """Return (source_dir, dest_dir_in_bundle) pairs for bundled assets."""
    bundles = [
        (ROOT / "md_sync" / "templates", "md_sync/templates"),
        (ROOT / "md_sync" / "plugins", "md_sync/plugins"),
        (ROOT / "md_sync" / "web" / "static", "md_sync/web/static"),
    ]
    return [(str(src), dest) for src, dest in bundles if src.exists()]


def build(clean: bool = False) -> None:
    if not ENTRY.exists():
        sys.exit(f"[build] Entry point not found: {ENTRY}")

    datas_args: list[str] = []
    for src, dest in _collect_datas():
        datas_args += ["--add-data", f"{src}{_sep()}{dest}"]

    args = [
        str(ENTRY),
        "--name",
        APP_NAME,
        "--onefile",
        "--noconfirm",
        "--paths",
        str(ROOT),
        *datas_args,
        "--hidden-import",
        "md_sync.watcher",
        "--hidden-import",
        "md_sync.plugin.loader",
        "--hidden-import",
        "md_sync.plugin.registry",
        "--hidden-import",
        "md_sync.web.ipc",
        # mypy (and its mypyc-compiled extensions) may live in the global
        # site-packages and get pulled in by analysis on some Python builds.
        # md-sync does not use it, so exclude to avoid corrupt-bundle errors.
        "--exclude-module",
        "mypy",
        "--exclude-module",
        "mypy_extensions",
    ]
    if clean:
        args.append("--clean")

    print(f"[build] Building {APP_NAME} for {_platform_label()} ...")
    PyInstaller.__main__.run(args)
    print(f"[build] Done. Output in: {ROOT / 'dist'}")


def _platform_label() -> str:
    import platform

    return platform.system()


if __name__ == "__main__":
    build(clean="--clean" in sys.argv)
