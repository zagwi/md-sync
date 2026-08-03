"""Qt GUI 侧覆盖：✂ 规范化源文档 与 📐 文档标准配置。

Web UI 已废弃（md_sync/web 已删除），原 test_source_normalize.py 覆盖的
「规范化源文档」行为由 Qt GUI 承载，这里用 offscreen 模式做等值覆盖：

  * _normalize_source：生成 <stem>_normalized.md、原文件不动、源文件重指向、
    自动勾选 md/<源语言> 输出、幂等重复点击不产生 *_normalized_normalized.md
  * TypographyDialog：8 条排版规则开关的读写 roundtrip
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pathlib

import pytest
from md_sync.qt_app import MainWindow, TypographyDialog
from md_sync.typography import TypographyConfig
from PySide6.QtWidgets import QApplication

MESSY_Z = (
    "这是一个用于规范化源文档测试的项目，该项目支持从Markdown源文件自动同步为多种格式输出，"
    "并遵循国家标准进行排版处理。该工具支持ChatGPT与Python3.12的文本处理，"
    "同时支持对代码块、行内代码与网址链接进行保护，保证内容完整性与一致性。"
    "使用该工具时需要注意，代码块与链接中的内容不会被规范化处理，"
    "这是为了保证源代码与超链接的完整性，避免破坏原有格式。"
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp, monkeypatch):
    """构造主窗口并屏蔽模态对话框（offscreen 下弹框会阻塞测试）。"""
    monkeypatch.setattr("md_sync.qt_app.QMessageBox.information", lambda *a, **k: None)
    monkeypatch.setattr("md_sync.qt_app.QMessageBox.warning", lambda *a, **k: None)
    monkeypatch.setattr("md_sync.qt_app.QMessageBox.critical", lambda *a, **k: None)
    return MainWindow()


def test_normalize_creates_file_repoints_source_and_checks_md(win, tmp_path: pathlib.Path) -> None:
    src = tmp_path / "README.md"
    src.write_text(MESSY_Z, encoding="utf-8")
    win.source_edit.setText(str(src))
    win.out_edit.setText(str(tmp_path / "dist"))

    win._normalize_source()

    target = tmp_path / "README_normalized.md"
    assert target.exists(), "normalized file not created"
    assert "支持 ChatGPT 与 Python3.12" in target.read_text(encoding="utf-8")

    # 原文件不被修改
    assert "支持ChatGPT与Python3.12" in src.read_text(encoding="utf-8")

    # 源文件重指向到规范化文件，并自动勾选 md/zh 输出
    assert win.source_edit.text() == str(target)
    assert win.fmt_checks[("md", "zh")].isChecked()


def test_normalize_is_idempotent(win, tmp_path: pathlib.Path) -> None:
    src = tmp_path / "README.md"
    src.write_text(MESSY_Z, encoding="utf-8")
    win.source_edit.setText(str(src))
    win.out_edit.setText(str(tmp_path / "dist"))

    win._normalize_source()
    # 第二次点击：源已是 *_normalized，原地重生成且内容不变
    win._normalize_source()
    assert not (tmp_path / "README_normalized_normalized.md").exists()
    assert (tmp_path / "README_normalized.md").exists()


def test_normalize_warns_when_source_missing(win, tmp_path: pathlib.Path) -> None:
    win.source_edit.setText("")
    win._normalize_source()  # 缺源文件 → warning（已被 patch 为 no-op），不抛异常


def test_typography_dialog_roundtrip(qapp) -> None:
    cfg = TypographyConfig()
    dlg = TypographyDialog(cfg)
    assert dlg._enabled.isChecked()
    for cb in dlg._boxes.values():
        assert cb.isChecked()

    # 关掉部分规则后 config() 应返回带差异的配置，且不改动传入对象
    dlg._boxes["cjk_latin_space"].setChecked(False)
    dlg._enabled.setChecked(False)
    out = dlg.config()
    assert out.enabled is False
    assert out.cjk_latin_space is False
    assert out.cjk_digit_space is True
    assert cfg.cjk_latin_space is True  # 原对象不受影响


def test_typography_dialog_covers_all_config_fields(qapp) -> None:
    """对话框的开关必须与 TypographyConfig 全部可配置字段一一对应。"""
    from dataclasses import fields

    config_keys = {f.name for f in fields(TypographyConfig)}
    assert config_keys == {
        "enabled",
        "cjk_latin_space",
        "cjk_digit_space",
        "number_unit_space",
        "fullwidth_punct_no_space",
        "en_no_space_before_punct",
        "en_space_after_punct",
        "en_collapse_spaces",
    }
    dlg = TypographyDialog(TypographyConfig())
    assert set(dlg._boxes) == config_keys - {"enabled"}
