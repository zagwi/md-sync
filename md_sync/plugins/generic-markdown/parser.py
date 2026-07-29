"""Generic Markdown Parser — handles any standard Markdown document.

This parser understands plain Markdown with:
- ``# Title`` header
- ``## Section`` / ``### Sub-section`` headings
- ``- bullet`` lists
- Plain paragraphs and inline formatting (bold, italic, code)

Unlike the resume parser, it does NOT understand:
- Bold items (``**text**``) as structured entries
- Date periods (``2024.01-2024.06``)
- Tech tags (``涉及技术：``)
- Contact info parsing
"""

from __future__ import annotations

import re

from md_sync.core.document import Document, Item, Section
from md_sync.core.parser import (
    _BULLET_RE,
    _SECTION_TITLE_RE,
)
from md_sync.plugin.interface import PLUGIN_TYPE_PACK, ParserPlugin, PluginManifest


class GenericMarkdownParser(ParserPlugin):
    """Parse any standard Markdown document into a Document.

    This is the universal parser — it handles arbitrary Markdown content
    without requiring a specific template format. It extracts:
    - Document title from ``# Title``
    - Sections from ``##`` / ``###`` headings
    - Content from bullet lists and plain paragraphs

    Always returns ``detect() == True`` so it serves as the universal
    fallback parser for any unrecognized document format.
    """

    def __init__(self):
        self._manifest = PluginManifest(
            name="generic-markdown",
            version="1.0",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="markdown",
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """Generic parser always returns True — it handles any text."""
        return True

    def parse(self, text: str) -> Document:
        """Parse plain Markdown text into a Document.

        Args:
            text: Raw Markdown text.

        Returns:
            A Document with sections and items extracted from headings,
            lists, and paragraphs.
        """
        doc = Document()
        lines = text.split("\n")
        i = 0
        n = len(lines)

        # ── Header ──────────────────────────────────────────────────
        if i < n and lines[i].startswith("# "):
            doc.name = lines[i].lstrip("# ").strip()
            i += 1
            # Skip blank lines and separators after header
            while i < n and (not lines[i].strip() or lines[i].strip().startswith("---")):
                i += 1
        else:
            # No H1 header — use first meaningful line or fallback name
            while i < n and not lines[i].strip():
                i += 1
            if i < n and not lines[i].startswith("##"):
                doc.name = lines[i].strip()[:60]
            else:
                doc.name = "Untitled"

        # ── Sections ────────────────────────────────────────────────
        current_section: Section | None = None

        while i < n:
            line = lines[i]

            # Section heading
            m = _SECTION_TITLE_RE.match(line)
            if m:
                if current_section:
                    doc.sections.append(current_section)
                level = len(m.group(1))
                title = m.group(2).strip()
                sec_id = title.lower().strip().replace(" ", "_").replace("-", "_")
                current_section = Section(id=sec_id, title=title, level=level)
                i += 1
                continue

            # Empty line or separator
            if not line.strip():
                i += 1
                continue

            # Horizontal rule (standalone ---, ***, ___)
            if re.match(r"^(---|\*\*\*|___)\s*$", line.strip()):
                target = current_section or Section(id="body", title="", level=1)
                if current_section is None:
                    current_section = target
                target.items.append(Item(type="hr"))
                i += 1
                continue

            # Fenced code block
            if line.strip().startswith("```") or line.strip().startswith("~~~"):
                lang = line.strip().lstrip("`~").strip()
                code_lines = []
                i += 1
                while i < n:
                    stripped = lines[i].strip()
                    if stripped.startswith("```") or stripped.startswith("~~~"):
                        i += 1
                        break
                    code_lines.append(lines[i])
                    i += 1
                target = current_section or Section(id="body", title="", level=1)
                if current_section is None:
                    current_section = target
                target.items.append(Item(type="code", content="\n".join(code_lines), language=lang))
                continue

            # Table row (detect pipe-separated content)
            if line.strip().startswith("|") and "|" in line.strip()[1:]:
                table_lines = [line.strip()]
                i += 1
                while i < n and lines[i].strip().startswith("|") and "|" in lines[i].strip()[1:]:
                    table_lines.append(lines[i].strip())
                    i += 1
                target = current_section or Section(id="body", title="", level=1)
                if current_section is None:
                    current_section = target
                target.items.append(Item(type="table", content="\n".join(table_lines)))
                continue

            # Ensure we have a target section
            target = current_section
            if target is None:
                target = Section(id="body", title="", level=1)
                current_section = target

            # Bullet
            bm = _BULLET_RE.match(line.strip())
            if bm:
                target.items.append(Item(type="bullet", content=bm.group(1).strip()))
            else:
                target.items.append(Item(type="text", content=line.strip()))
            i += 1

        if current_section:
            doc.sections.append(current_section)

        # If no sections created, put everything in a body section
        if not doc.sections:
            body = Section(id="body", title="", level=1)
            body.items.append(Item(type="text", content=text.strip()))
            doc.sections.append(body)

        return doc
