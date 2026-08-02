"""Page-size and margin helpers shared by the PDF / DOCX exporters.

Defines a small table of common page sizes, the *standard* margin that each
size maps to when the user does not override it, and helpers to:

* resolve a CSS margin string (``"15mm"`` / ``"5mm 8mm"``) or fall back to the
  standard margin for the chosen page size;
* convert a page size + margin into an OOXML ``.docx`` reference document so
  pandoc can adopt the page setup without a user-supplied reference file.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

# (width_mm, height_mm)
PAGE_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}

# Standard uniform margin (mm) per page size, used when the user does not
# explicitly override the margin.
#
# These values follow Chinese and international publishing standards:
#   A4 20mm  — 报告、标书、论文通用标准
#   A3 25mm  — 大版面，需要适当留白
#   A5 15mm  — 便携书／小册子标准
#   Letter 20mm — 中美通用文档
#   Legal 25mm — 法律文档标准
STANDARD_MARGIN_MM = {
    "A4": 20.0,
    "A3": 25.0,
    "A5": 15.0,
    "Letter": 20.0,
    "Legal": 25.0,
}

# Human-readable descriptions for the UI.
MARGIN_LABELS = {
    "": "自适应（按尺寸出版标准）",
    "15mm": "15mm（紧凑）",
    "20mm": "20mm（标准）",
    "25mm": "25mm（宽松）",
    "30mm": "30mm（宽边距）",
}

DEFAULT_PAGE_SIZE = "A4"

# 1 mm == 56.6929 twips (1 inch == 1440 twips, 1 inch == 25.4 mm)
_MM_TO_TWIPS = 56.6929

_MARGIN_RE = re.compile(
    r"^\s*([\d.]+)\s*(mm|cm|in|pt)\s*"
    r"(?:([\d.]+)\s*(mm|cm|in|pt))?\s*$",
    re.I,
)


def normalize_page_size(name: str) -> str:
    """Return *name* if it is a known page size, else the default (A4)."""
    name = (name or "").strip()
    return name if name in PAGE_SIZES_MM else DEFAULT_PAGE_SIZE


def resolve_margin(page_size: str, page_margin: str = "") -> str:
    """Return a CSS margin string for the given page size.

    If *page_margin* is provided (e.g. ``"15mm"`` or ``"5mm 8mm"``) it is used
    as-is. Otherwise the standard margin for *page_size* is returned.
    """
    page_size = normalize_page_size(page_size)
    if page_margin and page_margin.strip():
        return page_margin.strip()
    mm = STANDARD_MARGIN_MM.get(page_size, STANDARD_MARGIN_MM[DEFAULT_PAGE_SIZE])
    return f"{mm:g}mm"


def parse_margin_mm(margin: str) -> tuple[float, float]:
    """Parse a CSS-like margin string into ``(vertical_mm, horizontal_mm)``.

    Accepts a single value (uniform) or two values ``"5mm 8mm"`` (vertical
    horizontal). Inches / cm / pt are converted to millimetres.
    """
    margin = (margin or "").strip()
    if not margin:
        return (STANDARD_MARGIN_MM[DEFAULT_PAGE_SIZE],) * 2
    parts = margin.split()
    if len(parts) >= 2:
        return (_to_mm(parts[0]), _to_mm(parts[1]))
    if len(parts) == 1:
        v = _to_mm(parts[0])
        return (v, v)
    return (STANDARD_MARGIN_MM[DEFAULT_PAGE_SIZE],) * 2


def _to_mm(token: str) -> float:
    m = _MARGIN_RE.match(token)
    if not m:
        try:
            return float(token)
        except ValueError:
            return STANDARD_MARGIN_MM[DEFAULT_PAGE_SIZE]
    num = float(m.group(1))
    unit = m.group(2).lower()
    factor = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0}[unit]
    return num * factor


def page_size_to_twips(page_size: str) -> tuple[int, int]:
    w, h = PAGE_SIZES_MM[normalize_page_size(page_size)]
    return (int(round(w * _MM_TO_TWIPS)), int(round(h * _MM_TO_TWIPS)))


def build_reference_docx(page_size: str, margin: str, dest: Path | None = None) -> Path:
    """Build a minimal ``.docx`` whose ``w:sectPr`` sets the page size & margins.

    Pandoc copies the page setup (``w:pgSz`` / ``w:pgMar``) from the reference
    document, so this lets the DOCX exporter honour an explicit page size even
    when no styled reference file is supplied. The generated document carries
    only a default style.
    """
    w, h = page_size_to_twips(page_size)
    v_mm, h_mm = parse_margin_mm(margin)
    top = bot = int(round(v_mm * _MM_TO_TWIPS))
    left = right = int(round(h_mm * _MM_TO_TWIPS))
    sect = (
        f'<w:sectPr><w:pgSz w:w="{w}" w:h="{h}"/>'
        f'<w:pgMar w:top="{top}" w:right="{right}" w:bottom="{bot}" '
        f'w:left="{left}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t> </w:t></w:r></w:p>" + sect + "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    dest = Path(tempfile.mktemp(suffix=".docx")) if dest is None else Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return dest
