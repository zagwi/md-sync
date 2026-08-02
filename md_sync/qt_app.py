"""Native Qt GUI for md-sync — full replica of the Web dashboard, no HTTP server.

Visual language follows shadcn (neutral zinc palette, white cards, subtle
borders, rounded corners, blue accent) adapted to Qt Style Sheets. The window
is frameless with a custom title bar providing minimize / maximize / close.

Mirrors the web UI feature-for-feature but calls the core pipeline directly:
  · 选择源 Markdown 文件（自动检测源语言）
  · 选择中文 / 英文模板、输出格式（HTML / Markdown / PDF）
  · 「启动多格式同步输出」→ 后台持续同步：源文件一改动（防抖 1.5s）即自动重新生成
  · 输出文件表格：状态点、格式/语言、文件、大小、最后更新时间、〔打开〕
  · 同步日志（时间精确到毫秒、生成文件、耗时、错误）
  · 一键打开输出目录

Run:
    python -m md_sync.qt_app
    # or: md-sync gui
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

# Allow running as `python md_sync/qt_app.py` directly
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QStandardItem,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from md_sync.config import OutputConfig, ProjectConfig, derive_output_path
from md_sync.core.pipeline import SyncPipeline
from md_sync.exporters.page import MARGIN_LABELS
from md_sync.exporters.pdf import _find_chromium
from md_sync.plugin.interface import DirectoryPlugin, PluginManifest
from md_sync.plugin.registry import PluginRegistry
from md_sync.template.manager import TemplateManager
from md_sync.typography import TypographyConfig, normalize_for_lang
from md_sync.watcher import FileWatcher

LANG_LABELS = {"zh": "中文", "en": "英文"}

# ── Preview cache ───────────────────────────────────────────────
_PREVIEW_CACHE: dict[str, QPixmap | None] = {}
_PREVIEW_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "md-sync" / "previews"
)

# theme.typora.io 官方画廊缩略图映射（本地 CSS stem → 画廊缩略图 URL）。
# 由 https://theme.typora.io/ 页面抓取生成（2026-07 快照）。
_TYPORA_GALLERY_URLS = {
    "Screenplay": "https://theme.typora.io/media/thumbnails/screenplay.png",
    "alto": "https://theme.typora.io/media/thumbnails/alto.png",
    "amatriz": "https://theme.typora.io/media/thumbnails/amatriz.png",
    "amatriz-print-white": "https://theme.typora.io/media/thumbnails/amatriz.png",
    "amber": "https://theme.typora.io/media/thumbnails/Amber.png",
    "animal-island": "https://theme.typora.io/media/thumbnails/animal-island-thumbnail.png",
    "ash": "https://theme.typora.io/media/thumbnails/ash.png",
    "aspartate": "https://theme.typora.io/media/thumbnails/aspartate.png",
    "autumnus": "https://theme.typora.io/media/thumbnails/autumnus.png",
    "ava-diana": "https://theme.typora.io/media/thumbnails/ava-diana.png",
    "barfi": "https://theme.typora.io/media/thumbnails/barfi.png",
    "bios": "https://theme.typora.io/media/thumbnails/bios.png",
    "bit-clean-dark": "https://theme.typora.io/media/thumbnails/bit-clean-thumbnail.png",
    "bit-clean-light": "https://theme.typora.io/media/thumbnails/bit-clean-thumbnail.png",
    "blackout": "https://theme.typora.io/media/thumbnails/blackout.png",
    "blubook": "https://theme.typora.io/media/thumbnails/blubook.png",
    "blue-topaz": "https://theme.typora.io/media/thumbnails/blue-topaz.png",
    "blue-topaz-dark": "https://theme.typora.io/media/thumbnails/blue-topaz.png",
    "bluetex": "https://theme.typora.io/media/thumbnails/blueTex.png",
    "bonne-nouvelle": "https://theme.typora.io/media/thumbnails/bonne-nouvelle.png",
    "bronya": "https://theme.typora.io/media/thumbnails/bronya.png",
    "catfish": "https://theme.typora.io/media/thumbnails/catfish.png",
    "cement": "https://theme.typora.io/media/thumbnails/cement.png",
    "ceylon": "https://theme.typora.io/media/thumbnails/ceylon.png",
    "claude": "https://theme.typora.io/media/thumbnails/claude-typora-theme.png",
    "clean-light": "https://theme.typora.io/media/thumbnails/clean.png",
    "cobalt": "https://theme.typora.io/media/thumbnails/cobalt.png",
    "compact": "https://theme.typora.io/media/thumbnails/compact.png",
    "compact-night": "https://theme.typora.io/media/thumbnails/compact-night.png",
    "crisp-gothic": "https://theme.typora.io/media/thumbnails/crisp.png",
    "crisp-mincho": "https://theme.typora.io/media/thumbnails/crisp.png",
    "dracula": "https://theme.typora.io/media/thumbnails/dracula-typora.png",
    "drake": "https://theme.typora.io/media/thumbnails/drake-thumb.png",
    "dyzj": "https://theme.typora.io/media/thumbnails/dyzj.png",
    "dyzj-dark": "https://theme.typora.io/media/thumbnails/dyzj.png",
    "dyzj-light": "https://theme.typora.io/media/thumbnails/dyzj.png",
    "eloquent": "https://theme.typora.io/media/thumbnails/eloquent.png",
    "engwrite": "https://theme.typora.io/media/thumbnails/engwrite.png",
    "eternal": "https://theme.typora.io/media/thumbnails/Eternal.png",
    "eva": "https://theme.typora.io/media/thumbnails/eva.png",
    "everforest": "https://theme.typora.io/media/thumbnails/everforest.png",
    "everforest-dark": "https://theme.typora.io/media/thumbnails/everforest.png",
    "everforest-light": "https://theme.typora.io/media/thumbnails/everforest.png",
    "eyes-green": "https://theme.typora.io/media/thumbnails/eyes-green.png",
    "flexoki-light": "https://theme.typora.io/media/thumbnails/flexoki-light.png",
    "fluent": "https://theme.typora.io/media/thumbnails/fluent.png",
    "folio": "https://theme.typora.io/media/thumbnails/folio.png",
    "forest": "https://theme.typora.io/media/thumbnails/forest.png",
    "fro": "https://theme.typora.io/media/thumbnails/typora-fro.png",
    "github": "https://theme.typora.io/media/thumbnails/github.png",
    "github-night": "https://theme.typora.io/media/thumbnails/github-night.png",
    "gitlab": "https://theme.typora.io/media/thumbnails/gitlab.png",
    "gruvbox": "https://theme.typora.io/media/thumbnails/gruvbox.png",
    "happysimple": "https://theme.typora.io/media/thumbnails/Happysimple.png",
    "haru": "https://theme.typora.io/media/thumbnails/haru.png",
    "ia-typora": "https://theme.typora.io/media/thumbnails/iatypora.jpeg",
    "inkwell": "https://theme.typora.io/media/thumbnails/inkwell.png",
    "inside": "https://theme.typora.io/media/thumbnails/inside.png",
    "ivory-flow": "https://theme.typora.io/media/thumbnails/ivory-flow-thumbnail.png",
    "jamstatic": "https://theme.typora.io/media/thumbnails/jamstatic.png",
    "jetbrains-dark": "https://theme.typora.io/media/thumbnails/jetbrains-dark.png",
    "jinxiu": "https://theme.typora.io/media/thumbnails/Jinxiu.png",
    "johntor-dark-blue": "https://theme.typora.io/media/thumbnails/johntor-dark-blue.png",
    "juejin": "https://theme.typora.io/media/thumbnails/juejin.png",
    "kiro": "https://theme.typora.io/media/thumbnails/kiro.png",
    "konayuki-dark": "https://theme.typora.io/media/thumbnails/Konayuki.png",
    "konayuki-light": "https://theme.typora.io/media/thumbnails/Konayuki.png",
    "krafty": "https://theme.typora.io/media/thumbnails/krafty.png",
    "ladder": "https://theme.typora.io/media/thumbnails/ladder-theme.png",
    "lanyue": "https://theme.typora.io/media/thumbnails/lanyue.png",
    "lapis": "https://theme.typora.io/media/thumbnails/lapis.png",
    "lavender": "https://theme.typora.io/media/thumbnails/lavender.png",
    "law": "https://theme.typora.io/media/thumbnails/law.png",
    "lcars": "https://theme.typora.io/media/thumbnails/lcars.png",
    "light-monokai": "https://theme.typora.io/media/thumbnails/light-monokai.png",
    "lightmind": "https://theme.typora.io/media/thumbnails/lightmind.png",
    "liquid": "https://theme.typora.io/media/thumbnails/liquid.png",
    "lostkeys": "https://theme.typora.io/media/thumbnails/lostkeys.png",
    "maize": "https://theme.typora.io/media/thumbnails/maize.png",
    "mdmdt": "https://theme.typora.io/media/thumbnails/mdmdt.png",
    "mint": "https://theme.typora.io/media/thumbnails/mint.png",
    "mist-blue": "https://theme.typora.io/media/thumbnails/mist-blue.png",
    "mlike": "https://theme.typora.io/media/thumbnails/mlike.png",
    "mo": "https://theme.typora.io/media/thumbnails/mo.png",
    "monospace": "https://theme.typora.io/media/thumbnails/monospace.png",
    "morandigarden": "https://theme.typora.io/media/thumbnails/morandigarden.jpg",
    "neil-jetbrains-mono": "https://theme.typora.io/media/thumbnails/neil-jetbrains-mono-theme.png",
    "newsprint": "https://theme.typora.io/media/thumbnails/newsprint.png",
    "next": "https://theme.typora.io/media/thumbnails/next.jpg",
    "night": "https://theme.typora.io/media/thumbnails/night.png",
    "nocturne": "https://theme.typora.io/media/thumbnails/nocturne.png",
    "nord": "https://theme.typora.io/media/thumbnails/nord.png",
    "notes-dark": "https://theme.typora.io/media/thumbnails/notes-dark.png",
    "notion": "https://theme.typora.io/media/thumbnails/notion-thumb.jpg",
    "notion-onedark": "https://theme.typora.io/media/thumbnails/notion-onedark.png",
    "notion-style-dark": "https://theme.typora.io/media/thumbnails/notion-style.png",
    "notion-style-light": "https://theme.typora.io/media/thumbnails/notion-style.png",
    "onedark": "https://theme.typora.io/media/thumbnails/onedark.png",
    "onelight": "https://theme.typora.io/media/thumbnails/onelight.png",
    "onigiri": "https://theme.typora.io/media/thumbnails/onigiri.png",
    "opencode": "https://theme.typora.io/media/thumbnails/opencode.png",
    "orangeheart": "https://theme.typora.io/media/thumbnails/orangeheart.png",
    "panda": "https://theme.typora.io/media/thumbnails/panda.png",
    "paper": "https://theme.typora.io/media/thumbnails/paper.png",
    "paperglow": "https://theme.typora.io/media/thumbnails/paperglow-theme.png",
    "phycat.dark": "https://theme.typora.io/media/thumbnails/phycat.png",
    "phycat.light": "https://theme.typora.io/media/thumbnails/phycat.png",
    "pie": "https://theme.typora.io/media/thumbnails/pie.png",
    "pink-fairy": "https://theme.typora.io/media/thumbnails/pink-fairy.png",
    "pink-hsiao": "https://theme.typora.io/media/thumbnails/pink-hsiao.png",
    "pixyll": "https://theme.typora.io/media/thumbnails/pixyll.png",
    "print": "https://theme.typora.io/media/thumbnails/print.png",
    "purclaude": "https://theme.typora.io/media/thumbnails/purclaude.png",
    "purple": "https://theme.typora.io/media/thumbnails/purple.png",
    "rainbow": "https://theme.typora.io/media/thumbnails/rainbow.png",
    "ravel": "https://theme.typora.io/media/thumbnails/ravel.png",
    "redefine-dark": "https://theme.typora.io/media/thumbnails/redefine.png",
    "redefine-light": "https://theme.typora.io/media/thumbnails/redefine.png",
    "refine": "https://theme.typora.io/media/thumbnails/refine.png",
    "rhapsody": "https://theme.typora.io/media/thumbnails/Rhapsody.png",
    "rubrication": "https://theme.typora.io/media/thumbnails/rubrication.png",
    "saffron": "https://theme.typora.io/media/thumbnails/saffron.png",
    "salamander": "https://theme.typora.io/media/thumbnails/salamander.png",
    "scrolls": "https://theme.typora.io/media/thumbnails/scrolls.png",
    "see-yue-dark": "https://theme.typora.io/media/thumbnails/see-yue.png",
    "seniva": "https://theme.typora.io/media/thumbnails/seniva.png",
    "softgreen": "https://theme.typora.io/media/thumbnails/softgreen.png",
    "solarized": "https://theme.typora.io/media/thumbnails/solarized.png",
    "sonnet": "https://theme.typora.io/media/thumbnails/sonnet.png",
    "spring": "https://theme.typora.io/media/thumbnails/Spring.png",
    "swiss": "https://theme.typora.io/media/thumbnails/swiss.png",
    "tailwind": "https://theme.typora.io/media/thumbnails/tailwind.png",
    "tanda": "https://theme.typora.io/media/thumbnails/Tanda.png",
    "techo": "https://theme.typora.io/media/thumbnails/techo.png",
    "torillic": "https://theme.typora.io/media/thumbnails/torillic.png",
    "turing": "https://theme.typora.io/media/thumbnails/turing.png",
    "typora-docsify": "https://theme.typora.io/media/thumbnails/typora_docsify.png",
    "valve": "https://theme.typora.io/media/thumbnails/valve.png",
    "vercel": "https://theme.typora.io/media/thumbnails/vercel.png",
    "vintage": "https://theme.typora.io/media/thumbnails/vintage.png",
    "virgo": "https://theme.typora.io/media/thumbnails/virgo.png",
    "vlook-fancy": "https://theme.typora.io/media/thumbnails/vlook-fancy.png",
    "vlook-fancy-dark": "https://theme.typora.io/media/thumbnails/vlook-fancy.png",
    "vlook-fancy-light": "https://theme.typora.io/media/thumbnails/vlook-fancy.png",
    "vue": "https://theme.typora.io/media/thumbnails/vue.png",
    "warp-gradient": "https://theme.typora.io/media/thumbnails/warp-gradient.png",
    "whitelines": "https://theme.typora.io/media/thumbnails/whitelines.png",
    "whitey": "https://theme.typora.io/media/thumbnails/whitey.png",
    "xydark": "https://theme.typora.io/media/thumbnails/xydark.png",
    "zeus": "https://theme.typora.io/media/thumbnails/zeus.png",
}

# 访问 theme.typora.io 本机直连会超时，需走代理（依次尝试）
_TYPORA_PROXIES = ("http://127.0.0.1:1080", "")

# ── Typora 主题分组（两级下拉：仓库 → 主题） ─────────────────
# 分组条件：同一 GitHub 仓库的所有主题为一组，它们通常共享公共前缀
# （参考 https://theme.typora.io/ 按作者仓库分组）。前缀按「长的在前」排序，
# 避免短前缀误吞（如 claude-like 需先于 claude）。未命中的主题归入「其他主题」。
_TYPORA_GROUPS: list[tuple[str, str]] = [
    ("Claude-like", "claude-like"),
    ("Novel Tex", "novel-tex-"),
    ("Animal Island", "animal-island"),
    ("Esther Inspired", "esther-inspired-"),
    ("Neil JetBrains Mono", "neil-jetbrains-mono"),
    ("Middle East", "middle-east-"),
    ("Bit Clean", "bit-clean"),
    ("Blue Topaz", "blue-topaz"),
    ("Eyes Green", "eyes-green"),
    ("Konayuki", "konayuki-"),
    ("See-Yue", "see-yue-"),
    ("Themeable", "themeable"),
    ("Autumnus", "autumnus"),
    ("Everforest", "everforest"),
    ("Paperglow", "paperglow"),
    ("Redefine", "redefine"),
    ("Solarized", "solarized"),
    ("Lightmind", "lightmind"),
    ("Lostkeys", "lostkeys"),
    ("Monospace", "monospace"),
    ("Neumorphism", "neumorphism"),
    ("Happysimple", "happysimple"),
    ("Gruvbox", "gruvbox"),
    ("Inkwell", "inkwell"),
    ("Ladder", "ladder"),
    ("Lapis", "lapis"),
    ("Liquid", "liquid"),
    ("MDMDT", "mdmdt"),
    ("MLike", "mlike"),
    ("Onigiri", "onigiri"),
    ("Scrolls", "scrolls"),
    ("Sonnet", "sonnet"),
    ("Tailwind", "tailwind"),
    ("Terminal", "terminal"),
    ("Vintage", "vintage"),
    ("Virgo", "virgo"),
    ("Bloom", "bloom-"),
    ("Nexmoe", "nexmoe-"),
    ("Paradox", "paradox-"),
    ("Quartz", "quartz-"),
    ("Riwaq", "riwaq-"),
    ("Dogs", "dogs-"),
    ("I-W", "i-w-"),
    ("Pink", "pink-"),
    ("Crisp", "crisp-"),
    ("Clean", "clean-"),
    ("Compact", "compact"),
    ("Fluent", "fluent"),
    ("Folio", "folio"),
    ("Jinxiu", "jinxiu"),
    ("Ceylon", "ceylon"),
    ("Cement", "cement"),
    ("Amatriz", "amatriz"),
    ("Bluetex", "bluetex"),
    ("Alto", "alto"),
    ("iA Typora", "ia-typora"),
    ("One Dark", "onedark"),
    ("One Light", "onelight"),
    ("GitHub", "github"),
    ("Notion", "notion"),
    ("Purple", "purple-"),
    ("Phycat", "phycat-"),
    ("Drake", "drake"),
    ("vlook", "vlook-"),
    ("Seniva", "seniva"),
    ("Next", "next"),
    ("Ravel", "ravel"),
    ("Pie", "pie"),
    ("Print", "print"),
    ("Claude", "claude"),
    ("Mint", "mint"),
    ("Mo", "mo-"),
    ("DYZJ", "dyzj"),
    ("Haru", "haru"),
    ("Inside", "inside"),
    ("Vue", "vue"),
    ("Xy", "xy"),
]

# 下拉模型角色：组头行存分组显示名
_ROLE_GROUP = Qt.UserRole + 1


def _typora_group_key(stem: str) -> str | None:
    """返回 typora 主题 CSS stem 所属分组名，未命中返回 None。"""
    # 精确匹配优先（如 "mo" 主题：前缀 "mo-" 不含裸 "mo"，但 "mo" 不能作
    # 前缀否则会误吞 morandigarden）
    if stem.lower() == "mo":
        return "Mo"
    for name, prefix in _TYPORA_GROUPS:
        if stem.lower().startswith(prefix):
            return name
    return None


_SAMPLE_PREVIEW_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
{style_css}
html {{ overflow: hidden; background: #fff; }}
body {{ width: 380px; height: 480px; overflow: hidden; }}
</style>
</head>
<body>
<div id="write" class="body">
<h1>示例文档 Sample</h1>
<p>这是一段正文文字，展示当前主题的排版效果。</p>
<h2>章节标题 Section</h2>
<p>第二段文字，包含更多内容用于展示字体、行距、颜色等样式特征。</p>
<h3>列表展示</h3>
<ul>
  <li>列表项目一 - 描述文字内容</li>
  <li>列表项目二 - 更多描述</li>
  <li>列表项目三 - 演示效果</li>
</ul>
<h3>表格示例</h3>
<table>
  <tr><th>名称</th><th>版本</th><th>状态</th></tr>
  <tr><td>模块A</td><td>2.1</td><td>已完成</td></tr>
  <tr><td>模块B</td><td>3.0</td><td>开发中</td></tr>
</table>
<pre><code>def hello():
    print("Hello, World!")</code></pre>
</div>
</body>
</html>"""


