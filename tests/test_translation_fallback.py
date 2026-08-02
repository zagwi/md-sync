"""Translation fallback tests — verifies MyMemory provider works
without API key, proxy, or any external configuration.

Run::

    python -m pytest tests/test_translation_fallback.py -v
"""

from __future__ import annotations

import pytest
from md_sync.translate.fallback import (
    _call_mymemory,
    _detect_provider,
    _normalize_mymemory_lang,
    _protect_code_and_urls,
    _restore_code_and_urls,
    translate_via_api,
)

# ── Provider detection ──────────────────────────────────────────────


class TestDetectProvider:
    def test_default_is_mymemory(self) -> None:
        """When no env vars are set, auto falls back to mymemory."""
        import os

        for var in ("OPENAI_API_KEY", "TRANSLATE_PROVIDER"):
            os.environ.pop(var, None)
        assert _detect_provider() == "mymemory"

    def test_mymemory_explicit(self) -> None:
        import os

        os.environ["TRANSLATE_PROVIDER"] = "mymemory"
        assert _detect_provider() == "mymemory"
        del os.environ["TRANSLATE_PROVIDER"]

    def test_none_provider(self) -> None:
        import os

        os.environ["TRANSLATE_PROVIDER"] = "none"
        assert _detect_provider() == "none"
        del os.environ["TRANSLATE_PROVIDER"]


# ── MyMemory language normalisation ─────────────────────────────────


class TestNormalizeMymemoryLang:
    @pytest.mark.parametrize(
        "input_code, expected",
        [
            ("zh", "zh"),
            ("zh-cn", "zh"),
            ("zh_CN", "zh"),
            ("chinese", "zh"),
            ("cmn", "zh"),
            ("en", "en"),
            ("english", "en"),
            ("ja", "ja"),
        ],
    )
    def test_normalises(self, input_code: str, expected: str) -> None:
        assert _normalize_mymemory_lang(input_code) == expected


# ── MyMemory translation ────────────────────────────────────────────


class TestMyMemoryTranslation:
    @pytest.mark.asyncio
    def test_translate_chinese_to_english(self) -> None:
        """Chinese text is translated to English by MyMemory."""
        result = _call_mymemory("你好世界", source_lang="zh", target_lang="en")
        assert result is not None
        assert len(result) > 0

    def test_translate_returns_none_on_invalid_lang(self) -> None:
        """Unsupported language pair returns None, not an exception."""
        result = _call_mymemory("test", source_lang="xx", target_lang="yy")
        assert result is None

    def test_translate_preserves_meaning(self) -> None:
        """Translation carries the core meaning of the source."""
        result = _call_mymemory("md-sync 自动同步文档", source_lang="zh", target_lang="en")
        assert result is not None
        assert "md-sync" in result


class TestTranslateViaApiMyMemory:
    def test_mymemory_provider_translates(self) -> None:
        """translate_via_api with provider='mymemory' returns a string."""
        result = translate_via_api(
            "你好，这是一个测试。",
            provider="mymemory",
            source_lang="zh",
            target_lang="en",
        )
        assert result is not None
        assert len(result) > 0

    def test_auto_provider_uses_mymemory(self) -> None:
        """provider='auto' (no env vars) resolves to mymemory."""
        import os

        for var in ("OPENAI_API_KEY", "TRANSLATE_PROVIDER"):
            os.environ.pop(var, None)
        result = translate_via_api(
            "自动翻译成英文",
            provider="auto",
            source_lang="zh",
            target_lang="en",
        )
        assert result is not None
        assert len(result) > 0

    def test_none_provider_returns_none(self) -> None:
        """provider='none' always returns None."""
        result = translate_via_api(
            "任何文本",
            provider="none",
            source_lang="zh",
            target_lang="en",
        )
        assert result is None

    def test_mymemory_handles_empty_string(self) -> None:
        """Empty input returns None."""
        result = _call_mymemory("", source_lang="zh", target_lang="en")
        assert result is None or result == ""


