"""Pipeline: orchestrate the full sync from source MD to all outputs.

Flow:
  1. Read & parse source MD → Document
  2. For each output target:
     a. If translation needed → lookup / AI-fallback
     b. Render to target format
     c. Write file
     d. If PDF → export via Chromium
  3. Save translation cache
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from md_sync.config import OutputConfig, ProjectConfig
from md_sync.core.document import Document

logger = logging.getLogger(__name__)

from md_sync.core.parser import MdParser
from md_sync.exporters.page import resolve_margin
from md_sync.exporters.pandoc import export_via_pandoc
from md_sync.exporters.pdf import export_pdf as _export_pdf
from md_sync.plugin.registry import PluginRegistry
from md_sync.renderers.html import HtmlRenderer
from md_sync.renderers.md import MdRenderer
from md_sync.template.manager import TemplateManager
from md_sync.translate.fallback import _detect_provider
from md_sync.translate.manager import TranslationManager
from md_sync.translate.service import translate_document
from md_sync.typography import normalize_for_lang

# ── Typora dark-theme detection ────────────────────────────────────────
# The template injects concrete (not var-derived) table colors so dark
# themes get clearly visible borders. Detection must survive the wide
# variety of theme naming conventions:
#   * bloom 系            --bg / --text (hex)
#   * night / github-night --bg-color / --text-color (hex)
#   * compact-night       --bg-color / --text-color (hsl!)
#   * vlook-*-dark        --db-dk / --df-dk (own naming, filename has -dark)
#   * ia-typora night     no variables at all → filename/night heuristic
_TYPORA_BG_RX = re.compile(r"--(?:bg|bg-color)\s*:\s*([^;]+);")
_TYPORA_TEXT_RX = re.compile(r"--(?:text|text-color)\s*:\s*([^;]+);")
_VLOOK_DK_RX = re.compile(r"--db-dk\s*:\s*([^;]+);")
_HEX_RX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB_RX = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
_HSL_RX = re.compile(
    r"hsla?\(\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)%\s*[, ]\s*(\d+(?:\.\d+)?)%"
)
_DIRECT_BG_RX = re.compile(
    r"(?:html|body)\s*\{[^}]*background(?:-color)?\s*:\s*([#a-fA-F0-9]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\))"
)
_DIRECT_TEXT_RX = re.compile(
    r"#write\s*\{[^}]*color\s*:\s*([#a-fA-F0-9]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\))"
)


def _css_luminance(value: str) -> float | None:
    """Return relative luminance (0=black, 1=white) of a CSS color value.

    Accepts hex (#rgb/#rrggbb), rgb()/rgba() and hsl()/hsla() — some
    themes (compact-night) declare their palette in hsl(). Returns None
    when the value can't be parsed (var() indirection, named colors…).
    """
    v = (value or "").strip()
    m = _HEX_RX.search(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    m = _RGB_RX.search(v)
    if m:
        r, g, b = (int(m.group(i)) / 255 for i in (1, 2, 3))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    m = _HSL_RX.search(v)
    if m:
        h = float(m.group(1)) / 360
        s = float(m.group(2)) / 100
        l = float(m.group(3)) / 100
        if s == 0:
            r = g = b = l
        else:

            def _hue(p, q, t):
                if t < 0:
                    t += 1
                if t > 1:
                    t -= 1
                if t < 1 / 6:
                    return p + (q - p) * 6 * t
                if t < 1 / 2:
                    return q
                if t < 2 / 3:
                    return p + (q - p) * (2 / 3 - t) * 6
                return p

            q = l * (1 + s) if l < 0.5 else l + s - l * s
            p = 2 * l - q
            r = _hue(p, q, h + 1 / 3)
            g = _hue(p, q, h)
            b = _hue(p, q, h - 1 / 3)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    return None


def _detect_typora_dark(css: str, css_name: str = "") -> bool:
    """Detect whether a Typora theme is dark.

    Strategy (first conclusive answer wins):
      1. Filename heuristic — official themes are named ``*-night`` /
         ``*-dark``; vlook dark variants are ``vlook-*-dark``.
      2. Explicit ``--text``/``--text-color`` (light text ⇒ dark bg).
      3. ``--bg``/``--bg-color`` (dark bg ⇒ dark theme).
      4. vlook's own ``--db-dk`` (dark background token).
      5. Direct ``html/body`` background and ``#write`` color rules for
         themes with no variables at all.
    Returns False when nothing conclusive (safe light default).
    """
    name = css_name or ""
    # Filename is the most reliable signal. vlook ships *both* -light and
    # -dark variants whose CSS contains both -lg and -dk tokens, so an
    # explicit "light" in the name must win over any --db-dk token below.
    if re.search(r"dark", name, re.IGNORECASE) and not re.search(r"light", name, re.IGNORECASE):
        return True
    if re.search(r"light", name, re.IGNORECASE):
        return False
    if re.search(r"night", name, re.IGNORECASE):
        return True
    for rx in (_TYPORA_TEXT_RX, _TYPORA_BG_RX):
        for m in rx.finditer(css):
            lum = _css_luminance(m.group(1))
            if lum is None:
                continue
            if rx is _TYPORA_TEXT_RX:
                return lum > 0.55  # light text ⇒ dark bg
            return lum < 0.45  # dark bg ⇒ dark theme
    # vlook 的 --db-dk 只对 vlook 命名的文件可信(其它主题可能只是同名变量)
    if re.search(r"vlook", name, re.IGNORECASE):
        for m in _VLOOK_DK_RX.finditer(css):
            lum = _css_luminance(m.group(1))
            if lum is not None and lum < 0.45:
                return True
    for rx in (_DIRECT_BG_RX, _DIRECT_TEXT_RX):
        for m in rx.finditer(css):
            lum = _css_luminance(m.group(1))
            if lum is None:
                continue
            if rx is _DIRECT_TEXT_RX:
                return lum > 0.55  # light text ⇒ dark bg
            return lum < 0.45  # dark bg ⇒ dark theme
    return False


class SyncPipeline:
    """Orchestrate the sync pipeline for one project."""

    def __init__(self, config: ProjectConfig, log_callback: callable | None = None):
        self._config = config
        self._log = log_callback or (lambda msg: None)
        self._plugin_registry = PluginRegistry(config.project_dir)
        self._parser = MdParser(plugin_registry=self._plugin_registry)
        self._translator = TranslationManager(config.translation_path())
        self._md_renderer = MdRenderer(translator=self._translator)
        self._template_mgr = TemplateManager()
        self._stats: dict = {"source": "", "outputs": [], "errors": []}

    # ── Public API ──────────────────────────────────────────────────────

    def run(self, source_path: Path | None = None) -> dict:
        """Run a full sync cycle. Returns stats dict."""
        source = source_path or self._config.source_path

        # 1. Parse
        try:
            doc = self._parser.parse_file(source, schema=self._config.schema)
        except Exception as e:
            logger.warning("Parse failed: %s", e)
            self._stats["errors"].append(f"Parse failed: {e}")
            return self._stats

        self._stats["source"] = str(source)
        logger.info("[sync] ✓ Parsed: %s (%d sections)", source.name, len(doc.sections))

        # 中英文混排规范：仅对内存中的产物文本做规范化，绝不写回用户源文件。
        self._apply_typography(doc)

        # 2. Process each output
        for out_cfg in self._config.outputs:
            result = self._process_output(doc, out_cfg)
            self._stats["outputs"].append(result)

        # 3. Save translation cache
        self._translator.save()

        # Summary
        ok = len([r for r in self._stats["outputs"] if r.get("ok")])
        err = len([r for r in self._stats["outputs"] if not r.get("ok")])
        logger.info("[sync] ✓ %d outputs synced%s", ok, f", {err} errors" if err else "")
        return self._stats

    def run_dry(self, source_path: Path | None = None) -> dict:
        """Dry run — parse and show what would be synced.

        Reports, for each non-source language, how many content items are
        still missing a translation (so the UI can show "待翻译 N 条").
        """
        source = source_path or self._config.source_path
        doc = self._parser.parse_file(source, schema=self._config.schema)

        info = {
            "source": str(source),
            "source_lang": doc.source_lang,
            "sections": [],
            "missing_translations": {},
            "pending_translations": [],
        }
        for sec in doc.sections:
            info["sections"].append(
                {
                    "id": sec.id,
                    "title": sec.title,
                    "items": len(sec.items),
                }
            )

        # Count missing translations per target language.
        targets = {o.lang for o in self._config.outputs if o.lang != doc.source_lang}
        for target in targets:
            missing = 0
            for sec in doc.sections:
                for item in sec.items:
                    if item.content and not self._translator.has_translation(item.content, target):
                        missing += 1
            info["missing_translations"][target] = missing
        return info

    # ── Output processing ───────────────────────────────────────────────

    @staticmethod
    def _default_style_for(schema: str) -> str:
        """Pick the default render style for a document schema.

        ``markdown`` uses the generic ``standard`` template; the gongwen
        schema defaults to its own bundled ``gongwen`` style; everything else
        (resume, typora) falls back to ``bwx``.
        """
        if schema == "markdown":
            return "standard"
        if schema == "gongwen":
            return "gongwen"
        return "bwx"

    def _apply_typography(self, doc: Document) -> None:
        """Normalize ``doc.source_raw`` in memory for 中英文混排 / 英文排版.

        Only affects derived outputs (raw-layout HTML/PDF and the source-language
        markdown copy). The user's source file is never modified. Structured
        sections are left untouched — raw-layout documents are the ones that
        consume ``source_raw``.
        """
        cfg = self._config.typography
        if not cfg.enabled:
            return
        lang = doc.source_lang
        if lang not in ("zh", "en"):
            return
        if not doc.source_raw:
            return
        normalized = normalize_for_lang(doc.source_raw, cfg, lang)
        if normalized != doc.source_raw:
            logger.info(
                "[typography] %s 排版规范已应用（%d → %d 字符）",
                "中英文混排" if lang == "zh" else "英文排版",
                len(doc.source_raw),
                len(normalized),
            )
            doc.source_raw = normalized

    def _process_output(self, doc: Document, out_cfg: OutputConfig) -> dict:
        result = {"format": out_cfg.format, "lang": out_cfg.lang, "path": out_cfg.path, "ok": False}
        # 公文（gongwen）仅支持中文：英文输出直接跳过（不报错，配置里写了也忽略）。
        if self._config.schema == "gongwen" and out_cfg.lang != "zh":
            result["skipped"] = "gongwen-zh-only"
            result["ok"] = True
            result["reason"] = "公文仅支持中文输出"
            logger.info(
                "  – %s/%s: 公文（gongwen）仅支持中文输出，跳过", out_cfg.format, out_cfg.lang
            )
            return result
        style_name = out_cfg.style or out_cfg.theme or self._default_style_for(self._config.schema)
        # Normalize typora-* to just the sub-name for display
        if style_name.startswith("typora-"):
            style_name = "typora/" + style_name[7:]

        # The source-language *Markdown* output is a copy of the source file.
        # We copy it into the output directory so the output set is complete
        # (md/ should contain the source too), instead of leaving it missing.
        # HTML/PDF for the source language are still rendered normally below.
        if out_cfg.lang == doc.source_lang and out_cfg.format == "md":
            if not out_cfg.path:
                result["skipped"] = "unconfigured"
                result["ok"] = True
                result["reason"] = "输出路径未配置"
                logger.info("  – %s/%s: 输出路径未配置，跳过", out_cfg.format, out_cfg.lang)
                return result
            src = Path(self._config.source_path)
            out_path = Path(out_cfg.path)
            if out_path.resolve() == src.resolve():
                # Output path IS the source file — nothing to copy.
                result["skipped"] = "source"
                result["ok"] = True
                logger.info("  – %s/%s: 输出即源文件，无需复制", out_cfg.format, out_cfg.lang)
                return result
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if self._config.typography.enabled:
                    # 中英文混排规范：md 产物写规范化文本（源文件仍保持原样）。
                    out_path.write_text(doc.source_raw, encoding="utf-8")
                    result["ok"] = True
                    result["copied"] = True
                    result["normalized"] = True
                    logger.info("  ✓ %s (源文件副本 + 混排规范, %d chars)", out_path.name, len(doc.source_raw))
                else:
                    shutil.copy2(src, out_path)
                    result["ok"] = True
                    result["copied"] = True
                    logger.info(
                        "  ✓ %s (源文件副本, %d KB)", out_path.name, out_path.stat().st_size // 1024
                    )
            except Exception as e:
                result["error"] = f"复制源文件失败: {e}"
                logger.warning("复制源文件失败: %s", e)
                self._stats["errors"].append(result["error"])
            return result

        # Output path is a user-configured setting. If the user hasn't set one
        # we must not guess where to write — skip (without error) so the UI can
        # prompt for configuration, rather than writing to an unintended place.
        if not out_cfg.path:
            result["skipped"] = "unconfigured"
            result["ok"] = True
            result["reason"] = "输出路径未配置"
            logger.info(
                "  – %s/%s: 输出路径未配置，跳过（请在界面「📦 输出文件」卡片填写路径）",
                out_cfg.format,
                out_cfg.lang,
            )
            return result

        # Translate if this output is a non-source language. Translation is
        # an independent step (done explicitly via /api/translate or
        # ensure_translations), but we make sure the cache is filled here
        # too so a plain "sync" still works end-to-end.
        target_lang = out_cfg.lang
        if out_cfg.lang != doc.source_lang:
            if self._translation_possible():
                self._ensure_translations(doc)
                # If all translations for this target failed (pending/None),
                # fall back to source-language content so the output is not blank.
                if self._translations_all_failed(doc, target_lang):
                    self._log(f"  ⚠ 翻译全部失败，使用原文作为 {target_lang} 输出")
                    target_lang = doc.source_lang
            else:
                # No provider available → generate the target file from the
                # source-language content so a complete output still exists.
                target_lang = doc.source_lang

        # Render
        try:
            if out_cfg.format == "docx":
                out_path = Path(out_cfg.path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # ③ 插件机制：插件可提供 DOCX 导出器覆盖基础 pandoc 导出
                # （插件优先）。例如 gongwen 插件直接生成 GB/T 9704-2012
                # 红头公文 docx（版式/字体/页码），不再经 HTML→pandoc。
                exporter = self._plugin_registry.get_docx_exporter(self._config.schema)
                if exporter is not None:
                    try:
                        docx_ok = exporter.export(
                            doc=doc,
                            output_path=out_path,
                            style_name=style_name,
                            lang=target_lang,
                            translator=self._translator,
                        )
                    except Exception as e:
                        logger.warning(
                            "Plugin DOCX exporter failed (%s), falling back: %s", exporter.name, e
                        )
                        docx_ok = False
                    if not docx_ok:
                        # 插件导出失败（或返回 False）→ 回退基础 pandoc 路径，
                        # 与 PDF 分支行为一致。
                        logger.warning(
                            "Plugin DOCX export produced no file for %s, falling back to pandoc",
                            out_path.name,
                        )
                        self._export_docx_via_pandoc(doc, out_cfg, target_lang, style_name, result)
                        return result
                    result["ok"] = True
                    size_kb = out_path.stat().st_size // 1024 if out_path.exists() else 0
                    logger.info(
                        "  ✓ %s (%d KB, plugin docx) [%s]", out_path.name, size_kb, style_name
                    )
                    # ② 插件机制：转换产物写出后触发 after_render hook
                    self._plugin_registry.emit_after_render(
                        out_path,
                        {"lang": target_lang, "format": "docx", "path": str(out_path)},
                    )
                    return result
                # 无插件导出器 → 退回基础 pandoc 路径
                self._export_docx_via_pandoc(doc, out_cfg, target_lang, style_name, result)
            elif out_cfg.format == "epub":
                self._export_docx_via_pandoc(doc, out_cfg, target_lang, style_name, result)
            elif out_cfg.format == "html":
                content = self._render_html(doc, out_cfg, target_lang=target_lang)
                out_path = Path(out_cfg.path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                result["ok"] = True
                logger.info("  ✓ %s (%d chars) [%s]", out_path.name, len(content), style_name)
                # ② 插件机制：HTML 写出后触发 after_render hook
                self._plugin_registry.emit_after_render(
                    out_path,
                    {"lang": target_lang, "format": "html", "path": str(out_path)},
                )

                # PDF
                if out_cfg.pdf and out_cfg.pdf_path:
                    Path(out_cfg.pdf_path).parent.mkdir(parents=True, exist_ok=True)
                    # The template's template.yaml may declare its own PDF
                    # margin (e.g. gongwen's GB/T 9704-2012 版心). When the
                    # output config doesn't override, honor the template.
                    page_margin = out_cfg.page_margin or ""
                    if not page_margin.strip():
                        page_margin = (self._template_catalog(out_cfg).pdf or {}).get("margin", "")
                    page_margin = resolve_margin(out_cfg.page_size, page_margin)
                    # ③ 插件机制：插件可提供 PDF 导出器覆盖基础功能（插件优先）。
                    # 例如 gongwen 插件用 CDP 注入 GB/T 9704-2012 页码。
                    exporter = self._plugin_registry.get_pdf_exporter(self._config.schema)
                    if exporter is not None:
                        try:
                            pdf_ok = exporter.export(
                                html_path=out_path,
                                pdf_path=out_cfg.pdf_path,
                                page_margin=page_margin,
                                page_size=out_cfg.page_size,
                                style_name=style_name,
                            )
                        except Exception as e:
                            logger.warning(
                                "Plugin PDF exporter failed (%s), falling back: %s",
                                exporter.name,
                                e,
                            )
                            pdf_ok = _export_pdf(
                                html_path=out_path,
                                pdf_path=out_cfg.pdf_path,
                                page_margin=page_margin,
                                page_size=out_cfg.page_size,
                                style_name=style_name,
                            )
                    else:
                        pdf_ok = _export_pdf(
                            html_path=out_path,
                            pdf_path=out_cfg.pdf_path,
                            page_margin=page_margin,
                            page_size=out_cfg.page_size,
                            style_name=style_name,
                        )
                    result["pdf"] = pdf_ok
                    # ② 插件机制：PDF 生成后触发 after_render hook
                    self._plugin_registry.emit_after_render(
                        Path(out_cfg.pdf_path),
                        {"lang": target_lang, "format": "pdf", "path": out_cfg.pdf_path},
                    )
            elif out_cfg.format == "md":
                content = self._render_md(doc, target_lang)
                if self._config.typography.enabled:
                    content = normalize_for_lang(content, self._config.typography, target_lang)
                out_path = Path(out_cfg.path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                result["ok"] = True
                logger.info("  ✓ %s (%d chars)", out_path.name, len(content))
            else:
                result["error"] = f"Unknown format: {out_cfg.format}"
                self._stats["errors"].append(result["error"])
                return result
        except Exception as e:
            result["error"] = f"Render failed: {e}"
            logger.warning("Render failed: %s", e)
            self._stats["errors"].append(result["error"])
        return result

    def _template_catalog(self, out_cfg: OutputConfig):
        """Resolve the TemplateCatalog for an output config.

        Mirrors the template-name resolution used by :meth:`_render_html`
        (style > theme > schema default, ``typora-*`` normalised to the shared
        ``typora`` base). Falls back to ``bwx`` when the template is missing.
        """
        template_name = (
            out_cfg.style or out_cfg.theme or self._default_style_for(self._config.schema)
        )
        if template_name.startswith("typora-"):
            template_name = "typora"
        try:
            return self._template_mgr.resolve(template_name)
        except FileNotFoundError:
            logger.warning("Template '%s' not found, falling back to 'bwx'", template_name)
            return self._template_mgr.resolve("bwx")

    def _export_docx_via_pandoc(
        self,
        doc: Document,
        out_cfg: OutputConfig,
        target_lang: str,
        style_name: str,
        result: dict,
    ) -> bool:
        """Render HTML first, then convert via pandoc to .docx / .epub.

        This is the built-in fallback used when a schema has no plugin DOCX
        exporter (gongwen provides one that bypasses pandoc entirely).
        """
        html_content = self._render_html(doc, out_cfg, target_lang=target_lang)
        html_tmp = Path(out_cfg.path).with_suffix(".html")
        html_tmp.parent.mkdir(parents=True, exist_ok=True)
        html_tmp.write_text(html_content, encoding="utf-8")

        out_path = Path(out_cfg.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Honor the template's declared PDF margin (template.yaml) when
        # the output config doesn't override it (e.g. gongwen's
        # GB/T 9704-2012 版心), matching the PDF export path below.
        page_margin = out_cfg.page_margin or ""
        if not page_margin.strip():
            page_margin = (self._template_catalog(out_cfg).pdf or {}).get("margin", "")
        pandoc_ok = export_via_pandoc(
            input_path=html_tmp,
            output_path=out_path,
            to_format=out_cfg.format,
            page_size=out_cfg.page_size,
            margin=page_margin,
        )
        if pandoc_ok:
            result["ok"] = True
            size_kb = out_path.stat().st_size // 1024 if out_path.exists() else 0
            logger.info("  ✓ %s (%d KB, via pandoc) [%s]", out_path.name, size_kb, style_name)
            # ② 插件机制：转换产物写出后触发 after_render hook
            self._plugin_registry.emit_after_render(
                out_path,
                {"lang": target_lang, "format": out_cfg.format, "path": str(out_path)},
            )
        else:
            result["error"] = f"Pandoc export failed for {out_cfg.format}"
            self._stats["errors"].append(result["error"])
            return False

        # Cleanup temp HTML unless user also wants HTML output
        html_cfg = [
            o
            for o in self._config.outputs
            if o.format == "html" and o.lang == out_cfg.lang and o.path
        ]
        if not html_cfg:
            html_tmp.unlink(missing_ok=True)
        return True

    def _render_html(self, doc: Document, out_cfg: OutputConfig, target_lang: str = "zh") -> str:
        """Render doc to HTML using TemplateManager.

        Supports ``typora-<name>`` templates: loads the CSS from
        ``~/.config/Typora/themes/<name>.css`` and passes it to the
        ``HtmlRenderer`` as ``typora_css``, overriding the template's
        default ``style.css``.
        """
        # Determine template name: style > theme (legacy) > default
        template_name = (
            out_cfg.style or out_cfg.theme or self._default_style_for(self._config.schema)
        )

        # Typora theme handling
        typora_css = None
        if template_name.startswith("typora-"):
            css_name = template_name[7:]  # strip "typora-" prefix
            typora_css_path = Path.home() / ".config" / "Typora" / "themes" / f"{css_name}.css"
            if typora_css_path.exists():
                css_raw = typora_css_path.read_text(encoding="utf-8")
                # Strip Typora-specific @include-when-export directive
                css_raw = re.sub(r"@include-when-export\s+url\([^)]+\)\s*;?", "", css_raw)
                # Convert relative font/image URLs to absolute file:// paths
                # e.g. url("./bloom/fonts/MiSans-Regular.ttf") →
                #      url("file:///home/user/.config/Typora/themes/bloom/fonts/MiSans-Regular.ttf")
                typora_themes_dir = str(typora_css_path.parent) + "/"

                def _resolve_url(m: re.Match) -> str:
                    content = m.group(1).strip()
                    # Strip surrounding quotes if present
                    raw = content.strip("\"'")
                    # Leave absolute URLs, data URIs, and fragment identifiers as-is
                    if any(raw.startswith(p) for p in ("http://", "https://", "data:", "#", "%23")):
                        return m.group(0)
                    # Convert relative path to absolute file://
                    # Normalize path separators (handle both "./foo" and "foo")
                    rel = raw.lstrip("./").lstrip("/")
                    abs_path = typora_themes_dir + rel
                    return f'url("{abs_path}")'

                css_raw = re.sub(r"url\(\s*([^)]+?)\s*\)", _resolve_url, css_raw)
                typora_css = css_raw
            # Use the typora Jinja2 template (shared by all typora-* themes)
            template_name = "typora"
        catalog = self._template_catalog(out_cfg)

        theme_dir = catalog.info.directory
        if not theme_dir:
            raise FileNotFoundError(f"Template directory not found: {template_name}")

        # ② 插件机制：渲染前触发 before_render hook（插件可就地修改 doc）
        self._plugin_registry.emit_before_render(
            doc,
            {"lang": target_lang, "template": template_name, "theme_dir": str(theme_dir)},
        )

        # ① 插件机制：把插件声明的自定义过滤器注入渲染器
        renderer = HtmlRenderer(
            theme_dir,
            filters=self._plugin_registry.get_filters(),
        )
        kwargs = {}
        if typora_css:
            kwargs["typora_css"] = typora_css
            # 明暗检测：表格边框/表头底色注入具体颜色而非 CSS 变量回退链，
            # 确保 compact-night / vlook-*-dark 等非标准变量命名的 dark 主题
            # 也能得到可见的边框（不再落到深色 #333 而隐形）。
            kwargs["typora_dark"] = _detect_typora_dark(typora_css, css_name)

        # Both schemas are rendered by a single HtmlRenderer whose *layout*
        # strategy is selected by the schema, not by a separate code path:
        #   * "raw"        — linear docs (markdown / typora / gongwen) in the
        #                    source language: render the whole source in one
        #                    shot via markdown-it (maximal fidelity, no Item
        #                    splitting).
        #   * "structured" — resume-style docs (per-item chrome) and any
        #                    translation target (per-item translation cache).
        layout = (
            "raw"
            if (
                self._config.schema in ("markdown", "typora", "gongwen")
                and target_lang == doc.source_lang
                and doc.source_raw
            )
            else "structured"
        )
        # 中英文混排 / 英文排版：结构化渲染（resume 等）经模板 t() 钩子对输出文本规范化。
        # 原始文本仍是翻译缓存 key，规范化仅作用于展示值，源文件不受影响。
        if self._config.typography.enabled:
            typo_cfg = self._config.typography
            kwargs["normalize"] = lambda s, lang: normalize_for_lang(s, typo_cfg, lang)
        return renderer.render(
            doc,
            sections_meta=catalog.sections,
            translator=self._translator,
            lang=target_lang,
            layout=layout,
            **kwargs,
        )

    def _render_md(self, doc: Document, lang: str) -> str:
        return self._md_renderer.render(doc, lang=lang)

    def _translation_possible(self) -> bool:
        """Whether a translation provider is available (no API key needed).

        Free public providers (google/bing/mymemory) and OpenAI all count. We no
        longer require a pre-existing translation in the mapping, because
        translation is now an explicit, independent step (see
        ``translate_document``). Pending/empty entries are fine — the
        provider will fill them on demand.
        """
        return _detect_provider() in ("openai", "google", "bing", "mymemory")

    # ── Translation helpers ─────────────────────────────────────────────

    def ensure_translations_for(self, doc: Document, target_lang: str) -> dict:
        """Fill in missing translations of ``doc`` into ``target_lang`` only."""
        if target_lang == doc.source_lang:
            return {
                "source_lang": doc.source_lang,
                "target_lang": target_lang,
                "provider": _detect_provider(),
                "total": 0,
                "translated": 0,
                "cached": 0,
                "failed": 0,
            }
        self._log(f"🌐 翻译中… ({target_lang})")

        # Progress callback that logs to the pipeline's log callback
        def _on_progress(done: int, total: int, text: str, status: str):
            pct = int(done / max(total, 1) * 100)
            self._log(f"  翻译进度: {done}/{total} ({pct}%) … {status}")

        res = translate_document(
            doc,
            target_lang=target_lang,
            provider=self._config.translation.ai.provider,
            translator=self._translator,
            progress_callback=_on_progress,
        )
        self._translator.save()
        self._log(
            f"  ✓ 翻译完成: {res.get('translated', 0)} 条新译, "
            f"{res.get('cached', 0)} 条缓存, {res.get('failed', 0)} 条失败"
        )
        return res

    def ensure_translations(self, doc: Document) -> dict:
        """Fill in missing translations for *every* non-source language.

        This is the bidirectional, provider-driven translation step. It
        delegates to :func:`translate_document` for each target language
        and updates the shared translation cache. Returns a summary dict.
        """
        summary = {"targets": {}}
        for out_cfg in self._config.outputs:
            target = out_cfg.lang
            if target == doc.source_lang:
                continue
            summary["targets"][target] = self.ensure_translations_for(doc, target)
        return summary

    def _translations_all_failed(self, doc: Document, target_lang: str) -> bool:
        """Return True if NO items have a translation for ``target_lang``.

        Checks if every content item either has no translation (pending/failed)
        or is empty. When this is true, we fall back to source-language
        rendering so the output file is not blank.
        """
        for section in doc.sections:
            for item in section.items:
                if item.content and self._translator.has_translation(item.content, target_lang):
                    return False
        return True

    def _ensure_translations(self, doc: Document) -> None:
        """Translate only once per non-source language, then flag done."""
        if getattr(self, "_translated_langs", None) is None:
            self._translated_langs = set()
        for out_cfg in self._config.outputs:
            target = out_cfg.lang
            if target == doc.source_lang or target in self._translated_langs:
                continue
            self._translated_langs.add(target)
            self.ensure_translations_for(doc, target)

    def translate_only(self, target_lang: str | None = None) -> dict:
        """Run ONLY the translation step (no output generation).

        Detects the source language from the configured source file and
        translates it into ``target_lang`` (defaults to the non-source
        language among the configured outputs). Returns a summary dict.
        """
        doc = self._parser.parse_file(self._config.source_path, schema=self._config.schema)
        if target_lang is None:
            targets = [o.lang for o in self._config.outputs if o.lang != doc.source_lang]
            target_lang = targets[0] if targets else ("en" if doc.source_lang == "zh" else "zh")
        return self.ensure_translations_for(doc, target_lang)
