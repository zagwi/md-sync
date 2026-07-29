"""Language-agnostic structured data model for parsed documents.

A Document is composed of Sections, each containing Items.
This model is format-agnostic — parsers produce it, renderers consume it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ── Item (leaf node) ────────────────────────────────────────────────────────


@dataclass
class Metric:
    """A quantified metric value (e.g. QPS 10K+, 99.9%+)."""
    value: str
    context: str = ""  # what this metric measures


@dataclass
class Item:
    """A single entry within a section.

    ``type`` determines which fields are populated:
        bullet     → ``content``
        entry      → ``period``, ``title``, ``subtitle``, ``content``, ``people``
        project    → ``period``, ``title``, ``role``, ``content``, ``metrics``, ``tags``
        open_source→ ``title``, ``content``, ``features``, ``url``, ``tags``
    """
    type: str                       # bullet | entry | project | open_source
    content: str = ""               # main description text (markdown allowed)

    # Common structured fields
    period: str | None = None
    title: str | None = None     # company / school / project name
    subtitle: str | None = None  # role / department
    people: str | None = None    # team size

    # Project-specific
    role: str | None = None
    metrics: list[Metric] = field(default_factory=list)

    # Open-source specific
    features: list[str] = field(default_factory=list)
    url: str | None = None

    # Tech tags
    tags: list[str] = field(default_factory=list)

    # Code block support
    language: str | None = None  # programming language for code blocks

    # Internal
    _hash: str | None = None

    def content_hash(self) -> str:
        """Stable hash of this item's meaningful content for change detection."""
        if self._hash:
            return self._hash
        raw = f"{self.type}|{self.period}|{self.title}|{self.subtitle}|{self.content}"
        self._hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self._hash


# ── Section ─────────────────────────────────────────────────────────────────


@dataclass
class Section:
    """A heading-level section containing items."""
    id: str                  # machine-readable key: "professional_summary"
    title: str               # display title: "综合素质及能力"
    level: int               # heading depth (1 = top)
    items: list[Item] = field(default_factory=list)
    badge: str | None = None  # optional badge text: "20年"


# ── Document ────────────────────────────────────────────────────────────────


@dataclass
class Document:
    """Top-level document: header metadata + list of sections."""
    # Header
    name: str = ""
    title: str = ""
    contacts: dict[str, str] = field(default_factory=dict)
    meta_lines: list[str] = field(default_factory=list)

    # Body
    sections: list[Section] = field(default_factory=list)

    # Source tracking
    source_path: str | None = None
    source_lang: str = "zh"
    source_raw: str = ""  # original verbatim text of the source file

    def find_section(self, section_id: str) -> Section | None:
        """Look up a section by its machine-readable id."""
        for s in self.sections:
            if s.id == section_id:
                return s
        return None

    def section_titles(self) -> list[str]:
        """Return all section titles in order."""
        return [s.title for s in self.sections]
