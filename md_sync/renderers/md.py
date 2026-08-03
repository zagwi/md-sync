"""Markdown renderer: Document → Markdown file.

This renders the Document back to the source MD format (for the source language)
or generates an MD file in the target language (using the translation cache).
"""

from __future__ import annotations

from md_sync.core.document import Document
from md_sync.translate.manager import TranslationManager


class MdRenderer:
    """Render a Document back to Markdown format."""

    def __init__(self, translator: TranslationManager | None = None):
        self._translator = translator

    def render(self, doc: Document, lang: str = "zh") -> str:
        """Render document to Markdown.

        If ``lang`` equals doc.source_lang, the original source text is
        returned verbatim (no re-serialization, so nothing is lost or
        reformatted). Otherwise, attempts translation lookup for each
        textual field.
        """
        if lang == doc.source_lang and getattr(doc, "source_raw", ""):
            return doc.source_raw.strip() + "\n"

        lines: list[str] = []

        # ── Header ─────────────────────────────────────────────────────
        if doc.name:
            name = self._maybe_translate(doc.name, lang, doc.source_lang)
            title = self._maybe_translate(doc.title, lang, doc.source_lang) if doc.title else ""
            sep = " — " if title else ""
            lines.append(f"# {name}{sep}{title}")
            lines.append("")

        # Contacts
        if doc.meta_lines:
            lines.append(doc.meta_lines[0])
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Sections ───────────────────────────────────────────────────
        for section in doc.sections:
            if not section.title:
                # Intro/body section without a heading — render its items
                # directly instead of emitting an empty "# " heading.
                for item in section.items:
                    line = self._render_item(item, lang, doc.source_lang)
                    if line:
                        lines.append(line)
                lines.append("")
                continue
            section_title = self._maybe_translate(section.title, lang, doc.source_lang)
            lines.append(f"{'#' * section.level} {section_title}")
            lines.append("")

            for item in section.items:
                line = self._render_item(item, lang, doc.source_lang)
                if line:
                    lines.append(line)

            # Blank line after section
            lines.append("")

        return "\n".join(lines).strip() + "\n"

    def _render_item(self, item, lang: str, source_lang: str = "zh") -> str | None:
        """Render a single Item as a Markdown line/block."""
        if item.type == "bullet":
            content = self._maybe_translate(item.content, lang, source_lang)
            return f"- {content}"

        if item.type in ("entry", "project"):
            return self._render_entry(item, lang, source_lang)

        if item.type == "open_source":
            return self._render_open_source(item, lang, source_lang)

        if item.type == "table":
            return self._render_table(item.content, lang, source_lang)

        # Code blocks are NEVER translated and keep their fenced syntax
        # verbatim — translating them would corrupt the code/config content.
        if item.type == "code":
            fence = "```"
            lang_tag = item.language or ""
            return f"{fence}{lang_tag}\n{item.content}\n{fence}"

        # text / fallback
        if item.content:
            return self._maybe_translate(item.content, lang, source_lang)

        return None

    def _render_table(self, table_md: str, lang: str, source_lang: str = "zh") -> str:
        """Render a GFM table, translating each cell individually.

        Looking up each cell (rather than the whole block) matches how the
        translation cache is populated (per cell) and keeps the pipe/delimiter
        structure intact even when individual cell translations are missing.
        """
        if lang == source_lang or not self._translator:
            return table_md
        lines: list[str] = []
        for row in table_md.split("\n"):
            row = row.strip()
            if not row.startswith("|"):
                lines.append(row)
                continue
            if row.startswith("|---") or row.startswith("|:--"):
                lines.append(row)  # delimiter row — nothing to translate
                continue
            parts = []
            for cell in row.strip("|").split("|"):
                parts.append(self._maybe_translate(cell.strip(), lang, source_lang))
            lines.append("| " + " | ".join(parts) + " |")
        return "\n".join(lines)

    def _render_entry(self, item, lang: str, source_lang: str = "zh") -> str:
        """Render a work experience or education entry."""
        parts = []
        period = self._maybe_translate(item.period, lang, source_lang) if item.period else ""
        if period:
            parts.append(f"**{period}")

        name = self._maybe_translate(item.title, lang, source_lang) if item.title else ""
        subtitle = self._maybe_translate(item.subtitle, lang, source_lang) if item.subtitle else ""

        if item.type == "entry" and subtitle:
            parts.append(f"{name}（{subtitle}）")
        else:
            parts.append(name)

        parts[-1] = f"{parts[-1]}**"
        line = " ".join(parts)

        # Description content (indented below)
        desc = self._maybe_translate(item.content, lang, source_lang) if item.content else ""
        if desc:
            return f"{line}\n  {desc}"
        return line

    def _render_open_source(self, item, lang: str, source_lang: str = "zh") -> str:
        title = self._maybe_translate(item.title, lang, source_lang) if item.title else ""
        lines = []
        desc = self._maybe_translate(item.content, lang, source_lang) if item.content else ""
        if desc:
            lines.append(f"- **{title}**：{desc}" if lang == "zh" else f"- **{title}**: {desc}")
        if item.features:
            for feat in item.features:
                lines.append(f"  - {self._maybe_translate(feat, lang, source_lang)}")
        if item.url:
            url = self._maybe_translate(item.url, lang, source_lang)
            lines.append(f"  开源地址：{url}" if lang == "zh" else f"  Open source: {url}")
        if item.tags:
            prefix = "涉及技术：" if lang == "zh" else "Tech Stack: "
            translated_tags = [self._maybe_translate(t, lang, source_lang) for t in item.tags]
            lines.append(f"  **{prefix}**{'、'.join(translated_tags)}")
        return "\n".join(lines)

    def _maybe_translate(self, text: str, lang: str, source_lang: str = "zh") -> str:
        if lang == source_lang or not self._translator:
            return text
        cached = self._translator.lookup(text, lang)
        if cached:
            return cached
        return text  # fallback to original
