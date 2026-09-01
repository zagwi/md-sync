"""Shared PyInstaller packaging kernel for the web & desktop editions.

Not a user-facing script — imported by ``build_web.py`` (web edition) and
``build_desktop.py`` (backend embedded inside the desktop binary).

    package(web_only=True)   -> dist/md-sync-web[.exe]
    package(web_only=False)  -> dist/md-sync[.exe]

A fingerprint cache (``dist/.backend-<name>.fp``) records every input that
feeds the bundle (interpreter, packaging script, backend sources, bundled
assets). ``needs_rebuild()`` compares it so stale bundles are rebuilt
automatically instead of being blindly reused — a plain "file exists" check
would keep embedding outdated backends (e.g. one built before ``python-docx``
was installed).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent  # repo root (scripts/ -> repo/)


def _entry(web_only: bool) -> tuple[Path, str]:
    if web_only:
        return ROOT / "md_sync" / "web" / "__main__.py", "md-sync-web"
    return ROOT / "md_sync" / "cli.py", "md-sync"


def _sep() -> str:
    return os.pathsep


def _collect_datas() -> list[tuple[str, str]]:
    """Return (source_dir, dest_dir_in_bundle) pairs for bundled assets."""
    bundles = [
        (ROOT / "md_sync" / "templates", "md_sync/templates"),
        (ROOT / "md_sync" / "plugins", "md_sync/plugins"),
        (ROOT / "md_sync" / "web" / "static", "md_sync/web/static"),
    ]
    return [(str(src), dest) for src, dest in bundles if src.exists()]


def _fingerprint(web_only: bool) -> str:
    """Hash of everything that feeds the bundle: interpreter version, this
    packaging script, the entry point, backend sources and bundled assets."""
    entry, _ = _entry(web_only)
    hasher = hashlib.sha256()
    hasher.update(sys.version.encode())
    hasher.update(Path(__file__).resolve().read_bytes())
    hasher.update(entry.read_bytes())
    for f in sorted((ROOT / "md_sync").rglob("*.py")):
        hasher.update(f.read_bytes())
    for src, _ in _collect_datas():
        for f in sorted(Path(src).rglob("*")):
            if f.is_file():
                hasher.update(f.read_bytes())
    return hasher.hexdigest()


def _fp_file(app_name: str) -> Path:
    return ROOT / "dist" / f".backend-{app_name}.fp"


def needs_rebuild(web_only: bool) -> bool:
    """True when the bundle is missing or was built from different inputs."""
    _, app_name = _entry(web_only)
    exe = ROOT / "dist" / (app_name + (".exe" if os.name == "nt" else ""))
    fp = _fp_file(app_name)
    if not exe.exists() or not fp.exists():
        return True
    try:
        cached = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return cached.get("fingerprint") != _fingerprint(web_only)


def package(web_only: bool, clean: bool = False) -> Path:
    """Run PyInstaller for the requested edition and return the dist path."""
    entry, app_name = _entry(web_only)
    if not entry.exists():
        sys.exit(f"[pkg] Entry point not found: {entry}")

    # 打包前先删除旧产物与指纹缓存：PyInstaller --noconfirm 只会覆盖同名
    # 文件，旧的 onefile 残留可能被复用/混淆；指纹不删则 needs_rebuild()
    # 可能误判为 up-to-date 而跳过打包，导致"编译了但没生效"。
    exe = ROOT / "dist" / (app_name + (".exe" if os.name == "nt" else ""))
    if exe.exists():
        exe.unlink()
        print(f"[pkg] removed stale artifact: {exe}")
    fp = _fp_file(app_name)
    if fp.exists():
        fp.unlink()
        print(f"[pkg] removed stale fingerprint: {fp}")

    datas_args: list[str] = []
    for src, dest in _collect_datas():
        datas_args += ["--add-data", f"{src}{_sep()}{dest}"]

    args = [
        str(entry),
        "--name",
        app_name,
        "--onefile",
        "--noconfirm",
        "--specpath",
        str(ROOT / "build"),  # keep *.spec out of the repo root
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
        # Plugins are loaded dynamically via importlib at runtime, so
        # PyInstaller's static analysis never sees their imports. docx_exporter
        # needs python-docx; without this, DOCX export silently breaks inside
        # the bundle ("No module named 'docx'") even when it works in dev.
        "--hidden-import",
        "docx",
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

    import platform

    print(f"[pkg] Building {app_name} for {platform.system()} ...")
    PyInstaller.__main__.run(args)
    _fp_file(app_name).write_text(
        json.dumps({"fingerprint": _fingerprint(web_only)}), encoding="utf-8"
    )
    print(f"[pkg] Done: {exe}")
    return exe
