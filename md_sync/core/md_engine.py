"""Professional Markdown parsing/rendering engine (CommonMark + GFM).

This module is the single, central place where raw Markdown text is turned
into the structured :class:`~md_sync.core.document.Document` model and where
inline Markdown fragments are rendered to safe HTML.

It is backed by ``markdown-it-py`` (https://github.com/executablebooks/
markdown-it-py), a mature, CommonMark-compliant engine with GFM extensions.
Replacing the previous hand-rolled line scanner and regex-based inline
renderer eliminates two classes of bugs:

* **content loss** for arbitrary Markdown — the engine understands the full
  CommonMark/GFM syntax (nested lists, blockquotes, tables, fenced code,
  setext headings, reference links, …) instead of a small hand-picked subset;
* **HTML injection / document truncation** — raw HTML in the source is
  escaped by the engine (``html: False``), so a fragment such as
  ``templates/<style>/`` can never open a real ``<style>`` tag and swallow
  the rest of the document during rendering.

The intermediate representation (``Document`` / ``Section`` / ``Item``) is
intentionally preserved so the existing theme templates, translation layer
and multi-format exporters keep working unchanged.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markupsafe import Markup

from md_sync.core.document import Document, Item, Section

# gfm-like: enables GFM tables + strikethrough. We explicitly set html=False
# so that any raw HTML in the source (e.g. a literal `<style>` inside a code
# span) is ESCAPED rather than interpreted as real markup — this is what makes
# the inline renderer safe and prevents a stray tag from swallowing the rest of
# the document during PDF/HTML generation.
# We also disable markdown-it's own `linkify` rule (it requires the optional
# `linkify-it` package) and rely on our own `linkify` Jinja2 filter for
# bare-URL autolinking, applied *after* inline rendering.
# `lheading` (setext headings, i.e. an `===`/`---` underline under a paragraph)
# is disabled so that a `---` thematic break after a paragraph is always treated
# as a horizontal rule, never as a level-2 heading — this matters for both the
# resume header (`Contact | email\n---`) and arbitrary docs that use `---` as a
# separator.
_MD = MarkdownIt("gfm-like")
_MD.options["html"] = False
_MD.disable(["linkify", "lheading"])


def render_inline(text: str) -> Markup:
    """Render a fragment of inline Markdown to safe HTML.

    Uses markdown-it's inline renderer. Raw HTML in *text* is escaped, so a
    source fragment like ``templates/<style>/`` can never open a real tag and
    swallow the rest of the document.
    """
    return Markup(_MD.renderInline(str(text)))


def render_block(text: str) -> Markup:
    """Render a block of Markdown to safe HTML.

    Unlike :func:`render_inline` (which only handles inline syntax such as
    ``**bold**`` / ``[links]()``), this performs a *full block* render that
    preserves complex structure: nested lists, multi-paragraph blockquotes,
    reference-style links (``[text][1]`` + ``[1]: url``), footnotes, task
    lists, definition lists, etc.

    Raw HTML in *text* is still escaped by the engine (``html: False``), so the
    output is safe to inject with ``| safe``.
    """
    return Markup(_MD.render(str(text)))


def parse_document(src: str, source_lang: str = "zh") -> Document:
    """Parse a Markdown string into a structured :class:`Document`."""
    doc = Document(source_lang=source_lang)
    doc.source_raw = src
    tokens = _MD.parse(src)

    sections: list[Section] = []
    intro = Section(id="_intro", title="", level=1)
    sections.append(intro)
    current = intro

    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        ttype = t.type

        if ttype == "heading_open":
            level = int(t.tag[1:])
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            title = inline.content.strip() if inline else ""
            sec = Section(id=_slug(title, len(sections)), title=title, level=level)
            sections.append(sec)
            current = sec
            i += 2 if inline else 1
            continue

        if ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            if inline is not None and inline.content.strip():
                current.items.append(Item(type="text", content=inline.content.strip()))
            i += 2 if inline else 1
            continue

        if ttype in ("bullet_list_open", "ordered_list_open"):
            close = ttype.replace("_open", "_close")
            j = _find_matching(tokens, i, ttype, close)
            if _has_nested_list(tokens, i, j):
                # Nested lists lose hierarchy when flattened into bullet items,
                # so we fall back to a lossless block render of the raw source.
                raw = _slice_raw(src, tokens[i], tokens[j])
                if raw:
                    current.items.append(Item(type="md", content=raw.strip("\n")))
            else:
                for content in _collect_bullets(tokens, i + 1, j):
                    current.items.append(Item(type="bullet", content=content))
            i = j + 1
            continue

        if ttype == "table_open":
            j = _find_matching(tokens, i, "table_open", "table_close")
            md = _table_to_md(tokens, i, j)
            if md:
                current.items.append(Item(type="table", content=md))
            i = j + 1
            continue

        if ttype == "fence":
            lang = (t.info or "").strip().split()[0] if t.info else ""
            current.items.append(Item(type="code", content=t.content.rstrip("\n"), language=lang))
            i += 1
            continue

        if ttype == "hr":
            current.items.append(Item(type="hr"))
            i += 1
            continue

        if ttype == "blockquote_open":
            j = _find_matching(tokens, i, "blockquote_open", "blockquote_close")
            if _blockquote_is_complex(tokens, i + 1, j):
                # Multi-paragraph or nested blockquote — flattening into a
                # single text line loses structure, so keep the raw block.
                raw = _slice_raw(src, tokens[i], tokens[j])
                if raw:
                    current.items.append(Item(type="md", content=raw.strip("\n")))
            else:
                content = _collect_blockquote(tokens, i + 1, j)
                if content:
                    current.items.append(Item(type="text", content=content))
            i = j + 1
            continue

        # Other tokens (close tokens, html_block, etc.) are skipped.
        i += 1

    doc.sections = [s for s in sections if s.items or s.title]
    if not doc.sections:
        doc.sections = [intro]
    return doc


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_matching(tokens, start: int, open_type: str, close_type: str) -> int:
    """Return the index of the closing token matching the opener at *start*."""
    depth = 0
    for k in range(start, len(tokens)):
        if tokens[k].type == open_type:
            depth += 1
        elif tokens[k].type == close_type:
            depth -= 1
            if depth == 0:
                return k
    return len(tokens) - 1


def _collect_bullets(tokens, start: int, end: int) -> list[str]:
    """Extract bullet item texts from a list token range.

    Nested lists are flattened into the parent item's text (no content is
    dropped) — the theme renders every item as a bullet regardless.
    """
    items: list[str] = []
    k = start
    while k < end:
        if tokens[k].type == "list_item_open":
            parts: list[str] = []
            m = k + 1
            depth = 0
            while m < end and not (tokens[m].type == "list_item_close" and depth == 0):
                tk = tokens[m].type
                if tk in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                elif tk in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                elif tk == "inline":
                    parts.append(tokens[m].content.strip())
                m += 1
            text = " ".join(parts).strip()
            if text:
                items.append(text)
            k = m
        else:
            k += 1
    return items


def _table_to_md(tokens, start: int, end: int) -> str:
    """Serialize a markdown-it table token range back into GFM table text.

    The delimiter row is emitted as ``|---|---|`` so the theme templates
    (which detect the separator via ``row.startswith("|---")``) recognise it.
    """
    header: list[str] = []
    body: list[list[str]] = []
    cur: list[str] | None = None
    state: str | None = None
    for k in range(start, end):
        tk = tokens[k].type
        if tk == "thead_open":
            state = "header"
            cur = []
        elif tk == "tbody_open":
            state = "body"
        elif tk == "tr_open":
            cur = []
        elif tk == "tr_close":
            if state == "header" and cur is not None:
                header = cur
            elif cur is not None:
                body.append(cur)
            cur = []
        elif tk == "inline" and cur is not None:
            cur.append(tokens[k].content.strip())
    if not header:
        return ""
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _collect_blockquote(tokens, start: int, end: int) -> str:
    """Flatten a blockquote token range into Markdown blockquote text."""
    parts: list[str] = []
    for k in range(start, end):
        if tokens[k].type == "inline":
            parts.append(tokens[k].content.strip())
    text = " ".join(parts).strip()
    if not text:
        return ""
    return "\n".join("> " + line for line in text.split("\n"))


def _has_nested_list(tokens, start: int, end: int) -> bool:
    """Return True if a nested list appears inside the list token range.

    ``start`` points at the outer ``*_list_open`` token and ``end`` at its
    matching close, so we begin at depth 1 — any further ``*_list_open`` means
    a nested list and triggers the lossless fallback.
    """
    depth = 1
    for k in range(start, end + 1):
        tk = tokens[k].type
        if tk in ("bullet_list_open", "ordered_list_open"):
            if depth >= 2:
                return True
            depth += 1
        elif tk in ("bullet_list_close", "ordered_list_close"):
            depth -= 1
    return False


def _blockquote_is_complex(tokens, start: int, end: int) -> bool:
    """Return True if the blockquote has multiple paragraphs or nested blocks.

    A single-paragraph quote is rendered fine as inline ``text``; anything more
    complex (blank-line separated paragraphs, nested lists/blockquotes, code
    fences) would lose structure if flattened, so it should be kept raw.
    """
    para_breaks = 0
    for k in range(start, end):
        tk = tokens[k].type
        if tk in (
            "bullet_list_open",
            "ordered_list_open",
            "blockquote_open",
            "fence",
            "table_open",
        ):
            return True
        if tk == "paragraph_close":
            para_breaks += 1
    return para_breaks >= 2


def _slice_raw(src: str, open_token, close_token) -> str:
    """Recover the original Markdown source for a token range.

    markdown-it records the ``map`` (line range) for block tokens, so we can
    slice the exact source lines that produced this block. Closing tokens often
    have no ``map`` of their own, so we fall back to the opener's end line
    (``open.map[1]``) which marks the first line *after* the block.
    """
    lines = src.split("\n")
    omap = getattr(open_token, "map", None)
    cmap = getattr(close_token, "map", None)
    if not omap:
        return ""
    end = cmap[0] if cmap else omap[1]
    return "\n".join(lines[omap[0] : end])


def _slug(title: str, idx: int) -> str:
    """Build a stable, machine-readable section id from a title."""
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", title.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or f"section-{idx}"
