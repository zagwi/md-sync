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

import shutil
from pathlib import Path
from typing import Optional

from md_sync.config import ProjectConfig, OutputConfig
from md_sync.core.document import Document
from md_sync.core.parser import MdParser
from md_sync.exporters.pdf import export_pdf as _export_pdf
from md_sync.renderers.html import HtmlRenderer
from md_sync.renderers.md import MdRenderer
from md_sync.template.manager import TemplateManager
from md_sync.translate.fallback import _detect_provider
from md_sync.translate.manager import TranslationManager
from md_sync.translate.service import translate_document


class SyncPipeline:
    """Orchestrate the sync pipeline for one project."""

    def __init__(self, config: ProjectConfig):
        self._config = config
        self._parser = MdParser()
        self._translator = TranslationManager(config.translation_path())
        self._md_renderer = MdRenderer(translator=self._translator)
        self._template_mgr = TemplateManager()
        self._stats: dict = {"source": "", "outputs": [], "errors": []}

    # ── Public API ──────────────────────────────────────────────────────

    def run(self, source_path: Optional[Path] = None) -> dict:
        """Run a full sync cycle. Returns stats dict."""
        source = source_path or self._config.source_path

        # 1. Parse
        try:
            doc = self._parser.parse_file(source, schema=self._config.schema)
        except Exception as e:
            self._stats["errors"].append(f"Parse failed: {e}")
            return self._stats

        self._stats["source"] = str(source)
        print(f"[sync] ✓ Parsed: {source.name} ({len(doc.sections)} sections)")

        # 2. Process each output
        for out_cfg in self._config.outputs:
            result = self._process_output(doc, out_cfg)
            self._stats["outputs"].append(result)

        # 3. Save translation cache
        self._translator.save()

        # Summary
        ok = len([r for r in self._stats["outputs"] if r.get("ok")])
        err = len([r for r in self._stats["outputs"] if not r.get("ok")])
        print(f"[sync] ✓ {ok} outputs synced" + (f", {err} errors" if err else ""))
        return self._stats

    def run_dry(self, source_path: Optional[Path] = None) -> dict:
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
            info["sections"].append({
                "id": sec.id,
                "title": sec.title,
                "items": len(sec.items),
            })

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

    def _process_output(self, doc: Document, out_cfg: OutputConfig) -> dict:
        result = {"format": out_cfg.format, "lang": out_cfg.lang, "path": out_cfg.path, "ok": False}

        # The source-language *Markdown* output is a copy of the source file.
        # We copy it into the output directory so the output set is complete
        # (md/ should contain the source too), instead of leaving it missing.
        # HTML/PDF for the source language are still rendered normally below.
        if out_cfg.lang == doc.source_lang and out_cfg.format == "md":
            if not out_cfg.path:
                result["skipped"] = "unconfigured"
                result["ok"] = True
                result["reason"] = "输出路径未配置"
                print(f"  – {out_cfg.format}/{out_cfg.lang}: 输出路径未配置，跳过")
                return result
            src = Path(self._config.source_path)
            out_path = Path(out_cfg.path)
            if out_path.resolve() == src.resolve():
                # Output path IS the source file — nothing to copy.
                result["skipped"] = "source"
                result["ok"] = True
                print(f"  – {out_cfg.format}/{out_cfg.lang}: 输出即源文件，无需复制")
                return result
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out_path)
                result["ok"] = True
                result["copied"] = True
                print(f"  ✓ {out_path.name} (源文件副本, {out_path.stat().st_size // 1024} KB)")
            except Exception as e:
                result["error"] = f"复制源文件失败: {e}"
                self._stats["errors"].append(result["error"])
            return result

        # Output path is a user-configured setting. If the user hasn't set one
        # we must not guess where to write — skip (without error) so the UI can
        # prompt for configuration, rather than writing to an unintended place.
        if not out_cfg.path:
            result["skipped"] = "unconfigured"
            result["ok"] = True
            result["reason"] = "输出路径未配置"
            print(f"  – {out_cfg.format}/{out_cfg.lang}: 输出路径未配置，跳过（请在界面「📦 输出文件」卡片填写路径）")
            return result

        # Translate if this output is a non-source language. Translation is
        # an independent step (done explicitly via /api/translate or
        # ensure_translations), but we make sure the cache is filled here
        # too so a plain "sync" still works end-to-end.
        target_lang = out_cfg.lang
        if out_cfg.lang != doc.source_lang:
            if self._translation_possible():
                self._ensure_translations(doc)
            else:
                # No provider available → generate the target file from the
                # source-language content so a complete output still exists.
                target_lang = doc.source_lang

        # Render
        try:
            if out_cfg.format == "html":
                content = self._render_html(doc, out_cfg, target_lang=target_lang)
            elif out_cfg.format == "md":
                content = self._render_md(doc, target_lang)
            else:
                result["error"] = f"Unknown format: {out_cfg.format}"
                self._stats["errors"].append(result["error"])
                return result
        except Exception as e:
            result["error"] = f"Render failed: {e}"
            self._stats["errors"].append(result["error"])
            return result

        # Write
        out_path = Path(out_cfg.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        result["ok"] = True
        print(f"  ✓ {out_path.name} ({len(content)} chars)")

        # PDF
        if out_cfg.pdf and out_cfg.format == "html" and out_cfg.pdf_path:
            Path(out_cfg.pdf_path).parent.mkdir(parents=True, exist_ok=True)
            pdf_ok = _export_pdf(
                html_path=out_path,
                pdf_path=out_cfg.pdf_path,
            )
            result["pdf"] = pdf_ok

        return result

    def _render_html(self, doc: Document, out_cfg: OutputConfig, target_lang: str = "zh") -> str:
        """Render doc to HTML using TemplateManager."""
        # Determine template name: style > theme (legacy) > default
        template_name = out_cfg.style or out_cfg.theme or "bwx"

        try:
            catalog = self._template_mgr.resolve(template_name)
        except FileNotFoundError:
            print(f"  ⚠ Template '{template_name}' not found, falling back to 'bwx'")
            catalog = self._template_mgr.resolve("bwx")

        theme_dir = catalog.info.directory
        if not theme_dir:
            raise FileNotFoundError(f"Template directory not found: {template_name}")

        renderer = HtmlRenderer(theme_dir)
        return renderer.render(
            doc,
            sections_meta=catalog.sections,
            translator=self._translator,
            lang=target_lang,
        )

    def _render_md(self, doc: Document, lang: str) -> str:
        return self._md_renderer.render(doc, lang=lang)

    def _translation_possible(self) -> bool:
        """Whether a translation provider is available (no API key needed).

        Free public providers (google/bing) and OpenAI all count. We no
        longer require a pre-existing translation in the mapping, because
        translation is now an explicit, independent step (see
        ``translate_document``). Pending/empty entries are fine — the
        provider will fill them on demand.
        """
        return _detect_provider() in ("openai", "google", "bing")

    # ── Translation helpers ─────────────────────────────────────────────

    def ensure_translations_for(self, doc: Document, target_lang: str) -> dict:
        """Fill in missing translations of ``doc`` into ``target_lang`` only."""
        if target_lang == doc.source_lang:
            return {"source_lang": doc.source_lang, "target_lang": target_lang,
                    "provider": _detect_provider(), "total": 0,
                    "translated": 0, "cached": 0, "failed": 0}
        res = translate_document(
            doc,
            target_lang=target_lang,
            provider=self._config.translation.ai.provider,
            translator=self._translator,
        )
        self._translator.save()
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

    def _ensure_translations(self, doc: Document) -> None:
        """Backwards-compatible wrapper: translate all non-source languages."""
        self.ensure_translations(doc)

    def translate_only(self, target_lang: Optional[str] = None) -> dict:
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
