"""Parse Markdown source files into structured Document objects.

Supports multiple schemas via the SchemaStrategy pattern.
Currently implements: ``resume`` schema.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .document import Document, Item, Metric, Section


# ── Helpers ─────────────────────────────────────────────────────────────────


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


# ── Parser ──────────────────────────────────────────────────────────────────


class MdParser:
    """Parse a Markdown resume file into a Document."""

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
        if schema == "resume":
            return self._parse_resume(text)
        raise ValueError(f"Unknown schema: {schema}")

    # ── Resume parser ───────────────────────────────────────────────────

    def _parse_resume(self, text: str) -> Document:
        doc = Document()
        lines = text.split("\n")
        i = 0
        n = len(lines)

        # ── Header ──────────────────────────────────────────────────────
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

            # Skip blank lines after header
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

            # Skip separator ---
            while i < n and lines[i].strip().startswith("---"):
                i += 1

        # ── Sections ────────────────────────────────────────────────────
        current_section: Optional[Section] = None

        while i < n:
            line = lines[i]

            # Section heading
            m = _SECTION_TITLE_RE.match(line)
            if m:
                # Save previous section
                if current_section:
                    doc.sections.append(current_section)

                level = len(m.group(1))
                title = m.group(2).strip()
                sec_id = SECTION_IDS.get(title, self._slugify(title))
                current_section = Section(id=sec_id, title=title, level=level)
                i += 1
                continue

            # Blank line between sections — just skip
            if not line.strip():
                i += 1
                continue

            # Non-section content goes to current section as text
            if current_section is not None:
                self._parse_content_line(line, current_section)
            i += 1

        # Last section
        if current_section:
            doc.sections.append(current_section)

        # Post-process: merge continuation lines
        self._merge_continuations(doc)
        # Extract badges from sections
        self._extract_badges(doc)
        return doc

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
        """Parse one content line and add to the current section."""
        stripped = line.strip()
        if not stripped:
            return

        # Bold item: optional "- " then **text**
        bm = _BOLD_ITEM_RE.match(stripped)
        if bm:
            inner = bm.group(1).strip()
            remaining = stripped[bm.end():].strip()
            # Meta line "**涉及技术：...**" / "**Tech Stack: ...**" → tech tags
            # for the current (previous) item instead of a standalone item.
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
                    # project_experience reads better with an inline "涉及技术" note
                    if section.id == "project_experience" and last.content:
                        last.content += " 涉及技术：" + raw
                return
            self._add_bold_item(section, inner, remaining)
            return

        # Regular bullet
        m = _BULLET_RE.match(stripped)
        if m:
            section.items.append(Item(type="bullet", content=m.group(1).strip()))
            return

        # Indented continuation → append to the nearest real item
        # (skip over any separators that may sit between).
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

        # Plain text (not a bullet / bold)
        section.items.append(Item(type="text", content=stripped))

    def _add_bold_item(self, section: Section, inner: str, remaining: str = "") -> None:
        """Parse a **bold** item, classifying it by the current section type.

        The item text may start with a date range (``YYYY.MM-YYYY.MM``); the rest
        is the title, with an optional role in ``（…）`` or as trailing words.
        """
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

    def _merge_continuations(self, doc: Document) -> None:
        """Merge continuation lines (type=text) into the preceding real item."""
        for section in doc.sections:
            merged: list[Item] = []
            for item in section.items:
                if item.type == "separator":
                    continue
                if item.type == "text" and merged:
                    # It's a continuation — append to the last item's content
                    if item.content:
                        sep = "\n" if merged[-1].content else ""
                        merged[-1].content += sep + item.content
                else:
                    merged.append(item)
            section.items = merged
            self._extract_metrics(section)
            self._extract_tech_tags(section)

    def _extract_metrics(self, section: Section) -> None:
        """Extract quantified metrics from item content and store them."""
        for item in section.items:
            if item.type in ("bullet", "entry", "project"):
                found = _METRIC_RE.findall(item.content)
                item.metrics = [Metric(v) for v in found]

    def _extract_badges(self, doc: Document) -> None:
        """Extract badge info from section titles (e.g. "20年" from "综合素质及能力")."""
        # Hard-coded badge mappings for resume sections
        badge_map = {
            "professional_summary": "20年" if doc.source_lang == "zh" else "20 Yrs",
        }
        for section in doc.sections:
            if section.id in badge_map:
                section.badge = badge_map[section.id]

    def _extract_tech_tags(self, section: Section) -> None:
        """Extract tech tags from items that mention them."""
        for item in section.items:
            # Look for **涉及技术：** or **Tech Stack:** lines
            tech_m = re.search(r"\*\*涉及技术[：:]\*\*\s*(.+)", item.content)
            if not tech_m:
                tech_m = re.search(r"\*\*Tech Stack[：:]\*\*\s*(.+)", item.content)
            if tech_m:
                raw = tech_m.group(1)
                item.tags = [t.strip() for t in re.split(r"[、,，/]", raw) if t.strip()]

    @staticmethod
    def _slugify(title: str) -> str:
        """Convert a Chinese section title to a machine-readable id."""
        slug = title.lower().strip()
        slug = re.sub(r"[^\w\s]", "", slug)
        slug = re.sub(r"\s+", "_", slug)
        return slug or "unknown"


# ── Convenience ─────────────────────────────────────────────────────────────


def parse_resume(path: Path | str) -> Document:
    """Quick entry point: parse a resume MD file."""
    return MdParser().parse_file(path, schema="resume")
