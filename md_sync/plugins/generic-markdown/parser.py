"""Generic Markdown Parser — handles any standard Markdown document.

Powered by ``markdown-it-py`` (CommonMark + GFM) via
``md_sync.core.md_engine``. This is the universal parser: it understands the
full Markdown syntax (headings, lists, tables, code fences, blockquotes,
nested structures, …) and converts any document into the universal
``Document`` model without requiring a specific template format.
"""

from __future__ import annotations

from md_sync.core.document import Document
from md_sync.core.md_engine import parse_document
from md_sync.plugin.interface import PLUGIN_TYPE_PACK, ParserPlugin, PluginManifest


class GenericMarkdownParser(ParserPlugin):
    """Parse any standard Markdown document into a Document.

    Serves as the universal fallback parser for any unrecognized document
    format. ``detect()`` always returns ``True``.
    """

    def __init__(self):
        self._manifest = PluginManifest(
            name="generic-markdown",
            version="1.0",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="markdown",
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """Generic parser always returns True — it handles any text."""
        return True

    def parse(self, text: str) -> Document:
        """Parse plain Markdown text into a Document.

        Args:
            text: Raw Markdown text.

        Returns:
            A Document with sections and items extracted from headings,
            lists, tables, code blocks and paragraphs.
        """
        return parse_document(text)
