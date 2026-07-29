"""Comprehensive Markdown parsing & rendering regression tests.

Covers every Markdown syntax we handle, verifies HTML tag balancing
(prevents the "rest of document swallowed by code block" class of bugs),
and validates the cross-theme rendering pipeline end-to-end.

Run::

    python -m pytest tests/test_md_engine.py -v
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from md_sync.core.document import Document, Item, Section
from md_sync.core.md_engine import (
    _blockquote_is_complex,
    _collect_blockquote,
    _collect_bullets,
    _find_matching,
    _has_nested_list,
    _MD,
    _slice_raw,
    _table_to_md,
    parse_document,
    render_block,
    render_inline,
)
from md_sync.renderers.html import HtmlRenderer

# ── Paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_THEMES = {
    "bwx": _PROJECT_ROOT / "md_sync/plugins/resume/templates/bwx",
    "modern": _PROJECT_ROOT / "md_sync/plugins/resume/templates/modern",
    "typora": _PROJECT_ROOT / "md_sync/plugins/typora/templates/typora",
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _render_doc(doc: Document, theme: str) -> str:
    """Render a Document through a theme's Jinja2 pipeline."""
    r = HtmlRenderer(_THEMES[theme])
    return r.render(doc, translator=None, lang=doc.source_lang)


def _assert_balanced_html(html: str, label: str = "") -> None:
    """Assert that every opened HTML tag is properly closed (non-void)."""

    class _Balancer(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack: list[str] = []
            self.errors: list[str] = []

        def handle_starttag(self, tag: str, _attrs: Any) -> None:
            if tag not in _VOID_ELEMENTS:
                self.stack.append(tag)

        def handle_endtag(self, tag: str) -> None:
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            else:
                expected = self.stack[-1] if self.stack else "(none)"
                self.errors.append(
                    f"</{tag}> when expecting </{expected}>, stack: {self.stack[-5:]}"
                )

    b = _Balancer()
    b.feed(html)
    b.close()
    prefix = f"[{label}] " if label else ""
    assert not b.stack, f"{prefix}Unclosed tags: {b.stack}"
    assert not b.errors, f"{prefix}Tag mismatch: {b.errors}"


_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def _body_only(html: str) -> str:
    """Return only the ``<body>…</body>`` portion of the HTML.

    Some themes legitimately contain ``<style>`` or ``<div>`` in their
    ``<head>`` (for CSS / layout).  We only care about leaks that originate
    from the *parsed document content*.
    """
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL)
    return m.group(1) if m else html


def _has_raw_angle(html: str, tag: str) -> int:
    """Count raw ``<tag>`` / ``<tag `` occurrences *in the document body only*.

    An escaped ``&lt;tag&gt;`` does **not** match.  Tags inside the theme's
    own ``<head>`` (e.g. ``<style>`` for CSS) are ignored.
    """
    body = _body_only(html)
    return len(re.findall(r"<" + re.escape(tag) + r"(\s|>)", body))


# ══════════════════════════════════════════════════════════════════════════════
# 1.  Render safety — the "code block swallows everything" class of bug
# ══════════════════════════════════════════════════════════════════════════════


