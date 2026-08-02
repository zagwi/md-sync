"""Gongwen PDF exporter — 覆盖基础 PDF 导出，叠加 GB/T 9704-2012 页码。

基础 ``export_pdf``（Chromium CLI）无法注入页脚，而国标 7.5 要求每页在版心下
边缘之下 7mm 处编排 4 号半角宋体阿拉伯数字页码，数字左右各一条一字线
（— 1 —），单页码居右空一字、双页码居左空一字。

Chromium 150 的 ``Page.printToPDF`` 存在 bug：开启 ``displayHeaderFooter`` 时
会忽略全部边距参数（内容铺满整页）。因此这里改为两步：

1. 用 CDP 导出**带边距、无页脚**的 PDF（边距正常）；
2. 用 PyMuPDF 按国标坐标在每页叠加页码。

本插件导出器优先于基础导出器（见 ``pipeline`` 的 PDF 分支），无 CDP / 无
PyMuPDF 时自动降级（退基础 CLI 导出、跳过页码）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from md_sync.exporters.pdf import export_pdf, export_pdf_cdp
from md_sync.plugin.interface import PdfExporter

logger = logging.getLogger(__name__)

# GB/T 9704-2012 页码参数（单位：mm）
#   版心：天头 37 / 翻口 26 / 订口 28 / 下 35 → 版心宽 156、高 225
#   页码 4 号字 = 14pt；编排在版心下边缘之下 7mm
#   单页码居右空一字（距版心右缘 1 字 = 14pt）
#   双页码居左空一字（距版心左缘 1 字 = 14pt）
_MM = 72.0 / 25.4  # mm → pt
_PAGE_W_MM, _PAGE_H_MM = 210.0, 297.0
_RIGHT_MARGIN_MM = 26.0
_LEFT_MARGIN_MM = 28.0
_BOTTOM_MARGIN_MM = 35.0
_PAGE_NO_GAP_MM = 7.0
_CHAR_PT = 14.0  # 4 号字 = 14pt，一字宽 = 14pt
_FONT_SIZE_PT = 14.0

_FONT_CANDIDATES = (
    "FandolSong-Regular.otf",
    "FandolSong.ttf",
    "FandolSong-Regular.ttf",
)


def _song_font_path() -> Path | None:
    """Locate an installed 宋体 font (FandolSong) for page-number stamping."""
    dirs = [
        Path.home() / ".local/share/fonts/md-sync-fandol",
        Path.home() / ".fonts/md-sync-fandol",
        Path("/usr/share/fonts/fandol"),
    ]
    for d in dirs:
        if not d.is_dir():
            continue
        for candidate in _FONT_CANDIDATES:
            hit = d / candidate
            if hit.exists():
                return hit
    return None


def stamp_gongwen_page_numbers(pdf_path: Path) -> bool:
    """Overlay GB/T 9704-2012 page numbers ``— n —`` on a finished PDF.

    Requires PyMuPDF (``fitz``); returns False (and logs) when unavailable,
    leaving the PDF without page numbers.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("[gongwen-pdf] PyMuPDF 未安装，跳过页码叠加")
        return False

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        logger.warning("[gongwen-pdf] 无法打开 PDF 叠加页码: %s", e)
        return False

    right_edge = (_PAGE_W_MM - _RIGHT_MARGIN_MM) * _MM  # 版心右缘 (pt)
    left_edge = _LEFT_MARGIN_MM * _MM  # 版心左缘 (pt)
    baseline_y = (_PAGE_H_MM - _BOTTOM_MARGIN_MM + _PAGE_NO_GAP_MM) * _MM

    font_path = _song_font_path()
    try:
        font = fitz.Font(fontfile=str(font_path)) if font_path else None
    except Exception:
        font = None
    # 无字体文件时退回 PyMuPDF 内置宋体
    fontname = "china-s" if font is None else f"gongwen-{font_path.stem}"
    fontfile = None if font is None else str(font_path)

    for i in range(doc.page_count):
        page = doc[i]
        n = i + 1
        text = f"\u2014 {n} \u2014"  # — n —
        if font is not None:
            tw = font.text_length(text, fontsize=_FONT_SIZE_PT)
        else:
            tw = fitz.get_text_length(text, fontname, fontsize=_FONT_SIZE_PT)
        # 单页码：右缘对齐版心右缘空一字 → 起点 = 版心右缘 − 一字 − 文字宽
        # 双页码：左缘对齐版心左缘空一字
        x = (right_edge - _CHAR_PT - tw) if n % 2 == 1 else (left_edge + _CHAR_PT)
        page.insert_text(
            (x, baseline_y),
            text,
            fontsize=_FONT_SIZE_PT,
            fontname=fontname,
            fontfile=fontfile,
        )

    try:
        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    except Exception as e:
        logger.warning("[gongwen-pdf] 保存页码失败: %s", e)
        return False
    finally:
        doc.close()
    return True


class GongwenPdfExporter(PdfExporter):
    """GB/T 9704-2012 PDF 导出：A4 版心 156×225mm + 国标页码叠加。"""

    @property
    def name(self) -> str:
        return "gongwen"

    def export(
        self,
        html_path,
        pdf_path,
        chromium_path: str | None = None,
        page_margin: str = "37mm 26mm 35mm 28mm",
        page_size: str = "A4",
        extra_args: list[str] | None = None,
        style_name: str = "",
    ) -> bool:
        ok = export_pdf_cdp(
            html_path=html_path,
            pdf_path=pdf_path,
            chromium_path=chromium_path,
            page_margin=page_margin,
            page_size=page_size,
            footer_template="",  # Chromium 150 开页脚会丢边距 → 事后叠加页码
            style_name=style_name,
        )
        if not ok:
            # 降级：CDP 不可用时退回基础 Chromium CLI 导出（无页码）。
            logger.warning("[gongwen-pdf] CDP 导出失败，退回基础导出")
            ok = export_pdf(
                html_path=html_path,
                pdf_path=pdf_path,
                chromium_path=chromium_path,
                page_margin=page_margin,
                page_size=page_size,
                extra_args=extra_args,
                style_name=style_name,
            )
        if ok:
            stamp_gongwen_page_numbers(Path(pdf_path))
        return ok
