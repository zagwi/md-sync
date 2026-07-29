"""Template registry and resolution.

TemplateManager discovers templates from multiple sources:
  1. Plugins:         via PluginRegistry.get_template_dirs()  (template styles
                     live inside each plugin's own templates/ directory)
  2. Typora OS themes: auto-discovered from the user's Typora themes dir
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from md_sync.plugin.registry import PluginRegistry


# ── Data ────────────────────────────────────────────────────────────────────


@dataclass
class TemplateInfo:
    """Metadata for a registered template."""
    name: str
    label: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = "md-sync"
    schema: str = "resume"          # which document schema this works with
    inherits: Optional[str] = None  # base template name
    engine: str = "jinja2"
    tags: list[str] = field(default_factory=list)
    preview: Optional[str] = None   # preview image path

    # Resolved
    directory: Optional[Path] = None


@dataclass
class TemplateCatalog:
    """Full template metadata including section config."""
    info: TemplateInfo
    pdf: dict = field(default_factory=lambda: {"page_size": "A4", "margin": "5mm 8mm"})
    sections: dict[str, dict] = field(default_factory=dict)

    @property
    def section_ids(self) -> list[str]:
        return list(self.sections.keys())


# ── Manager ─────────────────────────────────────────────────────────────────


class TemplateManager:
    """Discover and resolve templates across sources."""

    def __init__(self, project_dir: Optional[Path] = None):
        self._project_dir = Path(project_dir).resolve() if project_dir else None
        self._install_dir = self._find_install_dir()
        self._cache: dict[str, TemplateCatalog] = {}
        self._plugin_registry = PluginRegistry(project_dir)

    # ── Listing ─────────────────────────────────────────────────────────

    def list_templates(self, schema: Optional[str] = None) -> list[TemplateInfo]:
        """List all available templates, optionally filtered by schema.

        Template styles are no longer shipped in a central ``templates/`` dir:
        each plugin carries its own styles under ``<plugin>/templates/<name>/``
        (see ``PluginRegistry.get_template_dirs``).

        Typora themes from ``~/.config/Typora/themes/`` are auto-discovered
        and registered under the ``typora-<name>`` prefix. They support any
        schema (``schema="*"``) so they appear for all document types.
        """
        results: list[TemplateInfo] = []
        seen: set[str] = set()

        # 1. Plugin-provided templates
        for plugin_tpl_dir in self._plugin_registry.get_template_dirs():
            for d in sorted(plugin_tpl_dir.iterdir()):
                if d.is_dir() and (d / "template.yaml").exists():
                    cat = self._load_from_dir(d)
                    if cat and cat.info.name not in seen:
                        if schema is None or cat.info.schema == schema or cat.info.schema == "*":
                            cat.info.author = f"plugin:{plugin_tpl_dir.parent.name}"
                            results.append(cat.info)
                            seen.add(cat.info.name)

        # 2. Typora themes — only if Typora is installed on this machine
        #    (themes live in an OS-specific dir; see md_sync.plugins.typora.paths).
        from md_sync.plugins.typora.paths import get_typora_themes_dir
        typora_dir = get_typora_themes_dir()
        if typora_dir is not None:
            # The bundled "typora" base style now ships inside the typora plugin.
            typora_base = (
                self._install_dir / "plugins" / "typora" / "templates" / "typora"
            )
            for css_file in sorted(typora_dir.glob("*.css")):
                name = f"typora-{css_file.stem}"
                if name in seen:
                    continue
                # Skip Typora user override files
                if css_file.stem.endswith(".user") or css_file.stem == "base":
                    continue
                # Typora themes are universal (render any doc.sections), so they
                # appear for ALL schemas — do not gate on `schema` here, otherwise
                # selecting the resume plugin hides every typora theme.
                results.append(TemplateInfo(
                    name=name,
                    label=f"Typora {css_file.stem.title()}",
                    description=f"Typora 主题: {css_file.stem}",
                    version="1.0",
                    author="Typora Community",
                    schema="*",
                    engine="jinja2",
                    directory=typora_base,
                ))
                seen.add(name)

        return results

    # ── Resolution ──────────────────────────────────────────────────────

    def resolve(self, name: str) -> TemplateCatalog:
        """Find a template by name. Raises FileNotFoundError if not found."""
        if name in self._cache:
            return self._cache[name]

        # Search order: plugins
        cat = self._try_load_from_plugins(name)

        if cat is None:
            raise FileNotFoundError(
                f"Template '{name}' not found. "
                f"Run 'md-sync template list' to see available templates."
            )

        self._cache[name] = cat
        return cat

    def resolve_path(self, name: str) -> Path:
        """Shortcut: get directory path for a template."""
        return self.resolve(name).info.directory  # type: ignore

    # ── Internal ────────────────────────────────────────────────────────

    def _try_load_from_plugins(self, name: str) -> Optional[TemplateCatalog]:
        """Search plugin-provided template directories."""
        for plugin_tpl_dir in self._plugin_registry.get_template_dirs():
            target = plugin_tpl_dir / name
            if target.exists() and (target / "template.yaml").exists():
                cat = self._load_from_dir(target)
                if cat:
                    return cat
        return None

    def _load_from_dir(self, directory: Path) -> Optional[TemplateCatalog]:
        yaml_path = directory / "template.yaml"
        if not yaml_path.exists():
            return None
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not raw:
            return None

        info = TemplateInfo(
            name=raw.get("name", directory.name),
            label=raw.get("label", raw.get("name", directory.name)),
            description=raw.get("description", ""),
            version=raw.get("version", "1.0"),
            author=raw.get("author", "md-sync"),
            schema=raw.get("schema", "resume"),
            inherits=raw.get("inherits"),
            engine=raw.get("engine", "jinja2"),
            tags=raw.get("tags", []),
            directory=directory,
        )
        return TemplateCatalog(
            info=info,
            pdf=raw.get("pdf", {"page_size": "A4", "margin": "5mm 8mm"}),
            sections=raw.get("sections", {}),
        )

    @staticmethod
    def _find_install_dir() -> Path:
        """Find the md-sync installation directory.

        Template styles now live inside each plugin (``md_sync/plugins/<name>/
        templates/``). All are inside the ``md_sync`` package so they are
        distributed with the wheel and resolvable at runtime.
        """
        import md_sync
        return Path(md_sync.__file__).resolve().parent


