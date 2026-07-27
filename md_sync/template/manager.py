"""Template registry and resolution.

TemplateManager discovers templates from multiple sources:
  1. Bundled styles:  <install_dir>/templates/<name>/
  2. User custom:     <install_dir>/templates/user/<name>/
  3. Plugins:         via PluginRegistry.get_template_dirs()
  4. Legacy themes:   <install_dir>/themes/<name>/  (backward compat)
"""
from __future__ import annotations

import json
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

    def list_templates(
        self, schema: Optional[str] = None, include_legacy: bool = False
    ) -> list[TemplateInfo]:
        """List all available templates, optionally filtered by schema.

        Legacy themes (e.g. resume-zh/resume-en) are duplicates of the
        bundled styles and hidden from the UI by default; pass
        ``include_legacy=True`` (CLI) to list them too.
        """
        results: list[TemplateInfo] = []
        seen: set[str] = set()

        # 1. Bundled styles
        styles_dir = self._install_dir / "templates"
        if styles_dir.exists():
            for d in sorted(styles_dir.iterdir()):
                if d.is_dir() and (d / "template.yaml").exists():
                    cat = self._load_from_dir(d)
                    if cat and cat.info.name not in seen:
                        if schema is None or cat.info.schema == schema:
                            results.append(cat.info)
                            seen.add(cat.info.name)

        # 2. User custom
        user_dir = styles_dir / "user" if styles_dir.exists() else None
        if user_dir and user_dir.exists():
            for d in sorted(user_dir.iterdir()):
                if d.is_dir() and (d / "template.yaml").exists():
                    cat = self._load_from_dir(d)
                    if cat and cat.info.name not in seen:
                        if schema is None or cat.info.schema == schema:
                            results.append(cat.info)
                            seen.add(cat.info.name)

        # 3. Plugin-provided templates
        for plugin_tpl_dir in self._plugin_registry.get_template_dirs():
            for d in sorted(plugin_tpl_dir.iterdir()):
                if d.is_dir() and (d / "template.yaml").exists():
                    cat = self._load_from_dir(d)
                    if cat and cat.info.name not in seen:
                        if schema is None or cat.info.schema == schema:
                            cat.info.author = f"plugin:{plugin_tpl_dir.parent.name}"
                            results.append(cat.info)
                            seen.add(cat.info.name)

        # 4. Legacy themes (backward compat) — hidden from the UI by default
        #    because they duplicate the bundled styles (resume-zh/en == bwx).
        if include_legacy:
            themes_dir = self._install_dir / "themes"
            if themes_dir.exists():
                for d in sorted(themes_dir.iterdir()):
                    if d.is_dir() and (d / "theme.yaml").exists():
                        # Check if there's a template.yaml in templates dir with same name
                        if d.name in seen:
                            continue
                        legacy = self._load_legacy_theme(d)
                        if legacy:
                            if schema is None or legacy.info.schema == schema:
                                results.append(legacy.info)
                                seen.add(legacy.info.name)

        return results

    # ── Resolution ──────────────────────────────────────────────────────

    def resolve(self, name: str) -> TemplateCatalog:
        """Find a template by name. Raises FileNotFoundError if not found."""
        if name in self._cache:
            return self._cache[name]

        # Search order: user > bundled > plugins > legacy
        cat = (
            self._try_load("user", name)
            or self._try_load("bundled", name)
            or self._try_load_from_plugins(name)
            or self._try_load_legacy(name)
        )

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

    # ── Create / Install ────────────────────────────────────────────────

    def create_scaffold(
        self,
        name: str,
        label: str = "",
        schema: str = "resume",
        base: Optional[str] = None,
    ) -> Path:
        """Scaffold a new template directory under user custom."""
        dest = self._install_dir / "templates" / "user" / name
        if dest.exists():
            raise FileExistsError(f"Template already exists: {name}")

        dest.mkdir(parents=True, exist_ok=True)
        (dest / "sections").mkdir(exist_ok=True)

        # If base template specified, copy its structure
        if base:
            base_cat = self.resolve(base)
            if base_cat and base_cat.info.directory:
                self._copy_template(base_cat.info.directory, dest)
                return dest

        # Generate fresh scaffold
        meta = {
            "name": name,
            "label": label or name,
            "description": f"Custom {schema} template — {name}",
            "version": "1.0",
            "author": "user",
            "schema": schema,
            "engine": "jinja2",
            "pdf": {"page_size": "A4", "margin": "5mm 8mm"},
        }
        with open(dest / "template.yaml", "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, sort_keys=False)

        # Generate base HTML template
        html = (
            "<!DOCTYPE html>\n<html lang=\"{{ lang or 'en' }}\">\n"
            "<head>\n<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            f"<title>{{{{ doc.name }}}} - {label}</title>\n"
            "<style>\n{{ style_css }}\n{% if print_css %}@media print {\n{{ print_css }}\n}{% endif %}\n</style>\n"
            "</head>\n<body>\n"
            "{% for section in doc.sections %}\n"
            "  {% include 'sections/' + section.id + '.html.j2' %}\n"
            "{% endfor %}\n"
            "</body>\n</html>\n"
        )
        (dest / "base.html.j2").write_text(html, encoding="utf-8")

        # Generate placeholder section templates
        for sid in ["summary", "education", "experience", "project", "open_source"]:
            (dest / "sections" / f"{sid}.html.j2").write_text(
                "<div class=\"section\">\n"
                f"  <h2>{{{{ section.title }}}}</h2>\n"
                "  {{ section.content }}\n"
                "</div>\n",
                encoding="utf-8",
            )

        # Default CSS
        (dest / "style.css").write_text(
            "/* Auto-generated template scaffold */\n"
            "body { font-family: system-ui, sans-serif; max-width: 210mm; margin: 0 auto; padding: 20px; }\n"
            ".section { margin-bottom: 1.5em; }\n"
            "h2 { border-bottom: 2px solid #333; padding-bottom: 4px; }\n",
            encoding="utf-8",
        )

        return dest

    # ── Internal ────────────────────────────────────────────────────────

    def _try_load(self, source: str, name: str) -> Optional[TemplateCatalog]:
        if source in ("user", "bundled"):
            base = self._install_dir / "templates"
            if source == "user":
                base = base / "user"
            target = base / name
            if target.exists() and (target / "template.yaml").exists():
                return self._load_from_dir(target)
        return None

    def _try_load_from_plugins(self, name: str) -> Optional[TemplateCatalog]:
        """Search plugin-provided template directories."""
        for plugin_tpl_dir in self._plugin_registry.get_template_dirs():
            target = plugin_tpl_dir / name
            if target.exists() and (target / "template.yaml").exists():
                cat = self._load_from_dir(target)
                if cat:
                    return cat
        return None

    def _try_load_legacy(self, name: str) -> Optional[TemplateCatalog]:
        themes_dir = self._install_dir / "themes"
        target = themes_dir / name
        if target.exists() and (target / "theme.yaml").exists():
            return self._load_legacy_theme(target)
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

    def _load_legacy_theme(self, directory: Path) -> Optional[TemplateCatalog]:
        """Convert legacy theme.yaml to TemplateCatalog."""
        yaml_path = directory / "theme.yaml"
        if not yaml_path.exists():
            return None
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not raw:
            return None

        info = TemplateInfo(
            name=raw.get("name", directory.name),
            label=raw.get("label", raw.get("description", directory.name)),
            description=raw.get("description", ""),
            version="1.0",
            author="md-sync (legacy)",
            schema="resume",
            engine="jinja2",
            directory=directory,
        )
        return TemplateCatalog(
            info=info,
            pdf=raw.get("pdf", {}),
            sections=raw.get("sections", {}),
        )

    @staticmethod
    def _find_install_dir() -> Path:
        """Find the md-sync installation directory."""
        import md_sync
        return Path(md_sync.__file__).resolve().parent.parent

    @staticmethod
    def _copy_template(src: Path, dst: Path) -> None:
        """Copy a template directory structure."""
        import shutil
        for item in src.iterdir():
            if item.name == "__pycache__":
                continue
            s_dst = dst / item.name
            if item.is_dir():
                shutil.copytree(item, s_dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, s_dst)
