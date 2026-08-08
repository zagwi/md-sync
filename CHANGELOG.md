# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Web 看板回归（FastAPI）：界面独立为 `md_sync/web/static/index.html`（单文件、Tailwind + 原生 JS），
  `md-sync start` / `uvicorn md_sync.web.app:app` 启动；上传 / 下载源文件、浏览器内配置文档标准、
  SSE 实时日志、输出文件列表。此前 Web 本应废弃（见下），已于 `feat(web)` 重建，`md-sync start` 恢复。
- 依赖补齐：`markdown-it-py`、`markupsafe`（实际直接 import 但此前未声明）；新增可选 extras
  `gui = ["PySide6>=6.6"]`、`pdf = ["PyMuPDF>=1.24"]`（CI 测试因此引入 PySide6）。
- 打包：`scripts/build_app.py` 增加 `md_sync/web/static` 资源，PyInstaller 产物可内置 Web 界面。

### Changed

- Qt GUI: added 「文档标准配置」 dialog exposing the 7 typography rules
  (previously only configurable in the Web UI); source-normalize now honors
  the dialog's current config.
- UI 审计（Web + Qt）：修复 `#typo-enabled` 重复绑定导致开关点击失效；窗口装饰按钮改为纯装饰
  `<span aria-hidden>`；状态徽标 / 日志增加 `role="status"`/`role="log"` live 区；对比度达标
  （success/warning/destructive/html/markdown/docx/violet 及 Qt 表头/meta 全量 ≥4.5:1）；
  `renderFiles` 对用户可控文本转义；`prefers-reduced-motion` 降级；≤1150/960px 响应式降级。
- Web 后端加固：`/api/file` 仅允许输出根与上传目录内路径（堵目录穿越）；`/api/logs` SSE 行内换行
  消毒；`/api/upload` 20MB 上限。

### Removed

- Web UI 旧实现（Python 端拼接 HTML 的页面）已彻底不存，由独立静态单文件
  `md_sync/web/static/index.html` + FastAPI JSON API 的看板取代。

## [0.1.0] - 2026-07-28

### Added
- Markdown source watcher with automatic multi-format (md / html / pdf) sync.
- Multi-language translation pipeline (zh / en) with a JSON translation cache.
- Built-in template and theme system under the `md_sync` package.
- Web UI (FastAPI) for dashboard, setup, sync history and events.
- Electron desktop shell wrapping the local Web UI.
- CLI entry point `md-sync`.
- MIT License.

### Changed
- Moved `templates/` and `themes/` into the `md_sync` package so they are
  distributed with the wheel and resolvable at runtime.
- Standardized `pyproject.toml` metadata (license, classifiers, URLs, pytest).
