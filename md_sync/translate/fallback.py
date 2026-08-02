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

from md_sync.translate.langdetect import detect_lang

logger = logging.getLogger(__name__)


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

    if provider == "openai":
        return _call_openai(
            text, model=model, api_key=api_key, base_url=base_url,
            source_lang=source_lang, target_lang=target_lang,
        )
    if provider == "google":
        return _call_google(text, source_lang=source_lang, target_lang=target_lang)
    if provider == "bing":
        return _call_bing(text, source_lang=source_lang, target_lang=target_lang)
    if provider == "mymemory":
        return _call_mymemory(text, source_lang=source_lang, target_lang=target_lang)

    return None


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
            proxies=proxy,
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
        iid = re.search(r'IID":"([^"]+)"', home.text) or re.search(
            r'data-iid="([^"]+)"', home.text
        )
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
    base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
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
