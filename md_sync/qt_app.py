"""Native Qt GUI for md-sync — full replica of the Web dashboard, no HTTP server.

Visual language follows shadcn (neutral zinc palette, white cards, subtle
borders, rounded corners, blue accent) adapted to Qt Style Sheets. The window
is frameless with a custom title bar providing minimize / maximize / close.

Mirrors the web UI feature-for-feature but calls the core pipeline directly:
  · 选择源 Markdown 文件（自动检测源语言）
  · 选择中文 / 英文模板、输出格式（HTML / Markdown / PDF）
  · 「启动多格式同步输出」→ 后台持续同步：源文件一改动（防抖 1.5s）即自动重新生成
  · 输出文件表格：状态点、格式/语言、文件、大小、修改时间(ms)、〔打开〕
  · 同步日志（时间精确到毫秒、生成文件、耗时、错误）
  · 一键打开输出目录

Run:
    python -m md_sync.qt_app
    # or: md-sync gui
"""
from __future__ import annotations

import os
import sys
import time
import traceback

# Allow running as `python md_sync/qt_app.py` directly
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QEvent, QPoint, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QFont, QColor, QPixmap, QPainter, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QSizePolicy,
    QLineEdit, QPushButton, QComboBox, QCheckBox,
    QTextEdit, QLabel, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from md_sync.config import ProjectConfig, OutputConfig, derive_output_path
from md_sync.core.pipeline import SyncPipeline
from md_sync.plugin.registry import PluginRegistry
from md_sync.plugin.interface import DirectoryPlugin, PluginManifest
from md_sync.template.manager import TemplateManager
from md_sync.watcher import FileWatcher

LANG_LABELS = {"zh": "中文", "en": "英文"}

# status colors (shadcn-ish)
C_SYNCED = "#22c55e"     # 已同步（绿）
C_PENDING = "#f59e0b"    # 待同步（黄）
C_MISSING = "#ef4444"    # 文件不存在（红）
C_RUNNING = "#3b82f6"    # 同步中（蓝，动态闪烁）

EDGE_MARGIN = 6


class StatusTag(QWidget):
    """圆点 + 文本 的状态标签；可闪烁以表示动态持续过程。"""

    def __init__(self, color: str = C_SYNCED, text: str = "", pulse: bool = False):
        super().__init__()
        self._color = color
        self._pulse = pulse
        self._alpha = 1.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.label = QLabel(text)
        self.label.setStyleSheet("font-size:12px;font-weight:600;")
        lay.addWidget(self.dot)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self.set_state(color, text, pulse)

    def set_state(self, color: str, text: str = "", pulse: bool = False):
        self._color = color
        self._pulse = pulse
        self.label.setText(text)
        self.label.setStyleSheet(f"font-size:12px;font-weight:600;color:{color};")
        self._apply()

    def set_alpha(self, a: float):
        self._alpha = a
        self._apply()

    @property
    def pulsing(self) -> bool:
        return self._pulse

    def _apply(self):
        c = QColor(self._color)
        c.setAlphaF(self._alpha)
        self.dot.setStyleSheet(
            f"background:{c.name(QColor.HexRgb)};"
            f"border-radius:5px;"
            f"opacity:{self._alpha};"
        )


def _file_status(path: str) -> dict:
    if not path:
        return {"exists": False, "size": 0, "mtime": 0}
    p = Path(path)
    if not p.exists():
        return {"exists": False, "size": 0, "mtime": 0}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": st.st_mtime}


def _status_color(status: dict, source_mtime: float) -> str:
    # A missing output needs to be regenerated → flag as 待同步 (not just
    # "文件不存在"), so the user knows a sync will recreate it.
    if not status["exists"]:
        return C_PENDING
    if source_mtime and source_mtime > status["mtime"]:
        return C_PENDING
    return C_SYNCED


def _status_text(color: str) -> str:
    return {
        C_SYNCED: "已同步",
        C_PENDING: "待同步",
        C_MISSING: "文件不存在",
        C_RUNNING: "同步中…",
    }.get(color, "—")


