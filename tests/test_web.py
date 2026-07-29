"""Test pipeline dry-run and output conversion.

Skipped automatically when the bundled sample project (projects/resume) is
absent.
"""
from pathlib import Path

import pytest
from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUME_YAML = PROJECT_ROOT / "projects" / "resume" / "md-sync.yaml"


def test_dry_run_and_outputs() -> None:
    if not RESUME_YAML.exists():
        pytest.skip(f"sample project not found: {RESUME_YAML}")

    cfg = ProjectConfig.load(RESUME_YAML)
    pipeline = SyncPipeline(cfg)

    info = pipeline.run_dry()
    assert "source" in info
    assert isinstance(info["sections"], list)

    outputs_data = []
    for out in cfg.outputs:
        outputs_data.append(
            {
                "format": out.format,
                "lang": out.lang,
                "path": out.path,
                "pdf": out.pdf,
                "pdf_path": out.pdf_path or "",
                "style": out.style or out.theme or "default",
            }
        )
    assert len(outputs_data) >= 1
    assert all(o["format"] for o in outputs_data)

    stats = pipeline.run()
    ok = len([r for r in stats.get("outputs", []) if r.get("ok")])
    assert ok >= 1, stats.get("errors")
    assert stats.get("errors") == [], stats.get("errors")
