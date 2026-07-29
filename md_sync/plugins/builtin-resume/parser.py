"""Built-in ResumeParser — parses documents written in the resume template.md format.

This parser is the ParserPlugin for the "resume" schema. It understands the
resume Markdown format with sections like 综合素质及能力, 教育经历, 工作经历,
项目经历, 开源项目 and their English equivalents.

Reuses shared regex patterns and helpers from md_sync.core.parser.
"""

from __future__ import annotations

import re

from md_sync.core.document import Document, Item, Metric, Section
from md_sync.core.parser import (
    _BOLD_ITEM_RE,
    _BULLET_RE,
    _DATE_PERIOD_RE,
    _METRIC_RE,
    _SECTION_TITLE_RE,
    SECTION_IDS,
    _slugify,
)
from md_sync.plugin.interface import PLUGIN_TYPE_PACK, ParserPlugin, PluginManifest


class ResumeParser(ParserPlugin):
    """Parse a resume Markdown document into a Document.

    Supports the full resume template format:
    - ``# Name — Title`` header
    - Contact info with ``|`` separators
    - ``---`` separator
    - Resume section headings (Chinese & English)
    - ``**bold**`` structured entries (experience, education, projects)
    - ``- bullet`` items
    - ``**涉及技术：**`` tech tag extraction
    - ``YYYY.MM-YYYY.MM`` date periods
    - Quantified metric extraction
    """

    def __init__(self):
        self._manifest = PluginManifest(
            name="builtin-resume",
            version="1.0",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="resume",
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """Auto-detect if text is a resume document.

        Returns True if the text has at least 2 of:
        - ``# Name — Title`` header with em-dash
        - ``---`` separator
        - Resume-specific section headings
        """
        has_header = bool(re.match(r"^# .+[—-]", text))
        has_separator = "---" in text
        has_resume_section = any(
            sec in text for sec in [
                "## 综合素质及能力",
                "## 工作经历",
                "## 教育经历",
                "## 项目经历",
                "## 开源项目",
                "## Work Experience",
                "## Education",
                "## Professional Summary",
            ]
        )
        score = sum([has_header, has_separator, has_resume_section])
        return score >= 2

    def parse(self, text: str) -> Document:
        """Parse resume Markdown text into a Document."""
        doc = Document()
        lines = text.split("\n")
        i = 0
        n = len(lines)

        # ── Header ──────────────────────────────────────────────────
        if i < n and lines[i].startswith("# "):
            header_line = lines[i].lstrip("# ").strip()
            if "—" in header_line:
                name, rest = header_line.split("—", 1)
            else:
                name = header_line
                rest = ""
            doc.name = name.strip()
            doc.title = rest.strip()
            i += 1

            # Skip blank lines
            while i < n and not lines[i].strip():
                i += 1
            # Contact line(s)
            contact_lines = []
            while i < n and lines[i].strip() and not lines[i].startswith("#"):
                contact_lines.append(lines[i].strip())
                i += 1
            if contact_lines:
                doc.meta_lines = contact_lines
                self._parse_contacts(doc, " ".join(contact_lines))

            # Skip --- separator
            while i < n and lines[i].strip().startswith("---"):
                i += 1

        # ── Sections ────────────────────────────────────────────────
        current_section: Section | None = None
        body_section = Section(id="body", title="", level=1)

        while i < n:
            line = lines[i]

            m = _SECTION_TITLE_RE.match(line)
            if m:
                if current_section:
                    doc.sections.append(current_section)
                level = len(m.group(1))
                title = m.group(2).strip()
                sec_id = SECTION_IDS.get(title, _slugify(title))
                current_section = Section(id=sec_id, title=title, level=level)
                i += 1
                continue

            if not line.strip():
                if current_section is None:
                    body_section.items.append(Item(type="separator"))
                i += 1
                continue

            target = current_section if current_section is not None else body_section
            self._parse_content_line(line, target)
            i += 1

        if current_section:
            doc.sections.append(current_section)

        if not doc.sections and body_section.items:
            doc.sections.append(body_section)

        # Post-process
        self._merge_continuations(doc)
        self._extract_badges(doc)
        return doc

    # ── Content parsing ────────────────────────────────────────────

    def _parse_contacts(self, doc: Document, line: str) -> None:
        """Extract phone, email, github from contact line."""
        parts = re.split(r"\s*[|│]\s*", line)
        for p in parts:
            p = p.strip()
            if "@" in p:
                doc.contacts["email"] = p
            elif re.match(r"^1\d{10}$", p) or re.match(r"^\+86\s*1\d{10}$", p):
                doc.contacts["phone"] = p
            elif "github" in p.lower():
                doc.contacts["github"] = p
            elif "硕士" in p or "M.Eng" in p or "学士" in p or "B.Eng" in p:
                doc.contacts["education"] = p
            elif "到岗" in p or "Available" in p:
                doc.contacts["availability"] = p

    def _parse_content_line(self, line: str, section: Section) -> None:
        """Parse one content line and add to the section."""
        stripped = line.strip()
        if not stripped:
            return

        # Bold item
        bm = _BOLD_ITEM_RE.match(stripped)
        if bm:
            inner = bm.group(1).strip()
            remaining = stripped[bm.end():].strip()
            tech = re.match(r"^涉及技术[：:]?\s*(.*)$", inner) or \
                   re.match(r"^Tech Stack[：:]?\s*(.*)$", inner)
            if tech and section.items:
                raw = (tech.group(1) or "").strip()
                if remaining:
                    raw = (raw + " " + remaining).strip() if raw else remaining
                if raw:
                    last = section.items[-1]
                    for t in re.split(r"[、,，/]", raw):
                        t = t.strip()
                        if t and t not in last.tags:
                            last.tags.append(t)
                    if section.id in ("project_experience",) and last.content:
                        last.content += " 涉及技术：" + raw
                return
            self._add_bold_item(section, inner, remaining)
            return

        # Bullet
        m = _BULLET_RE.match(stripped)
        if m:
            section.items.append(Item(type="bullet", content=m.group(1).strip()))
            return

        # Indented continuation
        if line.startswith("  ") or line.startswith("\t"):
            for it in reversed(section.items):
                if it.type != "separator":
                    if it.content:
                        it.content += " " + stripped
                    else:
                        it.content = stripped
                    break
            else:
                section.items.append(Item(type="text", content=stripped))
            return

        # Plain text
        section.items.append(Item(type="text", content=stripped))

    def _add_bold_item(self, section: Section, inner: str, remaining: str = "") -> None:
        """Parse a **bold** item into a structured entry."""
        period = None
        rest = inner
        pm = _DATE_PERIOD_RE.match(inner)
        if pm:
            period = f"{pm.group(1)}-{pm.group(2)}"
            rest = inner[pm.end():].strip()

        sec_id = section.id
        if sec_id == "work_experience":
            role, title = self._split_role(rest)
            section.items.append(Item(
                type="entry", period=period, title=title,
                subtitle=role, content=remaining,
            ))
        elif sec_id == "education":
            school, major = self._split_edu(rest)
            section.items.append(Item(
                type="entry", period=period, title=school, subtitle=major,
            ))
        elif sec_id == "project_experience":
            role, title = self._split_role(rest)
            section.items.append(Item(
                type="project", period=period, title=title,
                role=role, content=remaining,
            ))
        elif sec_id == "open_source":
            section.items.append(Item(type="open_source", title=inner, content=remaining))
        else:
            content = inner if not remaining else f"{inner} {remaining}"
            section.items.append(Item(type="text", content=content))

    @staticmethod
    def _split_role(rest: str):
        """Split ``Company（role）`` / ``Project 架构顾问`` into (role, title)."""
        role = None
        m = re.search(r"[（(](.+?)[）)]\s*$", rest)
        if m:
            role = m.group(1).strip()
            rest = rest[:m.start()].strip()
        else:
            mm = re.search(
                r"\s+(架构顾问|技术负责人|技术主管|系统工程师|项目经理|"
                r"架构师|负责人|主管|技术经理)$", rest)
            if mm:
                role = mm.group(1).strip()
                rest = rest[:mm.start()].strip()
        return role, rest

    @staticmethod
    def _split_edu(rest: str):
        """Split ``School（985）（major）`` into (school, combined subtitle)."""
        tags = re.findall(r"[（(](.+?)[）)]", rest)
        school = re.sub(r"[（(].+?[）)]", "", rest).strip()
        major = " · ".join(tags) if tags else ""
        return school, major

    # ── Post-processing ────────────────────────────────────────────

    def _merge_continuations(self, doc: Document) -> None:
        """Merge continuation lines into preceding items."""
        for section in doc.sections:
            merged: list[Item] = []
            prev_sep = False
            for item in section.items:
                if item.type == "separator":
                    prev_sep = True
                    continue
                if item.type == "text" and merged and not prev_sep:
                    if item.content:
                        sep = "\n" if merged[-1].content else ""
                        merged[-1].content += sep + item.content
                else:
                    merged.append(item)
                prev_sep = False
            section.items = merged
            self._extract_metrics(section)
            self._extract_tech_tags(section)

    def _extract_metrics(self, section: Section) -> None:
        """Extract quantified metrics from item content."""
        for item in section.items:
            if item.type in ("bullet", "entry", "project"):
                found = _METRIC_RE.findall(item.content)
                item.metrics = [Metric(v) for v in found]

    def _extract_badges(self, doc: Document) -> None:
        """Extract badge info from section titles."""
        badge_map = {
            "professional_summary": "20年" if doc.source_lang == "zh" else "20 Yrs",
        }
        for section in doc.sections:
            if section.id in badge_map:
                section.badge = badge_map[section.id]

    def _extract_tech_tags(self, section: Section) -> None:
        """Extract tech tags from items that mention them."""
        for item in section.items:
            tech_m = re.search(r"\*\*涉及技术[：:]\*\*\s*(.+)", item.content)
            if not tech_m:
                tech_m = re.search(r"\*\*Tech Stack[：:]\*\*\s*(.+)", item.content)
            if tech_m:
                raw = tech_m.group(1)
                item.tags = [t.strip() for t in re.split(r"[、,，/]", raw) if t.strip()]