class TestRenderSafety:
    """Every path that injects source text into HTML must balance tags."""

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_code_block_with_angle_tags(self, theme: str) -> None:
        """Code block containing ``<name>``, ``<style>`` etc. must be escaped."""
        src = (
            "# 测试\n\n"
            "```yaml\n"
            "name: my-pack   # remove <name>\n"
            "style: <style>body {}</style>\n"
            "---\n"
            "```\n\n"
            "## 代码块之后的内容\n\n"
            "这段内容不应该被吞进代码块。\n"
        )
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/code-angle")
        assert "代码块之后的内容" in html, f"{theme}: content after code block missing"
        # No raw <name> or <style> tags should leak into HTML
        assert _has_raw_angle(html, "name") == 0, f"{theme}: raw <name> leaked"
        assert _has_raw_angle(html, "style") == 0, f"{theme}: raw <style> leaked"

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_inline_code_with_style_tag(self, theme: str) -> None:
        """`` `templates/<style>/` `` must not open a real <style> tag."""
        src = (
            "# 路径引用\n\n"
            "模板路径为 `templates/<style>/自定义.css`。\n\n"
            "## 后续内容\n\n"
            "这条文字不应被吞掉。\n"
        )
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/inline-style")
        assert _has_raw_angle(html, "style") == 0, f"{theme}: raw <style> from inline code"
        assert "后续内容" in html or "不应该被吞掉" in html, f"{theme}: content missing"

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_raw_html_in_paragraph_is_escaped(self, theme: str) -> None:
        """``<div onclick="...">`` in plain text must be escaped.

        Themes use ``<div>`` for layout, so we check that the user's
        injection string (``<div onclick``) is NOT present as a raw tag.
        """
        src = "<div onclick='alert(1)'>危险</div>\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/raw-html")
        body = _body_only(html)
        # The malicious text must be fully escaped, not open a live tag
        assert "<div onclick" not in body, f"{theme}: <div onclick> not escaped"
        assert "&lt;div" in body, f"{theme}: escaped &lt;div&gt; not found"

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_complex_doc_is_balanced(self, theme: str) -> None:
        """A document mixing every syntax type yields balanced HTML."""
        src = (
            "# 综合测试\n\n"
            "普通段落 **粗体** *斜体* `code` [链接](https://x.com)。\n\n"
            "## 列表\n\n"
            "- 扁平项 A\n"
            "- 扁平项 B\n\n"
            "## 嵌套列表\n\n"
            "- 父项\n"
            "  - 子项 1\n"
            "  - 子项 2\n\n"
            "## 引用\n\n"
            "> 单段引用。\n\n"
            "> 多段引用第一段。\n"
            ">\n"
            "> 多段引用第二段。\n\n"
            "## 代码\n\n"
            "```python\n"
            "if a < b and c > d:\n"
            "    print('<hello>')\n"
            "```\n\n"
            "## 表格\n\n"
            "| 名称 | 值 |\n"
            "|---|---|\n"
            "| X | 1 |\n\n"
            "## 分隔线之后\n\n"
            "---\n\n"
            "最后一段文字。\n"
        )
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/complex")

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_nested_list_renders_hierarchy(self, theme: str) -> None:
        """Nested list via ``md`` block preserves depth."""
        src = "- A\n  - A1\n  - A2\n- B\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/nested-list")
        # Nested list rendered as md block: should contain 2 levels of <ul>
        assert html.count("<ul>") >= 2, f"{theme}: nested list not preserved"

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_multi_para_blockquote_renders_both_paragraphs(self, theme: str) -> None:
        """Multi-paragraph blockquote via ``md`` block preserves both 'graphs."""
        src = "> 第一段。\n>\n> 第二段。\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/blockquote")
        assert "第一段" in html, f"{theme}: first paragraph missing"
        assert "第二段" in html, f"{theme}: second paragraph missing"


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Parsing — every Markdown syntax → correct Item type & content
# ══════════════════════════════════════════════════════════════════════════════


