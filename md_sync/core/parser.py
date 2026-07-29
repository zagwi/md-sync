"""Parse Markdown source files into structured Document objects.

Parser dispatch chain (in order):
  1. Plugin parser (by schema name, e.g. "resume" or "my-resume")
  2. Plugin parser (by auto-detection via ``detect()``)
  3. Generic Markdown fallback (any standard MD document — always succeeds)

The built-in resume parser has been moved to ``md_sync/plugins/resume/``
as a proper ``ParserPlugin``, auto-discovered by the plugin registry.
"""
from __future__ import annotations

import re
from pathlib import Path

from .document import Document, Item, Section

# ── Helpers (shared with plugin packs) ───────────────────────────────────────


_SECTION_TITLE_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_BULLET_RE = re.compile(r"^-\s+(.+)$")
# Bold item: an optional leading "- " then **...**. Bare **bold** lines
# (without "-") are very common in resume MD, so we accept both forms.
_BOLD_ITEM_RE = re.compile(r"^(?:\s*-\s+)?\*\*(.+?)\*\*")
_METRIC_RE = re.compile(
    r"(\d+[Kk]?\s*[+%]?|"
    r"\d+倍|"
    r"提升\d+%[+]?|"
    r"降低\d+%[+]?|"
    r"减少\d+%[+]?|"
    r"P\d+[<>]\d+ms|"
    r"\d+\.\d+%[+]?)"
)

_DATE_PERIOD_RE = re.compile(r"(\d{4}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}|至今|Present)")


# ── Section ID mapping ──────────────────────────────────────────────────────

SECTION_IDS: dict[str, str] = {
    "综合素质及能力": "professional_summary",
    "教育经历": "education",
    "工作经历": "work_experience",
    "项目经历": "project_experience",
    "开源项目": "open_source",
    "Professional Summary": "professional_summary",
    "Education": "education",
    "Work Experience": "work_experience",
    "Project Experience": "project_experience",
    "Open Source": "open_source",
}


def _slugify(title: str) -> str:
    """Convert a Chinese section title to a machine-readable id."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    return slug or "unknown"


# ── Parser ──────────────────────────────────────────────────────────────────


class MdParser:
    """Parse a Markdown file into a Document.

    Dispatches to the first matching parser in this order:
      1. Plugin parser (by explicit schema name)
      2. Plugin parser (by auto-detection via ``detect()``)
      3. Generic Markdown fallback (always succeeds)

    The built-in resume parser is provided as a plugin pack at
    ``md_sync/plugins/resume/``, auto-discovered by ``PluginRegistry``,
    and registered under the ``resume`` schema name.
    """

    def __init__(self, plugin_registry=None):
        """
        Args:
            plugin_registry: Optional ``PluginRegistry`` to resolve plugin parsers.
        """
        self._plugin_registry = plugin_registry

    def parse_file(self, path: Path | str, schema: str = "resume") -> Document:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        doc = self.parse(text, schema=schema)
        doc.source_path = str(path)
        doc.source_raw = text
        # Detect language from file content
        zh_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        doc.source_lang = "zh" if zh_chars > 100 else "en"
        return doc

    def parse(self, text: str, schema: str = "resume") -> Document:
        """Parse Markdown text into a Document.

        Dispatch chain (first match wins):
          1. Plugin parser matching ``schema`` name
          2. Plugin parser auto-detected via ``detect()``
          3. Generic Markdown fallback (always succeeds)
        """
        # 1. Plugin parser by schema name
        if self._plugin_registry:
            parser = self._plugin_registry.get_parser(schema)
            if parser:
                return parser.parse(text)

        # 2. Plugin parser by auto-detection (when no explicit schema match)
        if self._plugin_registry:
            parser = self._plugin_registry.find_parser(text)
            if parser:
                return parser.parse(text)

        # 3. Generic Markdown fallback (always works)
        return self._parse_generic(text)


    # ── Generic Markdown fallback ───────────────────────────────────────

    def _parse_generic(self, text: str) -> Document:
        """Parse any standard Markdown document into a Document.

        This is the universal fallback parser. It handles:
        - Documents with standard Markdown headings (``#``, ``##``)
        - Bullet lists (``- item``)
        - Plain paragraphs
        - Fenced code blocks (`````...`````)
        - Markdown tables (consecutive ``|...|`` lines)
        - ``---`` thematic breaks as section separators

        Unlike the resume parser, it does NOT understand:
        - Bold items (``**text**``) as structured entries
        - Date periods (``2024.01-2024.06``)
        - Tech tags (``涉及技术：``)
        - Contact info parsing
        """
        doc = Document()
        lines = text.split("\n")
        i = 0
        n = len(lines)

        # ── Header ──────────────────────────────────────────────────
        if i < n and lines[i].startswith("# "):
            doc.name = lines[i].lstrip("# ").strip()
            i += 1
            # Skip blank + separator
            while i < n and (not lines[i].strip() or lines[i].strip().startswith("---")):
                i += 1
        else:
            # No header — auto-detect first meaningful line
            doc.name = Path(doc.source_path).stem if doc.source_path else "Untitled"

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
                sec_id = _slugify(title)
                current_section = Section(id=sec_id, title=title, level=level)
                i += 1
                continue

            # Thematic break → section separator
            if line.strip().startswith("---"):
                i += 1
                continue

            # Empty line
            if not line.strip():
                i += 1
                continue

            target = current_section if current_section is not None else Section(id="body", title="", level=1)
            if current_section is None:
                current_section = target

            # Fenced code block
            if line.strip().startswith("```"):
                fence_lang = line.strip()[3:].strip() or None
                code_lines = []
                i += 1
                while i < n and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing fence
                target.items.append(Item(
                    type="code", content="\n".join(code_lines),
                    language=fence_lang))
                continue

            # Table: consecutive lines starting with |
            if line.strip().startswith("|"):
                table_lines = []
                while i < n and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                target.items.append(Item(type="table", content="\n".join(table_lines)))
                continue

            # Bullet
            bm = _BULLET_RE.match(line.strip())
            if bm:
                target.items.append(Item(type="bullet", content=bm.group(1).strip()))
            else:
                target.items.append(Item(type="text", content=line.strip()))
            i += 1

        if current_section:
            doc.sections.append(current_section)

        if not doc.sections:
            body = Section(id="body", title="", level=1)
            body.items.append(Item(type="text", content=text.strip()))
            doc.sections.append(body)

        return doc


# ── Convenience ─────────────────────────────────────────────────────────────


def parse_resume(path: Path | str) -> Document:
    """Quick entry point: parse a resume MD file.

    Uses a PluginRegistry to discover the built-in resume parser.
    """
    from md_sync.plugin.registry import PluginRegistry
    reg = PluginRegistry()
    return MdParser(plugin_registry=reg).parse_file(path, schema="resume")