def _crop_whitespace(pixmap: QPixmap) -> QPixmap:
    """Remove trailing blank rows/columns (white with full opacity) from bottom and right edges.

    Chromium screenshots are taken at --window-size=420,540 but body content
    is only 380×480, producing ~40px right / ~60px bottom whitespace. Gallery
    downloads may also have padding. This function trims them to actual content.
    """
    if pixmap.isNull():
        return pixmap
    # 预览框仅 360×460：画廊原图可能高达 1967×1521（≈300 万像素），纯 Python
    # 逐像素扫描会阻塞 UI 1-4s。先等比缩小到 ≤700px 宽，裁剪再缩略显示。
    if pixmap.width() > 700:
        pixmap = pixmap.scaledToWidth(700, Qt.SmoothTransformation)
    img = pixmap.toImage()
    w, h = img.width(), img.height()

    def _is_blank_row(y: int) -> bool:
        for x in range(w):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and not (c.red() > 248 and c.green() > 248 and c.blue() > 248):
                return False
        return True

    def _is_blank_col(x: int, y_limit: int) -> bool:
        for y in range(y_limit):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and not (c.red() > 248 and c.green() > 248 and c.blue() > 248):
                return False
        return True

    # Trim from bottom
    trim_bottom = 0
    for y in range(h - 1, -1, -1):
        if _is_blank_row(y):
            trim_bottom += 1
        else:
            break

    # Trim from right (only scan rows that remain)
    content_h = h - trim_bottom
    trim_right = 0
    for x in range(w - 1, -1, -1):
        if _is_blank_col(x, content_h):
            trim_right += 1
        else:
            break

    if trim_bottom > 0 or trim_right > 0:
        new_w = w - trim_right
        new_h = h - trim_bottom
        if new_w > 10 and new_h > 10:  # sanity check
            return pixmap.copy(0, 0, new_w, new_h)
    return pixmap


