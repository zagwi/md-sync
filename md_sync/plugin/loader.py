"""Plugin loader — install, update, and remove plugins.

Supports:
  - Local directory:  ``md-sync plugin install ./my-plugin/``
  - Git repository:   ``md-sync plugin install https://github.com/user/md-sync-plugin-xxx``
  - Package name:     ``md-sync plugin install xxx`` (from PyPI)

Security notes:
  - Git install is restricted to ``https://`` / ``git@`` URLs (no plaintext http).
  - All subprocess calls are bounded by ``SUBPROCESS_TIMEOUT``.
  - Pip installs use ``--no-deps`` and may be constrained by the
    ``MD_SYNC_ALLOWED_PIP_PACKAGES`` allow-list (comma-separated names).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Bounded time for any single install subprocess (seconds).
SUBPROCESS_TIMEOUT = 120

# Common, trusted plugin host allow-list. Additional hosts can be appended via
# the ``MD_SYNC_ALLOWED_GIT_HOSTS`` comma-separated env var.
DEFAULT_GIT_HOSTS = ("github.com", "gitlab.com", "gitee.com")


def _git_host_allowed(url: str) -> bool:
    hosts = list(DEFAULT_GIT_HOSTS)
    extra = os.environ.get("MD_SYNC_ALLOWED_GIT_HOSTS", "")
    if extra:
        hosts += [h.strip() for h in extra.split(",") if h.strip()]
    for host in hosts:
        if host and host in url:
            return True
    return False


def _pip_allowed(package: str) -> bool:
    allow = os.environ.get("MD_SYNC_ALLOWED_PIP_PACKAGES", "")
    if not allow:
        # No allow-list configured → allow but warn (operator should pin it).
        logger.warning(
            "Installing pip package '%s' with no MD_SYNC_ALLOWED_PIP_PACKAGES "
            "allow-list. Set it to restrict installable packages.",
            package,
        )
        return True
    allowed = {p.strip() for p in allow.split(",") if p.strip()}
    return package in allowed


def install_plugin(
    source: str,
    name: str | None = None,
    target_dir: Path | None = None,
) -> Path:
    """Install a plugin from various source types.

    Args:
        source: Local path, git URL, or pip package name.
        name: Plugin name (auto-detected if None).
        target_dir: Install directory (default: ~/.md-sync/plugins/).

    Returns:
        Path to installed plugin directory.

    Raises:
        ValueError: If a remote source fails host/allow-list validation.
        RuntimeError: If installation fails.
    """
    target = target_dir or (Path.home() / ".md-sync" / "plugins")
    target.mkdir(parents=True, exist_ok=True)

    source_path = Path(source)

    if source_path.exists():
        # Local directory install
        if not source_path.is_dir():
            raise ValueError(f"Local source is not a directory: {source}")
        plugin_name = name or source_path.name
        dest = target / plugin_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_path, dest)
        logger.info("[plugin] Installed from local: %s → %s", source, dest)
        return dest

    if source.startswith(("https://", "git@")):
        # Git repository install — restrict to known hosts.
        if not _git_host_allowed(source):
            raise ValueError(
                f"Git host not allowed for plugin install: {source}. "
                f"Allowed hosts: {', '.join(DEFAULT_GIT_HOSTS)} "
                f"(extend via MD_SYNC_ALLOWED_GIT_HOSTS)."
            )
        plugin_name = name or source.rstrip("/").split("/")[-1].replace(".git", "")
        dest = target / plugin_name
        if dest.exists():
            shutil.rmtree(dest)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source, str(dest)],
                check=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"Git clone timed out after {SUBPROCESS_TIMEOUT}s: {source}") from e
        except subprocess.CalledProcessError as e:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"Git clone failed: {source}") from e
        logger.info("[plugin] Cloned from git: %s → %s", source, dest)
        return dest

    if source.startswith("http://"):
        raise ValueError(f"Refusing plaintext http plugin source (use https): {source}")

    # Try pip install (package name) — constrained by allow-list, no deps.
    if not _pip_allowed(source):
        raise ValueError(
            f"Package '{source}' is not in MD_SYNC_ALLOWED_PIP_PACKAGES. Refusing install."
        )
    try:
        result = subprocess.run(
            ["pip", "install", "--no-deps", source],
            capture_output=True,
            text=True,
            check=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        logger.info("[plugin] Installed via pip: %s", source)
        logger.debug("[plugin] pip output: %s", result.stdout.strip())
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Pip install timed out after {SUBPROCESS_TIMEOUT}s: {source}") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to install {source}: {e.stderr}") from e

    # The plugin will be loaded via entry points next time. Return a best-effort
    # placeholder path; real resolution happens through the import system.
    return target / source


def remove_plugin(name: str) -> bool:
    """Remove a plugin from the user plugin directory."""
    dest = Path.home() / ".md-sync" / "plugins" / name
    if dest.exists():
        shutil.rmtree(dest)
        logger.info("[plugin] Removed: %s", name)
        return True
    logger.warning("[plugin] Not found: %s", name)
    return False


def list_installed(target_dir: Path | None = None) -> list[Path]:
    """List all installed plugin directories."""
    target = target_dir or (Path.home() / ".md-sync" / "plugins")
    if not target.exists():
        return []
    return sorted([d for d in target.iterdir() if d.is_dir()])
