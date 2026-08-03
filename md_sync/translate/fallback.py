"""Translation fallback.

When a mapping miss occurs, this module can call a public translation
service to generate a first-pass translation. Three categories of
providers are supported:

  * Key-based LLM APIs (OpenAI-compatible) — require ``OPENAI_API_KEY``.
  * Free public web endpoints (Google / Bing "translate" web widgets) —
    no API key required. These are the unofficial web endpoints and may
    change at any time; they are used as a best-effort fallback and any
    failure simply returns ``None`` so the caller can fall back to the
    original text.
  * Free translation memory API (MyMemory) — no API key required,
    no proxy needed, reliable for Chinese-English.

Provider selection (``auto`` by default):

  1. ``OPENAI_API_KEY`` set  → ``openai``
  2. ``TRANSLATE_PROVIDER`` env (google|bing|openai|mymemory|none) → that
  3. otherwise                → ``mymemory`` (free, no key, no proxy)
"""

from __future__ import annotations

import logging
import os
import re

import httpx

from md_sync.core.md_engine import translate_md_leaves as _translate_md_leaves
from md_sync.translate.langdetect import detect_lang

logger = logging.getLogger(__name__)


# Placeholder tokens must survive translation engines unaltered: use ASCII
# letters only (no digits/symbols that engines may strip or reformat).
_PLACEHOLDER_PREFIX = "ZXQWPZLP"
_PLACEHOLDER_RE = re.compile(r"ZXQWPZLP(\d+)")


def _protect_code_and_urls(text: str) -> tuple[str, dict[str, str]]:
    """Replace inline code, URLs, and fenced code blocks with placeholders.

    Translation engines (Google/Bing/MyMemory) often rewrite inline code or
    URLs embedded in a paragraph (e.g. ``@page { margin: 0 }`` → ``@ page
    {margin: 0}``, ``--bg`` → `` --bg ``). Replacing them before translation
    and restoring afterwards keeps code and URLs byte-for-byte identical.

    Returns ``(protected_text, {placeholder: original})``.
    """
    placeholder_map: dict[str, str] = {}
    counter = [0]

    def _sub(match: re.Match) -> str:
        placeholder = f"{_PLACEHOLDER_PREFIX}{counter[0]}"
        counter[0] += 1
        placeholder_map[placeholder] = match.group(0)
        return placeholder

    # Fenced code blocks first (whole block protected so its content is kept
    # byte-for-byte; the backticks would otherwise confound the regexes below).
    text = re.sub(
        r"```.*?```",
        _sub,
        text,
        flags=re.DOTALL,
    )
    # Inline code `` `...` `` (single-line, no backticks inside).
    text = re.sub(r"`[^`\n]+`", _sub, text)
    # Bare URLs.
    text = re.sub(
        r"https?://[^\s)`\]}>]+",
        _sub,
        text,
    )
    # Markdown emphasis / strikethrough delimiters (``**``, ``*``, ``__``,
    # ``_``, ``~~``, ``***``). Unlike code/URLs we protect only the marker
    # characters, keeping the inner text translatable — otherwise engines
    # mangle the markers (``**X**`` → ``* * X * *``) and the emphasis no
    # longer renders.
    text = re.sub(
        r"(?<!\w)([*_~]{1,3})(?=\w)|(?<=\w)([*_~]{1,3})(?!\w)",
        _sub,
        text,
    )
    return text, placeholder_map


def _restore_code_and_urls(text: str, placeholder_map: dict[str, str]) -> str:
    """Put protected code/URL fragments back into the translated text.

    If the engine dropped or reordered a placeholder, the original fragment is
    re-appended so no code is ever lost.
    """
    result = text
    seen: set[str] = set()

    def _restore(match: re.Match) -> str:
        ph = match.group(0)
        seen.add(ph)
        return placeholder_map.get(ph, ph)

    result = _PLACEHOLDER_RE.sub(_restore, result)

    # Any placeholder the engine dropped entirely (never appeared in the
    # translated output) gets re-appended in order, so code is never lost.
    dropped = [
        (int(ph[len(_PLACEHOLDER_PREFIX) :]), placeholder_map[ph])
        for ph in placeholder_map
        if ph not in seen
    ]
    for _, orig in sorted(dropped):
        # A dropped emphasis delimiter (``**``/``*``…) must NOT be re-appended
        # to the end of the sentence — it would turn into stray markers. If the
        # engine dropped it, the emphasis is simply lost instead.
        if orig.strip(" *_~"):
            result = result.rstrip() + "\n" + orig
    return result