def _get_style_css(style_name: str, tmgr: TemplateManager) -> str:
    if style_name.startswith("typora-"):
        css_stem = style_name[7:]
        try:
            from md_sync.plugins.typora.paths import get_typora_themes_dir

            td = get_typora_themes_dir()
            if td:
                css_path = td / f"{css_stem}.css"
                if css_path.exists():
                    import re

                    return re.sub(
                        r"@font-face\s*\{[^}]*\}",
                        "",
                        css_path.read_text(encoding="utf-8"),
                        flags=re.DOTALL,
                    )
        except Exception:
            pass
        return ""
    try:
        tpl_dir = tmgr.resolve_path(style_name)
        css_path = tpl_dir / "style.css"
        if css_path.exists():
            import re

            return re.sub(
                r"@font-face\s*\{[^}]*\}", "", css_path.read_text(encoding="utf-8"), flags=re.DOTALL
            )
    except Exception:
        pass
    return ""


def _download_typora_file(css_stem: str, cache_path: Path) -> bool:
    """Download a theme.typora.io gallery thumbnail to local cache (file only)."""
    url = _TYPORA_GALLERY_URLS.get(css_stem)
    if not url:
        return False
    try:
        _PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for proxy in _TYPORA_PROXIES:
            cmd = ["curl", "-s", "--fail", "--max-time", "15"]
            if proxy:
                cmd += ["-x", proxy]
            cmd += ["-o", str(cache_path), url]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and cache_path.exists() and cache_path.stat().st_size > 100:
                return True
        # 下载失败：清掉可能残留的半截文件，避免磁盘缓存加载到损坏的图片
        try:
            if cache_path.exists():
                cache_path.unlink()
        except OSError:
            pass
        return False
    except Exception:
        try:
            if cache_path.exists():
                cache_path.unlink()
        except OSError:
            pass
        return False


def _get_or_create_preview(style_name: str, tmgr: TemplateManager) -> QPixmap | None:
    """获取风格预览图。优先级：内存 → 磁盘 → Typora 画廊下载 → Chromium 渲染。

    全部在主线程运行（慢但稳定）。首次生成某风格可能阻塞 1-2s（下载或
    Chromium headless 启动），之后即时从缓存返回。
    """
    try:
        if style_name in _PREVIEW_CACHE:
            return _PREVIEW_CACHE[style_name]
        cache_path = _PREVIEW_CACHE_DIR / f"{style_name}.png"
        # 磁盘缓存命中 → 直接加载，裁剪空白
        if cache_path.exists():
            pix = QPixmap(str(cache_path))
            if not pix.isNull():
                pix = _crop_whitespace(pix)
                _PREVIEW_CACHE[style_name] = pix
                return pix
        # Typora 主题：优先从 theme.typora.io 官方画廊下载缩略图
        if style_name.startswith("typora-"):
            css_stem = style_name[7:]
            if _download_typora_file(css_stem, cache_path):
                pix = QPixmap(str(cache_path))
                if not pix.isNull():
                    pix = _crop_whitespace(pix)
                    _PREVIEW_CACHE[style_name] = pix
                    return pix
            # 画廊未命中 → fall through 到本地 Chromium 渲染
        css_text = _get_style_css(style_name, tmgr)
        if not css_text:
            _PREVIEW_CACHE[style_name] = None
            return None
        html_content = _SAMPLE_PREVIEW_HTML.format(style_css=css_text)
        _PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        html_path = _PREVIEW_CACHE_DIR / f"{style_name}.html"
        html_path.write_text(html_content, encoding="utf-8")
        chromium = _find_chromium()
        if not chromium:
            _PREVIEW_CACHE[style_name] = None
            return None
        try:
            subprocess.run(
                [
                    chromium,
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    f"--screenshot={cache_path}",
                    "--window-size=420,540",
                    f"file://{html_path}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            _PREVIEW_CACHE[style_name] = None
            return None
        # Chromium 渲染结果 → 加载并裁剪空白
        if cache_path.exists():
            pix = QPixmap(str(cache_path))
            if not pix.isNull():
                pix = _crop_whitespace(pix)
                _PREVIEW_CACHE[style_name] = pix
                return pix
        _PREVIEW_CACHE[style_name] = None
        return None
    except Exception:
        # 最外层保护：任何异常都不会崩溃整个 app
        _PREVIEW_CACHE[style_name] = None
        return None


# ── status colors (shadcn-ish) ──
C_SYNCED = "#22c55e"  # 已同步（绿）
C_PENDING = "#f59e0b"  # 待同步（黄）
C_MISSING = "#ef4444"  # 文件不存在（红）
C_RUNNING = "#3b82f6"  # 同步中（蓝，动态闪烁）

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
            f"background:{c.name(QColor.HexRgb)};border-radius:5px;opacity:{self._alpha};"
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


_WINDOW_ICON_SIZE = 24


def _window_icon(kind: str) -> QIcon:
    """Draw a window-control icon (min / max / restore / close) programmatically.

    Replaces the Unicode glyphs (``▢``/``❐``) which render inconsistently or
    as missing-glyph boxes depending on the system font.
    """
    pm = QPixmap(_WINDOW_ICON_SIZE, _WINDOW_ICON_SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#52525b"), 1.7)
    p.setPen(pen)
    s = _WINDOW_ICON_SIZE
    m = 5
    if kind == "min":
        y = s - m - 2
        p.drawLine(m + 2, y, s - m - 2, y)
    elif kind == "max":
        p.drawRect(QRectF(m, m, s - 2 * m, s - 2 * m))
    elif kind == "restore":
        p.drawRect(QRectF(m, m + 3, s - 2 * m - 3, s - 2 * m - 3))
        p.drawRect(QRectF(m + 3, m, s - 2 * m - 3, s - 2 * m - 3))
    elif kind == "close":
        p.drawLine(m + 2, m + 2, s - m - 2, s - m - 2)
        p.drawLine(s - m - 2, m + 2, m + 2, s - m - 2)
    p.end()
    return QIcon(pm)


class TitleBar(QWidget):
    """Custom draggable title bar with minimize / maximize / close."""

    def __init__(self, parent: MainWindow):
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
        self.status_pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_pill)
        layout.addStretch(1)

        self.btn_min = self._make_btn("", "title_min", self._parent.showMinimized)
        self.btn_max = self._make_btn("", "title_max", self._toggle_max)
        self.btn_close = self._make_btn("", "title_close", self._parent.close)
        self.btn_min.setIcon(_window_icon("min"))
        self.btn_max.setIcon(_window_icon("max"))
        self.btn_close.setIcon(_window_icon("close"))
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

        self._drag_pos: QPoint | None = None

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


class FloatingPreview(QWidget):
    """浮动预览窗口：下拉选项悬停/导航时在 combobox 右侧弹出真实截图预览。"""

    MARGIN = 8
    SS_W, SS_H = 360, 460
    NAME_H, GAP, SHADOW = 24, 10, 3
    TOTAL_W = SS_W + MARGIN * 2 + SHADOW
    TOTAL_H = MARGIN + SS_H + GAP + NAME_H + MARGIN + SHADOW

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 鼠标事件穿透：预览是置顶窗口，若悬浮在下拉选项上方会吃掉点击
        # （三列弹层 684px 宽后，贴在 combo 右缘的预览会盖住弹层右侧），
        # 设为对鼠标透明后点击直接落到下面的下拉选项上。
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.TOTAL_W, self.TOTAL_H)
        self._pixmap: QPixmap | None = None
        self._name = ""

    def set_preview(self, pixmap: QPixmap | None, name: str):
        self._pixmap = pixmap
        self._name = name
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        w = self.TOTAL_W
        sx, sy = self.MARGIN, self.MARGIN
        if self._pixmap:
            # 等比缩放：按原截图比例适配预览框（不拉伸变形），居中留边。
            # 否则固定 360×460 拉伸会把细长/偏方的版面压变形，观感怪异。
            pw, ph = self._pixmap.width(), self._pixmap.height()
            if pw > 0 and ph > 0:
                scale = min(self.SS_W / pw, self.SS_H / ph)
                dw, dh = pw * scale, ph * scale
                dx = sx + (self.SS_W - dw) / 2
                dy = sy + (self.SS_H - dh) / 2
            else:
                dw, dh, dx, dy = self.SS_W, self.SS_H, sx, sy
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 28)))
            p.drawRoundedRect(sx + self.SHADOW, sy + self.SHADOW, self.SS_W, self.SS_H, 3, 3)
            p.setPen(QPen(QColor("#d0d0d0"), 0.5))
            p.drawRoundedRect(sx, sy, self.SS_W, self.SS_H, 2, 2)
            p.drawPixmap(QRectF(dx, dy, dw, dh), self._pixmap, QRectF(0, 0, pw, ph))
        else:
            p.setPen(QPen(QColor("#d0d0d0"), 1, Qt.DashLine))
            p.setBrush(QBrush(QColor("#f5f5f5")))
            p.drawRoundedRect(sx, sy, self.SS_W, self.SS_H, 4, 4)
            p.setPen(QColor("#aaaaaa"))
            nf = QFont()
            nf.setPointSize(11)
            p.setFont(nf)
            p.drawText(sx, sy, self.SS_W, self.SS_H, Qt.AlignCenter, "加载中…")
        p.setPen(QColor("#444444"))
        nf = QFont()
        nf.setPointSize(11)
        nf.setBold(True)
        p.setFont(nf)
        p.drawText(0, sy + self.SS_H + self.GAP, w, self.NAME_H, Qt.AlignCenter, self._name)
        p.end()


