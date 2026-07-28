"""HTML renderer: Document → HTML file via Jinja2 templates.

Loads a theme's Jinja2 environment and renders each section
through its corresponding component template.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import re

from md_sync.core.document import Document


# Metric pattern: digits followed by K/k/+/%/×/倍 etc.
_METRIC_RX = re.compile(
    r"(\d+[Kk]?\s*[+%]?|"
    r"\d+倍|"
    r"P\d+[<>]\d+ms|"
    r"\d+\.\d+%[+]?)"
)
# High-visibility numbers (like 3000+, 1000+, 15人, 25人, 100+, 25+)
_NUMBER_RX = re.compile(r"(\d+[+]?)([人]?)")


def _replace_metrics(text: str) -> str:
    """Jinja2 filter: wrap metric values with <span class='metric'>.
    
    Numbers ≥ 100 are wrapped in metric-blue, others in metric.
    """
    def _wrap(m):
        val = m.group(0).strip()
        # Extract numeric part
        nums = re.findall(r"\d+", val)
        is_high = nums and int(nums[0]) >= 100 and '%' not in val
        cls = "metric-blue" if is_high else "metric"
        return f'<span class="{cls}">{val}</span>'
    
    return _METRIC_RX.sub(_wrap, text)


class HtmlRenderer:
    """Render a Document to HTML using a theme's Jinja2 templates."""

    def __init__(self, theme_dir: Path | str):
        self._theme_dir = Path(theme_dir).resolve()
        self._env = Environment(
            loader=FileSystemLoader([
                str(self._theme_dir),
                str(self._theme_dir / "sections"),
            ]),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
            # Render None as empty string instead of the literal "None"
            finalize=lambda v: "" if v is None else v,
        )
        self._env.filters["replace_metrics"] = _replace_metrics
        self._load_theme_meta()

    def _load_theme_meta(self) -> None:
        meta_path = self._theme_dir / "theme.yaml"
        if meta_path.exists():
            import yaml
            self._meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        else:
            self._meta = {}

    @property
    def css(self) -> str:
        """Return the theme's style.css content."""
        css_path = self._theme_dir / "style.css"
        if css_path.exists():
            return css_path.read_text(encoding="utf-8")
        return ""

    @property
    def print_css(self) -> str:
        """Return the theme's print.css content (or None)."""
        css_path = self._theme_dir / "print.css"
        if css_path.exists():
            return css_path.read_text(encoding="utf-8")
        return ""

    def render(self, doc: Document, sections_meta: dict | None = None,
                translator=None, lang: str = "zh") -> str:
        """Render the document to a complete HTML string.

        Args:
            doc: Parsed document to render.
            sections_meta: Section template mapping from template.yaml
                           (e.g. {"professional_summary": {"template": "sections/summary.html.j2"}})
            translator: Optional TranslationManager used by templates to look
                        up translations for the current target language.
            lang: The target language of this HTML output (e.g. ``"en"``).
                  Used by templates to pick the right translation.
        """
        template = self._env.get_template("base.html.j2")

        def _t(text):
            """Translate ``text`` into the target language, falling back to
            the original when no translation is cached."""
            if translator is None or lang == doc.source_lang:
                return text
            cached = translator.lookup(text, lang)
            return cached if cached else text

        return template.render(
            doc=doc,
            theme=self._meta,
            sections_meta=sections_meta or {},
            style_css=self.css,
            print_css=self.print_css,
            translator=translator,
            source_lang=doc.source_lang,
            target_lang=lang,
            t=_t,
        )
