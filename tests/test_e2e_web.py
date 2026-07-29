"""End-to-end test: build rendered output from a generated sample and serve it.

Self-contained (no external project files required); runs in CI. The test
changes the working directory into a temporary project so that the relative
``source``/``path`` fields in ``md-sync.yaml`` resolve correctly.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.web.app import create_app

_YAML = (
    "project: 张三\n"
    "source: resume.md\n"
    "outputs:\n"
    "  - format: html\n"
    "    lang: zh\n"
    "    style: web\n"
    "    path: build\n"
)
_SOURCE = "# 张三\n\n## 工作经历\n\n- 公司A | 工程师\n"


@pytest.fixture
def sample_project():
    saved = os.getcwd()
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    (root / "md-sync.yaml").write_text(_YAML, encoding="utf-8")
    (root / "resume.md").write_text(_SOURCE, encoding="utf-8")
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(saved)
        tmp.cleanup()


def test_html_build_from_sample(sample_project: Path) -> None:
    cfg = ProjectConfig.load("md-sync.yaml")
    pipeline = SyncPipeline(cfg)
    ctx = pipeline.run()
    assert ctx.get("errors") == [], ctx.get("errors")

    outputs = ctx.get("outputs", [])
    assert outputs, "pipeline reported no outputs"
    for r in outputs:
        assert r.get("ok"), r.get("error")


def test_web_app_serves_project(sample_project: Path) -> None:
    cfg = ProjectConfig.load("md-sync.yaml")
    pipeline = SyncPipeline(cfg)
    pipeline.run()

    app = create_app(cfg, pipeline)
    assert app is not None
    client = TestClient(app)

    resp = client.get("/api/status")
    assert resp.status_code == 200, resp.status_code
    assert "project" in resp.json()

    resp = client.get("/")
    assert resp.status_code == 200, resp.status_code


def test_run_produces_output(sample_project: Path) -> None:
    cfg = ProjectConfig.load("md-sync.yaml")
    pipeline = SyncPipeline(cfg)
    ctx = pipeline.run()
    assert ctx.get("errors") == [], ctx.get("errors")
    assert ctx.get("outputs"), "pipeline reported no outputs"
