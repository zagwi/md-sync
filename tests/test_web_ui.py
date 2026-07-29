"""Smoke test the web UI app against the bundled resume project.

Skipped automatically when projects/resume/md-sync.yaml is absent.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.web.app import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUME_YAML = PROJECT_ROOT / "projects" / "resume" / "md-sync.yaml"


def test_web_ui_endpoints() -> None:
    if not RESUME_YAML.exists():
        pytest.skip(f"sample project not found: {RESUME_YAML}")

    cfg = ProjectConfig.load(RESUME_YAML)
    pipeline = SyncPipeline(cfg)
    pipeline.run()

    app = create_app(cfg, pipeline)
    if app is None:
        pytest.skip("fastapi/uvicorn not installed")

    client = TestClient(app)

    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "project" in data

    resp = client.get("/")
    assert resp.status_code == 200, resp.status_code
    html = resp.text
    for marker in ("源文件", "输出文件", "立即同步", "同步历史"):
        assert marker in html, f"missing marker: {marker}"
