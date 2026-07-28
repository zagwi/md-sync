# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
