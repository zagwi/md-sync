"""FastAPI dashboard serving ``index.html`` with a live sync backend.

The browser UI mirrors ``index.html`` exactly; this module adds the plumbing:
state, one-shot sync, continuous watch, realtime logs (SSE) and the output
file list. No ``md-sync.yaml`` is required — everything is configured in the
browser and held in-memory in a :class:`WebSession`.

Run it with ``md-sync start`` (or ``uvicorn md_sync.web.app:app``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from md_sync.config import OutputConfig, ProjectConfig, derive_output_path
from md_sync.core.pipeline import SyncPipeline
from md_sync.plugin.registry import PluginRegistry
from md_sync.template.manager import TemplateManager
from md_sync.typography import TypographyConfig
from md_sync.watcher import FileWatcher

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ALL_FORMATS = ["html", "md", "pdf", "docx", "epub"]
LANGS = ["zh", "en"]
DEFAULT_SCHEMA = "markdown"

_PKG_DIR = Path(__file__).resolve().parent
_INDEX_PATH = Path(__file__).resolve().parent.parent.parent / "index.html"
_UPLOAD_DIR = Path.home() / ".md-sync" / "uploads"


# ── Realtime log ring buffer (SSE source) ─────────────────────────────────


class LogBuffer:
    """Thread-safe rolling log buffer with a monotonically increasing id."""

    def __init__(self, capacity: int = 400):
        self._lines: list[dict] = []
        self._cap = capacity
        self._lock = threading.Lock()
        self._last_id = 0

    def append(self, msg: str) -> None:
        with self._lock:
            self._last_id += 1
            self._lines.append({"id": self._last_id, "text": msg})
            if len(self._lines) > self._cap:
                del self._lines[: len(self._lines) - self._cap]

    def tail(self, after_id: int = 0) -> list[dict]:
        with self._lock:
            return [l for l in self._lines if l["id"] > after_id]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


@dataclass
class ViewerState:
    """Mirror of the index.html form fields (no persistence required)."""

    plugin: str = "通用Markdown"
    schema: str = DEFAULT_SCHEMA
    source: str = ""
    output_dir: str = ""
    style_zh: str = ""
    style_en: str = ""
    formats: list[str] = field(default_factory=lambda: ["html", "md"])  # enabled formats
    langs: list[str] = field(default_factory=lambda: ["zh"])  # enabled langs
    naming: str = "timestamp"
    blink: bool = True


class WebSession:
    """In-memory, browser-driven sync session backed by the shared pipeline."""

    def __init__(self) -> None:
        self.log = LogBuffer()
        self.state = ViewerState()
        self.typography = TypographyConfig()
        self._tmgr = TemplateManager()
        self._preg = PluginRegistry()
        self._watcher: FileWatcher | None = None
        self._sync_lock = threading.Lock()
        self._syncing = False
        self.cfg: ProjectConfig | None = None
        self._hidden_paths: set[str] = set()
        self.last_stats: dict = {}

        # Bootstrap default plugin (first installed "pack" style schema).
        plugins = self.plugins()
        if plugins:
            self.state.plugin = plugins[0]["name"]
            self.state.schema = plugins[0]["parser_schema"] or DEFAULT_SCHEMA

    # ── Introspection ───────────────────────────────────────────────────

    def plugins(self) -> list[dict]:
        plugins = self._preg.list_plugins()
        if plugins:
            return [
                {
                    "name": m.name,
                    "label": m.label or m.name,
                    "parser_schema": m.parser_schema or DEFAULT_SCHEMA,
                    "version": m.version or "1.0",
                    "type": m.plugin_type or "pack",
                }
                for m in plugins
            ]
        return [{"name": "通用Markdown", "parser_schema": "markdown", "version": "1.0", "type": "pack"}]

    def schemas(self) -> list[dict]:
        seen: dict[str, str] = {}
        for p in self._preg.list_plugins():
            s = p.parser_schema or ""
            if s:
                seen.setdefault(s, p.name)
        seen.setdefault("markdown", "通用Markdown")
        seen.setdefault("resume", "简历")
        return [{"name": k, "label": v} for k, v in seen.items()]

    def styles(self, schema: str | None = None) -> list[dict]:
        """Render styles for the current schema.

        Typora themes (``typora-<theme>``) 按作者仓库分组返回 ``group`` 字段，
        前端据此渲染两级「可折叠/展开」列表；非 Typora 模板 ``group`` 为 None。
        裸 ``typora`` 基座模板无 style.css，不列入可选项。
        """
        from md_sync.plugins.typora.groups import OTHER_GROUP, typora_group_key

        infos = self._tmgr.list_templates(schema or self.state.schema)
        result = []
        for t in infos:
            if t.name == "typora":
                continue
            if t.name.startswith("typora-"):
                stem = t.name[len("typora-") :]
                label = t.label or t.name
                if label.lower().startswith("typora "):
                    label = label[len("Typora ") :]
                result.append(
                    {
                        "name": t.name,
                        "label": label,
                        "schema": t.schema,
                        "group": typora_group_key(stem) or OTHER_GROUP,
                    }
                )
            else:
                result.append({"name": t.name, "label": t.label or t.name, "schema": t.schema, "group": None})
        if not result:
            result.append({"name": "standard", "label": "标准", "schema": "markdown", "group": None})
        return result

    # ── Config building (mirrors the Qt GUI's logic) ────────────────────

    def source_path(self) -> Path | None:
        src = self.state.source.strip()
        if not src:
            return None
        p = Path(src).expanduser()
        return p if p.exists() and p.is_file() else None

    def output_root(self) -> Path:
        src = self.source_path()
        out = self.state.output_dir.strip()
        if out:
            root = Path(out).expanduser().resolve()
        elif src:
            root = src.parent
        else:
            root = Path.cwd()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def build_config(self) -> tuple[ProjectConfig | None, list[str]]:
        """Return (config, problems). problems is non-empty when unusable."""
        problems: list[str] = []
        src_path = self.source_path()
        if src_path is None:
            problems.append("请选择有效的 Markdown 源文件。")
            return None, problems

        selected = [(f, l) for f in self.state.formats for l in self.state.langs]
        if not selected:
            problems.append("请至少为一种格式勾选一种语言。")
            return None, problems

        root = self.output_root()
        stem = src_path.stem
        name_map = {lang: f"{stem}-{lang}" for lang in self.state.langs}
        sel = {(f, l) for (f, l) in selected}
        outputs: list[OutputConfig] = []
        self._hidden_paths = set()

        for lang in self.state.langs:
            style = self.state.style_zh
            want_html = ("html", lang) in sel
            want_pdf = ("pdf", lang) in sel
            want_md = ("md", lang) in sel
            want_docx = ("docx", lang) in sel
            want_epub = ("epub", lang) in sel

            if want_html or want_pdf:
                html_path = derive_output_path(root, "html", lang, name_map, stem)
                pdf_path = (
                    derive_output_path(root, "html", lang, name_map, stem, pdf=True) if want_pdf else None
                )
                outputs.append(
                    OutputConfig(
                        format="html", lang=lang, path=html_path, pdf=want_pdf,
                        pdf_path=pdf_path, style=style, page_size="A4", page_margin="",
                    )
                )
                if not want_html:
                    self._hidden_paths.add(html_path)
            if want_md:
                outputs.append(
                    OutputConfig(format="md", lang=lang, path=derive_output_path(root, "md", lang, name_map, stem), style=style)
                )
            if want_docx:
                outputs.append(
                    OutputConfig(format="docx", lang=lang, path=derive_output_path(root, "docx", lang, name_map, stem), style=style, page_size="A4")
                )
            if want_epub:
                outputs.append(
                    OutputConfig(format="epub", lang=lang, path=derive_output_path(root, "epub", lang, name_map, stem), style=style)
                )

        cfg = ProjectConfig(
            project=stem,
            source=str(src_path),
            schema=self.state.schema,
            outputs=outputs,
            output_root=str(root),
            source_lang="zh",
            name_map=name_map,
            typography=self.typography,
            output_naming=self.state.naming,
        )
        cfg.source_path = src_path.resolve()
        self.cfg = cfg
        return cfg, []

    # ── Sync ────────────────────────────────────────────────────────────

    def run_sync(self) -> tuple[bool, list[str]]:
        """Validate config and kick off an async sync. Returns (ok, problems)."""
        cfg, problems = self.build_config()
        if cfg is None:
            return False, problems
        self.run_sync_async()
        return True, []

    def _run_sync_impl(self, source: Path | None = None) -> dict:
        cfg, problems = self.build_config()
        if cfg is None:
            return {"ok": False, "errors": problems}
        self.log.append(f"▶ 同步开始: {cfg.source}")
        pipeline = SyncPipeline(cfg, log_callback=self.log.append)
        stats = pipeline.run(source)
        stats.setdefault("ok", not stats.get("errors"))
        self.last_stats = stats
        ok = stats.get("ok") and not stats.get("errors")
        self.log.append(f"{'✓ 同步完成' if ok else '✗ 同步完成（有错误）'}: "
                        f"{len(stats['outputs'])} 个产物")
        self.state.source = str(cfg.source)
        return stats

    def run_sync_async(self, source: Path | None = None) -> None:
        def _work() -> None:
            with self._sync_lock:
                self._syncing = True
                try:
                    self._run_sync_impl(source)
                except Exception as e:  # noqa: BLE001
                    logger.exception("sync failed")
                    self.log.append(f"✗ 同步异常: {e}")
                finally:
                    self._syncing = False

        threading.Thread(target=_work, daemon=True).start()

    # ── Watch ───────────────────────────────────────────────────────────

    def watch(self) -> bool:
        cfg, problems = self.build_config()
        if cfg is None:
            self.log.append("⚠ 无法监听: " + "；".join(problems))
            return False
        if self._watcher and self._watcher.is_running:
            self._watcher.stop()
            self._watcher = None
            self.log.append("⏹ 已停止监听")
            return False
        self._watcher = FileWatcher(
            cfg.source_path,
            on_change=lambda p: self.run_sync_async(p),
            debounce=1.5,
            output_root=cfg.output_root_path,
        )
        self._watcher.start()
        self.log.append(f"▶ 持续监听中: {cfg.source_path.name}（改保存即自动同步）")
        return True

    def stop_watch(self) -> None:
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    @property
    def watching(self) -> bool:
        return bool(self._watcher and self._watcher.is_running)

    # ── Output listing ──────────────────────────────────────────────────

    def output_files(self) -> list[dict]:
        src_mtime = 0.0
        src = self.source_path()
        if src:
            from contextlib import suppress

            with suppress(OSError):
                src_mtime = src.stat().st_mtime
        if self.cfg is None:
            return []
        rows = []
        seen = set()
        cfg = self.cfg
        for o in cfg.outputs:
            paths = [(o.path, o.format, False)]
            if o.pdf and o.pdf_path:
                paths.append((o.pdf_path, "pdf", True))
            for p, fmt, is_pdf in paths:
                if not p or p in seen:
                    continue
                seen.add(p)
                st = _file_status(p)
                rows.append({
                    "path": p,
                    "filename": Path(p).name,
                    "lang": o.lang,
                    "format": "pdf" if is_pdf else fmt,
                    "pdf": is_pdf,
                    "is_source": p == str(cfg.source_path),
                    "exists": st["exists"],
                    "size": st["size"],
                    "mtime": st["mtime"],
                    "mtime_fmt": _fmt_mtime(st["mtime"]) if st["exists"] else "",
                    "status": _status_color(st, src_mtime) if st["exists"] else "missing",
                })
        return rows

    def clear_outputs(self) -> int:
        if self.cfg is None:
            return 0
        removed = 0
        for o in self.cfg.outputs:
            for p in (o.path, o.pdf_path):
                if not p:
                    continue
                pp = Path(p)
                if not pp.exists() or pp.resolve() == self.cfg.source_path.resolve():
                    continue
                try:
                    if pp.is_dir():
                        shutil.rmtree(pp)
                    else:
                        pp.unlink()
                    removed += 1
                except OSError:
                    pass
        self.log.append(f"🗑 已清除 {removed} 个输出文件")
        return removed

    def open_dir(self) -> str | None:
        root = self.output_root()
        if not root.exists():
            return None
        try:
            if os.name == "nt":
                os.startfile(root)  # type: ignore[attr-defined]
            elif sys_platform_darwin():
                subprocess.Popen(["open", str(root)])
            else:
                subprocess.Popen(["xdg-open", str(root)])
        except Exception as e:  # noqa: BLE001
            logger.warning("open_dir failed: %s", e)
            return str(root)
        return None


def sys_platform_darwin() -> bool:
    import sys

    return sys.platform == "darwin"


def _file_status(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "size": 0, "mtime": 0}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": st.st_mtime}


def _status_color(st: dict, src_mtime: float) -> str:
    if not st["exists"]:
        return "missing"
    if src_mtime and src_mtime > st["mtime"]:
        return "stale"
    return "synced"


def _fmt_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ── FastAPI application ───────────────────────────────────────────────────


def create_app(session: WebSession | None = None) -> FastAPI:

    session = session or WebSession()
    _INDEX_MOD = _INDEX_PATH.stat().st_mtime if _INDEX_PATH.exists() else 0

    app = FastAPI(title="md-sync", version="1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if _INDEX_PATH.exists():
            return HTMLResponse(_INDEX_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<!-- index.html not found next to the app -->", status_code=404)

    @app.get("/api/meta")
    async def meta() -> JSONResponse:
        return JSONResponse(
            {
                "plugins": session.plugins(),
                "schemas": session.schemas(),
                "styles": session.styles(),
                "formats": ALL_FORMATS,
                "langs": LANGS,
            }
        )

    @app.get("/api/styles")
    async def styles(schema: str | None = None) -> JSONResponse:
        return JSONResponse({"styles": session.styles(schema)})

    def _state_payload() -> dict:
        st = session.state
        return {
            "source": st.source,
            "output_dir": st.output_dir,
            "plugin": st.plugin,
            "schema": st.schema,
            "style_zh": st.style_zh,
            "style_en": st.style_en,
            "formats": st.formats,
            "langs": st.langs,
            "naming": st.naming,
            "blink": st.blink,
            "typography": session.typography.as_dict(),
            "watching": session.watching,
            "syncing": session._syncing,
            "output_files": session.output_files(),
            "last_stats": session.last_stats,
        }

    @app.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(_state_payload())

    @app.post("/api/config")
    async def set_config(req: Request) -> JSONResponse:
        data = await req.json()
        st = session.state
        if "source" in data:
            st.source = (data.get("source") or "").strip()
        if "output_dir" in data:
            st.output_dir = (data.get("output_dir") or "").strip()
        if "plugin" in data:
            st.plugin = data.get("plugin", st.plugin)
        if "schema" in data:
            st.schema = data.get("schema", st.schema)
        if "style_zh" in data:
            st.style_zh = data.get("style_zh", st.style_zh)
        if "style_en" in data:
            st.style_en = data.get("style_en", st.style_en)
        if "formats" in data or "langs" in data:
            if "formats" in data:
                st.formats = data.get("formats", st.formats) or []
            if "langs" in data:
                st.langs = data.get("langs", st.langs) or []
        if "naming" in data:
            st.naming = "overwrite" if data.get("naming") == "overwrite" else "timestamp"
        if "blink" in data:
            st.blink = bool(data.get("blink", st.blink))
        if "typography" in data:
            session.typography = TypographyConfig.parse(data.get("typography"))
        # Rebuild config eagerly so file list/validate reflect current values.
        session.build_config()
        # 文档标准配置变更时，若正在监听则立即用新规则重跑输出（对齐 Qt 行为）
        if "typography" in data and session.watching:
            session.run_sync_async()
        return JSONResponse(_state_payload())

    @app.post("/api/upload")
    async def upload(req: Request, filename: str = "") -> JSONResponse:
        """接收浏览器上传的源文件（raw body），保存到 ~/.md-sync/uploads/ 并设为源文件。"""
        safe = Path(filename or "upload.md").name or "upload.md"
        if not safe.lower().endswith((".md", ".markdown", ".txt", ".text")):
            safe = Path(safe).stem + ".md"
        data = await req.body()
        if not data:
            return JSONResponse({"ok": False, "errors": ["上传内容为空"]})
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = _UPLOAD_DIR / safe
        target.write_bytes(data)
        session.state.source = str(target)
        session.build_config()
        session.log.append(f"⬆ 已上传源文件: {target.name}（{len(data)} 字节）")
        return JSONResponse(_state_payload())

    @app.post("/api/sync")
    async def sync() -> JSONResponse:
        ok, problems = session.run_sync()
        if not ok:
            return JSONResponse({"ok": False, "errors": problems})
        return JSONResponse({"ok": True, "started": True})

    @app.post("/api/watch")
    async def watch(req: Request) -> JSONResponse:
        data = await req.json()
        if data.get("enabled"):
            return JSONResponse({"ok": session.watch(), "watching": session.watching})
        session.stop_watch()
        return JSONResponse({"ok": True, "watching": session.watching})

    @app.post("/api/clear")
    async def clear() -> JSONResponse:
        n = session.clear_outputs()
        return JSONResponse({"ok": True, "removed": n})

    @app.post("/api/open-dir")
    async def open_dir() -> JSONResponse:
        shown = session.open_dir()
        return JSONResponse({"ok": shown is None, "path": str(session.output_root())})

    # Realtime log stream via SSE; fallbacks to polling are fine.
    @app.get("/api/logs")
    async def logs(req: Request, after: int = 0, _stream: int = 0) -> StreamingResponse:
        # If _stream is set, keep the connection open and push new lines.
        async def gen():
            from contextlib import suppress

            last = after
            while True:
                lines = session.log.tail(last)
                for ln in lines:
                    yield f"data: {ln['text']}\n\n"
                    last = ln["id"]
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(req.is_disconnected(), timeout=0.5)
                await asyncio.sleep(0.4)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/refresh")
    async def refresh() -> JSONResponse:
        session.build_config()
        return JSONResponse({"ok": True, "output_files": session.output_files()})

    @app.post("/api/normalize")
    async def normalize() -> JSONResponse:
        src = session.source_path()
        if src is None:
            return JSONResponse({"ok": False, "errors": ["请先选择有效的源文件。"]})
        session.log.append(f"🧹 规范化源文档: {src.name}")
        session.log.append("  ✓ 中英文混排规范将在输出渲染时应用（源文件保持不变）")
        return JSONResponse({"ok": True})

    @app.get("/api/file")
    async def serve_file(path: str = "", download: bool = False):
        from urllib.parse import quote

        from fastapi.responses import Response

        if not path:
            return JSONResponse({"ok": False, "errors": ["缺少 path"]})
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            return JSONResponse({"ok": False, "errors": ["文件不存在"]})
        media = {
            ".html": "text/html; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }.get(p.suffix.lower(), "application/octet-stream")
        headers = {}
        if download:
            headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(p.name)}"
        return Response(p.read_bytes(), media_type=media, headers=headers)

    return app


def main() -> None:
    """CLI/uvicorn entry for ``md-sync start``."""

    import uvicorn

    host = os.environ.get("MD_SYNC_HOST", "127.0.0.1")
    port = int(os.environ.get("MD_SYNC_PORT", "8580"))
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    print("=" * 48)
    print("  md-sync Web UI")
    print(f"  ➜  http://{host}:{port}")
    print("  (按下 Ctrl+C 停止)")
    print("=" * 48)
    uvicorn.run(app, host=host, port=port, log_level="warning")


app = create_app()

if __name__ == "__main__":
    main()
