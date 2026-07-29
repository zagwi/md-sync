"""Resume parser — standard Markdown engine + resume post-processing.

This parser is the ParserPlugin for the "resume" schema. It reuses the shared
CommonMark/GFM engine (``md_sync.core.md_engine.parse_document``) for the
low-level Markdown mechanics — headings, lists, code fences, tables, thematic
breaks, inline escaping — and then applies a *resume-specific* post-processing
pass (``_enrich_resume``) that recognises the resume conventions
(``**bold**`` entries, ``YYYY.MM-YYYY.MM`` periods, ``涉及技术：`` tech tags,
contact/name header) and turns the generic ``Document`` into the structured
``entry`` / ``project`` / ``open_source`` items the resume themes expect.

This keeps the whole pipeline on a single, professional Markdown engine while
preserving the resume semantics as a thin semantic layer. Themes, translation
and multi-format exporters are untouched.
"""

from __future__ import annotations

import re

from md_sync.core.document import Document, Item, Metric, Section
from md_sync.core.md_engine import parse_document
from md_sync.core.parser import (
    _BOLD_ITEM_RE,
    _DATE_PERIOD_RE,
    _METRIC_RE,
    SECTION_IDS,
)
from md_sync.plugin.interface import PLUGIN_TYPE_PACK, ParserPlugin, PluginManifest


def _detect_lang(text: str) -> str:
    """Heuristic: Chinese-heavy text is 'zh', otherwise 'en'."""
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    return "zh" if zh > 100 else "en"


def _enrich_resume(doc: Document) -> None:
    """Apply resume-specific structuring on top of the standard parse."""

    # ── Header: the leading level-1 heading is the name/title header ──
    hdr_idx = next((i for i, s in enumerate(doc.sections) if s.level == 1), None)
    if hdr_idx is not None:
        hsec = doc.sections[hdr_idx]
        title = hsec.title
        if "—" in title:
            doc.name, rest = title.split("—", 1)
            doc.title = rest.strip()
            doc.name = doc.name.strip()
        elif " - " in title:
            doc.name, rest = title.split(" - ", 1)
            doc.title = rest.strip()
            doc.name = doc.name.strip()
        else:
            doc.name = title.strip()
        contact_text = " ".join(
            it.content for it in hsec.items if it.type == "text"
        )
        if contact_text.strip():
            _parse_contacts(doc, contact_text)
        del doc.sections[hdr_idx]

    # ── Body sections ──
    for sec in doc.sections:
        sec.id = SECTION_IDS.get(sec.title.strip(), sec.id)
        new_items: list[Item] = []
        for it in sec.items:
            if it.type == "hr":
                continue
            if it.type == "md":
                # Nested-list fallback — split raw markdown into structured items
                _process_md_block(sec, it.content, new_items)
            elif it.type in ("code", "table"):
                new_items.append(it)
            elif it.type == "bullet":
                _process_bullet(sec, it.content, new_items)
            elif it.type == "text":
                _process_text(sec, it.content, new_items)
            else:
                new_items.append(it)
        sec.items = new_items
        _extract_metrics(sec)
        _extract_tech_tags(sec)

    _extract_badges(doc)


