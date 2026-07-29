"""HTML renderer: Document → HTML file via Jinja2 templates.

Loads a theme's Jinja2 environment and renders each section
through its corresponding component template.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import re
from markupsafe import escape, Markup

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


_URL_RX = re.compile(r"(https?://[^\s，。、）)）]+)")


def _linkify(text: str) -> str:
    """Jinja2 filter: turn bare URLs in plain text into clickable links.

    Non-URL text is escaped; only ``http(s)://`` runs are wrapped in an
    ``<a>`` tag, so any HTML in the source is neutralised.
    """
    out = []
    pos = 0
    for m in _URL_RX.finditer(text):
        out.append(escape(text[pos:m.start()]))
        url = m.group(1)
        out.append(
            f'<a class="ext-link" href="{escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(url)}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


# Inline Markdown formatting: **bold**, *italic*, `code`, ~~strike~~
_INLINE_BOLD_RX = re.compile(r"\*\*(.+?)\*\*")
_INLINE_ITALIC_RX = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_INLINE_CODE_RX = re.compile(r"`(.+?)`")
_INLINE_STRIKE_RX = re.compile(r"~~(.+?)~~")


def _md_inline(text: str) -> str:
    """Jinja2 filter: convert inline Markdown formatting to HTML.

    Handles:
    - ``**bold**`` → ``<strong>bold</strong>``
    - ``*italic*`` → ``<em>italic</em>``
    - ```code``` → ``<code>code</code>``
    - ``~~strike~~`` → ``<del>strike</del>``

    This filter should be applied AFTER ``linkify`` (or any other escaping
    filter) in the Jinja2 pipeline, because it does NOT re-escape the input.
    The input text is expected to be already HTML-safe (``<``, ``>``, ``&``
    already escaped).
    Returns a ``Markup`` object safe for ``| safe`` output.
    """
    out = str(text)  # Input already HTML-escaped by linkify (applied first)
    out = _INLINE_CODE_RX.sub(r"<code>\1</code>", out)
    out = _INLINE_BOLD_RX.sub(r"<strong>\1</strong>", out)
    out = _INLINE_ITALIC_RX.sub(r"<em>\1</em>", out)
    out = _INLINE_STRIKE_RX.sub(r"<del>\1</del>", out)
    return Markup(out)


class HtmlRenderer:
    """Render a Document to HTML using a theme's Jinja2 templates."""

    def __init__(self, theme_dir: Path | str, filters: dict | None = None):
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
        self._env.filters["linkify"] = _linkify
        self._env.filters["md_inline"] = _md_inline
        # ① 插件机制：接入插件通过 entry_points / filters.py 声明的自定义过滤器
        for _name, _fn in (filters or {}).items():
            self._env.filters[_name] = _fn
        self._env.globals["chr"] = chr  # for templates that split by newline
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
                translator=None, lang: str = "zh",
                typora_css: str | None = None) -> str:
        """Render the document to a complete HTML string.

        Args:
            doc: Parsed document to render.
            sections_meta: Section template mapping from template.yaml
                           (e.g. {"professional_summary": {"template": "sections/summary.html.j2"}})
            translator: Optional TranslationManager used by templates to look
                        up translations for the current target language.
            lang: The target language of this HTML output (e.g. ``"en"``).
                  Used by templates to pick the right translation.
            typora_css: When provided, overrides ``style_css`` with a Typora
                        theme's CSS content. Used by the ``typora-*`` template
                        family to inject external theme styles.
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
            style_css=typora_css or self.css,
            print_css=self.print_css,
            translator=translator,
            source_lang=doc.source_lang,
            target_lang=lang,
            t=_t,
        )
