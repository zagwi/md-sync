"""Plugin registry — discovers and manages plugins from multiple sources.

Discovery paths (in order):
  1. ~/.md-sync/plugins/<name>/         (user-wide)
  2. <project>/.md-sync/plugins/<name>/ (project-local)
  3. pip-installed packages with md-sync entry point
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from md_sync.plugin.interface import (
    DirectoryPlugin,
    PluginManifest,
    RenderPlugin,
    PLUGIN_TYPE_RENDER,
)


class PluginRegistry:
    """Discover, load, and manage plugins."""

    def __init__(self, project_dir: Optional[Path] = None):
        self._project_dir = Path(project_dir).resolve() if project_dir else None
        self._plugins: dict[str, RenderPlugin] = {}
        self._load_all()

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def plugins(self) -> dict[str, RenderPlugin]:
        return dict(self._plugins)

    def list_plugins(self, plugin_type: Optional[str] = None) -> list[PluginManifest]:
        """List loaded plugins, optionally filtered by type."""
        results = []
        for p in self._plugins.values():
            m = p.manifest
            if plugin_type is None or m.plugin_type == plugin_type:
                results.append(m)
        return results

    def get(self, name: str) -> Optional[RenderPlugin]:
        return self._plugins.get(name)

    def has_templates(self, name: str) -> bool:
        """Check if a loaded plugin provides templates."""
        p = self._plugins.get(name)
        return p is not None and bool(p.manifest.templates)

    def get_template_dirs(self) -> list[Path]:
        """Return all plugin directories that contain templates.

        Each plugin provides templates in <plugin_dir>/templates/<name>/
        """
        dirs = []
        for p in self._plugins.values():
            if p.manifest.templates and p.manifest.directory:
                tpl_dir = p.manifest.directory / "templates"
                if tpl_dir.exists():
                    dirs.append(tpl_dir)
        return dirs

    def get_filters(self) -> dict[str, callable]:
        """Aggregate all Jinja2 filters from all plugins."""
        filters = {}
        for p in self._plugins.values():
            filters.update(p.register_filters())
        return filters

    # ── Hooks ───────────────────────────────────────────────────────────

    def emit_before_render(self, doc, config: dict) -> None:
        for p in self._plugins.values():
            p.on_before_render(doc, config)

    def emit_after_render(self, output_path: Path, config: dict) -> None:
        for p in self._plugins.values():
            p.on_after_render(output_path, config)

    # ── Install / Remove ────────────────────────────────────────────────

    @staticmethod
    def install_from_directory(source: Path, name: str) -> Path:
        """Install or update a plugin from a local directory.

        Copies the directory to ~/.md-sync/plugins/<name>/.
        """
        dest_root = Path.home() / ".md-sync" / "plugins"
        dest = dest_root / name
        dest.mkdir(parents=True, exist_ok=True)

        import shutil
        for item in source.iterdir():
            s_dst = dest / item.name
            if item.is_dir():
                shutil.copytree(item, s_dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, s_dst)
        return dest

    def remove(self, name: str) -> bool:
        """Remove a plugin and unregister it."""
        if name in self._plugins:
            del self._plugins[name]
        # Remove from disk
        paths = [
            Path.home() / ".md-sync" / "plugins" / name,
        ]
        if self._project_dir:
            paths.append(self._project_dir / ".md-sync" / "plugins" / name)
        removed = False
        for p in paths:
            if p.exists():
                import shutil
                shutil.rmtree(p)
                removed = True
        return removed

    # ── Internal ────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Discover and load plugins from all sources."""
        for discovery_path in self._discovery_paths():
            if not discovery_path.exists():
                continue
            for d in sorted(discovery_path.iterdir()):
                if d.is_dir() and d.name not in self._plugins:
                    if (d / "plugin.yaml").exists() or (d / "templates").exists():
                        try:
                            plugin = DirectoryPlugin(d)
                            plugin.on_plugin_load()
                            self._plugins[d.name] = plugin
                        except Exception as e:
                            print(f"[plugin] Failed to load {d.name}: {e}")

        # Try pip-installed plugins
        self._load_entry_point_plugins()

    def _discovery_paths(self) -> list[Path]:
        paths = [
            Path.home() / ".md-sync" / "plugins",
        ]
        if self._project_dir:
            paths.append(self._project_dir / ".md-sync" / "plugins")
        return paths

    @staticmethod
    def _load_entry_point_plugins() -> None:
        """Discover plugins registered via setuptools entry points."""
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group="md_sync.plugins")
            for ep in eps:
                try:
                    plugin_cls = ep.load()
                    if isinstance(plugin_cls, type) and issubclass(plugin_cls, RenderPlugin):
                        plugin = plugin_cls()
                        plugin.on_plugin_load()
                        # Register is handled by caller
                except Exception:
                    pass
        except Exception:
            pass