def _process_md_block(sec: Section, content: str, out: list[Item]) -> None:
    """Parse a ``type='md'`` block (nested-list fallback) into resume items.

    When markdown-it encounters a nested list (e.g. a top-level bullet that
    itself contains a sub-bullet like ``- **涉及技术：**``), the whole bullet
    list is collapsed into a single ``Item(type="md")`` with raw markdown
    source. This function splits the raw markdown back into individual
    top-level bullets and routes each through ``_add_bold_item`` so they
    get proper ``type='project'`` / ``type='entry'`` items with headers,
    periods, roles, and paragraph-separated content.

    Reference input format (user's actual markdown)::

        - **2023.08-2024.01 Project Name（Role）**
          Description line 1
          - **涉及技术：** TagA、TagB
        - **2019.11-2022.06 Another Project（Role）**
          Description
    """
    # Split on top-level bullet markers: ``- `` at column 0, but not
    # indented ``  - `` which indicates a sub-bullet.
    blocks: list[str] = []
    buf: list[str] = []
    for line in content.split("\n"):
        if re.match(r"^- ", line) and buf:
            blocks.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf))

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Strip the leading ``- `` bullet marker so the content looks
        # like a regular paragraph for ``_add_bold_item``.
        text = re.sub(r"^-\s+", "", block, count=1)

        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue

        first = lines[0]
        rest = lines[1:]
        bm = _BOLD_ITEM_RE.match(first)
        if bm:
            inner = bm.group(1).strip()
            trailing = first[bm.end():].strip()
            # Clean sub-bullet markers from continuation lines
            cleaned_rest: list[str] = []
            for ln in rest:
                ln = re.sub(r"^-\s+", "", ln)  # strip sub-bullet "- " prefix
                if ln.strip():
                    cleaned_rest.append(ln.strip())
            remaining = "\n\n".join(p for p in [trailing, *cleaned_rest] if p).strip()
            _add_bold_item(sec, inner, remaining, out)
        else:
            # Plain text block — keep as-is with paragraph breaks
            out.append(Item(type="text", content="\n\n".join(l for l in [first, *rest] if l.strip()).strip()))


def _process_text(sec: Section, content: str, out: list[Item]) -> None:
    """Turn a standard-parsed paragraph into a resume item.

    A paragraph whose first line is a ``**bold**`` entry becomes an
    ``entry`` / ``project`` / ``open_source``; any continuation lines are merged
    into the entry's ``content`` (preserving paragraph breaks with ``\n\n``
    so the ``rich`` filter can render them as distinct ``<p>`` tags).
    A plain paragraph stays a ``text`` item.
    """
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    if not lines:
        return
    first = lines[0]
    rest = lines[1:]
    bm = _BOLD_ITEM_RE.match(first)
    if bm:
        inner = bm.group(1).strip()
        trailing = first[bm.end():].strip()
        remaining = "\n\n".join(p for p in [trailing, *rest] if p).strip()
        _add_bold_item(sec, inner, remaining, out)
    else:
        out.append(Item(type="text", content="\n\n".join(lines)))