class TitleBar(QWidget):
    """Custom draggable title bar with minimize / maximize / close."""

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self._parent = parent
        self.setFixedHeight(40)
        self.setObjectName("titlebar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(4)

        self.icon = QLabel("⬡")
        self.icon.setStyleSheet("color:#2563eb;font-size:15px;")
        self.title = QLabel("md-sync · 持续同步")
        self.title.setStyleSheet("font-weight:600;font-size:13px;color:#18181b;")
        layout.addWidget(self.icon)
        layout.addWidget(self.title)

        # 全局状态指示器（红/黄/绿/蓝，可闪烁）
        self.status_pill = StatusTag(C_SYNCED, "未开始")
        # 紧凑显示，避免占位拉伸把窗口标题挤掉/截断
        self.status_pill.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_pill)
        layout.addStretch(1)

        self.btn_min = self._make_btn("—", "title_min", self._parent.showMinimized)
        self.btn_max = self._make_btn("▢", "title_max", self._toggle_max)
        self.btn_close = self._make_btn("✕", "title_close", self._parent.close)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

        self._drag_pos: Optional[QPoint] = None

    def _make_btn(self, text: str, name: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setFixedSize(38, 28)
        b.clicked.connect(slot)
        return b

    def _toggle_max(self):
        if self._parent.isMaximized():
            self._parent.showNormal()
        else:
            self._parent.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._parent.isMaximized():
            self._drag_pos = e.globalPosition().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self._parent.move(self._parent.pos() + e.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = e.globalPosition().toPoint()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()
        super().mouseDoubleClickEvent(e)


class SyncWorker(QThread):
    """Run the conversion in a background thread (pipeline is blocking)."""
    log = Signal(str)
    finished = Signal(bool, str, list)  # (success, message, files)

    def __init__(self, cfg: ProjectConfig):
        super().__init__()
        self.cfg = cfg

    def run(self):
        try:
            self.log.emit("开始同步…")
            t0 = time.time()
            pipe = SyncPipeline(self.cfg, log_callback=lambda msg: self.log.emit(msg))
            result = pipe.run()

            # 检查管道是否有错误（解析失败等）
            errors = result.get("errors", [])
            if errors:
                for err in errors:
                    self.log.emit(f"  ❌ {err}")
                msg = f"同步失败（{time.time()-t0:.1f}s）：管道报告了 {len(errors)} 个错误"
                self.log.emit(msg)
                self.finished.emit(False, msg, [])
                return

            # 检查每个输出文件是否已生成
            ok, missing, files = [], [], []
            for o in self.cfg.outputs:
                targets = [o.path]
                if o.pdf and o.pdf_path:
                    targets.append(o.pdf_path)
                for t in targets:
                    if t and Path(t).exists():
                        ok.append(t)
                        files.append(t)
                    elif t:
                        missing.append(t)

            elapsed = f"{time.time() - t0:.1f}s"
            if missing:
                msg = f"同步结束（{elapsed}），但以下文件未生成：\n" + "\n".join(missing)
                self.log.emit(msg)
                self.finished.emit(False, msg, files)
            else:
                msg = f"同步完成（{elapsed}），生成 {len(ok)} 个文件"
                self.log.emit(msg)
                self.finished.emit(True, msg, files)
        except Exception as e:
            tb = traceback.format_exc()
            self.log.emit("同步失败：\n" + tb)
            self.finished.emit(False, str(e), [])


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("md-sync — 持续同步")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(920, 780)
        self.setMinimumSize(680, 540)

        self.tmgr = TemplateManager()
        self._plugin_registry = PluginRegistry()
        self._last_out_dir: Optional[str] = None
        self.cfg: Optional[ProjectConfig] = None
        self.worker: Optional[SyncWorker] = None
        self.watcher: Optional[FileWatcher] = None
        self.watching = False
        self.source_mtime = 0.0
        self._resizing = None
        self._pending_sync = False
        self._hidden_paths: set = set()  # 仅作 PDF 中间产物的 html，不在列表显示
        self._syncing = False            # 是否正在同步（用于闪烁状态）

        # 状态闪烁动画：定时切换透明度，表现“动态持续过程”
        self._status_tags: list[StatusTag] = []
        self._pulse_on = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)
        self._pulse_timer.start(450)
        self._drag_pos: Optional[QPoint] = None

        self._build_ui()
        self._load_templates()
        self._apply_style()
        self._validate_form()  # 初始：未选定输入/输出 → 启动按钮禁用

    # ── UI construction ────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("content")
        cw = QVBoxLayout(content)
        cw.setContentsMargins(16, 12, 16, 14)
        cw.setSpacing(8)
        self._build_plugin_card(cw)     # Card 1: 插件管理
        self._build_output_card(cw)     # Card 2: 输出设置
        self._build_actions(cw)
        self._build_file_list(cw)       # Card 3: 输出文件
        self._build_log(cw)             # Card 4: 同步日志

        root.addWidget(content, 1)

    def _build_plugin_card(self, parent: QVBoxLayout):
        """Card 1: 插件管理 — 选插件 → 看详情 → 生成模板 → 指定源文件"""
        card = QWidget()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 10)
        cv.setSpacing(6)

        title = QLabel("📦 插件管理")
        title.setObjectName("card_title")
        cv.addWidget(title)

        # ── 插件选择行（固定高度） ──
        sel_row = QWidget()
        sel_row.setFixedHeight(32)
        sel_h = QHBoxLayout(sel_row)
        sel_h.setContentsMargins(0, 0, 0, 0)
        sel_h.setSpacing(8)
        sel_lbl = QLabel("插件包")
        sel_lbl.setFixedWidth(60)
        sel_h.addWidget(sel_lbl)
        self._plugins: list[PluginManifest] = []
        self.plugin_combo = QComboBox()
        self.plugin_combo.currentIndexChanged.connect(self._on_plugin_changed)
        sel_h.addWidget(self.plugin_combo, 1)
        cv.addWidget(sel_row)

        # ── 插件详情区域（选中插件后显示，水平铺满） ──
        self._detail_area = QWidget()
        self._detail_area.setVisible(False)
        da = QVBoxLayout(self._detail_area)
        da.setContentsMargins(0, 2, 0, 0)
        da.setSpacing(2)

        # 第1行：名称 + schema + 版本  ←[stretch]→  🎨 风格
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._detail_name = QLabel()
        self._detail_name.setStyleSheet("font-size:13px;font-weight:700;color:#222;")
        top_row.addWidget(self._detail_name)
        self._detail_schema = QLabel()
        self._detail_schema.setStyleSheet("font-size:10px;color:#1a56db;background:#e8f0fe;padding:1px 6px;border-radius:2px;")
        top_row.addWidget(self._detail_schema)
        self._detail_version = QLabel()
        self._detail_version.setStyleSheet("font-size:10px;color:#999;background:#f5f5f5;padding:1px 6px;border-radius:2px;")
        top_row.addWidget(self._detail_version)
        top_row.addStretch(1)
        self._detail_templates = QLabel()
        self._detail_templates.setStyleSheet("font-size:11px;color:#999;")
        self._detail_templates.setFixedHeight(16)
        top_row.addWidget(self._detail_templates)
        da.addLayout(top_row)

        # 第2行：描述（独占整行宽度，充分利用水平空间）
        desc_row = QHBoxLayout()
        self._detail_desc = QLabel()
        self._detail_desc.setStyleSheet("font-size:11px;color:#888;")
        self._detail_desc.setWordWrap(True)
        self._detail_desc.setMaximumHeight(30)
        desc_row.addWidget(self._detail_desc, 1)
        da.addLayout(desc_row)

        # 一行：生成模板按钮 + 源文件已指定
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._template_btn = QPushButton("📄 生成模板")
        self._template_btn.setObjectName("primary")
        self._template_btn.setFixedHeight(26)
        self._template_btn.clicked.connect(self._generate_and_set_source)
        row2.addWidget(self._template_btn)

        self._source_row = QWidget()
        self._source_row.setVisible(False)
        sr = QHBoxLayout(self._source_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(4)
        check_icon = QLabel("✓")
        check_icon.setStyleSheet("color:#22c55e;font-size:12px;font-weight:700;")
        sr.addWidget(check_icon)
        self._source_label = QLabel()
        self._source_label.setStyleSheet("font-size:11px;color:#555;")
        self._source_label.setWordWrap(True)
        sr.addWidget(self._source_label, 1)
        row2.addWidget(self._source_row, 1)
        row2.addStretch(1)
        da.addLayout(row2)

        cv.addWidget(self._detail_area)
        parent.addWidget(card)

    def _generate_and_set_source(self):
        """生成选中插件的 template.md → 保存 → 设为源文件 → 打开编辑。"""
        idx = self.plugin_combo.currentIndex()
        if idx < 0 or idx >= len(self._plugins):
            return
        plugin = self._plugins[idx]
        if not plugin.directory:
            QMessageBox.warning(self, "错误", f"插件「{plugin.name}」没有本地目录，无法生成模板。")
            return
        dp = DirectoryPlugin(plugin.directory)
        source = dp.get_template_source()
        if not source:
            QMessageBox.warning(self, "无模板", f"插件「{plugin.name}」没有源模板 (template.md)。")
            return

        # 保存对话框
        default_name = f"{plugin.name}-template.md"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存模板文件", default_name,
            "Markdown (*.md);;All files (*)")
        if not save_path:
            return

        Path(save_path).write_text(source, encoding="utf-8")

        # 设为源文件
        self.source_edit.setText(save_path)
        if not self.out_edit.text().strip():
            self.out_edit.setText(str(Path(save_path).parent))

        # 显示源文件路径行
        self._source_label.setText(f"源文件已指定：{save_path}")
        self._source_row.setVisible(True)

        self._append_log(
            f"✓ 模板已保存 → {save_path}\n"
            f"  请编辑文件，然后配置输出并点击「启动多格式同步输出」")

        # 打开编辑器
        QDesktopServices.openUrl(QUrl.fromLocalFile(save_path))

    def _build_output_card(self, parent: QVBoxLayout):
        """Card 2: 输出设置 — 源文件、输出目录、风格、格式"""
        card = QWidget()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 10)
        cv.setSpacing(6)

        title = QLabel("输出设置")
        title.setObjectName("card_title")
        cv.addWidget(title)

        # ── 源文件（固定高度行） ──
        src_row_w = QWidget()
        src_row_w.setFixedHeight(32)
        src_h = QHBoxLayout(src_row_w)
        src_h.setContentsMargins(0, 0, 0, 0)
        src_h.setSpacing(8)
        src_lbl = QLabel("源文件")
        src_lbl.setFixedWidth(60)
        src_h.addWidget(src_lbl)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择或输入 Markdown 源文件")
        src_btn = QPushButton("选择文件…")
        src_btn.setObjectName("primary")
        src_btn.clicked.connect(self._browse_source)
        src_h.addWidget(self.source_edit, 1)
        src_h.addWidget(src_btn)
        cv.addWidget(src_row_w)

        # ── 输出目录（固定高度行） ──
        out_row_w = QWidget()
        out_row_w.setFixedHeight(32)
        out_h = QHBoxLayout(out_row_w)
        out_h.setContentsMargins(0, 0, 0, 0)
        out_h.setSpacing(8)
        out_lbl = QLabel("输出目录")
        out_lbl.setFixedWidth(60)
        out_h.addWidget(out_lbl)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空则输出到源文件所在目录")
        out_btn = QPushButton("选择目录…")
        out_btn.clicked.connect(self._browse_out)
        out_h.addWidget(self.out_edit, 1)
        out_h.addWidget(out_btn)
        cv.addWidget(out_row_w)

        # ── 渲染风格（固定高度行，选插件后显示） ──
        self._style_row_w = QWidget()
        self._style_row_w.setFixedHeight(32)
        self._style_row_w.setVisible(False)
        style_h = QHBoxLayout(self._style_row_w)
        style_h.setContentsMargins(0, 0, 0, 0)
        style_h.setSpacing(8)
        style_lbl = QLabel("渲染风格")
        style_lbl.setFixedWidth(60)
        style_h.addWidget(style_lbl)
        style_h.addWidget(QLabel("中文"))
        self.tpl_zh = QComboBox()
        style_h.addWidget(self.tpl_zh, 1)
        style_h.addWidget(QLabel("英文"))
        self.tpl_en = QComboBox()
        style_h.addWidget(self.tpl_en, 1)
        cv.addWidget(self._style_row_w)

        # ── 输出格式（紧凑 3+2 行） ──
        fmt_label = QLabel("输出格式")
        fmt_label.setStyleSheet("font-size:11px;color:#555;font-weight:600;margin-top:2px;")
        cv.addWidget(fmt_label)

        self.fmt_checks: dict[tuple[str, str], QCheckBox] = {}
        formats = [("html", "HTML"), ("md", "Markdown"), ("pdf", "PDF"),
                   ("docx", "DOCX"), ("epub", "EPUB")]
        fmt_row = QHBoxLayout()
        fmt_row.setContentsMargins(0, 0, 0, 0)
        fmt_row.setSpacing(6)

        for i, (fmt, label_txt) in enumerate(formats):
            fc = QWidget()
            fc.setObjectName("fmt_card")
            fc.setFixedHeight(48)
            fc_lay = QVBoxLayout(fc)
            fc_lay.setContentsMargins(8, 5, 8, 4)
            fc_lay.setSpacing(2)

            ttl = QLabel(label_txt)
            ttl.setObjectName("fmt_card_title")
            fc_lay.addWidget(ttl)

            cb_row = QHBoxLayout()
            cb_row.setContentsMargins(0, 0, 0, 0)
            cb_row.setSpacing(6)
            for lang in ("zh", "en"):
                cb = QCheckBox(LANG_LABELS.get(lang, lang))
                cb.setChecked(False)
                self.fmt_checks[(fmt, lang)] = cb
                cb_row.addWidget(cb)
            cb_row.addStretch(1)
            fc_lay.addLayout(cb_row)

            fmt_row.addWidget(fc, 1)

        cv.addLayout(fmt_row)

        # ── PDF 页边距（固定高度行） ──
        margin_row = QWidget()
        margin_row.setFixedHeight(32)
        mh = QHBoxLayout(margin_row)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.setSpacing(8)
        mh.addWidget(QLabel("PDF 页边距"))
        self.margin_combo = QComboBox()
        for val, label in [("15mm", "15mm（标准）"), ("20mm", "20mm（宽松）"), ("25mm", "25mm（宽边距）")]:
            self.margin_combo.addItem(label, val)
        self.margin_combo.setCurrentIndex(0)
        mh.addWidget(self.margin_combo)
        mh.addStretch(1)
        cv.addWidget(margin_row)

        # 表单变动校验
        self.source_edit.textChanged.connect(self._validate_form)
        self.out_edit.textChanged.connect(self._validate_form)

        parent.addWidget(card)

    def _build_actions(self, parent: QVBoxLayout):
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self.watch_btn = QPushButton("启动多格式同步输出")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.setMinimumWidth(220)
        self.watch_btn.clicked.connect(self._toggle_watch)
        self.watch_btn.setEnabled(False)  # 未选定输入/输出前不可点
        btn_row.addWidget(self.watch_btn)
        btn_row.addStretch(1)
        parent.addLayout(btn_row)

        self.src_info = QLabel("")
        self.src_info.setObjectName("src_info")
        self.src_info.setVisible(False)  # 初始隐藏，有内容时才显示
        parent.addWidget(self.src_info)

    def _build_file_list(self, parent: QVBoxLayout):
        """Card 3: 输出文件"""
        card = QWidget()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 10)
        cv.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("输出文件")
        title.setObjectName("card_title")
        header.addWidget(title)
        header.addStretch(1)
        self.open_btn = QPushButton("打开输出目录")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_out)
        header.addWidget(self.open_btn)
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.clicked.connect(self._refresh_file_list)
        header.addWidget(self.refresh_btn)
        self.clear_all_btn = QPushButton("清除全部")
        self.clear_all_btn.setObjectName("danger")
        self.clear_all_btn.clicked.connect(self._delete_all_output)
        self.clear_all_btn.setEnabled(False)
        header.addWidget(self.clear_all_btn)
        cv.addLayout(header)

        self.file_tbl = QTableWidget(0, 6)
        self.file_tbl.setObjectName("file_table")
        self.file_tbl.setHorizontalHeaderLabels(
            ["状态", "格式", "语言", "文件", "修改时间", "操作"])
        self.file_tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_tbl.setAlternatingRowColors(True)
        self.file_tbl.verticalHeader().setVisible(False)
        self.file_tbl.setMinimumHeight(150)
        self.file_tbl.verticalHeader().setDefaultSectionSize(54)
        self.file_tbl.verticalHeader().setMinimumSectionSize(54)
        th = self.file_tbl.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.Fixed)
        th.setSectionResizeMode(1, QHeaderView.Fixed)
        th.setSectionResizeMode(2, QHeaderView.Fixed)
        th.setSectionResizeMode(3, QHeaderView.Stretch)
        th.setSectionResizeMode(4, QHeaderView.Fixed)
        th.setSectionResizeMode(5, QHeaderView.Fixed)
        self.file_tbl.setColumnWidth(0, 62)
        self.file_tbl.setColumnWidth(1, 80)
        self.file_tbl.setColumnWidth(2, 70)
        self.file_tbl.setColumnWidth(4, 170)
        self.file_tbl.setColumnWidth(5, 200)
        self.file_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tbl.customContextMenuRequested.connect(self._on_file_context)
        self.file_tbl.itemDoubleClicked.connect(self._on_file_activated)
        cv.addWidget(self.file_tbl, 2)

        parent.addWidget(card)

    def _build_log(self, parent: QVBoxLayout):
        """Card 4: 同步日志"""
        card = QWidget()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 10)
        cv.setSpacing(6)
        title_lbl = QLabel("同步日志")
        title_lbl.setObjectName("card_title")
        cv.addWidget(title_lbl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(90)
        cv.addWidget(self.log, 1)

        parent.addWidget(card)

    def _load_templates(self):
        """加载插件包列表，并填充第一个插件的模板。"""
        # 加载插件包
        self._plugins = self._plugin_registry.list_plugins(plugin_type="pack")
        self.plugin_combo.clear()
        for p in self._plugins:
            schema = p.parser_schema or "—"
            label = f"{p.name} (schema: {schema})"
            self.plugin_combo.addItem(label, p.name)
        # 默认选中第一个（应是 builtin-resume）
        if self._plugins:
            self._on_plugin_changed(0)
        else:
            self._reload_style_combos()

    def _on_plugin_changed(self, idx: int):
        """插件包切换时，更新详情区域 + 显示/隐藏风格行。"""
        if idx < 0 or not self._plugins:
            self._detail_area.setVisible(False)
            self._style_row_w.setVisible(False)
            # Also hide source row
            self._source_row.setVisible(False)
            return

        plugin = self._plugins[idx]
        schema = plugin.parser_schema or "resume"

        # ── 更新插件详情 ──
        tpl_list = ", ".join(plugin.templates) if plugin.templates else "系统内置"
        self._detail_name.setText(f"{plugin.name}")
        self._detail_version.setText(f"v{plugin.version}" if plugin.version else "")
        self._detail_schema.setText(f"schema: {schema}")
        self._detail_desc.setText(plugin.description or "")
        self._detail_desc.setToolTip(plugin.description or "")
        self._detail_templates.setText(tpl_list)
        self._detail_area.setVisible(True)

        # ── 隐藏之前生成的源文件路径 ──
        self._source_row.setVisible(False)

        # ── 加载风格下拉框 ──
        try:
            infos = self.tmgr.list_templates(schema=schema)
        except Exception as e:
            self._append_log(f"加载模板失败：{e}")
            infos = []
        self._reload_style_combos(infos)
        self._style_row_w.setVisible(True)

        self._append_log(
            f"已选择插件包「{plugin.name}」schema={schema}，"
            f"风格：{', '.join(t.name for t in infos) if infos else '系统内置'}")

    @staticmethod
    def _typora_theme_icon(css_stem: str) -> QIcon:
        """Extract theme colors from a Typora CSS file and return a preview icon.

        Creates a 14×14 pixmap with two dots side by side:
           left = background color (--bg-color or body background)
           right = text color (--text-color or --control-text-color)

        Returns a blank (grey) icon when the CSS file can't be read or
        no colors are found.
        """
        import re
        css_path = Path.home() / ".config" / "Typora" / "themes" / f"{css_stem}.css"
        bg = "#cccccc"  # fallback grey
        fg = "#333333"

        if css_path.exists():
            try:
                text = css_path.read_text(encoding="utf-8")
                # Extract from :root CSS variables
                m = re.search(r'--bg-color\s*:\s*(#[0-9a-fA-F]{6})\s*;', text)
                if m:
                    bg = m.group(1)
                m = re.search(r'--text-color\s*:\s*(#[0-9a-fA-F]{6})\s*;', text)
                if m:
                    fg = m.group(1)
                # Fallback: body background
                if bg == "#cccccc":
                    m = re.search(r'body\s*\{[^}]*background(?:-color)?:\s*(#[0-9a-fA-F]{6})', text)
                    if m:
                        bg = m.group(1)
            except Exception:
                pass

        # Render a 14×14 pixmap with two 5px circles
        pix = QPixmap(14, 14)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(bg))
        p.drawEllipse(0, 4, 6, 6)
        p.setBrush(QColor(fg))
        p.drawEllipse(8, 4, 6, 6)
        p.end()
        return QIcon(pix)

    def _reload_style_combos(self, infos: Optional[list] = None):
        """填充风格下拉框。包含 Typora 主题的色点预览图标。"""
        for combo in (self.tpl_zh, self.tpl_en):
            combo.clear()
        if infos:
            for t in infos:
                icon = QIcon()  # default empty icon for non-typora templates
                if t.name.startswith("typora-"):
                    css_stem = t.name[7:]  # strip "typora-" prefix
                    icon = self._typora_theme_icon(css_stem)
                self.tpl_zh.addItem(icon, t.label, t.name)
                self.tpl_en.addItem(icon, t.label, t.name)
            for combo in (self.tpl_zh, self.tpl_en):
                idx = combo.findData("bwx")
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        else:
            for combo in (self.tpl_zh, self.tpl_en):
                combo.addItem("(默认 bwx)", "bwx")

    # ── Styling (shadcn-inspired) ─────────────────────────────────────
    def _apply_style(self):
        font = QFont("PingFang SC", 10) if "PingFang SC" in QFont().families() else QFont()
        self.setFont(font)
        qss = """
        QWidget {
            background: #fafafa;
            color: #18181b;
            font-size: 13px;
        }
        #titlebar {
            background: #ffffff;
            border-bottom: 1px solid #e5e7eb;
        }
        #content { background: #fafafa; }
        QLabel#section_title { color:#18181b; font-weight:700; font-size:14px; }
        /* 卡片（带内部标题，标题不会被圆角边框裁剪/遮挡） */
        QWidget#card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
        }
        QLabel#card_title { color:#52525b; font-weight:700; font-size:14px; }
        QWidget#fmt_card {
            background: #f8f9fb;
            border: 1px solid #e8ebf0;
            border-radius: 10px;
        }
        QWidget#fmt_card:hover {
            border: 1px solid #cbd5e1;
            background: #f5f7fa;
        }
        QLabel#fmt_card_title { color:#18181b; font-weight:700; font-size:13px; }
        QLabel#src_info { color:#71717a; font-size:12px; }
        QLineEdit {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 7px 10px;
        }
        QLineEdit:focus { border: 1px solid #2563eb; }
        QLineEdit::placeholder { color: #a1a1aa; }
        QComboBox {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 6px 10px;
        }
        QComboBox::drop-down { border: none; width: 18px; }
        QComboBox QAbstractItemView {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            selection-background-color: #eff6ff;
            border-radius: 8px;
        }
        QCheckBox { spacing: 7px; color: #27272a; font-size: 13px; }
        QCheckBox::indicator {
            width: 17px; height: 17px;
            border: 1.5px solid #cbd5e1; border-radius: 5px; background: #ffffff;
        }
        QCheckBox::indicator:checked {
            background: #4f46e5; border: 1.5px solid #4f46e5;
            image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMiIgaGVpZ2h0PSIxMiIgdmlld0JveD0iMCAwIDEyIDEyIj48cGF0aCBkPSJNMSAxLjVMNC41IDUgMTAgMSIgc3Ryb2tlPSIjZmZmIiBzdHJva2Utd2lkdGg9IjIiIGZpbGw9Im5vbmUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg==);
        }
        /* 输出设置：分组卡片内部标题已在上方定义，这里仅保留按钮样式 */
        QPushButton {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 7px 16px;
            color: #18181b;
        }
        QPushButton:hover { background: #f4f4f5; }
        QPushButton:pressed { background: #e4e4e7; }
        QPushButton:disabled { color: #a1a1aa; background: #fafafa; }
        QPushButton#primary {
            background: #2563eb; border: 1px solid #2563eb; color: #ffffff; font-weight: 500;
        }
        QPushButton#primary:hover { background: #1d4ed8; border-color: #1d4ed8; }
        QPushButton#primary:disabled { background:#93c5fd; border-color:#93c5fd; }
        QTextEdit {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 10px;
            font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
            font-size: 12px;
            color: #3f3f46;
        }
        QTableWidget {
            background: #ffffff;
            border: 1px solid #eceef2;
            border-radius: 14px;
            gridline-color: #f5f6f8;
            outline: 0;
            font-size: 13px;
            selection-background-color: #f5f8ff;
        }
        QTableWidget::item { padding: 9px 10px; border: none; }
        QTableWidget::item:selected { color: #18181b; }
        QTableWidget::item:hover { background: #f8fafc; }
        QHeaderView::section {
            background: transparent;
            border: none;
            border-bottom: 1px solid #eef0f3;
            padding: 11px 12px;
            color: #9aa3b2;
            font-weight: 600;
            font-size: 11px;
            letter-spacing: 1px;
        }
        /* 中文 / 英文模板 列标题 */
        #col_title_zh, #col_title_en {
            font-size: 13px;
            font-weight: 700;
            color: #1f2937;
            padding: 7px 16px;
            border-radius: 10px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 #f6f8ff);
            border: 1px solid #e8ebf2;
        }
        #col_title_zh { border-left: 3px solid #3b82f6; }
        #col_title_en { border-left: 3px solid #8b5cf6; }
        QPushButton#cell_btn {
            background: #ffffff;
            border: 1px solid #e6e8ef;
            border-radius: 9px;
            padding: 5px 12px;
            color: #4f46e5;
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#cell_btn:hover { background: #eef2ff; border-color: #6366f1; color: #4338ca; }
        QPushButton#cell_btn_danger {
            background: #ffffff;
            border: 1px solid #fecaca;
            border-radius: 9px;
            padding: 5px 12px;
            color: #ef4444;
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#cell_btn_danger:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }
        QPushButton#danger {
            background: #ffffff;
            border: 1px solid #fecaca;
            border-radius: 8px;
            padding: 7px 16px;
            color: #ef4444;
            font-size: 13px;
            font-weight: 500;
        }
        QPushButton#danger:hover { background: #fef2f2; border-color: #fca5a5; }
        QPushButton#danger:disabled { color: #fca5a5; border-color: #fee2e2; background: #fafafa; }
        /* 格式 tag */
        .tag_fmt {
            background: #eef2ff; color: #4f46e5; border: 1px solid #c7d2fe;
            border-radius: 7px; padding: 3px 10px; font-size: 11px;
            font-weight: 700; letter-spacing: 0.3px; min-width: 48px;
            text-align: center;
        }
        /* 语言 tag */
        .tag_lang {
            background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0;
            border-radius: 7px; padding: 3px 10px; font-size: 11px;
            font-weight: 700; letter-spacing: 0.3px; min-width: 40px;
            text-align: center;
        }
        /* 文件单元格：文件名 + 元信息两行 */
        #file_cell { background: transparent; }
        #file_name { font-size: 14px; font-weight: 600; color: #1f2937; line-height: 18px; }
        #file_meta { font-size: 11px; color: #aeb4c0; }
        #tag_cell { background: transparent; }
        QPushButton#title_min, QPushButton#title_max, QPushButton#title_close {
            background: transparent; border: none; border-radius: 8px;
            font-size: 14px; color: #52525b;
        }
        QPushButton#title_min:hover, QPushButton#title_max:hover { background: #f4f4f5; }
        QPushButton#title_close:hover { background: #ef4444; color: #ffffff; }
        """
        self.setStyleSheet(qss)

    # ── Dialogs ────────────────────────────────────────────────────────
    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Markdown 文件", "", "Markdown (*.md);;All files (*)")
        if path:
            self.source_edit.setText(path)
            if not self.out_edit.text().strip():
                self.out_edit.setText(str(Path(path).parent))
            self._validate_form()

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_edit.setText(d)
            self._validate_form()

    # ── Build config from the form ─────────────────────────────────────
    def _validate_form(self):
        """只有选定了源文件（且存在）并填好输出位置，「启动多格式同步输出」才可点。"""
        if self.watching:
            return  # 监听中不改按钮状态
        src = self.source_edit.text().strip()
        out = self.out_edit.text().strip()
        ready = bool(src) and Path(src).expanduser().exists() and bool(out)
        self.watch_btn.setEnabled(ready)

    def _build_config(self) -> Optional[ProjectConfig]:
        src = self.source_edit.text().strip()
        if not src:
            QMessageBox.warning(self, "缺少源文件", "请先选择 Markdown 源文件。")
            return None
        src_path = Path(src).expanduser()
        if not src_path.exists():
            QMessageBox.warning(self, "文件不存在", f"源文件不存在：\n{src_path}")
            return None

        stem = src_path.stem
        out_root = self.out_edit.text().strip()
        root = Path(out_root).expanduser().resolve() if out_root else src_path.parent
        root.mkdir(parents=True, exist_ok=True)

        sel = {key: cb.isChecked() for key, cb in self.fmt_checks.items()}
        all_formats = ["html", "md", "pdf", "docx", "epub"]
        langs = [l for l in ("zh", "en")
                 if any(sel[(f, l)] for f in all_formats)]
        if not langs:
            QMessageBox.warning(
                self, "缺少输出", "请至少为一种格式勾选一种语言。")
            return None

        zh_style = self.tpl_zh.currentData()
        en_style = self.tpl_en.currentData() or zh_style

        # 输出文件名：稳定、不含时间戳，仅用语言代码区分多语言，避免：
        #   1) zh/en 重名冲突；2) 每次同步生成新文件导致 watcher 死循环/堆积。
        # 例如源文件 Foo.md → Foo-zh.html / Foo-en.html（命名一致，都带语言后缀）。
        name_map = {lang: f"{stem}-{lang}" for lang in langs}

        outputs = []
        self._hidden_paths = set()
        for lang in langs:
            style = zh_style if lang == "zh" else en_style
            want_html = sel[("html", lang)]
            want_md = sel[("md", lang)]
            want_pdf = sel[("pdf", lang)]
            want_docx = sel[("docx", lang)]
            want_epub = sel[("epub", lang)]
            if want_html or want_pdf:
                html_path = derive_output_path(root, "html", lang, name_map, stem)
                pdf_path = (
                    derive_output_path(root, "html", lang, name_map, stem, pdf=True)
                    if want_pdf else None
                )
                outputs.append(OutputConfig(
                    format="html", lang=lang, path=html_path,
                    pdf=want_pdf, pdf_path=pdf_path, style=style,
                    page_margin=self.margin_combo.currentData()))
                if not want_html:
                    self._hidden_paths.add(html_path)
            if want_md:
                md_path = derive_output_path(root, "md", lang, name_map, stem)
                outputs.append(OutputConfig(
                    format="md", lang=lang, path=md_path, style=style))
            if want_docx:
                docx_path = derive_output_path(root, "docx", lang, name_map, stem)
                outputs.append(OutputConfig(
                    format="docx", lang=lang, path=docx_path, style=style))
            if want_epub:
                epub_path = derive_output_path(root, "epub", lang, name_map, stem)
                outputs.append(OutputConfig(
                    format="epub", lang=lang, path=epub_path, style=style))

        cfg = ProjectConfig(
            project=stem, source=str(src_path),
            schema=self._current_schema(),
            outputs=outputs, output_root=str(root), source_lang="zh",
            name_map=name_map)
        cfg.source_path = src_path.resolve()
        self._last_out_dir = str(root)
        return cfg

    def _current_schema(self) -> str:
        """获取当前选中插件包的 schema。"""
        idx = self.plugin_combo.currentIndex()
        if 0 <= idx < len(self._plugins):
            return self._plugins[idx].parser_schema or "resume"
        return "resume"

    # ── Watching (continuous sync) ─────────────────────────────────────
    def _toggle_watch(self):
        if self.watching:
            self._stop_watch()
            # 不 return — 直接 fall through 重新启动
        cfg = self._build_config()
        if cfg is None:
            return
        self.cfg = cfg
        self.source_mtime = cfg.source_path.stat().st_mtime
        self._refresh_src_info()
        self._run_sync()
        self.watcher = FileWatcher(
            cfg.source_path, self._on_source_changed, debounce=1.5,
            output_root=cfg.output_root)
        self.watcher.start()
        self.watching = True
        self.watch_btn.setText("重启输出")
        self.watch_btn.setStyleSheet("background:#ef4444;border:1px solid #ef4444;color:#fff;font-weight:500;")
        self.open_btn.setEnabled(True)
        self._update_status_pill()
        self._append_log(f"─ 已启动多格式同步输出：{cfg.source_path.name}（改动即自动同步）")

    def _stop_watch(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.watching = False
        self._pending_sync = False
        self.watch_btn.setText("启动多格式同步输出")
        self.watch_btn.setStyleSheet("")
        self._validate_form()  # 停止后按当前表单状态恢复可用性
        self._update_status_pill()
        self._append_log("─ 监听已停止")

    def _on_source_changed(self, path: Path):
        p = Path(path)
        # Only the source MD file should trigger a sync. Edits to generated
        # outputs must be ignored (watching them caused an infinite loop).
        if self.cfg and p.resolve() != self.cfg.source_path.resolve():
            return
        self.source_mtime = p.stat().st_mtime
        self._append_log(f"· 检测到源文件改动：{p.name}，正在重新同步…")
        self._run_sync()

    def _run_sync(self):
        if self.worker is not None and self.worker.isRunning():
            # A sync is in progress — mark a re-run so we don't lose this edit.
            self._pending_sync = True
            self._append_log("  （上一次同步仍在进行，已标记待补跑）")
            return
        self._syncing = True
        self._update_status_pill()
        self._refresh_file_list()  # 立即显示“同步中…”闪烁
        self.worker = SyncWorker(self.cfg)
        self.worker.log.connect(self._append_log)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_finished(self, success: bool, msg: str, files: list):
        self._syncing = False
        if self._last_out_dir:
            self.open_btn.setEnabled(True)
        self._update_status_pill()
        self._refresh_file_list()
        if not success:
            QMessageBox.critical(self, "同步失败", msg)
        # If a change arrived while we were syncing, re-run to capture it.
        if self.watching and self._pending_sync:
            self._pending_sync = False
            self._append_log("· 期间有新改动，补跑一次同步…")
            self._run_sync()

    def _pulse_step(self):
        """定时切换透明度，让处于 pulsing 状态的标签闪烁。"""
        self._pulse_on = not self._pulse_on
        alpha = 1.0 if self._pulse_on else 0.35
        for tag in self._status_tags:
            if tag.pulsing:
                tag.set_alpha(alpha)
        if self.title_bar.status_pill.pulsing:
            self.title_bar.status_pill.set_alpha(alpha)

    def _update_status_pill(self):
        """刷新标题栏全局状态指示器。"""
        pill = self.title_bar.status_pill
        if self._syncing:
            pill.set_state(C_RUNNING, "同步中…", pulse=True)
        elif self.watching:
            # 监听中：有任一待同步则黄，否则绿
            pending = False
            if self.cfg:
                for path, _, _, _ in self._iter_output_files():
                    if _status_color(_file_status(path), self.source_mtime) == C_PENDING:
                        pending = True
                        break
            if pending:
                pill.set_state(C_PENDING, "待同步", pulse=True)
            else:
                pill.set_state(C_SYNCED, "监听中", pulse=False)
        elif self.cfg is None:
            pill.set_state(C_MISSING, "未开始", pulse=False)
        else:
            pill.set_state(C_SYNCED, "已就绪", pulse=False)

    # ── File list (table) ──────────────────────────────────────────────
    @staticmethod
    def _fmt_mtime(mtime: float) -> str:
        if not mtime:
            return "--"
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"

    def _iter_output_files(self):
        """按语言分组输出文件： yield (path, fmt, color, lang)"""
        if self.cfg is None:
            return
        groups: dict[str, list] = {}
        for o in self.cfg.outputs:
            targets = [(o.path, o.format.upper())]
            if o.pdf and o.pdf_path:
                targets.append((o.pdf_path, "PDF"))
            lang = o.lang
            for t, fmt in targets:
                if not t or t in self._hidden_paths:
                    continue
                st = _file_status(t)
                color = _status_color(st, self.source_mtime)
                groups.setdefault(lang, []).append((t, fmt, color))
        for lang in ("zh", "en"):
            if lang in groups:
                for path, fmt, color in groups[lang]:
                    yield path, fmt, color, lang

    def _refresh_file_list(self):
        # 清空单表并重新注册本帧状态标签
        self.file_tbl.setRowCount(0)
        self._status_tags = []
        if self.cfg is None:
            return
        if self.cfg.source_path.exists():
            self.source_mtime = self.cfg.source_path.stat().st_mtime
        any_row = False
        for path, fmt, color, lang in self._iter_output_files():
            any_row = True
            p = Path(path)
            exists = p.exists()
            size = f"{p.stat().st_size // 1024}KB" if exists else "--"
            if self._syncing:
                color = C_RUNNING
            st_text = _status_text(color)
            pulse = self._syncing or color == C_PENDING
            row = self.file_tbl.rowCount()
            self.file_tbl.insertRow(row)
            self.file_tbl.setRowHeight(row, 54)

            status = StatusTag(color, st_text, pulse=pulse)
            self.file_tbl.setCellWidget(row, 0, status)
            self._status_tags.append(status)

            # 格式 badge
            fmt_tag = QLabel(fmt)
            fmt_tag.setObjectName("tag_fmt")
            fmt_tag.setProperty("class", "tag")
            fmt_tag.setAlignment(Qt.AlignCenter)
            fmt_tag.setMinimumWidth(48)
            fmt_cell = QWidget()
            fmt_cell.setObjectName("tag_cell")
            tl = QHBoxLayout(fmt_cell)
            tl.setContentsMargins(8, 0, 8, 0)
            tl.setSpacing(6)
            tl.addWidget(fmt_tag)
            tl.addStretch(1)
            self.file_tbl.setCellWidget(row, 1, fmt_cell)

            # 语言 badge
            lang_tag = QLabel(LANG_LABELS.get(lang, lang))
            lang_tag.setObjectName("tag_lang")
            lang_tag.setProperty("class", "tag")
            lang_tag.setAlignment(Qt.AlignCenter)
            lang_tag.setMinimumWidth(40)
            lang_cell = QWidget()
            lang_cell.setObjectName("tag_cell")
            ll = QHBoxLayout(lang_cell)
            ll.setContentsMargins(8, 0, 8, 0)
            ll.setSpacing(6)
            ll.addWidget(lang_tag)
            ll.addStretch(1)
            self.file_tbl.setCellWidget(row, 2, lang_cell)

            # 文件名 + 元信息
            file_cell = QWidget()
            file_cell.setObjectName("file_cell")
            file_cell.setProperty("path", path)
            fc = QVBoxLayout(file_cell)
            fc.setContentsMargins(6, 6, 6, 6)
            fc.setSpacing(4)
            name_lbl = QLabel(p.name)
            name_lbl.setObjectName("file_name")
            name_lbl.setToolTip(path)
            name_lbl.setWordWrap(True)
            meta_lbl = QLabel(
                f"{size}" if exists else "— 尚未生成")
            meta_lbl.setObjectName("file_meta")
            fc.addWidget(name_lbl)
            fc.addWidget(meta_lbl)
            self.file_tbl.setCellWidget(row, 3, file_cell)

            # 修改时间
            ts_lbl = QLabel(self._fmt_mtime(p.stat().st_mtime) if exists else "--")
            ts_lbl.setStyleSheet("font-size:12px;color:#71717a;padding:0 8px;")
            ts_cell = QWidget()
            ts_cell.setObjectName("tag_cell")
            ts_lo = QHBoxLayout(ts_cell)
            ts_lo.setContentsMargins(8, 0, 4, 0)
            ts_lo.addWidget(ts_lbl)
            ts_lo.addStretch(1)
            self.file_tbl.setCellWidget(row, 4, ts_cell)

            # 操作按钮行
            ops_cell = QWidget()
            ops_cell.setObjectName("tag_cell")
            ops_lo = QHBoxLayout(ops_cell)
            ops_lo.setContentsMargins(4, 0, 4, 0)
            ops_lo.setSpacing(4)

            open_btn = QPushButton("打开")
            open_btn.setObjectName("cell_btn")
            open_btn.clicked.connect(self._on_open_clicked)
            open_btn._file_path = path
            ops_lo.addWidget(open_btn)

            del_btn = QPushButton("删除")
            del_btn.setObjectName("cell_btn_danger")
            del_btn.clicked.connect(self._on_delete_clicked)
            del_btn._file_path = path
            ops_lo.addWidget(del_btn)

            self.file_tbl.setCellWidget(row, 5, ops_cell)
        self.clear_all_btn.setEnabled(any_row)
        if not any_row:
            self.file_tbl.insertRow(0)
            self.file_tbl.setItem(0, 0, QTableWidgetItem("（未配置输出）"))
        if not self._syncing:
            self._update_status_pill()

    def _refresh_src_info(self):
        if self.cfg is None:
            self.src_info.setText("")
            self.src_info.setVisible(False)
            return
        try:
            info = SyncPipeline(self.cfg).run_dry()
            sl = info.get("source_lang", "") or "?"
            tl = "en" if sl == "zh" else ("zh" if sl == "en" else "?")
            pend = info.get("pending_translations", [])
            pend_txt = "、".join(
                f"{LANG_LABELS.get(p.get('lang'), p.get('lang'))} 待译 {p.get('missing')} 条"
                for p in pend) or "无"
            secs = len(info.get("sections", []))
            self.src_info.setText(
                f"源语言：{LANG_LABELS.get(sl, sl)} → 目标：{LANG_LABELS.get(tl, tl)} ｜ "
                f"章节 {secs} ｜ {pend_txt} ｜ 文件：{self.cfg.source_path.name}")
            self.src_info.setVisible(True)
        except Exception as e:
            self.src_info.setText(f"源语言检测失败：{e}")
            self.src_info.setVisible(True)

    # ── Open / copy ────────────────────────────────────────────────────
    def _delete_output_file(self, path: str):
        """删除单个输出文件并刷新列表。"""
        if not path or not Path(path).exists():
            return
        name = Path(path).name
        ret = QMessageBox.question(
            self, "删除文件",
            f"确定要删除「{name}」吗？\n{path}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            Path(path).unlink(missing_ok=True)
            self._append_log(f"🗑 已删除：{name}")
            self._refresh_file_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"无法删除文件：\n{e}")

    def _delete_all_output(self):
        """删除所有输出文件并刷新列表。"""
        paths = []
        if self.cfg:
            for o in self.cfg.outputs:
                if o.path:
                    paths.append(o.path)
                if o.pdf and o.pdf_path:
                    paths.append(o.pdf_path)
        # deduplicate and filter to only existing files
        unique = sorted(set(p for p in paths if p and Path(p).exists()))
        if not unique:
            self._append_log("没有可删除的输出文件")
            return

        names = "\n".join(Path(p).name for p in unique)
        ret = QMessageBox.question(
            self, "清除全部输出文件",
            f"确定要删除以下 {len(unique)} 个文件吗？\n\n{names}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        deleted = 0
        failed = []
        for p in unique:
            try:
                Path(p).unlink(missing_ok=True)
                deleted += 1
            except Exception as e:
                failed.append(f"{Path(p).name}: {e}")
        self._append_log(f"🗑 已清除 {deleted} 个文件" + (f"，失败：{failed}" if failed else ""))
        self._refresh_file_list()
        if failed:
            QMessageBox.warning(self, "部分删除失败", "\n".join(failed))

    def _open_out(self):
        if self._last_out_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_out_dir))

    def _on_open_clicked(self):
        """Slot for the 打开 button in the output file table."""
        btn = self.sender()
        path = getattr(btn, '_file_path', None) if btn else None
        self._open_file(path)

    def _on_delete_clicked(self):
        """Slot for the 删除 button in the output file table."""
        btn = self.sender()
        path = getattr(btn, '_file_path', None) if btn else None
        self._delete_output_file(path)

    def _open_file(self, path):
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _on_file_activated(self, item: QTableWidgetItem):
        if item is None or self.file_tbl is None:
            return
        cell = self.file_tbl.cellWidget(item.row(), 3)
        if cell is None:
            return
        self._open_file(cell.property("path"))

    def _on_file_context(self, pos):
        item = self.file_tbl.itemAt(pos)
        if item is None:
            return
        cell = self.file_tbl.cellWidget(item.row(), 3)
        if cell is None:
            return
        path = cell.property("path")
        if not path:
            return
        menu = self.file_tbl.createStandardContextMenu(pos)
        act_open = menu.addAction("打开文件")
        act_copy = menu.addAction("复制路径")
        act_delete = menu.addAction("删除文件")
        choice = menu.exec(self.file_tbl.mapToGlobal(pos))
        if choice == act_open:
            self._open_file(path)
        elif choice == act_copy:
            QApplication.clipboard().setText(path)
        elif choice == act_delete:
            self._delete_output_file(path)

    # ── Logging ────────────────────────────────────────────────────────
    def _append_log(self, msg: str):
        now = datetime.now()
        ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        self.log.append(f"[{ts}] {msg}")

    # ── Frameless drag + edge resize + maximize icon ───────────────────
    def changeEvent(self, e: QEvent):
        if e.type() == QEvent.Type.WindowStateChange:
            self.title_bar.btn_max.setText("❐" if self.isMaximized() else "▢")
        super().changeEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.isMaximized():
            edge = self._edge_at(e.pos())
            if edge:
                self._resizing = edge
                self._drag_pos = e.globalPosition().toPoint()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing and e.buttons() & Qt.LeftButton:
            self._do_resize(e.globalPosition().toPoint())
            return
        if not self.isMaximized():
            self._update_cursor(e.pos())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = None
        super().mouseReleaseEvent(e)

    def _edge_at(self, pos: QPoint) -> Optional[str]:
        r = self.rect()
        x, y = pos.x(), pos.y()
        if x < EDGE_MARGIN:
            edge = "l"
        elif x >= r.width() - EDGE_MARGIN:
            edge = "r"
        else:
            edge = ""
        if y < EDGE_MARGIN:
            edge += "t"
        elif y >= r.height() - EDGE_MARGIN:
            edge += "b"
        return edge or None

    def _do_resize(self, gpos: QPoint):
        geo = self.geometry()
        if "l" in self._resizing:
            geo.setLeft(min(gpos.x(), geo.right() - 200))
        if "r" in self._resizing:
            geo.setRight(gpos.x())
        if "t" in self._resizing:
            geo.setTop(min(gpos.y(), geo.bottom() - 160))
        if "b" in self._resizing:
            geo.setBottom(gpos.y())
        self.setGeometry(geo)

    def _update_cursor(self, pos: QPoint):
        edge = self._edge_at(pos)
        if edge is None:
            self.setCursor(Qt.ArrowCursor)
            return
        if ("l" in edge and "t" in edge) or ("r" in edge and "b" in edge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif ("r" in edge and "t" in edge) or ("l" in edge and "b" in edge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "l" in edge or "r" in edge:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.SizeVerCursor)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