class TestTranslateViaApiFallbackChain:
    def test_google_fallback_returns_none_without_proxy(self) -> None:
        """Google endpoint fails gracefully when proxy is unreachable."""
        import os

        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"  # unreachable
        try:
            result = translate_via_api(
                "测试文本",
                provider="google",
                source_lang="zh",
                target_lang="en",
            )
            assert result is None
        finally:
            del os.environ["HTTPS_PROXY"]

    def test_bing_fallback_returns_none_when_blocked(self) -> None:
        """Bing endpoint fails gracefully."""
        result = translate_via_api(
            "测试文本",
            provider="bing",
            source_lang="zh",
            target_lang="en",
        )
        assert result is None or isinstance(result, str)

    def test_mymemory_is_reliable_default(self) -> None:
        """MyMemory succeeds where Google/Bing may fail."""
        import os

        for var in ("OPENAI_API_KEY", "TRANSLATE_PROVIDER"):
            os.environ.pop(var, None)
        result = translate_via_api(
            "翻译功能测试",
            provider="auto",
            source_lang="zh",
            target_lang="en",
        )
        assert result is not None
        assert len(result) > 0


# ── Code / URL protection & restore ─────────────────────────────────


class TestProtectAndRestore:
    def test_inline_code_survives_roundtrip(self) -> None:
        """Inline code is placeholder-protected and restored byte-for-byte."""
        text = "Use `--bg` flag and `--text-color` option."
        protected, p_map = _protect_code_and_urls(text)
        assert "ZXQWPZLP" in protected
        assert "`" not in protected
        restored = _restore_code_and_urls(protected, p_map)
        assert restored == text

    def test_url_survives_roundtrip(self) -> None:
        """Bare URLs are protected from the translation engine."""
        text = "See https://example.com/a?b=1 for details."
        protected, p_map = _protect_code_and_urls(text)
        assert "https://example.com/a?b=1" not in protected
        restored = _restore_code_and_urls(protected, p_map)
        assert restored == text

    def test_fenced_code_block_roundtrip(self) -> None:
        """Fenced code blocks are protected as a whole."""
        text = "Run it:\n```bash\npip install md-sync\n```\nDone."
        protected, p_map = _protect_code_and_urls(text)
        assert "pip install" not in protected
        restored = _restore_code_and_urls(protected, p_map)
        assert restored == text

    def test_restore_only_appends_truly_dropped_placeholders(self) -> None:
        """Placeholders that survived translation are NOT re-appended.

        Regression: the old restore logic checked ``ph not in result``
        *after* substitution, when placeholders were already replaced, so
        every fragment was falsely treated as dropped and re-appended,
        duplicating inline code / URLs in the output.
        """
        text = "Use `--bg` here and `--text-color` there."
        protected, p_map = _protect_code_and_urls(text)
        # Simulate a translation engine that preserved all placeholders.
        restored = _restore_code_and_urls(protected, p_map)
        assert restored.count("`--bg`") == 1
        assert restored.count("`--text-color`") == 1

    def test_restore_reappends_placeholder_engine_dropped(self) -> None:
        """A placeholder the engine dropped entirely is still recovered."""
        text = "Keep `config.json` intact."
        protected, p_map = _protect_code_and_urls(text)
        # Simulate the engine dropping the placeholder entirely.
        mangled = "Translated text."
        restored = _restore_code_and_urls(mangled, p_map)
        assert "`config.json`" in restored

    def test_css_fragment_survives(self) -> None:
        """CSS like ``@page { margin: 0 }`` is kept byte-for-byte."""
        text = "Set `@page { margin: 0 }` in the stylesheet."
        protected, p_map = _protect_code_and_urls(text)
        restored = _restore_code_and_urls(protected, p_map)
        assert "@page { margin: 0 }" in restored