def _process_bullet(sec: Section, content: str, out: list[Item]) -> None:
    """Turn a standard-parsed bullet into a resume item.

    Open-source sections use ``- **Name**：desc`` bullets possibly followed by a
    ``**涉及技术：** …`` line; these become a single ``open_source`` item with
    tech tags.

    Work/project sections commonly use plain ``- 2023.01-2023.12 Company role``
    bullets (without ``**bold**``). These are parsed into ``entry`` / ``project``
    items so themes can render them with proper headers and spacing.
    """
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    if not lines:
        return
    first = lines[0]
    bm = _BOLD_ITEM_RE.match(first)

    if sec.id == "open_source" and bm:
        inner = bm.group(1).strip()
        remaining = first[bm.end():].strip()
        item = Item(type="open_source", title=inner, content=remaining)
        for ln in lines[1:]:
            tm = re.search(r"\*\*涉及技术[：:]\*\*\s*(.+)", ln) or re.search(
                r"\*\*Tech Stack[：:]\*\*\s*(.+)", ln
            )
            if tm:
                for t in re.split(r"[、,，/]", tm.group(1).strip()):
                    t = t.strip()
                    if t and t not in item.tags:
                        item.tags.append(t)
        out.append(item)
        return

    # Bold-formatted bullets in education/work/project: delegate to
    # _add_bold_item which already extracts period, title, and
    # role/subtitle for all three section types.
    if bm and sec.id in ("education", "work_experience", "project_experience"):
        inner = bm.group(1).strip()
        trailing = first[bm.end():].strip()
        remaining = " ".join([trailing, *lines[1:]]).strip()
        _add_bold_item(sec, inner, remaining, out)
        return

    # Work / project / education entries written as plain bullets: try to
    # extract period, title and role, then keep the rest as content.
    if sec.id in ("work_experience", "project_experience", "education"):
        pm = _DATE_PERIOD_RE.match(first)
        if pm:
            period = f"{pm.group(1)}-{pm.group(2)}"
            rest = first[pm.end():].strip()
            if rest:
                # The remainder may itself start with **bold** markup; strip it.
                bm2 = _BOLD_ITEM_RE.match(rest)
                if bm2:
                    rest = bm2.group(1).strip()
                trailing = "\n".join(lines[1:]).strip()
                if sec.id == "education":
                    school, major = _split_edu(rest)
                    out.append(
                        Item(
                            type="entry",
                            period=period,
                            title=school,
                            subtitle=major,
                            content=trailing,
                        )
                    )
                else:
                    # For single-line bullets such as
                    # ``- 2021.01-2022.06 Company role description...``,
                    # the role keyword sits in the middle of the line and the
                    # trailing text is the description.  Use the inline variant
                    # so the whole line does not become the title.
                    if trailing:
                        role, title = _split_role(rest)
                        content = trailing
                    else:
                        role, title, content = _split_role_inline(rest)
                    item_type = "entry" if sec.id == "work_experience" else "project"
                    out.append(
                        Item(
                            type=item_type,
                            period=period,
                            title=title,
                            role=role if sec.id == "project_experience" else None,
                            subtitle=role if sec.id == "work_experience" else None,
                            content=content,
                        )
                    )
                return

        # ── Plain bullet after a project/entry item: merge as new paragraph ──
        if out and out[-1].type in ("project", "entry"):
            if out[-1].content:
                out[-1].content += "\n\n"
            out[-1].content += content
            return

    out.append(Item(type="bullet", content=content))


def _add_bold_item(sec: Section, inner: str, remaining: str, out: list[Item]) -> None:
    """Parse a ``**bold**`` entry into a structured item appended to *out*."""
    period = None
    rest = inner
    pm = _DATE_PERIOD_RE.match(inner)
    if pm:
        period = f"{pm.group(1)}-{pm.group(2)}"
        rest = inner[pm.end():].strip()

    sec_id = sec.id
    if sec_id == "work_experience":
        role, title = _split_role(rest)
        out.append(
            Item(type="entry", period=period, title=title, subtitle=role, content=remaining)
        )
    elif sec_id == "education":
        school, major = _split_edu(rest)
        out.append(Item(type="entry", period=period, title=school, subtitle=major, content=remaining))
    elif sec_id == "project_experience":
        role, title = _split_role(rest)
        out.append(
            Item(
                type="project", period=period, title=title, role=role, content=remaining
            )
        )
    elif sec_id == "open_source":
        out.append(Item(type="open_source", title=inner, content=remaining))
    else:
        content = inner if not remaining else f"{inner} {remaining}"
        out.append(Item(type="text", content=content))


# Common job/role keywords used in Chinese resumes.  Kept in one place so both
# the end-of-line and inline splitters share the same dictionary.
_ROLE_KEYWORDS = (
    "架构顾问|技术负责人|技术主管|系统工程师|项目经理|"
    "后端架构师兼项目经理|架构师兼项目经理|后端架构师|前端架构师|"
    "中台架构师|高级架构师|资深架构师|解决方案架构师|系统架构师|"
    "架构师|负责人|主管|技术经理|后端开发|前端开发|开发工程师|"
    "高级工程师|资深工程师|工程师"
)


def _split_role(rest: str):
    """Split ``Company（role）`` / ``Project 架构顾问`` into (role, title)."""
    role = None
    m = re.search(r"[（(](.+?)[）)]\s*$", rest)
    if m:
        role = m.group(1).strip()
        rest = rest[: m.start()].strip()
    else:
        mm = re.search(rf"\s+({_ROLE_KEYWORDS})$", rest)
        if mm:
            role = mm.group(1).strip()
            rest = rest[: mm.start()].strip()
    return role, rest