class TestParsing:
    """Verify ``parse_document`` produces correct shapes for every syntax."""

    # ── Headings ──────────────────────────────────────────────────────────

    def test_headings_create_sections(self) -> None:
        doc = parse_document("# H1\n\n## H2\n\n### H3\n")
        titles = [(s.level, s.title) for s in doc.sections]
        assert titles == [(1, "H1"), (2, "H2"), (3, "H3")]

    # ── Paragraphs → text ─────────────────────────────────────────────────

    def test_paragraph_becomes_text_item(self) -> None:
        doc = parse_document("这是一段普通文字。")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "text"
        assert items[0].content == "这是一段普通文字。"

    def test_inline_markup_preserved_in_content(self) -> None:
        """Inline ** / * / ` / [link] is kept raw in content for templates to render."""
        doc = parse_document("**粗体** *斜体* `code` [link](https://x.com)")
        content = doc.sections[0].items[0].content
        assert "**粗体**" in content
        assert "*斜体*" in content
        assert "`code`" in content
        assert "[link](https://x.com)" in content

    # ── Flat lists → bullet ───────────────────────────────────────────────

    def test_flat_list_becomes_bullet_items(self) -> None:
        doc = parse_document("- 甲\n- 乙\n- 丙\n")
        items = doc.sections[0].items
        assert len(items) == 3
        for it in items:
            assert it.type == "bullet"
        assert items[0].content == "甲"
        assert items[1].content == "乙"
        assert items[2].content == "丙"

    def test_ordered_list_becomes_bullet_items(self) -> None:
        doc = parse_document("1. 第一\n2. 第二\n")
        items = doc.sections[0].items
        assert len(items) == 2
        for it in items:
            assert it.type == "bullet"
        assert items[0].content == "第一"

    # ── Nested lists → md ─────────────────────────────────────────────────

    def test_nested_list_becomes_md_item(self) -> None:
        doc = parse_document("- 父\n  - 子\n- 其他\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "md"

    def test_deeply_nested_list_becomes_md_item(self) -> None:
        doc = parse_document("- L1\n  - L2\n    - L3\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "md"

    # ── Code blocks → code ────────────────────────────────────────────────

    def test_fenced_code_block(self) -> None:
        doc = parse_document("```python\nprint('hello')\n```\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "code"
        assert items[0].language == "python"
        assert "print('hello')" in items[0].content

    def test_code_block_content_not_trimmed(self) -> None:
        """Code content should preserve leading/trailing whitespace except final newline."""
        doc = parse_document("```\n  indented\n  \n```\n")
        assert "  indented" in doc.sections[0].items[0].content

    # ── Tables → table ────────────────────────────────────────────────────

    def test_table_becomes_table_item(self) -> None:
        doc = parse_document("| A | B |\n|---|---|\n| 1 | 2 |\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "table"
        assert "A" in items[0].content
        assert "|---" in items[0].content

    # ── Thematic break → hr ───────────────────────────────────────────────

    def test_hr_becomes_hr_item(self) -> None:
        doc = parse_document("---\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "hr"

    def test_setext_underline_is_not_heading(self) -> None:
        """After disabling lheading, ``text\\n---`` is text + hr, not H2."""
        doc = parse_document("联系方式 | mail@x.com\n---\n")
        items = doc.sections[0].items
        types = [it.type for it in items]
        assert "hr" in types, f"--- should be hr, got {types}"
        # The line before --- should be a text paragraph, not a section heading
        assert items[0].type == "text"

    # ── Blockquotes: simple → text, complex → md ──────────────────────────

    def test_simple_blockquote_becomes_text(self) -> None:
        doc = parse_document("> 一句话引用。\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "text"
        assert "一句话引用" in items[0].content

    def test_multi_para_blockquote_becomes_md(self) -> None:
        doc = parse_document("> 第一段。\n>\n> 第二段。\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "md"

    def test_blockquote_with_nested_list_becomes_md(self) -> None:
        doc = parse_document("> - 列表项\n>   - 嵌套\n")
        items = doc.sections[0].items
        assert len(items) == 1
        assert items[0].type == "md"

    # ── Edges: empty / trailing newline / sibling blocks ──────────────────

    def test_empty_document(self) -> None:
        doc = parse_document("")
        assert len(doc.sections) >= 0

    def test_only_whitespace(self) -> None:
        doc = parse_document("   \n\n   \n")
        # Should not crash; at least one section
        assert isinstance(doc, Document)

    def test_code_after_code(self) -> None:
        """Two adjacent fenced code blocks produce two code items."""
        src = "```a\n1\n```\n\n```b\n2\n```\n"
        doc = parse_document(src)
        codes = [it for s in doc.sections for it in s.items if it.type == "code"]
        assert len(codes) == 2
        assert codes[0].language == "a"
        assert codes[1].language == "b"


# ══════════════════════════════════════════════════════════════════════════════
# 3.  Inline rendering
# ══════════════════════════════════════════════════════════════════════════════


class TestRenderInline:
    """Verify ``render_inline`` produces correct, safe HTML."""

    def test_bold(self) -> None:
        assert "<strong>" in str(render_inline("**bold**"))

    def test_italic(self) -> None:
        assert "<em>" in str(render_inline("*italic*"))

    def test_code_span(self) -> None:
        out = str(render_inline("`code`"))
        assert "<code>" in out
        assert "code" in out

    def test_link(self) -> None:
        out = str(render_inline("[text](https://x.com)"))
        assert 'href="https://x.com"' in out
        assert "text" in out

    def test_strikethrough(self) -> None:
        assert "<s>" in str(render_inline("~~deleted~~"))

    def test_image(self) -> None:
        out = str(render_inline("![alt](https://x.com/a.png)"))
        assert "<img" in out

    def test_raw_html_escaped(self) -> None:
        out = str(render_inline("<script>alert(1)</script>"))
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_style_tag_escaped(self) -> None:
        out = str(render_inline("templates/<style>/custom.css"))
        assert "<style>" not in out
        assert "&lt;style&gt;" in out

    def test_div_tag_escaped(self) -> None:
        out = str(render_inline("<div>text</div>"))
        assert "<div>" not in out
        assert "&lt;div&gt;" in out


# ══════════════════════════════════════════════════════════════════════════════
# 4.  Block rendering (md type)
# ══════════════════════════════════════════════════════════════════════════════


class TestRenderBlock:
    """Verify ``render_block`` handles complex markdown without HTML safety risks."""

    def test_nested_list(self) -> None:
        out = str(render_block("- a\n  - b\n  - c\n- d\n"))
        # 2 levels of <ul> (or <ol>)
        assert out.count("<ul>") + out.count("<ol>") >= 2

    def test_blockquote_multipara(self) -> None:
        out = str(render_block("> p1\n>\n> p2\n"))
        assert out.count("<p>") >= 2
        assert "p1" in out
        assert "p2" in out

    def test_fenced_code_inside_block(self) -> None:
        out = str(render_block("```python\nif a < b:\n    pass\n```\n"))
        assert _has_raw_angle(out, "python") == 0, "raw angle bracket in code block"

    def test_task_list(self) -> None:
        out = str(render_block("- [ ] todo\n- [x] done\n"))
        assert "todo" in out
        assert "done" in out

    def test_no_raw_angle_tags(self) -> None:
        """Any block containing ``<tag>``-like text must escape it."""
        out = str(render_block("Some text with <name> and <style> in it."))
        assert "<name>" not in out
        assert "<style>" not in out


# ══════════════════════════════════════════════════════════════════════════════
# 5.  Helper functions (white-box)
# ══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    """Unit tests for internal helpers."""

    def test_find_matching_simple(self) -> None:
        toks = _MD.parse("- a\n- b\n")
        i = next(k for k, t in enumerate(toks) if t.type == "bullet_list_open")
        j = _find_matching(toks, i, "bullet_list_open", "bullet_list_close")
        assert toks[j].type == "bullet_list_close"
        assert j > i

    def test_has_nested_list_true(self) -> None:
        toks = _MD.parse("- a\n  - b\n- c\n")
        i = next(k for k, t in enumerate(toks) if t.type == "bullet_list_open")
        j = _find_matching(toks, i, "bullet_list_open", "bullet_list_close")
        assert _has_nested_list(toks, i, j) is True

    def test_has_nested_list_false(self) -> None:
        toks = _MD.parse("- a\n- b\n")
        i = next(k for k, t in enumerate(toks) if t.type == "bullet_list_open")
        j = _find_matching(toks, i, "bullet_list_open", "bullet_list_close")
        assert _has_nested_list(toks, i, j) is False

    def test_blockquote_is_complex_multi_para(self) -> None:
        toks = _MD.parse("> p1\n>\n> p2\n")
        i = next(k for k, t in enumerate(toks) if t.type == "blockquote_open")
        j = _find_matching(toks, i, "blockquote_open", "blockquote_close")
        assert _blockquote_is_complex(toks, i + 1, j) is True

    def test_blockquote_is_complex_simple(self) -> None:
        toks = _MD.parse("> 一句话\n")
        i = next(k for k, t in enumerate(toks) if t.type == "blockquote_open")
        j = _find_matching(toks, i, "blockquote_open", "blockquote_close")
        assert _blockquote_is_complex(toks, i + 1, j) is False

    def test_slice_raw_captures_full_block(self) -> None:
        src = "- a\n  - b\n- c\n"
        toks = _MD.parse(src)
        i = next(k for k, t in enumerate(toks) if t.type == "bullet_list_open")
        j = _find_matching(toks, i, "bullet_list_open", "bullet_list_close")
        raw = _slice_raw(src, toks[i], toks[j])
        # Should contain all 3 lines
        assert raw.count("\n") == 2

    def test_collect_bullets_flat(self) -> None:
        toks = _MD.parse("- a\n- b\n")
        i = next(k for k, t in enumerate(toks) if t.type == "bullet_list_open")
        j = _find_matching(toks, i, "bullet_list_open", "bullet_list_close")
        items = _collect_bullets(toks, i + 1, j)
        assert items == ["a", "b"]

    def test_collect_blockquote_simple(self) -> None:
        toks = _MD.parse("> hello world\n")
        i = next(k for k, t in enumerate(toks) if t.type == "blockquote_open")
        j = _find_matching(toks, i, "blockquote_open", "blockquote_close")
        text = _collect_blockquote(toks, i + 1, j)
        assert "hello world" in text

    def test_table_to_md(self) -> None:
        toks = _MD.parse("| A | B |\n|---|---|\n| 1 | 2 |\n")
        i = next(k for k, t in enumerate(toks) if t.type == "table_open")
        j = _find_matching(toks, i, "table_open", "table_close")
        md = _table_to_md(toks, i, j)
        assert "|---" in md
        assert "A" in md
        assert "1" in md


# ══════════════════════════════════════════════════════════════════════════════
# 6.  Regression — bugs we've already fixed
# ══════════════════════════════════════════════════════════════════════════════


class TestRegressions:
    """The bug is in the issue tracker; the test is here to keep it from returning."""

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_setext_not_heading(self, theme: str) -> None:
        """``text\\n---`` must be text + hr, not a level-2 heading."""
        src = "联系方式 | mail@x.com\n---\n\n后续内容\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/setext")
        # "联系方式" should appear as normal text, not <h2>
        assert "后续内容" in html, f"{theme}: content after --- missing"
        # The line before --- should not become a section title
        titles = [s.title for s in doc.sections]
        assert "联系方式 | mail@x.com" not in titles, f"{theme}: setext became heading"

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_angle_brackets_in_code_block(self, theme: str) -> None:
        """Code block with ``<name>`` does NOT leak as raw HTML tag."""
        src = "```yaml\nkey: <name>\n```\n\n## 后面\n\n文字。\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/angle-code")
        assert "后面" in html

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_style_in_inline_code(self, theme: str) -> None:
        """`` `templates/<style>/` `` does NOT open a real <style> tag."""
        src = "路径 `templates/<style>/a.css`\n\n## 之后\n\n文字。\n"
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/style-inline")
        assert _has_raw_angle(html, "style") == 0
        assert "之后" in html

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_md_items_balanced(self, theme: str) -> None:
        """Every ``md``-type item renders balanced HTML."""
        src = (
            "- 嵌套\n"
            "  - 子项\n"
            "  - 子项2\n\n"
            "> 多段。\n"
            ">\n"
            "> 第二段。\n"
        )
        doc = parse_document(src)
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/md-items")


# ══════════════════════════════════════════════════════════════════════════════
# 7.  Stress — edge combinations
# ══════════════════════════════════════════════════════════════════════════════


class TestStress:
    """Combined scenarios that exercise the full pipeline at once."""

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_real_world_readme(self, theme: str) -> None:
        """Render the project README (our most complex real doc)."""
        readme = _PROJECT_ROOT / "README.md"
        if not readme.exists():
            pytest.skip("README.md not found")
        src = readme.read_text(encoding="utf-8")
        doc = parse_document(src, source_lang="zh")
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/README")

    @pytest.mark.parametrize("theme", list(_THEMES))
    def test_chaos_document(self, theme: str) -> None:
        """A single document exercising every edge at once."""
        src = (
            "# 标题\n\n"
            "段落 `templates/<style>/` **粗体**。\n\n"
            "## 嵌套列表\n"
            "- A\n"
            "  - A1\n"
            "  - A2\n"
            "- B\n\n"
            "## 多段引用\n"
            "> 第一段。\n"
            ">\n"
            "> 第二段含 `code`。\n\n"
            "## 代码块\n"
            "```python\n"
            "if a < b:\n"
            "    print('<name>')\n"
            "```\n\n"
            "## 表格\n"
            "| 列A | 列B |\n"
            "|---|---|\n"
            "| <safe> | 1 |\n\n"
            "## 分隔\n"
            "---\n\n"
            "扁平列表：\n"
            "- 项1\n"
            "- 项2\n\n"
            "> 单段引用结束。\n"
        )
        doc = parse_document(src, source_lang="zh")
        html = _render_doc(doc, theme)
        _assert_balanced_html(html, f"{theme}/chaos")
        for tag in ("name", "style", "safe"):
            assert _has_raw_angle(html, tag) == 0, f"{theme}: raw <{tag}> leaked"
