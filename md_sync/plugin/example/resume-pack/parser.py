"""ParserPlugin implementation for the resume template format.

This parser understands the resume.md format defined in template.md
and converts it into the universal Document model.

Format supported:
  # Name — Title
  Contact info
  ---
  ## Section Title
  - bullet item
  **period Company（Role）**
    description
  **涉及技术：** tag1、tag2
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from md_sync.plugin.interface import ParserPlugin, PluginManifest, PLUGIN_TYPE_PACK
from md_sync.core.document import Document, Item, Metric, Section


# ── Regex patterns ──────────────────────────────────────────────────────────

_SECTION_TITLE_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_BULLET_RE = re.compile(r"^-\s+(.+)$")
_BOLD_ITEM_RE = re.compile(r"^(?:\s*-\s+)?\*\*(.+?)\*\*")
_DATE_PERIOD_RE = re.compile(
    r"(\d{4}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}|至今|Present)"
)
_TECH_TAG_RE = re.compile(r"^\*\*涉及技术[：:]\*\*\s*(.+)", re.IGNORECASE)
_METRIC_RE = re.compile(
    r"(\d+[Kk]?\s*[+%]?|"
    r"\d+倍|"
    r"提升\d+%[+]?|"
    r"降低\d+%[+]?|"
    r"减少\d+%[+]?|"
    r"P\d+[<>]\d+ms|"
    r"\d+\.\d+%[+]?)"
)

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


class MyResumeParser(ParserPlugin):
    """Parser for the resume template.md format."""

    def __init__(self):
        self._manifest = PluginManifest(
            name="resume-pack",
            version="1.0",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="my-resume",
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """Auto-detect if this text matches the resume template format.

        Heuristics:
        - First line starts with # and contains — or 的
        - Contains ## 工作经历 or ## 教育经历
        - Has --- separator
        """
        has_header = bool(re.match(r"^# .+[—-]", text))
        has_resume_sections = any(
            sec in text for sec in [
                "## 工作经历", "## 教育经历", "## 项目经历",
                "## Work Experience", "## Education",
            ]
        )
        has_separator = "---" in text
        # Require at least 2 out of 3 heuristics
        score = sum([has_header, has_resume_sections, has_separator])
        return score >= 2

    def parse(self, text: str) -> Document:
        """Parse resume markdown text into a Document."""
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

            while i < n and not lines[i].strip():
                i += 1
            contact_lines = []
            while i < n and lines[i].strip() and not lines[i].startswith("#"):
                contact_lines.append(lines[i].strip())
                i += 1
            if contact_lines:
                doc.meta_lines = contact_lines
                self._parse_contacts(doc, " ".join(contact_lines))

            while i < n and lines[i].strip().startswith("---"):
                i += 1

        # ── Sections ────────────────────────────────────────────────
        current_section: Optional[Section] = None

        while i < n:
            line = lines[i]

            # Section heading
            m = _SECTION_TITLE_RE.match(line)
            if m:
                if current_section:
                    doc.sections.append(current_section)
                level = len(m.group(1))
                title = m.group(2).strip()
                sec_id = SECTION_IDS.get(title, self._slugify(title))
                current_section = Section(id=sec_id, title=title, level=level)
                i += 1
                continue

            if not line.strip():
                i += 1
                continue

            target = current_section
            if target is not None:
                self._parse_content_line(line, target)
            i += 1

        if current_section:
            doc.sections.append(current_section)

        # Post-process
        self._merge_continuations(doc)
        self._extract_metrics(doc)
        return doc

    def _parse_content_line(self, line: str, section: Section) -> None:
        """Parse one content line and add to the section."""
        stripped = line.strip()
        if not stripped:
            return

        # **涉及技术：** → tech tags
        tech_m = _TECH_TAG_RE.match(stripped)
        if tech_m and section.items:
            raw = tech_m.group(1).strip()
            last = section.items[-1]
            for t in re.split(r"[、,，/]", raw):
                t = t.strip()
                if t and t not in last.tags:
                    last.tags.append(t)
            if section.id == "project_experience" and last.content:
                last.content += " 涉及技术：" + raw
            return

        # **bold** item → structured entry
        bm = _BOLD_ITEM_RE.match(stripped)
        if bm:
            inner = bm.group(1).strip()
            remaining = stripped[bm.end():].strip()
            self._add_bold_item(section, inner, remaining)
            return

        # Bullet
        bm2 = _BULLET_RE.match(stripped)
        if bm2:
            section.items.append(Item(type="bullet", content=bm2.group(1).strip()))
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
    def _parse_contacts(doc: Document, line: str) -> None:
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

    @staticmethod
    def _split_role(rest: str):
        role = None
        m = re.search(r"[（(](.+?)[）) ]*\s*$", rest)
        if m:
            role = m.group(1).strip()
            rest = rest[:m.start()].strip()
        return role, rest

    @staticmethod
    def _split_edu(rest: str):
        tags = re.findall(r"[（(](.+?)[）)]", rest)
        school = re.sub(r"[（(].+?[）)]", "", rest).strip()
        major = " · ".join(tags) if tags else ""
        return school, major

    def _merge_continuations(self, doc: Document) -> None:
        """Merge text continuations into preceding items."""
        for section in doc.sections:
            merged: list[Item] = []
            for item in section.items:
                if item.type == "separator":
                    continue
                if item.type == "text" and merged:
                    sep = "\n" if merged[-1].content else ""
                    merged[-1].content += sep + item.content
                else:
                    merged.append(item)
            section.items = merged

    def _extract_metrics(self, doc: Document) -> None:
        """Extract quantified metrics from item content."""
        for section in doc.sections:
            for item in section.items:
                if item.type in ("bullet", "entry", "project"):
                    found = _METRIC_RE.findall(item.content)
                    item.metrics = [Metric(v) for v in found]

    @staticmethod
    def _slugify(title: str) -> str:
        slug = title.lower().strip()
        slug = re.sub(r"[^\w\s]", "", slug)
        slug = re.sub(r"\s+", "_", slug)
        return slug or "unknown"
