"""中英文混排规范 (Chinese mixed-script typesetting) tests.

Covers the ``TypographyConfig`` data model, the ``normalize_zh_mixed``
spacing rules, code/URL protection, and the pipeline integration
(``doc.source_raw`` is normalized in memory; the source file is never
modified).

Run::

    python -m pytest tests/test_typography.py -v
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest
import yaml
from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.typography import (
    TypographyConfig,
    normalize_en,
    normalize_for_lang,
    normalize_zh_mixed,
)

# ── Config model ───────────────────────────────────────────────────────


class TestTypographyConfig:
    def test_defaults(self) -> None:
        cfg = TypographyConfig()
        assert cfg.enabled is True
        assert cfg.cjk_latin_space is True
        assert cfg.cjk_digit_space is True
        assert cfg.number_unit_space is True
        assert cfg.fullwidth_punct_no_space is True

    def test_parse_empty_returns_defaults(self) -> None:
        cfg = TypographyConfig.parse(None)
        assert cfg == TypographyConfig()
        cfg = TypographyConfig.parse({})
        assert cfg == TypographyConfig()

    def test_parse_custom(self) -> None:
        cfg = TypographyConfig.parse(
            {"enabled": False, "cjk_latin_space": False, "number_unit_space": True}
        )
        assert cfg.enabled is False
        assert cfg.cjk_latin_space is False
        assert cfg.cjk_digit_space is True  # default preserved
        assert cfg.number_unit_space is True
        assert cfg.fullwidth_punct_no_space is True

    def test_as_dict_roundtrips(self) -> None:
        cfg = TypographyConfig.parse({"enabled": False, "cjk_latin_space": False})
        assert TypographyConfig.parse(cfg.as_dict()) == cfg


# ── Normalization rules ────────────────────────────────────────────────


class TestNormalizeZhMixed:
    @pytest.mark.parametrize(
        "source, expected",
        [
            ("支持ChatGPT的中文", "支持 ChatGPT 的中文"),
            ("md-sync自动同步", "md-sync 自动同步"),
            ("使用Python处理", "使用 Python 处理"),
            ("前后夹英文A和B", "前后夹英文 A 和 B"),
        ],
    )
    def test_cjk_latin_space(self, source: str, expected: str) -> None:
        assert normalize_zh_mixed(source, TypographyConfig()) == expected

    def test_cjk_latin_space_off(self) -> None:
        cfg = TypographyConfig(cjk_latin_space=False)
        assert normalize_zh_mixed("支持ChatGPT", cfg) == "支持ChatGPT"

    @pytest.mark.parametrize(
        "source, expected",
        [
            ("花100元", "花 100 元"),
            ("Python3.12发布", "Python3.12 发布"),  # 数字→中文侧
            ("需要64GB内存", "需要 64 GB 内存"),  # 中文→数字侧 + 数字→单位
        ],
    )
    def test_cjk_digit_space(self, source: str, expected: str) -> None:
        assert normalize_zh_mixed(source, TypographyConfig()) == expected

    def test_cjk_digit_space_off(self) -> None:
        cfg = TypographyConfig(cjk_digit_space=False)
        assert normalize_zh_mixed("花100元", cfg) == "花100元"

    @pytest.mark.parametrize(
        "source, expected",
        [
            ("20Gbps网速", "20 Gbps 网速"),
            ("15MB文件", "15 MB 文件"),  # 数字→单位 + 单位→中文
            ("90°不拆", "90°不拆"),  # 度分符号例外
            ("15%不拆", "15%不拆"),  # 百分比例外
            ("5G不拆", "5G 不拆"),  # 单字母不做数字→单位（G与中文仍加空格）
        ],
    )
    def test_number_unit_space(self, source: str, expected: str) -> None:
        assert normalize_zh_mixed(source, TypographyConfig()) == expected

    def test_number_unit_space_off(self) -> None:
        cfg = TypographyConfig(number_unit_space=False)
        assert normalize_zh_mixed("20Gbps", cfg) == "20Gbps"

    @pytest.mark.parametrize(
        "source, expected",
        [
            ("iPhone ，好用", "iPhone，好用"),
            ("（ 你好 ）", "（你好）"),
            ("中文。 English", "中文。English"),
        ],
    )
    def test_fullwidth_punct_no_space(self, source: str, expected: str) -> None:
        assert normalize_zh_mixed(source, TypographyConfig()) == expected

    def test_fullwidth_punct_no_space_off(self) -> None:
        cfg = TypographyConfig(fullwidth_punct_no_space=False)
        assert normalize_zh_mixed("iPhone ，好用", cfg) == "iPhone ，好用"


# ── English rules (英文排版规范) ─────────────────────────────────────────


class TestNormalizeEn:
    @pytest.mark.parametrize(
        "source, expected",
        [
            ("Hello ,world", "Hello, world"),
            ("specifications .It", "specifications. It"),
            ("text ;next", "text; next"),
            ("list ) item", "list) item"),
            ("see :this", "see: this"),
        ],
    )
    def test_no_space_before_punct_then_space_after(self, source: str, expected: str) -> None:
        assert normalize_en(source, TypographyConfig()) == expected

    @pytest.mark.parametrize(
        "source, expected",
        [
            ("Hello,world", "Hello, world"),
            ("parts,a,b", "parts, a, b"),
        ],
    )
    def test_space_after_punct(self, source: str, expected: str) -> None:
        assert normalize_en(source, TypographyConfig()) == expected

    @pytest.mark.parametrize(
        "source, expected",
        [
            ("1,000 items", "1,000 items"),  # 数字千分位不拆
            ("10:30 meeting", "10:30 meeting"),  # 时间不拆
            ("3.14 value", "3.14 value"),  # 小数不拆
            ("U.S.A. flag", "U.S.A. flag"),  # 缩写不拆
            ("e.g.example", "e.g.example"),  # 小写缩写后接小写不拆
            ("Hello...world", "Hello...world"),  # 省略号不拆
        ],
    )
    def test_punctuation_exemptions(self, source: str, expected: str) -> None:
        assert normalize_en(source, TypographyConfig()) == expected

    def test_sentence_boundary_period(self) -> None:
        assert normalize_en("end.It works", TypographyConfig()) == "end. It works"

    def test_collapse_spaces(self) -> None:
        assert normalize_en("Hello   world", TypographyConfig()) == "Hello world"

    def test_collapse_preserves_markdown_hard_break(self) -> None:
        assert normalize_en("line1  \nline2", TypographyConfig()) == "line1  \nline2"

    def test_collapse_preserves_leading_indentation(self) -> None:
        assert normalize_en("  - item   here", TypographyConfig()) == "  - item here"

    def test_rules_off(self) -> None:
        cfg = TypographyConfig(
            en_no_space_before_punct=False, en_space_after_punct=False, en_collapse_spaces=False
        )
        assert normalize_en("Hello ,world", cfg) == "Hello ,world"

    def test_collapse_off_keeps_double_space(self) -> None:
        cfg = TypographyConfig(en_collapse_spaces=False)
        assert normalize_en("Hello   world", cfg) == "Hello   world"

    def test_code_and_url_protected(self) -> None:
        assert normalize_en("keep`code ,inline`intact", TypographyConfig()) == "keep`code ,inline`intact"
        assert normalize_en("see https://example.com/a?b=1 ,ok", TypographyConfig()) == "see https://example.com/a?b=1, ok"


class TestNormalizeForLang:
    def test_zh_dispatch(self) -> None:
        assert normalize_for_lang("支持ChatGPT", TypographyConfig(), "zh") == "支持 ChatGPT"

    def test_en_dispatch(self) -> None:
        assert normalize_for_lang("Hello ,world", TypographyConfig(), "en") == "Hello, world"

    def test_unknown_lang_unchanged(self) -> None:
        assert normalize_for_lang("Hello ,world", TypographyConfig(), "ja") == "Hello ,world"

    def test_disabled_config_unchanged(self) -> None:
        assert normalize_for_lang("Hello ,world", TypographyConfig(enabled=False), "en") == "Hello ,world"


# ── Code / URL protection ──────────────────────────────────────────────


class TestProtection:
    @pytest.mark.parametrize(
        "source",
        [
            "中文``代码``保留",
            "中文`x`English",
            "```\ncode中文100\n```不碰",
            "中文https://example.com/a?q=100后面",
            "`中文代码100`前后都不碰",
        ],
    )
    def test_protected_content_never_modified(self, source: str) -> None:
        assert normalize_zh_mixed(source, TypographyConfig()) == source

    def test_mixed_real_text_and_code(self) -> None:
        """Real text is normalized while adjacent inline code stays intact."""
        assert normalize_zh_mixed("使用`--bg`处理数据", TypographyConfig()) == "使用`--bg`处理数据"
        # Real Latin text adjacent to CJK IS normalized.
        assert normalize_zh_mixed("code块", TypographyConfig()) == "code 块"


# ── Disabled / empty input ─────────────────────────────────────────────


class TestDisabled:
    def test_disabled_config_returns_unchanged(self) -> None:
        text = "支持ChatGPT的中文"
        assert normalize_zh_mixed(text, TypographyConfig(enabled=False)) == text

    def test_none_config_returns_unchanged(self) -> None:
        assert normalize_zh_mixed("支持ChatGPT", None) == "支持ChatGPT"

    def test_empty_text(self) -> None:
        assert normalize_zh_mixed("", TypographyConfig()) == ""


# ── Pipeline integration ───────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path: pathlib.Path):
    """A minimal markdown project with a zh source and a zh+en html output."""
    source = tmp_path / "README.md"
    source.write_text(
        "# 项目介绍\n\n"
        "这是一个用于中英文混排规范测试的项目。该项目支持从 Markdown 源文件自动同步为多种格式输出，"
        "并遵循国家标准进行排版处理。该工具支持ChatGPT与Python3.12的文本处理，"
        "同时支持对代码块、行内代码与网址链接进行保护，保证内容完整性与一致性。"
        "使用该工具时需要注意，代码块与链接中的内容不会被规范化处理，"
        "这是为了保证源代码与超链接的完整性，避免破坏原有格式。\n\n"
        "```python\ncode中文100\n```\n",
        encoding="utf-8",
    )
    yaml_path = tmp_path / "md-sync.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "project": "demo",
                "source": str(source),  # absolute — config.load resolves against CWD
                "schema": "markdown",
                "output_root": str(tmp_path / "dist"),
                "source_lang": "zh",
                "outputs": [
                    {"format": "md", "lang": "zh", "pdf": False},
                    {"format": "html", "lang": "zh"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return tmp_path, source, yaml_path


def _tr_key(text: str) -> str:
    """Translation-manager cache key (md5 of the stripped source text)."""
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:12]


@pytest.fixture()
def en_md_project(tmp_path: pathlib.Path):
    """A zh-source markdown project whose en md output is fed from a sloppy
    (pre-populated) translation cache — exercises English typography rules on
    the translated md content without any network calls."""
    source = tmp_path / "README.md"
    para = (
        "这是一个用于中英文混排规范测试的项目，该项目支持从Markdown源文件自动同步为多种格式输出，"
        "并遵循国家标准进行排版处理。该工具支持ChatGPT与Python3.12的文本处理，"
        "同时支持对代码块、行内代码与网址链接进行保护，保证内容完整性与一致性。"
    )
    bullet = "支持代码块、行内代码与网址链接进行保护，保证内容完整性。"
    source.write_text(
        "# 项目介绍\n\n" + para + "\n\n## 功能\n\n- " + bullet + "\n", encoding="utf-8"
    )
    yaml_path = tmp_path / "md-sync.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "project": "demo",
                "source": str(source),
                "schema": "markdown",
                "output_root": str(tmp_path / "dist"),
                "source_lang": "zh",
                "outputs": [{"format": "md", "lang": "en"}],
                "translation": {
                    "mapping_file": str(tmp_path / "tr.json"),
                    "strategy": "mapping",
                    "ai": {"provider": "none"},
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    cache = {
        _tr_key(para): {
            "zh": para,
            "en": "This project supports mixed typesetting specifications .It syncs "
            "multiple output formats automatically ,including HTML and PDF .",
            "status": "done",
        },
        _tr_key(bullet): {
            "zh": bullet,
            "en": "Protect code blocks ,inline code and URL links .",
            "status": "done",
        },
    }
    (tmp_path / "tr.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return tmp_path, source, yaml_path


_RESUME_SOURCE = """\
# 张三

