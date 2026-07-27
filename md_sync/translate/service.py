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

from typing import Optional

from md_sync.core.document import Document
from md_sync.translate.fallback import translate_via_api
from md_sync.translate.fallback import _detect_provider
from md_sync.translate.manager import TranslationManager


def _distinct_contents(doc: Document) -> list[str]:
    out: list[str] = []
    seen = set()
    for section in doc.sections:
        for item in section.items:
            text = (item.content or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def translate_document(
    doc: Document,
    target_lang: str,
    provider: Optional[str] = None,
    translator: Optional[TranslationManager] = None,
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

    Returns a summary dict::

        {
          "source_lang": "zh",
          "target_lang": "en",
          "provider": "google",
          "total": 8,
          "translated": 5,     # freshly translated this run
          "cached": 3,         # already in cache
          "failed": 0,
        }
    """
    provider = provider or _detect_provider()
    # provider resolution: explicit > config.ai.provider > auto
    if provider == "auto":
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

    for text in _distinct_contents(doc):
        total += 1
        if translator and translator.has_translation(text, target_lang):
            cached += 1
            continue
        if translator and translator.get_status(text) == "pending":
            failed += 1
            continue

        # Circuit-breaker: after one hard failure, stop hammering the API.
        result = translate_via_api(
            text,
            provider=provider,
            source_lang=doc.source_lang,
            target_lang=target_lang,
        )
        if result:
            if translator:
                translator.store(text, result, target_lang, status="auto")
            translated += 1
        else:
            if translator:
                translator.mark_pending(text, target_lang)
            failed += 1

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
    }


def translate_plan(doc: Document, translator: Optional[TranslationManager] = None) -> dict:
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
