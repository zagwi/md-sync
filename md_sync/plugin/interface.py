"""Plugin interface for md-sync.

Plugins can be:
  - ``RenderPlugin``: provides HTML rendering templates + CSS + filters
  - ``ParserPlugin``: provides a custom Markdown source parser
  - ``PluginPack``:    bundles parser + template.md + HTML templates + CSS
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── Plugin types ────────────────────────────────────────────────────────────

PLUGIN_TYPE_RENDER = "render"       # provides rendering templates
PLUGIN_TYPE_PARSER = "parser"       # provides a custom MD source parser
PLUGIN_TYPE_PACK = "pack"           # provides parser + template + style (full pack)
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

    # Plugin Pack fields (type="pack")
    template: Optional[str] = None   # relative path to source template.md
    parser_schema: Optional[str] = None  # schema identifier, e.g. "my-resume"
    parser_class: Optional[str] = None   # Python class path, e.g. "parser.MyParser"


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


# ── ParserPlugin (for custom MD source parsers) ──────────────────────────


class ParserPlugin(ABC):
    """Base class for a Markdown source parsing plugin.

    A parser plugin knows how to read documents written in a specific
    template format and convert them into the universal ``Document`` model.

    Key methods:
      ``detect(text)``  — auto-detect if this parser can handle the text
      ``parse(text)``   — parse text into a ``Document``
    """

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest:
        """Plugin metadata."""
        ...

    def detect(self, text: str) -> bool:
        """Auto-detect whether the given text matches this parser's format.

        Override for auto-discovery. Default returns False (user must
        select this parser explicitly by schema name in config).
        """
        return False

    @abstractmethod
    def parse(self, text: str) -> Document:
        """Parse Markdown text into a structured Document."""
        ...

    def parse_file(self, path: Path) -> Document:
        """Parse a file into a Document (with source tracking)."""
        text = path.read_text(encoding="utf-8")
        # Import Document inline to avoid circular import
        from md_sync.core.document import Document as _Doc
        doc = self.parse(text)
        if not isinstance(doc, _Doc):
            raise TypeError(f"ParserPlugin.parse() must return a Document, got {type(doc)}")
        doc.source_path = str(path)
        doc.source_raw = text
        # Detect language from file content
        zh_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        doc.source_lang = "zh" if zh_chars > 100 else "en"
        return doc


# ── Simple directory-based plugin ───────────────────────────────────────────


class DirectoryPlugin(RenderPlugin):
    """Plugin loaded from a local directory (no Python package needed).

    Directory structure::

        my-plugin/
        ├── plugin.yaml          # Plugin manifest
        ├── template.md          # Source template (for pack type)
        ├── parser.py            # Parser module (for pack type)
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

    def load_parser(self) -> Optional[ParserPlugin]:
        """Load the parser from parser.py if it exists and is configured."""
        if not self._manifest.parser_class:
            return None
        parser_path = self._directory / "parser.py"
        if not parser_path.exists():
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"{self._manifest.name}_parser", parser_path
        )
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Resolve parser class from dotted path relative to parser.py
        parts = self._manifest.parser_class.split(".")
        cls_name = parts[-1]
        cls = getattr(mod, cls_name, None)
        if cls is None or not isinstance(cls, type) or not issubclass(cls, ParserPlugin):
            return None
        return cls()

    def get_template_source(self) -> Optional[str]:
        """Get the content of the plugin's template.md source template."""
        if not self._manifest.template:
            return None
        tmpl_path = self._directory / self._manifest.template
        if tmpl_path.exists():
            return tmpl_path.read_text(encoding="utf-8")
        return None

    def _load_manifest(self) -> PluginManifest:
        yaml_path = self._directory / "plugin.yaml"
        if yaml_path.exists():
            import yaml
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if raw:
                # Parse parser config if present
                parser_cfg = raw.get("parser", {}) or {}
                parser_class = parser_cfg.get("class", "") if isinstance(parser_cfg, dict) else ""
                parser_schema = parser_cfg.get("schema", "") if isinstance(parser_cfg, dict) else ""
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
                    template=raw.get("template"),
                    parser_schema=parser_schema,
                    parser_class=parser_class,
                )
        return PluginManifest(
            name=self._directory.name,
            directory=self._directory,
        )