## 联系方式

- 电话：13800000000
- 邮箱：zhangsan@example.com
- 地址：北京市海淀区中关村软件园

## 工作经历

**高级软件工程师** · 2020.06-2023.09
- 负责人工智能与物联网相关项目的研发工作，使用Python进行后端开发。
- 支持ChatGPT与Python3.12的混排处理，同时保证代码块与链接不被破坏。
- 使用Redis与消息队列优化系统性能，将接口平均延迟显著降低。

**初级软件工程师** · 2018.07-2020.05
- 参与企业级管理系统的开发与维护，负责前后端联调与单元测试。
- 熟练运用Linux命令行与Git进行版本管理，编写自动化部署脚本。

## 项目

**md-sync** · 2024.01-至今
- 支持ChatGPT与Python3.12的混排处理。
- 遵循国家标准进行排版，保证中文与英文数字之间的间距统一。

涉及技术：Python、FastAPI、Redis
"""


@pytest.fixture()
def resume_project(tmp_path: pathlib.Path):
    """A resume-schema project (structured layout) with zh source."""
    source = tmp_path / "resume.md"
    source.write_text(_RESUME_SOURCE, encoding="utf-8")
    yaml_path = tmp_path / "md-sync.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "project": "resume-demo",
                "source": str(source),
                "schema": "resume",
                "output_root": str(tmp_path / "dist"),
                "source_lang": "zh",
                "outputs": [{"format": "html", "lang": "zh"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return tmp_path, source, yaml_path


class TestPipelineIntegration:
    def _run_and_get_html(self, tmp_path: pathlib.Path, yaml_path: pathlib.Path) -> str:
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        html_files = list((tmp_path / "dist" / "html").glob("*.html"))
        assert html_files, "no html output produced"
        return html_files[0].read_text(encoding="utf-8")

    def test_html_output_is_normalized(self, project) -> None:
        """zh html (raw layout) reflects the normalized 混排 text."""
        tmp_path, _source, yaml_path = project
        content = self._run_and_get_html(tmp_path, yaml_path)
        assert "该工具支持 ChatGPT 与 Python3.12 的文本处理" in content
        assert "code中文100" in content  # fenced code content untouched (no 空格 inserted)

    def test_source_file_never_modified(self, project) -> None:
        tmp_path, source, yaml_path = project
        before = source.read_text(encoding="utf-8")
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        assert source.read_text(encoding="utf-8") == before
        assert "支持 ChatGPT" not in before

    def test_typography_disabled_keeps_source_text(self, project) -> None:
        tmp_path, _source, yaml_path = project
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        raw["typography"] = {"enabled": False}
        yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        content = self._run_and_get_html(tmp_path, yaml_path)
        assert "该工具支持ChatGPT与Python3.12的文本处理" in content
        assert "该工具支持 ChatGPT 与" not in content

    def test_zh_md_output_binds_to_source_and_is_skipped(self, project) -> None:
        """The zh md output IS the source file → skipped, never written as a copy."""
        tmp_path, source, yaml_path = project
        cfg = ProjectConfig.load(yaml_path)
        stats = SyncPipeline(cfg, log_callback=print).run()
        md_result = next(r for r in stats["outputs"] if r["format"] == "md")
        assert md_result.get("skipped") == "source"
        assert not (tmp_path / "dist" / "md" / "README.md").exists()

    def test_en_md_output_is_normalized(self, en_md_project) -> None:
        """Translated en md output is normalized per English typography rules."""
        tmp_path, source, yaml_path = en_md_project
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        md_files = list((tmp_path / "dist" / "md").glob("*.md"))
        assert md_files, "no en md output produced"
        text = md_files[0].read_text(encoding="utf-8")
        # sloppy cache entries rewritten to correct English spacing
        assert "specifications. It syncs" in text
        assert "formats automatically, including HTML and PDF." in text
        assert "Protect code blocks, inline code and URL links." in text
        # source file untouched
        assert "支持代码块、行内代码与网址链接进行保护，保证内容完整性。" in source.read_text(
            encoding="utf-8"
        )

    def test_en_md_output_typography_disabled(self, en_md_project) -> None:
        """With typography disabled the sloppy translation passes through unchanged."""
        tmp_path, _source, yaml_path = en_md_project
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        raw["typography"] = {"enabled": False}
        yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        md_files = list((tmp_path / "dist" / "md").glob("*.md"))
        assert md_files
        text = md_files[0].read_text(encoding="utf-8")
        assert "specifications .It syncs" in text
        assert "formats automatically ,including HTML and PDF ." in text
        assert "specifications. It syncs" not in text

    def test_en_raw_html_output_is_normalized(self, en_md_project) -> None:
        """zh source + raw layout (markdown schema) + en target html also gets
        English typography applied to the translated body (via the normalize
        hook on _translate_raw_blocks, not the t() per-item hook)."""
        tmp_path, _source, yaml_path = en_md_project
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        raw["outputs"] = [{"format": "html", "lang": "en"}]
        yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        html_files = list((tmp_path / "dist" / "html").glob("*.html"))
        assert html_files, "no en raw html output produced"
        text = html_files[0].read_text(encoding="utf-8")
        # sloppy cache entries rewritten to correct English spacing
        assert "specifications. It syncs" in text
        assert "formats automatically, including HTML and PDF." in text
        assert "Protect code blocks, inline code and URL links." in text

    def test_en_raw_html_typography_disabled(self, en_md_project) -> None:
        """Typography disabled → sloppy translation passes through raw html unchanged."""
        tmp_path, _source, yaml_path = en_md_project
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        raw["outputs"] = [{"format": "html", "lang": "en"}]
        raw["typography"] = {"enabled": False}
        yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        html_files = list((tmp_path / "dist" / "html").glob("*.html"))
        assert html_files
        text = html_files[0].read_text(encoding="utf-8")
        assert "specifications .It syncs" in text
        assert "formats automatically ,including HTML and PDF ." in text
        assert "specifications. It syncs" not in text


class TestStructuredTypography:
    @staticmethod
    def _text(html: str) -> str:
        """Strip HTML tags so metric spans (<span class="metric">) don't
        fragment digits and break substring assertions."""
        return re.sub(r"<[^>]+>", "", html)

    def test_resume_structured_html_normalized(self, resume_project) -> None:
        """Resume (structured) zh html normalizes item content via the t() hook."""
        tmp_path, source, yaml_path = resume_project
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        html_files = list((tmp_path / "dist" / "html").glob("*.html"))
        assert html_files
        text = self._text(html_files[0].read_text(encoding="utf-8"))
        assert "支持 ChatGPT 与 Python3.12 的混排处理" in text
        assert "支持ChatGPT与Python3.12的混排处理" not in text
        # source file untouched
        assert "支持ChatGPT与Python3.12的混排处理" in source.read_text(encoding="utf-8")

    def test_resume_structured_typography_disabled(self, resume_project) -> None:
        tmp_path, source, yaml_path = resume_project
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        raw["typography"] = {"enabled": False}
        yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        cfg = ProjectConfig.load(yaml_path)
        SyncPipeline(cfg, log_callback=print).run()
        text = self._text(
            next((tmp_path / "dist" / "html").glob("*.html")).read_text(encoding="utf-8")
        )
        assert "支持ChatGPT与Python3.12的混排处理" in text
        assert "支持 ChatGPT 与" not in text
