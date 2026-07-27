"""Start web UI, sync once, then verify dashboard renders."""
from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.web.app import create_app
import uvicorn
import threading
import time
import urllib.request

cfg = ProjectConfig.load("projects/resume/md-sync.yaml")
pipeline = SyncPipeline(cfg)

# Sync first
stats = pipeline.run()
ok = sum(1 for r in stats["outputs"] if r.get("ok"))
print(f"Sync: {ok} OK, {len(stats.get('errors',[]))} errors")

# Create FastAPI app
app = create_app(cfg, pipeline)
if app is None:
    print("FAIL: fastapi/uvicorn not installed")
    exit(1)

# Start server in background
t = threading.Thread(
    target=lambda: uvicorn.run(app, host="127.0.0.1", port=8580, log_level="warning"),
    daemon=True,
)
t.start()
time.sleep(2)

# Test API
try:
    resp = urllib.request.urlopen("http://127.0.0.1:8580/api/status")
    import json
    data = json.loads(resp.read())
    print(f"API OK: project={data['project']}, sections={len(data['sections'])}")
except Exception as e:
    print(f"API FAIL: {e}")
    exit(1)

# Test Dashboard
try:
    resp = urllib.request.urlopen("http://127.0.0.1:8580/")
    html = resp.read().decode()
    print(f"DASHBOARD OK: {len(html)} chars")
    for marker in ["源文件", "输出文件", "立即同步", "同步历史"]:
        found = marker in html
        print(f"  {'OK' if found else 'MISSING'}: {marker}")
except Exception as e:
    print(f"DASHBOARD FAIL: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print("\nALL PASSED")
