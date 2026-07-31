"""Plugin registry — discovers and manages plugins from multiple sources.

Discovery paths (in order):
  1. ~/.md-sync/plugins/<name>/         (user-wide)
  2. <project>/.md-sync/plugins/<name>/ (project-local)
  3. pip-installed packages with md-sync entry point

Parser resolution:
  Pack-type plugins (type: pack) can register a custom parser under a
  schema name. The MdParser dispatcher tries plugin parsers first, then
  built-in parsers, and finally falls back to generic markdown parsing.
"""
from __future__ import annotations

import logging
from pathlib import Path

from md_sync.plugin.interface import (
    PLUGIN_TYPE_PACK,
    PLUGIN_TYPE_PARSER,
    DirectoryPlugin,
    DocxExporter,
    ParserPlugin,
    PdfExporter,
    PluginManifest,
    RenderPlugin,
)

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Discover, load, and manage plugins."""

    def __init__(self, project_dir: Path | None = None):
        self._project_dir = Path(project_dir).resolve() if project_dir else None
        self._plugins: dict[str, RenderPlugin] = {}
        self._parsers: dict[str, ParserPlugin] = {}  # schema_name -> ParserPlugin
        self._pdf_exporters: dict[str, PdfExporter] = {}  # schema_or_name -> PdfExporter
        self._docx_exporters: dict[str, DocxExporter] = {}  # schema_or_name -> DocxExporter
        self._load_all()

    # ── Public API (General) ────────────────────────────────────────────

    @property
    def plugins(self) -> dict[str, RenderPlugin]:
        return dict(self._plugins)

    def list_plugins(self, plugin_type: str | None = None) -> list[PluginManifest]:
        """List loaded plugins, optionally filtered by type."""
        results = []
        for p in self._plugins.values():
            m = p.manifest
            if plugin_type is None or m.plugin_type == plugin_type:
                results.append(m)
        return results

    def get(self, name: str) -> RenderPlugin | None:
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

    # ── PDF export overrides ────────────────────────────────────────────

    def get_pdf_exporter(self, schema: str | None = None) -> PdfExporter | None:
        """Return the plugin PDF exporter that overrides the built-in one.

        A plugin exporter only ever applies to the document *schema* it was
        registered for (e.g. ``"gongwen"`` → the gongwen pack's GB/T 9704-2012
        exporter). It must NEVER affect other schemas — typora, resume,
        markdown and any unregistered schema keep the built-in exporter.

        Returns ``None`` when the schema has no plugin override — callers then
        fall back to the built-in Chromium exporter.
        """
        if schema and schema in self._pdf_exporters:
            return self._pdf_exporters[schema]
        return None

    def list_pdf_exporters(self) -> list[tuple[str, PdfExporter]]:
        """List all registered PDF exporters: [(key, exporter), ...]."""
        return list(self._pdf_exporters.items())

    # ── DOCX export overrides ───────────────────────────────────────────

    def get_docx_exporter(self, schema: str | None = None) -> DocxExporter | None:
        """Return the plugin DOCX exporter that overrides the built-in one.

        A plugin exporter only ever applies to the document *schema* it was
        registered for (e.g. ``"gongwen"`` → the gongwen pack's GB/T 9704-2012
        exporter). It must NEVER affect other schemas — typora, resume,
        markdown and any unregistered schema keep the built-in pandoc
        exporter.

        Returns ``None`` when the schema has no plugin override — callers then
        fall back to the built-in pandoc exporter.
        """
        if schema and schema in self._docx_exporters:
            return self._docx_exporters[schema]
        return None

    def list_docx_exporters(self) -> list[tuple[str, DocxExporter]]:
        """List all registered DOCX exporters: [(key, exporter), ...]."""
        return list(self._docx_exporters.items())

    # ── Parser resolution ───────────────────────────────────────────────

    def get_parser(self, schema: str) -> ParserPlugin | None:
        """Get a parser by schema name (e.g. "my-resume")."""
        return self._parsers.get(schema)

    def find_parser(self, text: str) -> ParserPlugin | None:
        """Find a parser that can handle the given text, via ``detect()``.

        Iterates all registered parsers and returns the first one whose
        ``detect(text)`` returns True.
        """
        for parser in self._parsers.values():
            try:
                if parser.detect(text):
                    return parser
            except Exception:
                logger.debug("detect() raised for parser %r", parser, exc_info=True)
                continue
        return None

    def list_parsers(self) -> list[tuple[str, ParserPlugin]]:
        """List all registered parsers: [(schema_name, ParserPlugin), ...]."""
        return list(self._parsers.items())

    def detect_schema(self, text: str) -> dict | None:
        """Auto-detect the best-matching schema for a given text content.

        Reuses ``find_parser()`` to find a matching parser, then looks up
        its schema name from the registry.

        Returns::
            {"schema": "resume", "name": "resume", "method": "detect", "confidence": "high"}
            or None if no parser matches.
        """
        parser = self.find_parser(text)
        if not parser:
            return None
        # Find the schema name for this parser instance
        for schema_name, p in self._parsers.items():
            if p is parser:
                return {
                    "schema": schema_name,
                    "name": parser.manifest.name,
                    "method": "detect",
                    "confidence": "high",
                }
        return None

    def get_template_source(self, name: str) -> str | None:
        """Get the content of a plugin's source ``template.md`` file.

        Args:
            name: Plugin name.

        Returns:
            The full text of the plugin's template.md, or None if not found.
        """
        plugin = self._plugins.get(name)
        if not plugin or not isinstance(plugin, DirectoryPlugin):
            return None
        return plugin.get_template_source()

    def get_template_source_by_schema(self, schema: str) -> str | None:
        """Get the source template.md for the plugin that registered a schema."""
        # Find the plugin that registered this schema
        for p in self._plugins.values():
            m = p.manifest
            if m.parser_schema == schema and isinstance(p, DirectoryPlugin):
                return p.get_template_source()
        return None

    def get_pack_info(self, name: str) -> PluginManifest | None:
        """Get full manifest for a pack-type plugin."""
        plugin = self._plugins.get(name)
        if plugin and plugin.manifest.plugin_type == PLUGIN_TYPE_PACK:
            return plugin.manifest
        return None

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
            # Also unregister parsers from this plugin
            manifest = self._plugins[name].manifest
            if manifest.parser_schema and manifest.parser_schema in self._parsers:
                del self._parsers[manifest.parser_schema]
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
                            # If this is a pack-type plugin, load its parser
                            self._register_parser_if_pack(plugin)
                            # If it declares a PDF exporter, register the override
                            self._register_pdf_exporter(plugin)
                            # If it declares a DOCX exporter, register the override
                            self._register_docx_exporter(plugin)
                        except Exception as e:
                            logger.warning("[plugin] Failed to load %s: %s", d.name, e)

        # Try pip-installed plugins
        self._load_entry_point_plugins()

    def _register_parser_if_pack(self, plugin: DirectoryPlugin) -> None:
        """If the plugin is a pack type, load its parser and register it."""
        manifest = plugin.manifest
        if manifest.plugin_type not in (PLUGIN_TYPE_PACK, PLUGIN_TYPE_PARSER):
            return
        if not manifest.parser_schema:
            return
        if manifest.parser_schema in self._parsers:
            return  # already registered
        try:
            parser = plugin.load_parser()
            if parser:
                self._parsers[manifest.parser_schema] = parser
                logger.info(
                    "[plugin] ✓ Registered parser '%s' from '%s'",
                    manifest.parser_schema, manifest.name)
        except Exception as e:
            logger.warning(
                "[plugin] Failed to load parser for '%s': %s", manifest.name, e)

    def _register_pdf_exporter(self, plugin: DirectoryPlugin) -> None:
        """Register a plugin's PDF exporter override (if any).

        The exporter is keyed by the plugin's parser schema when available,
        otherwise by the plugin name, so callers can look it up per document
        schema via :meth:`get_pdf_exporter`.
        """
        manifest = plugin.manifest
        if not manifest.pdf_exporter_class:
            return
        key = manifest.parser_schema or manifest.name
        if key in self._pdf_exporters:
            return  # already registered
        try:
            exporter = plugin.load_pdf_exporter()
            if exporter:
                self._pdf_exporters[key] = exporter
                logger.info(
                    "[plugin] ✓ Registered PDF exporter '%s' from '%s'",
                    key, manifest.name)
        except Exception as e:
            logger.warning(
                "[plugin] Failed to load PDF exporter for '%s': %s", manifest.name, e)

    def _register_docx_exporter(self, plugin: DirectoryPlugin) -> None:
        """Register a plugin's DOCX exporter override (if any).

        The exporter is keyed by the plugin's parser schema when available,
        otherwise by the plugin name, so callers can look it up per document
        schema via :meth:`get_docx_exporter`.
        """
        manifest = plugin.manifest
        if not manifest.docx_exporter_class:
            return
        key = manifest.parser_schema or manifest.name
        if key in self._docx_exporters:
            return  # already registered
        try:
            exporter = plugin.load_docx_exporter()
            if exporter:
                self._docx_exporters[key] = exporter
                logger.info(
                    "[plugin] ✓ Registered DOCX exporter '%s' from '%s'",
                    key, manifest.name)
        except Exception as e:
            logger.warning(
                "[plugin] Failed to load DOCX exporter for '%s': %s", manifest.name, e)

    def _discovery_paths(self) -> list[Path]:
        """Discovery paths for plugins (later paths override earlier ones).

        Order:
          1. Project-local:    <project>/.md-sync/plugins/<name>/
          2. User-wide:        ~/.md-sync/plugins/<name>/
          3. Built-in plugins: <install_dir>/plugins/<name>/
        """
        paths = []
        if self._project_dir:
            paths.append(self._project_dir / ".md-sync" / "plugins")
        paths.append(Path.home() / ".md-sync" / "plugins")
        paths.append(self._builtin_plugins_dir())
        return paths

    @staticmethod
    def _builtin_plugins_dir() -> Path:
        """Return the path to built-in plugin packs shipped with md-sync."""
        import md_sync
        return Path(md_sync.__file__).resolve().parent / "plugins"

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
                    logger.debug("entry-point plugin load failed: %s", ep, exc_info=True)
        except Exception:
            logger.debug("entry-point discovery failed", exc_info=True)