class SyncWorker(QThread):
    """Run the conversion in a background thread (pipeline is blocking)."""

    log = Signal(str)
    sync_finished = Signal(bool, str, list)  # (success, message, files)

    # NOTE: The signal is named ``sync_finished`` instead of the built-in
    # ``finished`` to avoid overriding ``QThread.finished``.  PySide6 emits
    # the inherited ``finished()`` (no args) when ``run()`` exits; overriding
    # it with a different signature would crash the event loop.

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
                msg = f"同步失败（{time.time() - t0:.1f}s）：管道报告了 {len(errors)} 个错误"
                self.log.emit(msg)
                self.sync_finished.emit(False, msg, [])
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
                self.sync_finished.emit(True, msg, files)
        except Exception as e:
            tb = traceback.format_exc()
            self.log.emit("同步失败：\n" + tb)
            self.sync_finished.emit(False, str(e), [])


class FontInstallWorker(QThread):
    """后台下载并安装 Fandol 公文字体集（不阻塞主界面）。"""

    done = Signal(bool, str)  # (ok, message)

    def run(self):
        try:
            from md_sync.plugins.gongwen.fonts import install_fonts

            installed = install_fonts()
            self.done.emit(True, f"已安装 {len(installed)} 个字体文件")
        except Exception as e:
            self.done.emit(False, f"字体安装失败：{e}")


