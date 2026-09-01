"""Chinese / English text normalization (中英文混排与英文排版规范).

Chinese rules follow W3C CLReq / CY/T 154-2017 style spacing:

  * 中英文之间加空格         支持ChatGPT → 支持 ChatGPT
  * 中文与数字之间加空格     花100元      → 花 100 元
  * 数字与单位之间加空格     20Gbps       → 20 Gbps  (90° / 15% excluded)
  * 全角标点旁不加空格       iPhone ，好用 → iPhone，好用

English rules follow conventional English punctuation spacing:

  * 标点前不留空格           Hello ,world  → Hello,world
  * 标点后加空格             Hello,world   → Hello, world
  * 连续空格合并为一个       Hello   world → Hello world

Fenced code blocks, inline code, and URLs are always protected so the rules
never alter them. Normalization is applied to derived outputs only — the user's
source file is never modified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from md_sync.translate.fallback import _protect_code_and_urls, _restore_code_and_urls

_CJK = "\u4e00-\u9fff"
_LATIN = "A-Za-z"
_FULLWIDTH_PUNCT = "，。、；：！？…—（）「」『』【】《》"
_PLACEHOLDER_RE = re.compile(r"ZXQWPZLP-(\d+)-")


@dataclass
class TypographyConfig:
    """Global rules for Chinese mixed-script and English typesetting."""

    enabled: bool = True
    # 中英文之间加空格（支持ChatGPT → 支持 ChatGPT）
    cjk_latin_space: bool = True
    # 中文与数字之间加空格（花100元 → 花 100 元）
    cjk_digit_space: bool = True
    # 数字与单位之间加空格（20Gbps → 20 Gbps；90°、15% 除外）
    number_unit_space: bool = True
    # 全角标点旁不加空格（iPhone ，好用 → iPhone，好用）
    fullwidth_punct_no_space: bool = True
    # 英文标点前不留空格（Hello ,world → Hello,world）
    en_no_space_before_punct: bool = True
    # 英文标点后加空格（Hello,world → Hello, world；数字分组 1,000 除外）
    en_space_after_punct: bool = True
    # 英文连续空格合并为一个（Hello   world → Hello world）
    en_collapse_spaces: bool = True

    @classmethod
    def parse(cls, raw: dict | None) -> TypographyConfig:
        if not raw:
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", True)),
            cjk_latin_space=bool(raw.get("cjk_latin_space", True)),
            cjk_digit_space=bool(raw.get("cjk_digit_space", True)),
            number_unit_space=bool(raw.get("number_unit_space", True)),
            fullwidth_punct_no_space=bool(raw.get("fullwidth_punct_no_space", True)),
            en_no_space_before_punct=bool(raw.get("en_no_space_before_punct", True)),
            en_space_after_punct=bool(raw.get("en_space_after_punct", True)),
            en_collapse_spaces=bool(raw.get("en_collapse_spaces", True)),
        )

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "cjk_latin_space": self.cjk_latin_space,
            "cjk_digit_space": self.cjk_digit_space,
            "number_unit_space": self.number_unit_space,
            "fullwidth_punct_no_space": self.fullwidth_punct_no_space,
            "en_no_space_before_punct": self.en_no_space_before_punct,
            "en_space_after_punct": self.en_space_after_punct,
            "en_collapse_spaces": self.en_collapse_spaces,
        }


def _process_segments(text: str, rule_fn) -> str:
    """Apply *rule_fn* to the plain-text segments of *text*.

    Code blocks / inline code / URLs are protected first and restored verbatim
    afterwards; the rules run only on the text between placeholders, so
    protected content — and the text adjacent to it — is never altered.
    """
    protected, p_map = _protect_code_and_urls(text)

    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(protected):
        if m.start() > pos:
            parts.append(rule_fn(protected[pos : m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    if pos < len(protected):
        parts.append(rule_fn(protected[pos:]))

    return _restore_code_and_urls("".join(parts), p_map)


def _apply_zh_rules(text: str, config: TypographyConfig) -> str:
    """Apply the configured Chinese spacing rules to a plain-text segment."""
    if config.cjk_latin_space:
        text = re.sub(rf"([{_CJK}])([{_LATIN}])", r"\1 \2", text)
        text = re.sub(rf"([{_LATIN}])([{_CJK}])", r"\1 \2", text)
    if config.cjk_digit_space:
        text = re.sub(rf"([{_CJK}])([0-9])", r"\1 \2", text)
        text = re.sub(rf"([0-9])([{_CJK}])", r"\1 \2", text)
    if config.number_unit_space:
        text = re.sub(rf"([0-9])([{_LATIN}]{{2,}})", r"\1 \2", text)
    if config.fullwidth_punct_no_space:
        text = re.sub(rf"[ \t]+([{_FULLWIDTH_PUNCT}])", r"\1", text)
        text = re.sub(rf"([{_FULLWIDTH_PUNCT}])[ \t]+", r"\1", text)
    return text


def _apply_en_rules(text: str, config: TypographyConfig) -> str:
    """Apply the configured English punctuation-spacing rules to a plain-text
    segment."""
    if config.en_no_space_before_punct:
        text = re.sub(r"[ \t]+(?=[,.;:!?)\]}:])", "", text)
    if config.en_space_after_punct:
        # Comma / semicolon / colon / ? / ! followed directly by a word char
        # need a space — but NOT between two digits (number grouping like
        # "1,000", times like "10:30").
        text = re.sub(r"(?<![\d])([,;:!?])(?=[0-9A-Za-z])", r"\1 ", text)
        # A period followed by an uppercase letter is a sentence boundary
        # ("end.It" → "end. It") — skip decimals, ellipses, and acronyms
        # ("U.S.A", "3.14", "...").
        text = re.sub(r"(?<![A-Z\d.])(\.)(?=[A-Z])", r"\1 ", text)
    if config.en_collapse_spaces:
        # Collapse runs of 2+ spaces between words. Line-start indentation and
        # trailing hard-break spaces (markdown "two spaces + newline") survive.
        text = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", text)
    return text


def normalize_zh_mixed(text: str, config: TypographyConfig | None) -> str:
    """Apply the configured Chinese spacing rules to *text*.

    When ``config`` is falsy or disabled, *text* is returned unchanged.
    """
    if not config or not config.enabled or not text:
        return text
    return _process_segments(text, lambda s: _apply_zh_rules(s, config))


def normalize_en(text: str, config: TypographyConfig | None) -> str:
    """Apply the configured English punctuation-spacing rules to *text*."""
    if not config or not config.enabled or not text:
        return text
    return _process_segments(text, lambda s: _apply_en_rules(s, config))


def normalize_for_lang(text: str, config: TypographyConfig | None, lang: str) -> str:
    """Normalize *text* according to the rules for *lang* (``"zh"`` / ``"en"``).

    Unknown or disabled languages return *text* unchanged.
    """
    if not config or not config.enabled or not text:
        return text
    if lang == "zh":
        return _process_segments(text, lambda s: _apply_zh_rules(s, config))
    if lang == "en":
        return _process_segments(text, lambda s: _apply_en_rules(s, config))
    return text
