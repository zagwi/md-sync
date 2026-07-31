"""Gongwen 标准公文 (GB/T 9704-2012) parser for md-sync.

Reuses the shared CommonMark/GFM engine (``md_sync.core.md_engine``) so any
standard Markdown document parses correctly, and applies a *gongwen-specific*
post-processing pass that recognises the 公文 conventions:

* the leading ``# 关于……的通知`` title becomes ``doc.name`` / ``doc.title``
* ``## 一、`` / ``### （一）`` numbered headings become sections
* trailing 落款（单位名称 + 成文日期）is kept as body text
* the 主送机关 line (``各有关单位：``) is kept as intro text

The gongwen render style (二号小标宋标题 / 黑体 / 楷体 / 仿宋正文) is applied
by the bundled ``gongwen`` HTML template, which ships its own CSS — users do
**not** need Typora installed to render official documents.
"""

from __future__ import annotations

import re

from md_sync.core.document import Document
from md_sync.core.md_engine import parse_document
from md_sync.plugin.interface import PLUGIN_TYPE_PACK, ParserPlugin, PluginManifest

# 公文标题（标题行）：`# 关于……的通知`
_GONGWEN_TITLE_RE = re.compile(
    r"^#\s+(关于.{0,80}的(?:通知|决定|报告|请示|批复|意见|方案|函|纪要|命令|公告|通告|通报)|印发.{0,80}的通知)\s*$"
)
# 一级标题：`## 一、`、`## （一）`、`## 1.`
_LEVEL_1_RE = re.compile(r"^##\s+[一二三四五六七八九十]+[、．.．]", re.MULTILINE)
# 二级标题：`### （一）`
_LEVEL_2_RE = re.compile(r"^###\s+[（(][一二三四五六七八九十]+[）)]", re.MULTILINE)
# 落款日期：`2026年7月31日`
_SIGN_DATE_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$", re.MULTILINE)


def _enrich_gongwen(doc: Document) -> None:
    """Apply gongwen-specific structuring on top of the standard parse.

    Recognises the leading ``#`` title (公文标题) and moves it to
    ``doc.name`` / ``doc.title``, mirroring how the resume parser promotes its
    header. Section headings keep their standard parse (levels 2-4), so the
    gongwen template's CSS maps them to 黑体/楷体/仿宋 per GB/T 9704-2012.
    """
    hdr_idx = next(
        (i for i, s in enumerate(doc.sections) if s.level == 1 and s.title.strip()),
        None,
    )
    if hdr_idx is not None:
        hsec = doc.sections[hdr_idx]
        title = hsec.title.strip()
        if title:
            doc.title = title
            doc.name = title
        # 保留标题下方的联系/主送机关行（如果有），然后移除标题段本身
        del doc.sections[hdr_idx]


def _detect_gongwen(text: str) -> bool:
    """Heuristic: does the text look like a 公文 document?"""
    score = 0
    if _GONGWEN_TITLE_RE.search(text):
        score += 1
    if _LEVEL_1_RE.search(text):
        score += 1
    if _LEVEL_2_RE.search(text):
        score += 1
    if "主送机关" in text or re.search(r"各[^，。\n]{1,20}[，。]", text):
        score += 1
    if _SIGN_DATE_RE.search(text):
        score += 1
    return score >= 2


class GongwenParser(ParserPlugin):
    """Parse a 标准公文 Markdown document into a Document.

    Pipeline: ``parse_document`` (shared CommonMark/GFM engine) →
    ``_enrich_gongwen`` (gongwen semantic layer: title promotion).
    """

    def __init__(self):
        self._manifest = PluginManifest(
            name="gongwen",
            version="1.0",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="gongwen",
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """Auto-detect if text is a 公文 document."""
        return _detect_gongwen(text)

    def parse(self, text: str) -> Document:
        """Parse a 公文 Markdown document into a Document."""
        doc = parse_document(text, source_lang="zh")
        _enrich_gongwen(doc)
        return doc
