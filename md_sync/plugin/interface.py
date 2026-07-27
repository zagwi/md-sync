"""Plugin interface for md-sync.

All plugins must implement RenderPlugin.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── Plugin types ────────────────────────────────────────────────────────────

PLUGIN_TYPE_RENDER = "render"       # provides rendering templates
PLUGIN_TYPE_TRANSLATE = "translate"  # provides translation backend
PLUGIN_TYPE_EXPORT = "export"       # provides export backend (e.g. PDF)
PLUGIN_TYPE_HOOK = "hook"           # provides pipeline hooks only


# ── Plugin manifest ─────────────────────────────────────────────────────────


@dataclass
class PluginManifest:
    """Information about an installed plugin."""
    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    plugin_type: str = PLUGIN_TYPE_RENDER
    entry_point: str = ""           # Python module path, e.g. "my_plugin.main"
    directory: Optional[Path] = None
    templates: list[str] = field(default_factory=list)  # template names provided
    dependencies: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)      # hook names provided


# ── Plugin base class ───────────────────────────────────────────────────────


class RenderPlugin(ABC):
    """Base class for a rendering plugin.

    A plugin can provide:
    - Template files (Jinja2/CSS) in a ``templates/`` subdirectory
    - Custom Jinja2 filters registered via ``register_filters()``
    - Pipeline hook handlers via ``on_before_render()`` / ``on_after_render()``
    """

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest:
        """Plugin metadata."""
        ...

    # ── Optional hooks ──────────────────────────────────────────────────

    def on_plugin_load(self) -> None:
        """Called when plugin is loaded into the registry."""
        pass

    def on_before_render(self, doc: Any, config: dict) -> None:
        """Called before rendering starts. Can modify doc in-place."""
        pass

    def on_after_render(self, output_path: Path, config: dict) -> None:
        """Called after an output file is written."""
        pass

    def register_filters(self) -> dict[str, callable]:
        """Return Jinja2 filters to register: {name: callable}."""
        return {}

    # ── Built-in discovery ──────────────────────────────────────────────

    @classmethod
    def from_directory(cls, directory: Path) -> "DirectoryPlugin":
        """Create a DirectoryPlugin from a plugin directory."""
        return DirectoryPlugin(directory)


# ── Simple directory-based plugin ───────────────────────────────────────────


class DirectoryPlugin(RenderPlugin):
    """Plugin loaded from a local directory (no Python package needed).

    Directory structure::

        my-plugin/
        ├── plugin.yaml          # Plugin manifest
        ├── templates/           # Jinja2 template styles
        │   ├── modern/          #   same layout as templates/<name>/
        │   └── classic/
        └── filters.py           # Optional: custom Jinja2 filters
    """

    def __init__(self, directory: Path):
        self._directory = directory.resolve()
        self._manifest = self._load_manifest()

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def on_plugin_load(self) -> None:
        """Try to import filters.py if it exists."""
        filters_path = self._directory / "filters.py"
        if filters_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"{self._manifest.name}_filters", filters_path
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

    def register_filters(self) -> dict[str, callable]:
        filters_path = self._directory / "filters.py"
        if filters_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"{self._manifest.name}_filters", filters_path
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "filters") and isinstance(mod.filters, dict):
                    return mod.filters
        return {}

    def _load_manifest(self) -> PluginManifest:
        yaml_path = self._directory / "plugin.yaml"
        if yaml_path.exists():
            import yaml
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if raw:
                return PluginManifest(
                    name=raw.get("name", self._directory.name),
                    version=raw.get("version", "1.0"),
                    description=raw.get("description", ""),
                    author=raw.get("author", ""),
                    plugin_type=raw.get("type", PLUGIN_TYPE_RENDER),
                    entry_point=raw.get("entry_point", ""),
                    directory=self._directory,
                    templates=raw.get("templates", []),
                    dependencies=raw.get("dependencies", []),
                    hooks=raw.get("hooks", []),
                )
        return PluginManifest(
            name=self._directory.name,
            directory=self._directory,
        )
