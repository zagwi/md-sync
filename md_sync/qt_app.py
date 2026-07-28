"""Native Qt GUI for md-sync — full replica of the Web dashboard, no HTTP server.

Visual language follows shadcn (neutral zinc palette, white cards, subtle
borders, rounded corners, blue accent) adapted to Qt Style Sheets. The window
is frameless with a custom title bar providing minimize / maximize / close.

Mirrors the web UI feature-for-feature but calls the core pipeline directly:
  · 选择源 Markdown 文件（自动检测源语言）
  · 选择中文 / 英文模板、输出格式（HTML / Markdown / PDF）
  · 「开始监听」→ 后台持续同步：源文件一改动（防抖 1.5s）即自动重新生成
  · 输出文件表格：状态点、格式/语言、文件、大小、修改时间(ms)、〔打开〕
  · 同步日志（时间精确到毫秒、生成文件、耗时、错误）
  · 一键打开输出目录

Run:
    python -m md_sync.qt_app
    # or: md-sync gui
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QEvent, QPoint, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QFont, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSizePolicy,
    QLineEdit, QPushButton, QComboBox, QCheckBox,
    QTextEdit, QLabel, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from md_sync.config import ProjectConfig, OutputConfig, derive_output_path
from md_sync.core.pipeline import SyncPipeline
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
            SyncPipeline(self.cfg).run()

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
        self._validate_form()  # 初始：未选定输入/输出 → 开始监听禁用

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
        cw.setContentsMargins(18, 16, 18, 18)
        cw.setSpacing(14)
        self._build_form(cw)
        self._build_options(cw)
        self._build_actions(cw)
        self._build_file_list(cw)
        self._build_log(cw)

        root.addWidget(content, 1)

    def _build_form(self, parent: QVBoxLayout):
        card = QWidget()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 16, 16, 14)
        cv.setSpacing(12)

        title = QLabel("项目")
        title.setObjectName("card_title")
        cv.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择 Markdown 源文件")
        src_btn = QPushButton("选择文件…")
        src_btn.setObjectName("primary")
        src_btn.clicked.connect(self._browse_source)
        src_row = QHBoxLayout(); src_row.setSpacing(8)
        src_row.addWidget(self.source_edit, 1)
        src_row.addWidget(src_btn)
        form.addRow("源文件", src_row)

        self.tpl_zh = QComboBox()
        self.tpl_en = QComboBox()
        form.addRow("中文模板", self.tpl_zh)
        form.addRow("英文模板", self.tpl_en)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空则输出到源文件所在目录")
        out_btn = QPushButton("选择目录…")
        out_btn.clicked.connect(self._browse_out)
        out_row = QHBoxLayout(); out_row.setSpacing(8)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(out_btn)
        form.addRow("输出目录", out_row)

        # 输入/输出任一变动即重新校验「开始监听」可用性
        self.source_edit.textChanged.connect(self._validate_form)
        self.out_edit.textChanged.connect(self._validate_form)

        cv.addLayout(form)
        parent.addWidget(card)

    def _build_options(self, parent: QVBoxLayout):
        """输出设置：每种格式一组，组内横向排列中文/英文勾选，各组平分宽度。"""
        label = QLabel("输出设置")
        label.setObjectName("section_title")
        parent.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.fmt_checks: dict[tuple[str, str], QCheckBox] = {}
        for fmt, label_txt in [("html", "HTML"), ("md", "Markdown"), ("pdf", "PDF")]:
            card = QWidget()
            card.setObjectName("fmt_card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(10)
            t = QLabel(label_txt)
            t.setObjectName("fmt_card_title")
            cl.addWidget(t)
            cb_row = QHBoxLayout()
            cb_row.setSpacing(20)
            cb_row.addStretch(1)
            for lang in ("zh", "en"):
                cb = QCheckBox(LANG_LABELS.get(lang, lang))
                cb.setObjectName("fmt_cb")
                self.fmt_checks[(fmt, lang)] = cb
                cb_row.addWidget(cb)
                cb_row.addStretch(1)
            cl.addLayout(cb_row)
            row.addWidget(card, 1)  # 三组等分，占满整行宽度

        # 默认：所有格式 × 所有语言 全部选中
        for cb in self.fmt_checks.values():
            cb.setChecked(True)

        parent.addLayout(row)

    def _build_actions(self, parent: QVBoxLayout):
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.watch_btn = QPushButton("开始监听")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.setMinimumWidth(120)
        self.watch_btn.clicked.connect(self._toggle_watch)
        self.watch_btn.setEnabled(False)  # 未选定输入/输出前不可点
        self.open_btn = QPushButton("打开输出目录")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_out)
        btn_row.addWidget(self.watch_btn)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        parent.addLayout(btn_row)

        self.src_info = QLabel("")
        self.src_info.setObjectName("src_info")
        parent.addWidget(self.src_info)

    def _build_file_list(self, parent: QVBoxLayout):
        header = QHBoxLayout()
        label = QLabel("输出文件")
        label.setObjectName("section_title")
        header.addWidget(label)
        header.addStretch(1)
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.clicked.connect(self._refresh_file_list)
        header.addWidget(self.refresh_btn)
        parent.addLayout(header)

        # 中文模板 / 英文模板 两列布局：每列一个子表
        cols = QHBoxLayout()
        cols.setSpacing(12)
        self.lang_lists = {}
        for lang in ("zh", "en"):
            col = QVBoxLayout()
            col.setSpacing(6)
            col_title = QLabel(LANG_LABELS.get(lang, lang) + "模板")
            col_title.setObjectName(f"col_title_{lang}")
            col.addWidget(col_title)
            tbl = QTableWidget(0, 4)
            tbl.setObjectName(f"file_table_{lang}")
            tbl.setHorizontalHeaderLabels(
                ["状态", "格式", "文件", "操作"])
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.setSelectionMode(QAbstractItemView.SingleSelection)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setAlternatingRowColors(True)
            tbl.verticalHeader().setVisible(False)
            tbl.setMinimumHeight(150)
            tbl.verticalHeader().setDefaultSectionSize(54)  # 行高足够容纳文件名+大小两行
            tbl.verticalHeader().setMinimumSectionSize(54)
            th = tbl.horizontalHeader()
            th.setSectionResizeMode(2, QHeaderView.Stretch)
            th.setSectionResizeMode(3, QHeaderView.Fixed)
            tbl.setColumnWidth(0, 78)
            tbl.setColumnWidth(1, 70)
            tbl.setColumnWidth(3, 72)
            tbl.setContextMenuPolicy(Qt.CustomContextMenu)
            tbl.customContextMenuRequested.connect(
                lambda pos, t=tbl: self._on_file_context(pos, t))
            tbl.itemDoubleClicked.connect(
                lambda item, t=tbl: self._on_file_activated(item, t))
            col.addWidget(tbl, 2)
            cols.addLayout(col)
            self.lang_lists[lang] = tbl
        parent.addLayout(cols, 2)

    def _build_log(self, parent: QVBoxLayout):
        label = QLabel("同步日志")
        label.setObjectName("section_title")
        parent.addWidget(label)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(90)
        parent.addWidget(self.log, 1)

    def _load_templates(self):
        try:
            infos = self.tmgr.list_templates(schema="resume")
        except Exception as e:
            self._append_log(f"加载模板失败：{e}")
            infos = []
        if not infos:
            self.tpl_zh.addItem("(默认 bwx)", "bwx")
            return
        for t in infos:
            self.tpl_zh.addItem(t.label, t.name)
            self.tpl_en.addItem(t.label, t.name)
        # 中英文模板均默认「黑白商务」(bwx)
        for combo in (self.tpl_zh, self.tpl_en):
            idx = combo.findData("bwx")
            if idx >= 0:
                combo.setCurrentIndex(idx)

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
            background: #ffffff;
            border: 1px solid #eceef2;
            border-radius: 12px;
            padding: 16px 14px 14px 14px;
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
            padding: 8px 16px;
            color: #4f46e5;
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#cell_btn:hover { background: #eef2ff; border-color: #6366f1; color: #4338ca; }
        /* 格式 tag */
        .tag_fmt {
            background: #eef2ff; color: #4f46e5; border: 1px solid #c7d2fe;
            border-radius: 7px; padding: 3px 10px; font-size: 11px;
            font-weight: 700; letter-spacing: 0.3px;
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
        """只有选定了源文件（且存在）并填好输出位置，「开始监听」才可点。"""
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
        langs = [l for l in ("zh", "en")
                 if any(sel[(f, l)] for f in ("html", "md", "pdf"))]
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
            if want_html or want_pdf:
                html_path = derive_output_path(root, "html", lang, name_map, stem)
                pdf_path = (
                    derive_output_path(root, "html", lang, name_map, stem, pdf=True)
                    if want_pdf else None
                )
                outputs.append(OutputConfig(
                    format="html", lang=lang, path=html_path,
                    pdf=want_pdf, pdf_path=pdf_path, style=style))
                if not want_html:
                    # 仅要 PDF：html 只是中间产物，不在文件列表展示
                    self._hidden_paths.add(html_path)
            if want_md:
                md_path = derive_output_path(root, "md", lang, name_map, stem)
                outputs.append(OutputConfig(
                    format="md", lang=lang, path=md_path, style=style))

        cfg = ProjectConfig(
            project=stem, source=str(src_path), schema="resume",
            outputs=outputs, output_root=str(root), source_lang="zh",
            name_map=name_map)
        cfg.source_path = src_path.resolve()
        self._last_out_dir = str(root)
        return cfg

    # ── Watching (continuous sync) ─────────────────────────────────────
    def _toggle_watch(self):
        if self.watching:
            self._stop_watch()
            return
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
        self.watch_btn.setText("停止监听")
        self.watch_btn.setStyleSheet("background:#ef4444;border:1px solid #ef4444;color:#fff;font-weight:500;")
        self.open_btn.setEnabled(True)
        self._update_status_pill()
        self._append_log(f"─ 开始监听：{cfg.source_path.name}（改动即自动同步）")

    def _stop_watch(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.watching = False
        self._pending_sync = False
        self.watch_btn.setText("开始监听")
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
                for _, files in self._iter_output_files():
                    if any(
                        _status_color(_file_status(t), self.source_mtime) == C_PENDING
                        for t, _, _ in files
                    ):
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
        """按语言分组输出文件： yield (lang, [(path, fmt, color), ...])"""
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
                yield lang, groups[lang]

    def _refresh_file_list(self):
        # 清空两列子表并重新注册本帧状态标签
        for tbl in self.lang_lists.values():
            tbl.setRowCount(0)
        self._status_tags = []
        if self.cfg is None:
            return
        if self.cfg.source_path.exists():
            self.source_mtime = self.cfg.source_path.stat().st_mtime
        any_row = False
        for lang, files in self._iter_output_files():
            tbl = self.lang_lists.get(lang)
            if tbl is None:
                continue
            for path, fmt, color in files:
                any_row = True
                p = Path(path)
                exists = p.exists()
                size = f"{p.stat().st_size // 1024}KB" if exists else "--"
                # 同步进行中：整行显示为蓝色“同步中…”并闪烁，表示动态持续过程
                if self._syncing:
                    color = C_RUNNING
                st_text = _status_text(color)
                # 闪烁规则：同步中（蓝）必闪；待同步（黄，表示待重新生成）也轻微闪烁
                pulse = self._syncing or color == C_PENDING
                row = tbl.rowCount()
                tbl.insertRow(row)
                tbl.setRowHeight(row, 54)  # 固定行高，确保文件名+大小两行有足够高度可点击

                status = StatusTag(color, st_text, pulse=pulse)
                tbl.setCellWidget(row, 0, status)
                self._status_tags.append(status)

                # 格式 → 单个 tag 标签
                fmt_tag = QLabel(fmt)
                fmt_tag.setObjectName("tag_fmt")
                fmt_tag.setProperty("class", "tag")
                tag_cell = QWidget()
                tag_cell.setObjectName("tag_cell")
                tl = QHBoxLayout(tag_cell)
                tl.setContentsMargins(8, 0, 8, 0)
                tl.setSpacing(6)
                tl.addWidget(fmt_tag)
                tl.addStretch(1)
                tbl.setCellWidget(row, 1, tag_cell)

                # 文件名 + 元信息（大小 · 修改时间）两行，形成层级
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
                    f"{size} · {self._fmt_mtime(p.stat().st_mtime)}" if exists else "— 尚未生成")
                meta_lbl.setObjectName("file_meta")
                fc.addWidget(name_lbl)
                fc.addWidget(meta_lbl)
                tbl.setCellWidget(row, 2, file_cell)

                btn = QPushButton("打开")
                btn.setObjectName("cell_btn")
                btn.clicked.connect(lambda _=False, p=path: self._open_file(p))
                tbl.setCellWidget(row, 3, btn)
        if not any_row:
            for tbl in self.lang_lists.values():
                tbl.insertRow(0)
                tbl.setItem(0, 0, QTableWidgetItem("（未配置输出）"))
        if not self._syncing:
            self._update_status_pill()

    def _refresh_src_info(self):
        if self.cfg is None:
            self.src_info.setText("")
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
        except Exception as e:
            self.src_info.setText(f"源语言检测失败：{e}")

    # ── Open / copy ────────────────────────────────────────────────────
    def _open_out(self):
        if self._last_out_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_out_dir))

    def _open_file(self, path):
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _on_file_activated(self, item: QTableWidgetItem, tbl: QTableWidget):
        if item is None or tbl is None:
            return
        cell = tbl.cellWidget(item.row(), 2)
        if cell is None:
            return
        self._open_file(cell.property("path"))

    def _on_file_context(self, pos, tbl: QTableWidget):
        item = tbl.itemAt(pos)
        if item is None:
            return
        cell = tbl.cellWidget(item.row(), 2)
        if cell is None:
            return
        path = cell.property("path")
        if not path:
            return
        menu = tbl.createStandardContextMenu(pos)
        act_open = menu.addAction("打开文件")
        act_copy = menu.addAction("复制路径")
        choice = menu.exec(tbl.mapToGlobal(pos))
        if choice == act_open:
            self._open_file(path)
        elif choice == act_copy:
            QApplication.clipboard().setText(path)

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