def _repair_emphasis_spacing(text: str) -> str:
    """Collapse whitespace a translation engine inserted inside Markdown markers.

    Engines often rewrite ``**X**`` as ``** X **`` (or ``* X *``); those spaces
    make the emphasis stop rendering. This drops spaces directly inside an
    opening/closing emphasis delimiter pair while leaving normal sentence
    spacing (``**X** and Y``) untouched.
    """
    # Drop space(s) right after an *opening* delimiter: "** X" -> "**X".
    # (An opening delimiter is preceded by start/whitespace/open-punctuation —
    # a closing delimiter is preceded by content, so it is never touched here.)
    text = re.sub(
        r"(^|[\s([{<，。；：！？])([*_~]{1,3})[ \t]+(?=[^\s*_~])",
        r"\1\2",
        text,
    )
    # Drop space(s) right before a *closing* delimiter: "X **" -> "X**".
    # (The delimiter must be followed by whitespace/punctuation/end — otherwise
    # it is an opening delimiter and its preceding space is real sentence flow.)
    text = re.sub(
        r"(?<=[^\s*_~])[ \t]+([*_~]{1,3})(?=$|[\s)\]},.;:!?，。；：！？])",
        r"\1",
        text,
    )
    return text


def translate_via_api(
    text: str,
    provider: str = "auto",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str | None:
    """Translate ``text`` from ``source_lang`` to ``target_lang``.

    Supported providers (``provider``):
        auto       → auto-detect from env / defaults (see above)
        openai     → OpenAI-compatible LLM API (needs OPENAI_API_KEY)
        google     → Google web translate endpoint (free, no key)
        bing       → Bing web translate endpoint (free, no key)
        mymemory   → MyMemory translation memory (free, no key, no proxy)
        none       → skip, return None

    Returns the translated text, or None on failure.
    """
    if provider in (None, "none"):
        return None

    if provider == "auto":
        provider = _detect_provider()

    def _call(protected: str) -> str | None:
        try:
            if provider == "openai":
                return _call_openai(
                    protected,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            if provider == "google":
                return _call_google(protected, source_lang=source_lang, target_lang=target_lang)
            if provider == "bing":
                return _call_bing(protected, source_lang=source_lang, target_lang=target_lang)
            if provider == "mymemory":
                return _call_mymemory(protected, source_lang=source_lang, target_lang=target_lang)
        except Exception:
            logger.debug("provider call failed", exc_info=True)
            return None
        return None

    def _translate_leaf(leaf: str) -> str | None:
        """Translate one plain-text leaf, keeping its surrounding whitespace."""
        core = leaf.strip()
        if not core:
            return leaf
        lead = leaf[: len(leaf) - len(leaf.lstrip())]
        trail = leaf[len(leaf.rstrip()) :]
        # Bare URLs / inline code inside the leaf (linkify is disabled at parse
        # time, so a bare URL is a plain text leaf) are protected so the engine
        # can't mangle them, then restored byte-for-byte.
        protected, placeholder_map = _protect_code_and_urls(core)
        result = _call(protected)
        if not result:
            return None
        restored = _restore_code_and_urls(result, placeholder_map)
        # Engines often pad a short leaf ("HTML" → " HTML "). The leaf's own
        # source spacing (``lead``/``trail``) is authoritative — drop the
        # engine's padding, or a neighbouring ``**``/``*`` marker stops parsing.
        return lead + restored.strip() + trail

    # Primary path: hand the engine only the unadorned text leaves, so Markdown
    # syntax (``**``, links, code, tables…) is never exposed to it and cannot be
    # mangled. ``translate_md_leaves`` returns ``None`` for block constructs it
    # cannot round-trip, in which case we fall back to the whole-string path
    # below (with markers still protected).
    translated = _translate_md_leaves(text, _translate_leaf)
    if translated is not None:
        return translated

    # Fallback: protect inline code, URLs, code fences, and Markdown emphasis
    # markers from being mangled by the engine, then restore them after
    # translation. This keeps the output format strictly identical to the
    # source (code is never translated).
    protected, placeholder_map = _protect_code_and_urls(text)
    result = _call(protected)
    if not result:
        return None
    restored = _restore_code_and_urls(result, placeholder_map)
    return _repair_emphasis_spacing(restored)


def auto_detect_lang(text: str) -> str:
    """Detect the source language of ``text`` (delegates to langdetect)."""
    return detect_lang(text)


def _detect_provider() -> str:
    """Resolve the provider for ``auto`` mode."""
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    env_provider = (os.environ.get("TRANSLATE_PROVIDER") or "").lower()
    if env_provider in ("google", "bing", "openai", "mymemory", "none"):
        return env_provider
    # Free, no-key, no-proxy public endpoint is the default.
    return "mymemory"


def _normalize_google_lang(code: str) -> str:
    code = (code or "").lower()
    if code in ("zh", "zh-cn", "zh_cn", "chinese", "cmn"):
        return "zh-CN"
    if code in ("en", "english"):
        return "en"
    return code


def _get_proxy() -> str | None:
    """Return the HTTP proxy URL to use for translation API calls.

    Priority:
      1. ``HTTPS_PROXY`` / ``HTTP_PROXY`` env var (standard convention)
      2. ``TRANSLATE_PROXY`` env var (explicit translation proxy)
      3. Default ``http://127.0.0.1:1080`` (common local proxy port)

    Returns ``None`` if none is set.
    """
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "TRANSLATE_PROXY"):
        val = os.environ.get(var) or os.environ.get(var.lower(), "").strip()
        if val and val != "":
            return val
    # Default: common local proxy (Clash, V2Ray, etc.)
    return "http://127.0.0.1:1080"


def _request_without_proxy(method: str, url: str, **kwargs):
    """Make an httpx request ignoring all system proxy settings.

    This is needed because environments often have SOCKS proxies set that
    prevent direct HTTPS connection to translation APIs.
    """
    import httpx as _httpx

    return _httpx.request(method, url, trust_env=False, **kwargs)


def _call_google(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str | None:
    """Free Google web translate endpoint (client=gtx, no key)."""
    sl = _normalize_google_lang(source_lang)
    tl = _normalize_google_lang(target_lang)
    try:
        proxy = _get_proxy()
        client = httpx.Client(
            proxy=proxy,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        resp = client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text},
        )
        resp.raise_for_status()
        data = resp.json()
        # data[0] is a list of [translated_fragment, original, ...] segments.
        parts = []
        for seg in data[0]:
            if seg and seg[0]:
                parts.append(seg[0])
        result = "".join(parts).strip()
        return result or None
    except Exception:
        logger.debug("provider fallback failed", exc_info=True)
        return None


def _normalize_bing_lang(code: str) -> str:
    code = (code or "").lower()
    if code in ("zh", "zh-cn", "zh_cn", "chinese", "cmn"):
        return "zh-Hans"
    if code in ("en", "english"):
        return "en"
    return code


def _call_bing(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str | None:
    """Free Bing web translate endpoint (ttranslatev3, no key).

    Bing's web widget requires a couple of anti-bot tokens (``IG`` /
    ``IID``) that are scraped from the translator page. This is fragile;
    on any failure we simply return None.
    """
    sl = _normalize_bing_lang(source_lang)
    tl = _normalize_bing_lang(target_lang)
    try:
        session = httpx.Client(
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            follow_redirects=True,
        )
        home = session.get("https://www.bing.com/translator")
        home.raise_for_status()
        ig = re.search(r'IG:"([^"]+)"', home.text)
        iid = re.search(r'IID":"([^"]+)"', home.text) or re.search(r'data-iid="([^"]+)"', home.text)
        ig_val = ig.group(1) if ig else ""
        iid_val = iid.group(1) if iid else ""

        resp = session.post(
            "https://www.bing.com/ttranslatev3",
            params={"isVertical": "1", "IG": ig_val, "IID": iid_val},
            data={"fromLang": sl, "to": tl, "text": text},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            translations = data[0].get("translations", [{}])
            return translations[0].get("text") or None
        return None
    except Exception:
        logger.debug("provider fallback failed", exc_info=True)
        return None


def _call_openai(
    text: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str | None:
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    )
    model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are a professional translator. "
                            f"Translate the following text from {source_lang} to natural, "
                            f"idiomatic {target_lang}. "
                            "Preserve all markdown formatting, metric values, and proper nouns. "
                            "Output ONLY the translation, no explanations."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.debug("provider fallback failed", exc_info=True)
        return None


def _normalize_mymemory_lang(code: str) -> str:
    """Normalize language codes for MyMemory API.

    MyMemory expects 2-letter codes (e.g., ``zh``, ``en``).  We collapse
    common variants so callers can pass ``zh-cn``, ``chinese``, etc.
    """
    code = (code or "").lower()
    if code in ("zh", "zh-cn", "zh_cn", "chinese", "cmn"):
        return "zh"
    if code in ("en", "english"):
        return "en"
    return code


def _call_mymemory(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str | None:
    """Free MyMemory translation memory endpoint.

    - No API key required.
    - No proxy required (direct HTTPS).
    - Daily free limit ~100 000 characters.
    - On any failure returns ``None`` so the caller can fall back.
    """
    sl = _normalize_mymemory_lang(source_lang)
    tl = _normalize_mymemory_lang(target_lang)
    try:
        params = {
            "q": text,
            "langpair": f"{sl}|{tl}",
            "de": "md-sync@users.noreply.github.com",
        }
        resp = _request_without_proxy(
            "GET",
            "https://api.mymemory.translated.net/get",
            params=params,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("responseStatus") == 200:
            return data["responseData"]["translatedText"]
        else:
            logger.debug(
                "MyMemory error: %s",
                data.get("responseDetails", "unknown"),
            )
            return None
    except Exception:
        logger.debug("provider fallback failed", exc_info=True)
        return None
