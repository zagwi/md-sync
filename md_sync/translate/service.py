"""Standalone translation service.

This module separates "translation" from "synchronization". The flow the
UI exposes is:

    1. detect the source file's language,
    2. translate (fill the translation cache for the *other* language),
    3. only then sync (generate the output files).

`translate_document` does step 2 only: it walks the parsed document and
fills in missing translations for the requested target language, using
the configured provider (OpenAI key, or free Google/Bing web endpoint).
It does NOT touch any output files.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from md_sync.core.document import Document

logger = logging.getLogger(__name__)
from md_sync.translate.fallback import _detect_provider, translate_via_api
from md_sync.translate.manager import TranslationManager

# How many concurrent translation API calls to allow.
# Google Translate web endpoint handles 10+ parallel requests fine.
_TRANSLATE_WORKERS = 10


def _distinct_contents(doc: Document) -> list[str]:
    """Extract all translatable text fields from a Document.

    Beyond ``item.content``, this collects Section titles, the document
    name/title, bullet list items (including those nested in ``md`` items
    that were stored as raw markdown blocks), and structured fields like
    entry titles/subtitles, project roles, open-source titles/features.
    """
    out: list[str] = []
    seen = set()

    def _add(text: str | None) -> None:
        text = (text or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    # Document-level metadata
    _add(doc.name)
    _add(doc.title)

    # Contact values
    if doc.contacts:
        for val in doc.contacts.values():
            _add(val)

    for section in doc.sections:
        _add(section.title)

        for item in section.items:
            # Code blocks are never translated (they contain config/code that
            # must stay byte-for-byte identical).
            if item.type == "code":
                continue
            # Always translate content
            _add(item.content)

            # Structured / resume-style fields
            _add(item.title)
            _add(item.subtitle)
            _add(item.period)
            _add(item.role)
            _add(item.people)
            if item.url:
                _add(item.url)

            # Lists of strings
            for feat in item.features:
                _add(feat)
            for tag in item.tags:
                _add(tag)

            # Metrics
            for metric in item.metrics:
                _add(metric.value)
                _add(metric.context)

            # For table-type items, translate each cell (content already
            # contains the full markdown table, so just translate that).
    return out


def translate_document(
    doc: Document,
    target_lang: str,
    provider: str | None = None,
    translator: TranslationManager | None = None,
    progress_callback: callable | None = None,
) -> dict:
    """Translate every content item of ``doc`` into ``target_lang``.

    Args:
        doc: Parsed source document.
        target_lang: The language to translate *into* (e.g. ``"en"`` if
            the source is Chinese, ``"zh"`` if the source is English).
        provider: Override provider (``auto``/``openai``/``google``/``bing``).
            Defaults to the config's ``translation.ai.provider``.
        translator: Optional pre-built TranslationManager. If omitted, one
            is created from the document's config.
        progress_callback: Optional callable ``fn(done, total, text)`` called
            each time a text item is translated or resolved from cache.

    Returns a summary dict::

        {
          "source_lang": "zh",
          "target_lang": "en",
          "provider": "mymemory",
          "total": 8,
          "translated": 5,     # freshly translated this run
          "cached": 3,         # already in cache
          "failed": 0,
          "results": {...},    # text -> translation for fresh successes
        }
    """
    if provider is None or provider == "auto":
        provider = _detect_provider()

    total = 0
    translated = 0
    cached = 0
    failed = 0

    # We need a translator; if not supplied, build a throwaway one so this
    # function is usable standalone. (Callers usually pass the pipeline's.)
    local_tm = None
    if translator is None:
        cfg = getattr(doc, "_config", None)
        if cfg is not None:
            local_tm = TranslationManager(cfg.translation_path())
            translator = local_tm

    # First pass: identify texts we still need to translate and count the rest.
    # ``pending`` entries are RETRIED rather than skipped forever — a transient
    # provider failure (e.g. MyMemory daily quota) must not permanently poison
    # the cache.
    pending_texts: list[str] = []
    for text in _distinct_contents(doc):
        total += 1
        if translator and translator.has_translation(text, target_lang):
            cached += 1
            if progress_callback:
                progress_callback(total, total, text, "cached")
            continue
        pending_texts.append(text)

    # Second pass: translate uncached texts in parallel. Successful results are
    # stored into the cache so renderers (which read via ``lookup``) can find
    # them; failures are marked pending so a later run can retry.
    results: dict[str, str] = {}
    if pending_texts:
        with ThreadPoolExecutor(max_workers=_TRANSLATE_WORKERS) as pool:
            fut_to_text = {}
            for text in pending_texts:
                fut = pool.submit(
                    translate_via_api,
                    text,
                    provider=provider,
                    source_lang=doc.source_lang,
                    target_lang=target_lang,
                )
                fut_to_text[fut] = text
            done_count = cached  # items already resolved before the parallel run
            for fut in as_completed(fut_to_text):
                text = fut_to_text[fut]
                try:
                    result = fut.result()
                except Exception:
                    logger.debug("translate task failed", exc_info=True)
                    result = None
                if result:
                    results[text] = result
                    if translator:
                        translator.store(text, result, target_lang, status="auto")
                    translated += 1
                else:
                    if translator:
                        translator.mark_pending(text, target_lang)
                    failed += 1
                done_count += 1
                if progress_callback:
                    progress_callback(done_count, total, text, "translated" if result else "failed")

    if local_tm is not None:
        local_tm.save()

    return {
        "source_lang": doc.source_lang,
        "target_lang": target_lang,
        "provider": provider,
        "total": total,
        "translated": translated,
        "cached": cached,
        "failed": failed,
        "results": results,
    }


def translate_plan(doc: Document, translator: TranslationManager | None = None) -> dict:
    """Return what *would* be translated, without doing any network calls.

    Useful for the UI to show "源语言：中文 → 待翻译：英文 5 条" before the
    user clicks translate.
    """
    target = "en" if doc.source_lang == "zh" else "zh"
    total = 0
    missing = 0
    for text in _distinct_contents(doc):
        total += 1
        if translator and translator.has_translation(text, target):
            continue
        missing += 1
    return {
        "source_lang": doc.source_lang,
        "target_lang": target,
        "total": total,
        "missing": missing,
    }
