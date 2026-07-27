"""Plugin loader — install, update, and remove plugins.

Supports:
  - Local directory:  ``md-sync plugin install ./my-plugin/``
  - Git repository:   ``md-sync plugin install https://github.com/user/md-sync-plugin-xxx``
  - Package name:     ``md-sync plugin install xxx`` (from PyPI)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def install_plugin(
    source: str,
    name: Optional[str] = None,
    target_dir: Optional[Path] = None,
) -> Path:
    """Install a plugin from various source types.

    Args:
        source: Local path, git URL, or pip package name.
        name: Plugin name (auto-detected if None).
        target_dir: Install directory (default: ~/.md-sync/plugins/).

    Returns:
        Path to installed plugin directory.
    """
    target = target_dir or (Path.home() / ".md-sync" / "plugins")
    target.mkdir(parents=True, exist_ok=True)

    source_path = Path(source)

    if source_path.exists():
        # Local directory install
        plugin_name = name or source_path.name
        dest = target / plugin_name
        import shutil
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_path, dest)
        print(f"[plugin] ✓ Installed from local: {source} → {dest}")
        return dest

    if source.startswith(("http://", "https://", "git@")):
        # Git repository install
        plugin_name = name or source.rstrip("/").split("/")[-1].replace(".git", "")
        dest = target / plugin_name
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        subprocess.run(["git", "clone", source, str(dest)], check=True)
        print(f"[plugin] ✓ Cloned from git: {source} → {dest}")
        return dest

    # Try pip install
    try:
        result = subprocess.run(
            ["pip", "install", source],
            capture_output=True, text=True, check=True,
        )
        print(f"[plugin] ✓ Installed via pip: {source}")
        print(f"  {result.stdout.strip()}")
        # The plugin will be loaded via entry points next time
        return target / source  # placeholder
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to install {source}: {e.stderr}")


def remove_plugin(name: str) -> bool:
    """Remove a plugin from the user plugin directory."""
    dest = Path.home() / ".md-sync" / "plugins" / name
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
        print(f"[plugin] ✓ Removed: {name}")
        return True
    print(f"[plugin] Not found: {name}")
    return False


def list_installed(target_dir: Optional[Path] = None) -> list[Path]:
    """List all installed plugin directories."""
    target = target_dir or (Path.home() / ".md-sync" / "plugins")
    if not target.exists():
        return []
    return sorted([d for d in target.iterdir() if d.is_dir()])
