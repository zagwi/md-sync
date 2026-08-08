"""Qt GUI 侧覆盖：中文配置 / 英文配置两张并列卡与全局「文档排版规范」母开关。

原「规范化源文档」按钮已删除——规范化直接作用于生成产物，勾选规则后
自动选中对应的 中文/英文 MD 输出。这里用 offscreen 模式覆盖：

  * 三个顶部卡片各占 1/3：插件管理 / 中文配置（中英文混排规则）/ 英文配置（英文排版规则）
  * 「文档排版规范」母开关位于卡片上方，统一控制两组并列规则
  * 内联规则开关与 TypographyConfig 全部可配置字段一一对应，切换后 _typo_cfg 实时同步
  * 勾选规范规则 → 自动选中 md/zh、md/en 输出（只增不减，不影响其它格式）
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from md_sync.qt_app import TYPO_EN_RULES, TYPO_ZH_RULES, MainWindow
from md_sync.typography import TypographyConfig
from PySide6.QtWidgets import QApplication


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


def test_inline_typo_widgets_cover_all_config_fields(win) -> None:
    """内联规则开关必须与 TypographyConfig 全部可配置字段一一对应。"""
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
    assert set(win._typo_boxes) == config_keys - {"enabled"}


def test_inline_typo_toggle_updates_config(win) -> None:
    """内联开关默认全开；切换后 _typo_cfg 实时同步，且不改动其它规则。"""
    assert win._typo_enabled.isChecked()
    for cb in win._typo_boxes.values():
        assert cb.isChecked()

    win._typo_boxes["cjk_latin_space"].setChecked(False)
    win._typo_enabled.setChecked(False)

    assert win._typo_cfg.enabled is False
    assert win._typo_cfg.cjk_latin_space is False
    assert win._typo_cfg.cjk_digit_space is True


def test_typo_rules_auto_select_md_outputs_by_default(win) -> None:
    """规范规则默认全开 → 启动时自动选中 md/zh 与 md/en；其它格式不受影响。"""
    assert win.fmt_checks[("md", "zh")].isChecked()
    assert win.fmt_checks[("md", "en")].isChecked()
    assert not win.fmt_checks[("html", "zh")].isChecked()
    assert not win.fmt_checks[("pdf", "zh")].isChecked()


def test_typo_rule_toggle_rechecks_matching_md_output(win) -> None:
    """只重新勾选中文规则 → 仅 md/zh 被自动选中，md/en 不受影响（对应关系）。"""
    win.fmt_checks[("md", "zh")].setChecked(False)
    for key, _ in TYPO_EN_RULES:
        win._typo_boxes[key].setChecked(False)
    win.fmt_checks[("md", "en")].setChecked(False)  # 英文规则已全部关闭，取消后不会被自动重选

    zh_cb = win._typo_boxes[TYPO_ZH_RULES[0][0]]
    zh_cb.setChecked(False)
    zh_cb.setChecked(True)  # 触发 _on_typo_changed → 自动重新选中 md/zh

    assert win.fmt_checks[("md", "zh")].isChecked()
    assert not win.fmt_checks[("md", "en")].isChecked()


def test_english_rule_toggle_selects_md_en(win) -> None:
    """英文规则对应 md/en：关闭全部英文规则后再开启一条，md/en 被自动选中。"""
    win.fmt_checks[("md", "en")].setChecked(False)
    for key, _ in TYPO_EN_RULES:
        win._typo_boxes[key].setChecked(False)

    en_cb = win._typo_boxes[TYPO_EN_RULES[0][0]]
    en_cb.setChecked(True)

    assert win.fmt_checks[("md", "en")].isChecked()