class TypographyDialog(QDialog):
    """「文档标准配置」— 中英文排版规则开关，对齐 TypographyConfig 全部字段。"""

    # (字段名, 展示文案) — 字段顺序与 typography.py 默认值定义保持一致。
    ZH_RULES = [
        ("cjk_latin_space", "中英文之间加空格（支持ChatGPT → 支持 ChatGPT）"),
        ("cjk_digit_space", "中文与数字之间加空格（花100元 → 花 100 元）"),
        ("number_unit_space", "数字与单位之间加空格（20Gbps → 20 Gbps；90°、15% 除外）"),
        ("fullwidth_punct_no_space", "全角标点旁不加空格（iPhone ，好用 → iPhone，好用）"),
    ]
    EN_RULES = [
        ("en_no_space_before_punct", "标点前不加空格（Hello ,world → Hello,world）"),
        ("en_space_after_punct", "标点后加空格（Hello,world → Hello, world；1,000、10:30 除外）"),
        ("en_collapse_spaces", "合并连续空格（Hello   world → Hello world，保留缩进与换行）"),
    ]

    def __init__(self, cfg: TypographyConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("📐 文档标准配置")
        self.setMinimumWidth(600)
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        tip = QLabel(
            "中英文混排规范（参照 W3C CLReq / CY/T 154-2017）与英文标点间距规范。"
            "作用于生成产物与「✂ 规范化源文档」；源文件不会被自动修改。"
            "代码块、行内代码与网址链接始终不受影响。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#71717a;font-size:12px;")
        layout.addWidget(tip)

        def _add_group(title: str, rules: list[tuple[str, str]]) -> None:
            group = QGroupBox(title)
            group.setStyleSheet(
                "QGroupBox{font-size:12px;font-weight:600;color:#333;margin-top:4px;}"
                "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}"
            )
            v = QVBoxLayout(group)
            v.setSpacing(6)
            for key, label in rules:
                cb = QCheckBox(label)
                cb.setStyleSheet("font-size:12px;color:#444;")
                self._boxes[key] = cb
                v.addWidget(cb)
            layout.addWidget(group)

        _add_group("中英文混排规则（作用于中文产物）", self.ZH_RULES)
        _add_group("英文排版规则（作用于英文产物）", self.EN_RULES)

        self._enabled = QCheckBox("启用文档排版规范")
        self._enabled.setStyleSheet("font-size:12px;color:#333;font-weight:600;")
        layout.addWidget(self._enabled)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("保存")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

        self._load(cfg)

    def _load(self, cfg: TypographyConfig) -> None:
        self._enabled.setChecked(cfg.enabled)
        for key, cb in self._boxes.items():
            cb.setChecked(bool(getattr(cfg, key)))

    def config(self) -> TypographyConfig:
        return TypographyConfig(
            enabled=self._enabled.isChecked(),
            **{key: cb.isChecked() for key, cb in self._boxes.items()},
        )


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("md-sync — 持续同步")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(920, 780)
        self.setMinimumSize(680, 540)

        self.tmgr = TemplateManager()
        self._plugin_registry = PluginRegistry()
        self._last_out_dir: str | None = None
        self._typo_cfg = TypographyConfig()
        self.cfg: ProjectConfig | None = None
        self.worker: SyncWorker | None = None
        self.watcher: FileWatcher | None = None
        self.watching = False
        self.source_mtime = 0.0
        self._resizing = None
        self._pending_sync = False
        self._hidden_paths: set = set()  # 仅作 PDF 中间产物的 html，不在列表显示
        self._syncing = False  # 是否正在同步（用于闪烁状态）

        # 状态闪烁动画：定时切换透明度，表现“动态持续过程”
        self._status_tags: list[StatusTag] = []
        self._pulse_on = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)
        self._pulse_timer.start(450)
        self._drag_pos: QPoint | None = None

        # 后台分块预下载 Typora 画廊预览图（主线程 QTimer，不阻塞不崩溃）
        self._pregen_queue: list[str] = []
        self._pregen_timer = QTimer(self)
        self._pregen_timer.timeout.connect(self._pregen_step)

        # 渲染主题下拉：两级分组（组头可折叠）
        self._style_base: list = []  # 非 typora- 前缀的模板（置顶）
        self._style_groups: list[tuple[str, list]] = []  # [(组名, [TemplateInfo])]
        self._collapsed_groups: set[str] | None = None  # 折叠的组名；None=未初始化（首次全部折叠）

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
        self._build_plugin_card(cw)  # Card 1: 插件管理
        self._build_output_card(cw)  # Card 2: 输出设置
        self._build_actions(cw)
        self._build_file_list(cw)  # Card 3: 输出文件
        self._build_log(cw)  # Card 4: 同步日志

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
        sel_lbl = QLabel("插件")
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
        self._detail_schema.setStyleSheet(
            "font-size:10px;color:#1a56db;background:#e8f0fe;padding:1px 6px;border-radius:2px;"
        )
        top_row.addWidget(self._detail_schema)
        self._detail_version = QLabel()
        self._detail_version.setStyleSheet(
            "font-size:10px;color:#999;background:#f5f5f5;padding:1px 6px;border-radius:2px;"
        )
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

        # 模板使用提示（需要特定源模板时红色警示；否则隐藏）
        self._template_warn = QLabel()
        self._template_warn.setStyleSheet("font-size:11px;color:#dc2626;font-weight:600;")
        self._template_warn.setWordWrap(True)
        self._template_warn.setMaximumHeight(30)
        da.addWidget(self._template_warn)

        # 公文字体缺失提示 + 一键安装（仅 gongwen 插件显示）
        self._font_warn = QLabel()
        self._font_warn.setStyleSheet("font-size:11px;color:#b45309;font-weight:600;")
        self._font_warn.setWordWrap(True)
        self._font_warn.setVisible(False)
        da.addWidget(self._font_warn)

        self._font_btn = QPushButton("⬇ 下载并安装公文字体（免费）")
        self._font_btn.setFixedHeight(24)
        self._font_btn.setVisible(False)
        self._font_btn.clicked.connect(self._install_gongwen_fonts)
        da.addWidget(self._font_btn)

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
            self, "保存模板文件", default_name, "Markdown (*.md);;All files (*)"
        )
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
            f"✓ 模板已保存 → {save_path}\n  请编辑文件，然后配置输出并点击「启动多格式同步输出」"
        )

        # 打开编辑器
        QDesktopServices.openUrl(QUrl.fromLocalFile(save_path))

    def _install_gongwen_fonts(self):
        """后台下载安装 Fandol 公文字体集，完成后刷新提示。"""
        if getattr(self, "_font_worker", None) and self._font_worker.isRunning():
            return
        self._font_btn.setEnabled(False)
        self._font_btn.setText("⏳ 正在下载安装字体…（约 27MB）")
        self._font_warn.setText("正在下载 Fandol 免费字体集并安装，请稍候…")

        self._font_worker = FontInstallWorker()
        self._font_worker.done.connect(self._on_fonts_installed)
        self._font_worker.start()

    def _on_fonts_installed(self, ok: bool, message: str):
        self._font_btn.setEnabled(True)
        self._append_log(("✓ " if ok else "✗ ") + message)
        if ok:
            try:
                from md_sync.plugins.gongwen.fonts import missing_fonts

                missing = missing_fonts()
            except Exception:
                missing = []
            if missing:
                self._font_warn.setText(
                    "⚠ 已安装字体，但仍有缺失：" + "、".join(missing) + "（可能需重启应用后生效）"
                )
                self._font_btn.setText("⬇ 重新下载并安装公文字体（免费）")
            else:
                self._font_warn.setText("✓ 公文标准字体已就绪（Fandol 仿宋/黑体/楷体/宋体）")
                self._font_warn.setStyleSheet("font-size:11px;color:#16a34a;font-weight:600;")
                self._font_btn.setVisible(False)
        else:
            self._font_warn.setText("⚠ " + message)
            self._font_btn.setText("⬇ 重试下载并安装公文字体（免费）")

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
        norm_btn = QPushButton("✂ 规范化源文档")
        norm_btn.setToolTip("按当前排版规范生成规范化副本作为源文件（原始文件不被修改），并自动勾选 md 输出")
        norm_btn.setStyleSheet(
            "background:#f59e0b;color:#ffffff;border:none;border-radius:6px;"
            "padding:0 10px;font-size:12px;"
        )
        norm_btn.clicked.connect(self._normalize_source)
        src_h.addWidget(self.source_edit, 1)
        src_h.addWidget(src_btn)
        src_h.addWidget(norm_btn)
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

        # ── 渲染主题（选插件后显示）—— 下拉时在选项光标处浮动预览 ──
        self._style_row_w = QWidget()
        self._style_row_w.setVisible(False)
        self._style_row_w.setMinimumHeight(48)  # 窗口偏矮时防止下拉框被压扁
        sr_lay = QVBoxLayout(self._style_row_w)
        sr_lay.setContentsMargins(0, 0, 0, 0)
        sr_lay.setSpacing(4)
        style_h = QHBoxLayout()
        style_h.setSpacing(6)
        style_lbl = QLabel("渲染主题")
        style_lbl.setFixedWidth(60)
        style_h.addWidget(style_lbl)
        zh_tag = QLabel("中文")
        zh_tag.setFixedWidth(32)
        style_h.addWidget(zh_tag)
        self.tpl_zh = QComboBox()
        self.tpl_zh.setFixedWidth(160)
        style_h.addWidget(self.tpl_zh)
        self._tpl_en_label = QLabel("英文")
        self._tpl_en_label.setFixedWidth(32)
        style_h.addWidget(self._tpl_en_label)
        self.tpl_en = QComboBox()
        self.tpl_en.setFixedWidth(160)
        style_h.addWidget(self.tpl_en)
        style_h.addStretch(1)
        sr_lay.addLayout(style_h)
        cv.addWidget(self._style_row_w)
        # ── 浮动预览窗口（紧贴 combobox 右侧，随下拉导航更新） ──
        self._floating_preview = FloatingPreview()
        zv = self.tpl_zh.view()
        zv.setMouseTracking(True)
        ev = self.tpl_en.view()
        ev.setMouseTracking(True)
        zv.entered.connect(lambda i: self._on_combo_preview(self.tpl_zh, i))
        ev.entered.connect(lambda i: self._on_combo_preview(self.tpl_en, i))
        zv.selectionModel().currentChanged.connect(
            lambda c, p: self._on_combo_preview(self.tpl_zh, c)
        )
        ev.selectionModel().currentChanged.connect(
            lambda c, p: self._on_combo_preview(self.tpl_en, c)
        )
        zv.installEventFilter(self)
        ev.installEventFilter(self)
        # 组头点击必须在 viewport 层拦截（见 eventFilter）：QComboBox 的弹层
        # 容器在 viewport 上也装了事件过滤器，鼠标释放落在任意 enabled 项上都会
        # hidePopup；组头是 enabled 但不可选中，若让释放事件传到内部过滤器，
        # 弹层会被关闭。我们在 viewport 上先消费掉组头点击，弹层保持打开。
        zvv = zv.viewport()
        evv = ev.viewport()
        zvv.installEventFilter(self)
        evv.installEventFilter(self)
        self.tpl_zh.activated.connect(self._floating_preview.hide)
        self.tpl_en.activated.connect(self._floating_preview.hide)
        # 兜底：若 viewport 过滤器因平台差异未命中，clicked 信号仍可触发折叠
        zv.clicked.connect(lambda i: self._on_style_group_clicked(self.tpl_zh, i))
        ev.clicked.connect(lambda i: self._on_style_group_clicked(self.tpl_en, i))
        # 键盘导航落组头（不可选中）时跳到组内首个主题（保险起见）
        self.tpl_zh.currentIndexChanged.connect(lambda _: self._fix_group_current(self.tpl_zh))
        self.tpl_en.currentIndexChanged.connect(lambda _: self._fix_group_current(self.tpl_en))
        # 弹层高度：此 PySide6 版本忽略 maxVisibleItems（实测 359 项弹层仍撑到
        # 800px），设标准值仅作兜底；真正生效靠 eventFilter 在弹层打开后
        # 延迟一帧封顶容器高度（_cap_popup_height），超长列表自动滚动。
        for combo in (self.tpl_zh, self.tpl_en):
            combo.setMaxVisibleItems(14)

        # ── 输出格式（每个格式一个组，组内堆叠「格式卡片」+「专属控制项」） ──
        fmt_head = QWidget()
        fmt_head.setStyleSheet("background:transparent;")
        fmt_head_lay = QHBoxLayout(fmt_head)
        fmt_head_lay.setContentsMargins(0, 0, 0, 0)
        fmt_head_lay.setSpacing(8)
        fmt_label = QLabel("输出格式")
        fmt_label.setStyleSheet("font-size:11px;color:#555;font-weight:600;margin-top:2px;")
        fmt_head_lay.addWidget(fmt_label)
        fmt_head_lay.addStretch(1)
        self.typo_btn = QPushButton("📐 文档标准配置")
        self.typo_btn.setObjectName("typo_btn")
        self.typo_btn.setToolTip(
            "中英文排版规则（对齐 W3C CLReq / CY/T 154-2017）——影响生成产物与「规范化源文档」，不改源文件"
        )
        self.typo_btn.clicked.connect(self._open_typography)
        fmt_head_lay.addWidget(self.typo_btn)
        cv.addWidget(fmt_head)

        self.fmt_checks: dict[tuple[str, str], QCheckBox] = {}
        formats = [
            ("html", "HTML"),
            ("md", "Markdown"),
            ("pdf", "PDF"),
            ("docx", "DOCX"),
            ("epub", "EPUB"),
        ]
        fmt_row = QHBoxLayout()
        fmt_row.setContentsMargins(0, 0, 0, 0)
        fmt_row.setSpacing(6)

        for i, (fmt, label_txt) in enumerate(formats):
            # 格式组容器：纵向堆叠「格式卡片」与其专属控制项（如 PDF 页边距），
            # 让每种格式的控制项各自成组、互不影响。
            group = QWidget()
            group.setObjectName("fmt_group")
            group_lay = QVBoxLayout(group)
            group_lay.setContentsMargins(0, 0, 0, 0)
            group_lay.setSpacing(4)
            group_lay.setAlignment(Qt.AlignTop)

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

            group_lay.addWidget(fc)

            # 专属控制项：仅 PDF 有页边距，直接挂在 PDF 组下方，与 PDF 卡片成组
            if fmt == "pdf":
                margin_w = QWidget()
                margin_w.setFixedHeight(28)
                mh = QHBoxLayout(margin_w)
                mh.setContentsMargins(2, 0, 2, 0)
                mh.setSpacing(4)
                mh.addWidget(QLabel("页边距"))
                self.margin_combo = QComboBox()
                self.margin_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                for val, label in MARGIN_LABELS.items():
                    self.margin_combo.addItem(label, val)
                self.margin_combo.setCurrentIndex(0)
                mh.addWidget(self.margin_combo)
                mh.addStretch(8)
                mh.addWidget(QLabel("页面尺寸"))
                self.page_size_combo = QComboBox()
                self.page_size_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                for val in ("A4", "A3", "A5", "Letter", "Legal"):
                    self.page_size_combo.addItem(val, val)
                self.page_size_combo.setCurrentText("A4")
                mh.addWidget(self.page_size_combo)
                group_lay.addWidget(margin_w)

            fmt_row.addWidget(group, 1)

        cv.addLayout(fmt_row)

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
        # ── 重名处理 — 与标题同一行，选插件后显示 ──
        self._naming_label = QLabel("重名处理")
        self._naming_label.setObjectName("naming_label")
        self._naming_label.setVisible(False)
        header.addWidget(self._naming_label)
        self.naming_ts = QRadioButton("加时间戳")
        self.naming_ts.setVisible(False)
        self.naming_overwrite = QRadioButton("覆盖")
        self.naming_overwrite.setVisible(False)
        self._naming_group = QButtonGroup(self)
        self._naming_group.addButton(self.naming_ts)
        self._naming_group.addButton(self.naming_overwrite)
        self.naming_ts.setChecked(True)
        header.addWidget(self.naming_ts)
        header.addWidget(self.naming_overwrite)
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

        self.src_info = QLabel("")
        self.src_info.setObjectName("src_info")
        self.src_info.setVisible(False)  # 初始隐藏，有内容时才显示
        cv.addWidget(self.src_info)

        self.file_tbl = QTableWidget(0, 6)
        self.file_tbl.setObjectName("file_table")
        self.file_tbl.setHorizontalHeaderLabels(
            ["状态", "格式", "语言", "文件", "最后更新时间", "操作"]
        )
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
        self.file_tbl.setColumnWidth(0, 96)
        self.file_tbl.setColumnWidth(1, 80)
        self.file_tbl.setColumnWidth(2, 70)
        self.file_tbl.setColumnWidth(4, 210)
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
            # 选项名用中文显示名（用户语言），回退到机器名；保留 Typora 等专名
            self.plugin_combo.addItem(p.label or p.name, p.name)
        # 默认选中第一个（应是 resume）
        if self._plugins:
            self._on_plugin_changed(0)
        else:
            self._reload_style_combos()

    def _on_plugin_changed(self, idx: int):
        """插件包切换时，更新详情区域 + 显示/隐藏风格行。"""
        # 插件切换后下拉内容已失效，残留的浮动预览一并隐藏
        self._floating_preview.hide()
        self._pregen_timer.stop()
        if idx < 0 or not self._plugins:
            self._detail_area.setVisible(False)
            self._style_row_w.setVisible(False)
            self._naming_label.setVisible(False)
            self.naming_ts.setVisible(False)
            self.naming_overwrite.setVisible(False)
            # Also hide source row
            self._source_row.setVisible(False)
            return
        plugin = self._plugins[idx]
        schema = plugin.parser_schema or "resume"

        # ── 语言策略：公文（gongwen）仅支持中文，禁用英文选项 ──
        self._apply_lang_policy()

        # ── 更新插件详情 ──
        tpl_list = ", ".join(plugin.templates) if plugin.templates else "系统内置"
        self._detail_name.setText(plugin.label or plugin.name)
        self._detail_name.setToolTip(f"插件标识：{plugin.name}")
        self._detail_version.setText(f"v{plugin.version}" if plugin.version else "")
        self._detail_schema.setText(f"schema: {schema}")
        self._detail_desc.setText(plugin.description or "")
        self._detail_desc.setToolTip(plugin.description or "")
        # Typora 插件：若本机未安装 Typora（主题目录不存在），提示用户
        if plugin.name == "typora":
            from md_sync.plugins.typora.paths import is_typora_installed

            if not is_typora_installed():
                self._detail_desc.setText(
                    (plugin.description or "") + "\n\n⚠ 未检测到本机已安装 Typora，暂无可用主题。"
                    "请先安装 Typora，其主题会自动出现在「渲染主题」下拉框中。"
                )

        # ── 公文字体检测：gongwen 插件缺字体时提示安装（免费 Fandol） ──
        self._font_warn.setVisible(False)
        self._font_btn.setVisible(False)
        if plugin.name == "gongwen":
            try:
                from md_sync.plugins.gongwen.fonts import missing_fonts

                missing = missing_fonts()
            except Exception:
                missing = []
            if missing:
                self._font_warn.setText(
                    "⚠ 本机缺少公文标准字体（" + "、".join(missing) + "），"
                    "渲染会回退到 Noto，字形不标准。可一键下载免费 Fandol 字体安装。"
                )
                self._font_warn.setVisible(True)
                self._font_btn.setVisible(True)

        self._detail_templates.setText(tpl_list)
        self._detail_area.setVisible(True)

        # ── 模板生成与警示 ──
        if plugin.requires_template:
            self._template_btn.setVisible(True)
            self._template_warn.setVisible(True)
            self._template_warn.setText(
                "⚠ 必须使用生成的模板：请点击「生成模板」获取规定的源文件格式，否则无法正确解析。"
            )
        else:
            self._template_btn.setVisible(False)
            self._template_warn.setVisible(False)

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

        # ── 预生成当前默认风格预览（同步，仅一个，用户可接受等 1-2s） ──
        default_style = self.tpl_zh.currentData()
        if default_style:
            _get_or_create_preview(default_style, self.tmgr)
        # ── 后台分块预下载 Typora 画廊预览图（QTimer 主线程，不阻塞 UI） ──
        self._start_pregen(infos)

        # ── 显示「重名处理」并按当前策略勾选 ──
        self._naming_label.setVisible(True)
        self.naming_ts.setVisible(True)
        self.naming_overwrite.setVisible(True)
        naming = getattr(self.cfg, "output_naming", "timestamp") or "timestamp"
        (self.naming_overwrite if naming == "overwrite" else self.naming_ts).setChecked(True)

        self._append_log(
            f"已选择插件包「{plugin.name}」schema={schema}，"
            f"风格：{', '.join(t.name for t in infos) if infos else '系统内置'}"
        )

    def _apply_lang_policy(self):
        """公文（gongwen）仅支持中文：禁用英文输出选项与英文模板选择。"""
        zh_only = self._current_schema() == "gongwen"
        for (fmt, lang), cb in self.fmt_checks.items():
            if lang == "en":
                cb.setEnabled(not zh_only)
                if zh_only:
                    cb.setChecked(False)
        self._tpl_en_label.setEnabled(not zh_only)
        self.tpl_en.setEnabled(not zh_only)

    def _start_pregen(self, infos: list | None):
        """收集未缓存的 Typora 主题，分块下载画廊预览图（主线程 QTimer）。"""
        if self._pregen_timer.isActive():
            self._pregen_timer.stop()
        self._pregen_queue = []
        if not infos:
            return
        for t in infos:
            if not t.name.startswith("typora-"):
                continue
            css_stem = t.name[7:]
            if css_stem not in _TYPORA_GALLERY_URLS:
                continue  # 画廊无此主题 → 交给运行时 Chromium 渲染
            p = _PREVIEW_CACHE_DIR / f"{t.name}.png"
            if p.exists() or t.name in _PREVIEW_CACHE:
                continue  # 已有缓存
            self._pregen_queue.append(css_stem)
        if self._pregen_queue:
            self._pregen_timer.start(200)  # 每 200ms 下载一个，降低 UI 抖动

    def _pregen_step(self):
        """每个 tick 下载一个画廊缩略图到磁盘缓存。"""
        if not self._pregen_queue:
            self._pregen_timer.stop()
            return
        css_stem = self._pregen_queue.pop(0)
        cache_path = _PREVIEW_CACHE_DIR / f"typora-{css_stem}.png"
        if not cache_path.exists():
            _download_typora_file(css_stem, cache_path)
        if not self._pregen_queue:
            self._pregen_timer.stop()

    def closeEvent(self, event):
        self._floating_preview.hide()
        self._pregen_timer.stop()
        super().closeEvent(event)

    def _on_combo_preview(self, combo: QComboBox, index):
        # 只在下拉弹层真正打开时显示预览。selectionModel().currentChanged
        # 在弹层关闭后（如 _reload_style_combos 调用 setCurrentIndex）也会触发，
        # 此时弹层不可见，预览必须隐藏而不是重新弹出。
        if not combo.view().isVisible():
            self._floating_preview.hide()
            return
        if not index.isValid():
            self._floating_preview.hide()
            return
        style_name = combo.itemData(index.row()) or ""
        if not style_name:
            self._floating_preview.hide()
            return
        display_name = (
            style_name[7:].replace("-", " ").title()
            if style_name.startswith("typora-")
            else style_name
        )

        # Step 1: 优先从缓存获取（即时）
        cached_pix = _PREVIEW_CACHE.get(style_name)
        if cached_pix is not None:
            self._floating_preview.set_preview(cached_pix, display_name)
            gp = combo.mapToGlobal(combo.rect().topRight())
            self._floating_preview.move(gp.x(), gp.y())
            self._floating_preview.show()
            self._floating_preview.raise_()
            return

        # Step 2: 缓存未命中 → 先显示「加载中…」，强制刷新，再生成
        self._floating_preview.set_preview(None, display_name)
        gp = combo.mapToGlobal(combo.rect().topRight())
        self._floating_preview.move(gp.x(), gp.y())
        self._floating_preview.show()
        self._floating_preview.raise_()
        QApplication.processEvents()  # 立即绘制「加载中…」

        pix = _get_or_create_preview(style_name, self.tmgr)
        self._floating_preview.set_preview(pix, display_name)

    def eventFilter(self, obj, event):
        # 弹层打开（视图被弹层容器接管后收到 Show）：延迟一帧封顶弹层高度。
        # 此 PySide6 版本 QComboBox 弹层忽略 maxVisibleItems，359 项能撑出 800px
        # 弹层；限制容器高度后其余滚动（竖向滚动条 ScrollBarAsNeeded）。
        if event.type() == QEvent.Show:
            for combo in (getattr(self, "tpl_zh", None), getattr(self, "tpl_en", None)):
                if combo is not None and obj is combo.view():
                    QTimer.singleShot(0, lambda c=combo: self._cap_popup_height(c))
                    break
        if event.type() == QEvent.Hide and hasattr(self, "_floating_preview"):
            for combo in (getattr(self, "tpl_zh", None), getattr(self, "tpl_en", None)):
                if combo is not None and obj is combo.view():
                    self._floating_preview.hide()
                    break
        # 组头点击（▸/▾ 分组行）→ 展开/收起，弹层保持打开、不误选。
        # 在 viewport 上消费鼠标释放：QComboBox 内部容器在 viewport 上也装了
        # 事件过滤器，释放落在任意 enabled 项上都会 hidePopup；组头是 enabled
        # 但不可选中，若不拦截，弹层会被内部过滤器关闭。返回 True 后事件不再
        # 传给容器，弹层保留，同时视图不产生 clicked（不会重复触发）。
        if event.type() == QEvent.MouseButtonRelease:
            for combo in (getattr(self, "tpl_zh", None), getattr(self, "tpl_en", None)):
                if combo is not None and obj is combo.view().viewport():
                    idx = combo.view().indexAt(event.position().toPoint())
                    if idx.isValid():
                        gname = combo.itemData(idx.row(), _ROLE_GROUP)
                        if gname:
                            # 延迟一帧重建：先让本次点击事件完整结束，再改模型，
                            # 弹层容器在 modelReset 后按新行数自适应高度。
                            QTimer.singleShot(
                                0, lambda g=gname, c=combo: self._toggle_style_group(g, c)
                            )
                            return True
                    break
        return super().eventFilter(obj, event)

    def _cap_popup_height(self, combo: QComboBox):
        """普通下拉弹层封顶 ~14 行：行数少时按实际行数自适应，多时其余滚动。"""
        cont = combo.view().parent()
        if cont is None or cont is combo:
            return  # 容器尚未创建/未被接管（弹层未打开过）
        n = combo.count()
        # 组头为粗体行，略高于普通行；取首行行高并加保险值，避免末行被裁
        row_h = combo.view().sizeHintForRow(0)
        if row_h <= 0:
            row_h = 24
        row_h = max(row_h, 22)
        h = min(max(n, 1), 14) * row_h + 12  # +12 容器边框/内边距余量
        if cont.height() != h:
            cont.setFixedHeight(h)

    def _reload_style_combos(self, infos: list | None = None):
        """填充渲染主题下拉框（Typora 主题按仓库分组，两级可折叠）。"""
        # 下拉即将重建，任何残留的浮动预览都要先隐藏
        self._floating_preview.hide()
        if infos:
            self._style_base = [t for t in infos if not t.name.startswith("typora-")]
            self._style_groups = self._group_typora_infos(
                [t for t in infos if t.name.startswith("typora-")]
            )
            # 默认全部折叠，减少选项；保留用户展开过的组不变。
            # 仅在首次遇到非空分组列表时初始化（None 哨兵）：resume 等无分组
            # 插件会把集合置空，切回 typora 时若再判断空集就会错误地全部重折叠。
            if self._collapsed_groups is None and self._style_groups:
                self._collapsed_groups = set(g for g, _ in self._style_groups)
        else:
            self._style_base = []
            self._style_groups = []
        for combo in (self.tpl_zh, self.tpl_en):
            self._rebuild_style_combo(combo)
            self._cap_popup_height(combo)

    @staticmethod
    def _style_disp(t) -> str:
        """模板显示名：去掉 "Typora " 前缀。"""
        disp = t.label
        if disp.lower().startswith("typora "):
            disp = disp[len("Typora ") :]
        return disp

    def _group_typora_infos(self, infos: list) -> list[tuple[str, list]]:
        """把 typora 主题按仓库公共前缀分组，保持字母序。"""
        groups: dict[str, list] = {}
        others: list = []
        for t in infos:
            stem = t.name[len("typora-") :]
            key = _typora_group_key(stem)
            if key:
                groups.setdefault(key, []).append(t)
            else:
                others.append(t)
        ordered = [(k, groups[k]) for k in sorted(groups)]
        if others:
            ordered.append(("其他主题", sorted(others, key=lambda t: t.name)))
        return ordered

    def _rebuild_style_combo(self, combo: QComboBox):
        """按当前分组状态重建单个下拉框（组头不可选中，可点击折叠）。"""
        prev = combo.currentData()
        # 当前选中的主题所在组强制展开：避免收起后选中项消失、无声回退到 bwx
        if prev is not None:
            for gname, members in self._style_groups:
                if any(m.name == prev for m in members):
                    self._collapsed_groups.discard(gname)
                    break
        combo.clear()
        model = combo.model()

        def _add_item(disp: str, name: str):
            it = QStandardItem(disp)
            it.setData(name, Qt.UserRole)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            model.appendRow(it)

        if not self._style_base and not self._style_groups:
            _add_item("(默认 bwx)", "bwx")
            combo.setCurrentIndex(0)
            return

        for t in self._style_base:
            _add_item(self._style_disp(t), t.name)
        for gname, members in self._style_groups:
            collapsed = gname in self._collapsed_groups
            head = QStandardItem(f"{'▸' if collapsed else '▾'} {gname} ({len(members)})")
            head.setData(gname, _ROLE_GROUP)
            head.setFlags(Qt.ItemIsEnabled)  # 可点击（事件过滤器），不可选中
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            head.setForeground(QBrush(QColor("#6b7280")))
            head.setBackground(QBrush(QColor("#f1f1f4")))
            model.appendRow(head)
            if not collapsed:
                for t in members:
                    _add_item(self._style_disp(t), t.name)

        # 恢复之前选中（当前选中仍有效则保持；否则回退 bwx / 首项）
        idx = combo.findData(prev) if prev is not None else -1
        if idx < 0:
            idx = combo.findData("bwx")
        if idx < 0 and combo.count() > 0:
            idx = 0
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _on_style_group_clicked(self, combo: QComboBox, index):
        """点击组头（▸/▾）→ 展开/收起该分组（viewport 过滤器未命中时的兜底）。"""
        if not index.isValid():
            return
        gname = combo.itemData(index.row(), _ROLE_GROUP)
        if not gname:
            return
        QTimer.singleShot(0, lambda g=gname, c=combo: self._toggle_style_group(g, c))

    def _toggle_style_group(self, gname: str, combo: QComboBox):
        """展开/收起一个分组，重建两个下拉框（保持弹层打开）。"""
        if self._collapsed_groups is None:
            self._collapsed_groups = set()
        if gname in self._collapsed_groups:
            self._collapsed_groups.discard(gname)
        else:
            self._collapsed_groups.add(gname)
        for c in (self.tpl_zh, self.tpl_en):
            self._rebuild_style_combo(c)
            self._cap_popup_height(c)

    def _fix_group_current(self, combo: QComboBox):
        """键盘导航落到组头（不可选中）时，跳到组内第一个主题。"""
        idx = combo.currentIndex()
        if idx < 0 or idx >= combo.count():
            return
        if combo.itemData(idx, _ROLE_GROUP) is None:
            return  # 非组头
        # 找到下一个可选中项（组内第一个主题，或后续项）
        for i in range(idx + 1, combo.count()):
            if combo.itemData(i, _ROLE_GROUP) is None:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

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
        QLabel#naming_label { color:#71717a; font-size:13px; padding-left: 6px; }
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
        /* 重名处理单选（亮色主题） */
        QRadioButton { color: #27272a; spacing: 6px; font-size: 13px; }
        QRadioButton::indicator {
            width: 16px; height: 16px; border-radius: 8px;
            border: 1.5px solid #cbd5e1; background: #ffffff;
        }
        QRadioButton::indicator:checked { background: #1890ff; border: 1.5px solid #1890ff; }
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
        QPushButton#secondary {
            background: #ffffff; border: 1px solid #e5e7eb; color: #3f3f46;
        }
        QPushButton#secondary:hover { background: #f4f4f5; }
        QPushButton#typo_btn {
            background: #ffffff;
            border: 1px solid #d8deea;
            border-radius: 7px;
            padding: 3px 10px;
            color: #4f46e5;
            font-size: 11px;
            font-weight: 600;
        }
        QPushButton#typo_btn:hover { background: #eef2ff; border-color: #6366f1; color: #4338ca; }
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
            self, "选择 Markdown 文件", "", "Markdown (*.md);;All files (*)"
        )
        if path:
            self.source_edit.setText(path)
            if not self.out_edit.text().strip():
                self.out_edit.setText(str(Path(path).parent))
            self._validate_form()

    def _open_typography(self):
        """打开「文档标准配置」对话框；保存后若正在监听则立即用新规则重跑输出。"""
        dlg = TypographyDialog(self._typo_cfg, self)
        if dlg.exec():
            self._typo_cfg = dlg.config()
            self._append_log("· 排版规范已更新（文档标准配置）")
            if self.watching:
                self._run_sync()

    def _normalize_source(self):
        """生成规范化源文档副本并设为源文件（原始文件不被修改）。

        - 按当前排版规范（默认全开，可在「文档标准配置」调整）规范化源文本
        - 写到 <stem>_normalized.md（源已是 *_normalized 则原地重生成）
        - 将「源文件」输入框指向新文件，并自动勾选 md/<源语言> 输出
        """
        src_txt = self.source_edit.text().strip()
        if not src_txt:
            QMessageBox.warning(self, "缺少源文件", "请先选择 Markdown 源文件。")
            return
        src = Path(src_txt).expanduser()
        if not src.exists():
            QMessageBox.warning(self, "文件不存在", f"源文件不存在：\n{src}")
            return
        try:
            text = src.read_text(encoding="utf-8")
            # 语言跟随源文件：与解析器一致的判定（zh 字符数 > 100 → 中文）
            zh_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            lang = "zh" if zh_chars > 100 else "en"
            normalized = normalize_for_lang(text, self._typo_cfg, lang)
            if normalized == text:
                QMessageBox.information(
                    self,
                    "规范化源文档",
                    f"源文档已符合排版规范，无需修改（{LANG_LABELS.get(lang, lang)}）。",
                )
                return

            stem = src.stem
            target = src if stem.endswith("_normalized") else src.with_name(f"{stem}_normalized.md")
            target.write_text(normalized, encoding="utf-8")
            self.source_edit.setText(str(target))
            if not self.out_edit.text().strip():
                self.out_edit.setText(str(target.parent))
            self.fmt_checks[("md", lang)].setChecked(True)
            self._validate_form()
            QMessageBox.information(
                self,
                "规范化源文档",
                f"✓ 已生成规范化源文档（{LANG_LABELS.get(lang, lang)}，"
                f"{len(normalized)}/{len(text)} 字符）\n\n"
                f"新源文件：{target}\n\n"
                f"原始文件未被修改，并已自动勾选 md/{lang} 输出。",
            )
        except Exception as e:
            QMessageBox.critical(self, "规范化失败", f"规范化失败：\n{e}")

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

    def _build_config(self) -> ProjectConfig | None:
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
        langs = [l for l in ("zh", "en") if any(sel[(f, l)] for f in all_formats)]
        if not langs:
            QMessageBox.warning(self, "缺少输出", "请至少为一种格式勾选一种语言。")
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
                    if want_pdf
                    else None
                )
                outputs.append(
                    OutputConfig(
                        format="html",
                        lang=lang,
                        path=html_path,
                        pdf=want_pdf,
                        pdf_path=pdf_path,
                        style=style,
                        page_size=self.page_size_combo.currentData(),
                        page_margin=self.margin_combo.currentData(),
                    )
                )
                if not want_html:
                    self._hidden_paths.add(html_path)
            if want_md:
                md_path = derive_output_path(root, "md", lang, name_map, stem)
                outputs.append(OutputConfig(format="md", lang=lang, path=md_path, style=style))
            if want_docx:
                docx_path = derive_output_path(root, "docx", lang, name_map, stem)
                outputs.append(
                    OutputConfig(
                        format="docx",
                        lang=lang,
                        path=docx_path,
                        style=style,
                        page_size=self.page_size_combo.currentData(),
                    )
                )
            if want_epub:
                epub_path = derive_output_path(root, "epub", lang, name_map, stem)
                outputs.append(OutputConfig(format="epub", lang=lang, path=epub_path, style=style))

        cfg = ProjectConfig(
            project=stem,
            source=str(src_path),
            schema=self._current_schema(),
            outputs=outputs,
            output_root=str(root),
            source_lang="zh",
            name_map=name_map,
            typography=self._typo_cfg,
        )
        cfg.source_path = src_path.resolve()
        # 重名处理策略：来自设置面板的「重名处理」单选（默认时间戳）
        cfg.output_naming = "overwrite" if self.naming_overwrite.isChecked() else "timestamp"
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
            cfg.source_path, self._on_source_changed, debounce=1.5, output_root=cfg.output_root
        )
        self.watcher.start()
        self.watching = True
        self.watch_btn.setText("重启输出")
        self.watch_btn.setStyleSheet(
            "background:#ef4444;border:1px solid #ef4444;color:#fff;font-weight:500;"
        )
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
        self.worker.sync_finished.connect(self._on_finished)
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
            meta_lbl = QLabel(f"{size}" if exists else "— 尚未生成")
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
            ts_lo.setContentsMargins(4, 0, 4, 0)
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
            # 非同步完成（待同步 / 文件不存在）时禁用「打开」：无可打开的文件
            open_btn.setEnabled(color == C_SYNCED)
            ops_lo.addWidget(open_btn)

            del_btn = QPushButton("删除")
            del_btn.setObjectName("cell_btn_danger")
            del_btn.clicked.connect(self._on_delete_clicked)
            del_btn._file_path = path
            ops_lo.addWidget(del_btn)

            self.file_tbl.setCellWidget(row, 5, ops_cell)
        self.clear_all_btn.setEnabled(any_row)
        if not self.cfg or not any_row:
            # 未配置输出，或（删除全部后）已无任何产物时，显示空态提示；
            # 否则保留剩余产物行不追加占位行，避免「文件 + 占位」并存。
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
            # 目标语言 = 实际配置的输出语言（不再按源语言反推），避免误导
            out_langs = sorted({o.lang for o in self.cfg.outputs})
            tl_txt = "、".join(LANG_LABELS.get(l, l) for l in out_langs) or "未配置"
            pend = info.get("pending_translations", [])
            pend_txt = (
                "、".join(
                    f"{LANG_LABELS.get(p.get('lang'), p.get('lang'))} 待译 {p.get('missing')} 条"
                    for p in pend
                )
                or "无"
            )
            secs = len(info.get("sections", []))
            self.src_info.setText(
                f"源语言：{LANG_LABELS.get(sl, sl)} ｜ 目标：{tl_txt} ｜ "
                f"章节 {secs} ｜ {pend_txt} ｜ 文件：{self.cfg.source_path.name}"
            )
            self.src_info.setVisible(True)
        except Exception as e:
            self.src_info.setText(f"源语言检测失败：{e}")
            self.src_info.setVisible(True)

    # ── Open / copy ────────────────────────────────────────────────────
    def _delete_output_file(self, path: str):
        """删除单个输出文件，并同步从配置产物中移除对应条目，使该行清空。"""
        if not path:
            return
        p = Path(path)
        name = p.name
        if not p.exists():
            # 文件已不存在，但配置里仍有该行：直接移除条目并刷新
            self._remove_output_entry(path)
            self._refresh_file_list()
            return
        ret = QMessageBox.question(
            self,
            "删除文件",
            f"确定要删除「{name}」吗？\n{path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            p.unlink(missing_ok=True)
            # 同步从 cfg.outputs 移除该产物（含其 PDF 项），否则刷新后此行仍会显示
            self._remove_output_entry(path)
            self._append_log(f"🗑 已删除：{name}")
            self._refresh_file_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"无法删除文件：\n{e}")

    def _remove_output_entry(self, path: str):
        """从 self.cfg.outputs 中移除指向 path（或以其为 pdf_path）的产物条目。"""
        if self.cfg is None:
            return
        norm = Path(path).resolve()
        kept = []
        for o in self.cfg.outputs:
            if o.path and Path(o.path).resolve() == norm:
                continue
            if o.pdf_path and Path(o.pdf_path).resolve() == norm:
                # 仅 PDF 文件被删：清空 pdf_path，保留主产物行
                o.pdf_path = None
                o.pdf = False
            kept.append(o)
        self.cfg.outputs = kept

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
            self,
            "清除全部输出文件",
            f"确定要删除以下 {len(unique)} 个文件吗？\n\n{names}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
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
        path = getattr(btn, "_file_path", None) if btn else None
        self._open_file(path)

    def _on_delete_clicked(self):
        """Slot for the 删除 button in the output file table."""
        btn = self.sender()
        path = getattr(btn, "_file_path", None) if btn else None
        self._delete_output_file(path)

    def _open_file(self, path):
        if not path or not Path(path).exists():
            QMessageBox.information(self, "文件未生成", "该文件尚未生成或已删除，无法打开。")
            return
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
            self.title_bar.btn_max.setIcon(_window_icon("restore" if self.isMaximized() else "max"))
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

    def _edge_at(self, pos: QPoint) -> str | None:
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
    # Avoid the Qt warning "invalid style override 'kvantum' passed" that
    # appears when QT_STYLE_OVERRIDE names a style plugin not available in
    # this Qt build (e.g. running headless / minimal Qt with only Windows/Fusion).
    # Only clear it when the requested style is genuinely unavailable, so a
    # valid user preference in a real desktop session is preserved.
    if "QT_STYLE_OVERRIDE" in os.environ:
        try:
            from PySide6.QtWidgets import QStyleFactory

            if os.environ["QT_STYLE_OVERRIDE"] not in QStyleFactory.keys():
                os.environ.pop("QT_STYLE_OVERRIDE")
        except Exception:
            pass
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
