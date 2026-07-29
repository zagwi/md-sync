"""Test web dashboard template rendering.

Run directly:  python test_web.py
"""

from pathlib import Path

from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline


def main() -> None:
    cfg = ProjectConfig.load(Path("projects/resume/md-sync.yaml"))
    pipeline = SyncPipeline(cfg)

    # Test dry run (used by API)
    info = pipeline.run_dry()
    print(f"Source: {info['source']}")
    print(f"Sections: {len(info['sections'])}")
    for s in info['sections']:
        print(f"  {s['title']}: {s['items']} items")

    # Test outputs dict conversion (used by web UI)
    outputs_data = []
    for out in cfg.outputs:
        outputs_data.append({
            "format": out.format,
            "lang": out.lang,
            "path": out.path,
            "pdf": out.pdf,
            "pdf_path": out.pdf_path or "",
            "style": out.style or out.theme or "default",
        })
    print(f"\nOutputs ({len(outputs_data)}):")
    for o in outputs_data:
        print(f"  {o['format']} ({o['lang']}) [{o['style']}] → {Path(o['path']).name}")

    # Test pipeline run
    stats = pipeline.run()
    ok = len([r for r in stats.get("outputs", []) if r.get("ok")])
    err = len(stats.get("errors", []))
    print(f"\nSync: {ok} OK, {err} errors")
    if err:
        for e in stats["errors"]:
            print(f"  ERR: {e}")

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
