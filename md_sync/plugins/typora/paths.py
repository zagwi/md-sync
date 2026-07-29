"""Cross-platform discovery of the local Typora installation.

Typora stores its user themes in an OS-specific location. md-sync only needs the
*themes* directory to discover installed ``.css`` themes; if that directory cannot
be found, Typora is treated as not installed and its themes are simply not offered
(so the style dropdown degrades gracefully instead of erroring).

Reference paths (Typora docs / common installs):
  * Windows: ``%APPDATA%\\Typora\\themes``
  * macOS:   ``~/Library/Application Support/abnerworks.Typora/themes``
  * Linux:   ``~/.config/Typora/themes``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def typora_themes_candidates() -> list[Path]:
    """Return candidate Typora themes directories for the current OS."""
    home = Path.home()
    if sys.platform.startswith("win"):
        # Respect the environment in case it has been redirected.
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return [appdata / "Typora" / "themes"]
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        # Older bundle id vs. newer flat name — probe both.
        return [
            support / "abnerworks.Typora" / "themes",
            support / "Typora" / "themes",
        ]
    # Linux and other POSIX systems.
    return [home / ".config" / "Typora" / "themes"]


def get_typora_themes_dir() -> Path | None:
    """Return the first existing Typora themes directory, or ``None`` if not installed."""
    for candidate in typora_themes_candidates():
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def is_typora_installed() -> bool:
    """Whether Typora (and its themes directory) is present on this machine."""
    return get_typora_themes_dir() is not None
