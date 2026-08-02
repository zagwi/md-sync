"""Lightweight language detection for the supported languages (zh / en).

This is intentionally simple: it counts CJK characters vs ASCII/Latin
letters and picks the dominant script. Good enough to decide whether a
source file is Chinese or English so the translation step can run the
right direction (zh→en or en→zh).
"""

from __future__ import annotations

import re

# CJK Unified Ideographs + common CJK punctuation
_CJK_RX = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3000-\u303f\uff00-\uffef]"
)
# Latin letters (a-z, A-Z) — excludes digits/punctuation
_LATIN_RX = re.compile(r"[A-Za-z]")

# Human-readable labels for zh / en
LANG_LABELS = {"zh": "中文", "en": "English"}


def detect_lang(text: str) -> str:
    """Return ``"zh"`` or ``"en"`` based on the dominant script in ``text``.

    Falls back to ``"en"`` when neither script dominates (e.g. empty text).
    """
    cjk = len(_CJK_RX.findall(text))
    latin = len(_LATIN_RX.findall(text))
    if cjk > latin:
        return "zh"
    return "en"


def lang_label(code: str) -> str:
    return LANG_LABELS.get(code, code)