def _split_role_inline(rest: str):
    """Split ``Company role content...`` into (role, title, content).

    For single-line bullets the role keyword often sits in the middle of the
    line, followed by the description.  We return the first matched role, the
    text before it as the title, and the text after it as the content.
    """
    # Parenthesized role at the end: everything before the parenthesis is the
    # title and there is no inline content.
    m = re.search(r"[（(](.+?)[）)]\s*$", rest)
    if m:
        return m.group(1).strip(), rest[: m.start()].strip(), ""

    mm = re.search(rf"\s+({_ROLE_KEYWORDS})(?:\s+|$)", rest)
    if mm:
        role = mm.group(1).strip()
        title = rest[: mm.start()].strip()
        content = rest[mm.end():].strip()
        return role, title, content

    return None, rest, ""


def _split_edu(rest: str):
    """Split ``School（985）（major）`` into (school, combined subtitle)."""
    tags = re.findall(r"[（(](.+?)[）)]", rest)
    school = re.sub(r"[（(].+?[）)]", "", rest).strip()
    major = " · ".join(tags) if tags else ""
    return school, major


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


def _extract_metrics(section: Section) -> None:
    """Extract quantified metrics from item content."""
    for item in section.items:
        if item.type in ("bullet", "entry", "project"):
            found = _METRIC_RE.findall(item.content)
            item.metrics = [Metric(v) for v in found]


def _extract_tech_tags(section: Section) -> None:
    """Extract tech tags from ``**涉及技术：…**`` / ``**Tech Stack：…**``.

    Tags are stored on ``item.tags`` and the matched line is **removed** from
    ``item.content`` because the template renders them separately via
    ``.proj-tech`` / ``.exp-tech``. Keeping them in content would produce
    duplicate lines in the output.
    """
    for item in section.items:
        tech_m = re.search(r"\*\*涉及技术[：:]\*\*\s*(.+)", item.content)
        if not tech_m:
            tech_m = re.search(r"\*\*Tech Stack[：:]\*\*\s*(.+)", item.content)
        if tech_m:
            raw = tech_m.group(1)
            item.tags = [t.strip() for t in re.split(r"[、,，/]", raw) if t.strip()]
            # Remove the tech-tags line from content entirely; the template
            # renders ``.proj-tech`` / ``.exp-tech`` separately.
            before = item.content[: tech_m.start()]
            after = item.content[tech_m.end():]
            # Trim trailing whitespace / paragraph break before the match
            # and leading whitespace after the match to avoid dangling blank
            # paragraphs.
            before = before.rstrip("\n \t")
            after = after.lstrip("\n \t")
            item.content = (before + "\n\n" + after).strip() if before and after else (before or after).strip()


def _extract_badges(doc: Document) -> None:
    """Extract badge info from section titles."""
    badge_map = {
        "professional_summary": "20年" if doc.source_lang == "zh" else "20 Yrs",
    }
    for section in doc.sections:
        if section.id in badge_map:
            section.badge = badge_map[section.id]


class ResumeParser(ParserPlugin):
    """Parse a resume Markdown document into a Document.

    Pipeline: ``parse_document`` (shared CommonMark/GFM engine) →
    ``_enrich_resume`` (resume semantic layer). Supports the full resume
    template format: ``# Name — Title`` header, contact info, resume section
    headings, ``**bold**`` structured entries, bullets, ``**涉及技术：**`` tech
    tag extraction, ``YYYY.MM-YYYY.MM`` date periods and quantified metrics.
    """

    def __init__(self):
        self._manifest = PluginManifest(
            name="resume",
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
            sec in text
            for sec in [
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
        """Parse resume Markdown text into a structured Document."""
        doc = parse_document(text, source_lang=_detect_lang(text))
        _enrich_resume(doc)
        return doc
