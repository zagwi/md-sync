"""Start md-sync web server for browser demo."""
from pathlib import Path

from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.web.app import create_app
import uvicorn

HOST = "127.0.0.1"
PORT = 8580

ROOT = Path(__file__).resolve().parent.parent  # repo root (scripts/ -> repo/)
cfg = ProjectConfig.load(ROOT / "projects" / "resume" / "md-sync.yaml")
pipeline = SyncPipeline(cfg)

# 启动时不自动同步，等待用户在仪表盘点击「同步」后开始
app = create_app(cfg, pipeline)

print("=" * 48)
print("  md-sync 已启动")
print("  在浏览器打开：")
print(f"  ➜  http://{HOST}:{PORT}")
print("=" * 48)

uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
