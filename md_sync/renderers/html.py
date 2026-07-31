"""HTML renderer: Document → HTML file via Jinja2 templates.

Loads a theme's Jinja2 environment and renders each section
through its corresponding component template.
"""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import escape

from md_sync.core.document import Document
from md_sync.core.md_engine import render_inline as _md_inline
from md_sync.core.md_engine import render_block as _md_block

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
    ``<a>`` tag, so any HTML in the source is neutralised. URLs that already
    appear inside an HTML tag (e.g. an ``<a href="…">`` produced upstream by
    the ``md_inline`` filter) are left untouched to avoid double-wrapping.
    """
    out = []
    pos = 0
    for m in _URL_RX.finditer(text):
        start = m.start()
        # Skip URLs that sit inside an unclosed HTML tag (no '>' between the
        # last '<' and this match). They belong to an attribute value such as
        # href="…" and must not be linkified again.
        pre = text[pos:start]
        lt = pre.rfind('<')
        if lt != -1 and '>' not in pre[lt + 1:]:
            out.append(text[pos:start])
            pos = start
            continue
        out.append(escape(text[pos:start]))
        url = m.group(1)
        out.append(
            f'<a class="ext-link" href="{escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(url)}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def _replace_metrics_tag_safe(html: str) -> str:
    """Apply :func:`_replace_metrics` to *text* nodes only, never inside tags.

    After markdown-it inline rendering, digits may legitimately appear inside
    attribute values (e.g. ``<a href=".../2024">``). Wrapping those would
    corrupt the markup, so we split on tags and only transform the plain-text
    segments.
    """
    parts = re.split(r"(<[^>]+>)", html)
    return "".join(_replace_metrics(p) if i % 2 == 0 else p
                   for i, p in enumerate(parts))


def _rich(text: str) -> str:
    """Resume personalisation filter: render inline Markdown via the shared
    markdown-it kernel, then highlight metrics in a tag-safe manner.

    This routes entry / project / bullet item text through the *same* inline
    engine as generic documents (bold, italics, code, links) instead of the old
    hand-rolled ``linkify`` filter, keeping the kernel as the single source of
    truth. Bare URLs are intentionally NOT linkified — markdown-it (the generic
    kernel) does not autolink either, so both layouts stay consistent.

    Multi-paragraph content (separated by ``\n\n``) is split and each paragraph
    wrapped in ``<p>`` so theme templates get proper line breaks even when the
    content was joined from multiple source lines (e.g. bullet items merged
    into a single project entry by the resume parser).
    """
    stripped = text.strip()
    if "\n\n" in stripped:
        paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
        out_parts = []
        for p in paragraphs:
            rendered = _md_inline(p)
            out_parts.append(f"<p>{rendered}</p>")
        return _replace_metrics_tag_safe("".join(out_parts))
    return _replace_metrics_tag_safe(_md_inline(text))


# Inline Markdown is rendered by a mature CommonMark/GFM engine
# (markdown-it-py) via ``md_sync.core.md_engine.render_inline``. Raw HTML in
# the source is escaped by the engine, so a fragment like
# ``templates/<style>/`` can never open a real ``<style>`` tag and swallow the
# rest of the document. The previous hand-rolled regex implementation has been
# removed entirely.
# (``_md_inline`` is imported at the top of this module.)


def _split_md_blocks(text: str) -> list[str]:
    """Split Markdown into top-level blocks, keeping fenced code blocks whole.

    Splitting on blank lines would otherwise break a fenced code block that
    contains an internal blank line, so we track fence state and never split
    inside one.
    """
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
                buf.append(line)
                continue
            if line.strip() == "":
                if buf:
                    blocks.append("\n".join(buf))
                    buf = []
                blocks.append("")  # preserve the blank separator
                continue
            buf.append(line)
        else:
            buf.append(line)
            if stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
                blocks.append("\n".join(buf))
                buf = []
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def _translate_raw_blocks(source: str, translator, lang: str) -> str:
    """Translate a whole Markdown source block-by-block for the raw path.

    Each top-level block is looked up in the translation cache; cache misses
    fall back to the original text. Fenced code blocks are kept intact so their
    internal blank lines are never split across lookups.
    """
    out: list[str] = []
    for block in _split_md_blocks(source):
        if not block.strip():
            out.append(block)
            continue
        translated = translator.lookup(block, lang)
        out.append(translated if translated else block)
    return "\n".join(out)


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
        self._env.filters["md_block"] = _md_block
        self._env.filters["rich"] = _rich
        # ① 插件机制：接入插件通过 entry_points / filters.py 声明的自定义过滤器
        for _name, _fn in (filters or {}).items():
            self._env.filters[_name] = _fn
        self._env.globals["chr"] = chr  # for templates that split by newline
        # Expose the environment so templates can safely guard optional
        # plugin filters, e.g. ``{% if 'gongwen_chrome' in environment.filters %}``
        self._env.globals["environment"] = self._env
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
                typora_css: str | None = None,
                layout: str = "structured") -> str:
        """Render the document to a complete HTML string.

        A single markdown-it kernel (``render_block`` / ``render_inline`` from
        :mod:`md_sync.core.md_engine`) backs *both* layouts; only the assembly
        differs:

        * ``layout="raw"`` — lightweight path for linear documents (the
          ``markdown`` / ``typora`` schemas). The whole source is rendered in
          one shot by markdown-it and wrapped in the theme shell. Fidelity is
          maximal (nested lists, multi-paragraph blockquotes, raw-HTML
          escaping) because the document is never fragmented into ``Item``\\ s.
        * ``layout="structured"`` — personalisation layer for resume-style
          schemas that need per-section / per-item chrome (entry period & title,
          project metrics & tags, the ``<header>`` block). Generic Markdown
          content still flows through the same markdown-it kernel (the
          ``md_block`` / ``md_inline`` filters); only the *chrome* is
          schema-specific.

        Args:
            doc: Parsed document to render.
            sections_meta: Section template mapping from template.yaml
                           (structured layout only).
            translator: Optional TranslationManager used by templates to look
                        up translations for the current target language.
            lang: The target language of this HTML output (e.g. ``"en"``).
                  Used by templates to pick the right translation.
            typora_css: When provided, overrides ``style_css`` with a Typora
                        theme's CSS content.
            layout: ``"raw"`` or ``"structured"``.
        """
        template = self._env.get_template("base.html.j2")

        def _t(text):
            """Translate ``text`` into the target language, falling back to
            the original when no translation is cached."""
            if translator is None or lang == doc.source_lang:
                return text
            cached = translator.lookup(text, lang)
            return cached if cached else text

        if layout == "raw":
            source_raw = doc.source_raw or ""
            if translator and lang != doc.source_lang:
                body_md = _translate_raw_blocks(source_raw, translator, lang)
            else:
                body_md = source_raw
            body_html = _md_block(body_md)
        else:
            body_html = None

        return template.render(
            doc=doc,
            theme=self._meta,
            sections_meta=sections_meta or {},
            style_css=typora_css or self.css,
            print_css=self.print_css,
            typora_css=typora_css,
            translator=translator,
            source_lang=doc.source_lang,
            target_lang=lang,
            t=_t,
            body_html=body_html,
        )
