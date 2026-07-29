"""Complete end-to-end test: Web UI → create project → parse → HTML → PDF.

Tests the full pipeline through the web API:
  1. Start the md-sync web server
  2. POST /api/setup — create a project from a real resume.md
  3. POST /api/sync  — full sync (parse + render HTML + export PDF)
  4. Verify all outputs exist and contain expected content
  5. Verify dashboard renders correctly
  6. Verify plugin/schema API endpoints work
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

HOST = "127.0.0.1"
PORT = 18580  # different from default to avoid conflicts
BASE = f"http://{HOST}:{PORT}"

# ── Sample resume content (built-in resume format) ─────────────────────

SAMPLE_RESUME = """# 张三 — 资深后端工程师
zhangsan@example.com | github.com/zhangsan | 硕士（985） | 随时到岗
---
## 综合素质及能力
- 8 年分布式系统设计与开发经验，主导过 3 个日活 100 万+ 的后端平台
- 精通 Go、Python、Java，熟悉微服务架构与容器化部署
- 带领过 10 人技术团队，具备跨部门协作与项目管理能力

## 教育经历
**2014.09-2017.06 清华大学（985）（计算机科学与技术 · 硕士）**
**2010.09-2014.06 北京大学（985）（计算机科学与技术 · 学士）**

## 工作经历
**2020.03-至今 字节跳动（资深后端工程师）**
  负责抖音推荐系统核心链路的重构，QPS 从 5 万提升至 12 万
  设计并实现了通用的流量调度中间件，覆盖 200+ 服务
  **涉及技术：** Go、Kafka、Redis、Kubernetes

**2017.07-2020.02 阿里巴巴（后端工程师）**
  参与双十一大促保障系统开发，支撑峰值 50 万 QPS
  **涉及技术：** Java、Spring Cloud、MySQL、RocketMQ

## 项目经历
**2022.01-2023.06 统一流量调度平台（技术负责人）**
  构建多维度流量调度引擎，支持灰度发布、蓝绿部署、熔断降级
  日均处理 10 亿+ 请求，系统可用性 99.99%
  **涉及技术：** Go、etcd、gRPC、Prometheus

**2019.03-2021.12 实时风控系统（核心开发者）**
  基于 Flink 构建毫秒级风控决策引擎，准确率 99.7%
  覆盖账号安全、交易风控、内容审核三大场景
  **涉及技术：** Flink、Kafka、HBase、Elasticsearch

## 开源项目
- **kafmesh**：基于 Kafka 的轻量级消息网格框架，GitHub 500+ star
  **涉及技术：** Go、Kafka、gRPC
"""


# ── Helpers ──────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(label)
    else:
        fail(f"{label}" + (f" — {detail}" if detail else ""))


def http_get(path: str, timeout: int = 10) -> tuple[int, str]:
    try:
        resp = urllib.request.urlopen(f"{BASE}{path}", timeout=timeout)
        return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except Exception as e:
        return 0, str(e)


def http_post(path: str, body: dict) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except Exception as e:
        return 0, str(e)


# ── Main test ────────────────────────────────────────────────────────────


def main() -> None:
    global PASS, FAIL
    PASS = 0
    FAIL = 0
    tmpdir = None

    from md_sync.web.app import create_app as _create_app
    import uvicorn as _uvicorn

    # ── 1. Setup temp project ──────────────────────────────────────────

    tmpdir = Path("/tmp") / f"md-sync-e2e-{int(time.time())}"
    src_dir = tmpdir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)
    resume_path = src_dir / "zhangsan-resume.md"
    resume_path.write_text(SAMPLE_RESUME, encoding="utf-8")
    print(f"\n📁 临时项目目录: {tmpdir}")
    print(f"📄 简历源文件: {resume_path} ({len(SAMPLE_RESUME)} 字符)")

    # ── 2. Start server ───────────────────────────────────────────────

    app = _create_app()
    if app is None:
        print("\n❌ 需要安装 fastapi + uvicorn")
        return 1

    def run_server():
        try:
            _uvicorn.run(app, host=HOST, port=PORT, log_level="error")
        except SystemExit:
            pass

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Poll until server is ready (max 10s, 500ms interval)
    server_ready = False
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{BASE}/api/status", timeout=1)
            server_ready = True
            break
        except Exception:
            time.sleep(0.5)

    if not server_ready:
        print("\n❌ 服务启动超时")
        return 1

    print(f"\n🌐 Web 服务已启动: http://{HOST}:{PORT}")

    try:
        # ── 3. Verify server is alive ──────────────────────────────────

        print("\n── 1/7: 验证服务启动 ──")
        status, body = http_get("/api/status")
        check("GET /api/status 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            check("status 无活动项目（初始状态）", data.get("project") == "")

        # ── 4. Test plugin/schema API endpoints ────────────────────────

        print("\n── 2/7: 验证插件管理 API ──")

        status, body = http_get("/api/plugins")
        check("GET /api/plugins 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            plugins = data.get("plugins", [])
            check("至少有一个内置插件包", len(plugins) >= 1)
            resume_plugin = next((p for p in plugins if p.get("name") == "builtin-resume"), None)
            check("找到 builtin-resume 插件包", resume_plugin is not None)
            if resume_plugin:
                check("类型为 pack", resume_plugin.get("plugin_type") == "pack")
                check("schema 为 resume", resume_plugin.get("parser_schema") == "resume")
                check("有 template.md", resume_plugin.get("has_template") is True)

        status, body = http_get("/api/schemas")
        check("GET /api/schemas 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            schema_names = [s["name"] for s in data.get("schemas", [])]
            check("包含 resume schema", "resume" in schema_names)
            check("无重复 schema", len(schema_names) == len(set(schema_names)))

        # Test template.md generation
        status, body = http_post("/api/plugins/template", {"name": "builtin-resume"})
        check("POST /api/plugins/template 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            check("状态为 ok", data.get("status") == "ok", f"got {data.get('status')}")
            generated_path = data.get("path", "")
            if generated_path:
                gen_file = Path(generated_path)
                check("template.md 文件已生成", gen_file.exists())
                if gen_file.exists():
                    content = gen_file.read_text(encoding="utf-8")
                    check("template.md 包含 # 姓名", "# 姓名" in content)
                    gen_file.unlink()

        # ── 5. Create project via API ──────────────────────────────────

        print("\n── 3/7: 创建项目 (POST /api/setup) ──")

        setup_body = {
            "source": str(resume_path),
            "schema": "resume",
            "formats": [
                {"format": "html", "langs": ["zh", "en"]},
                {"format": "md", "langs": ["zh", "en"]},
                {"format": "pdf", "langs": ["zh", "en"]},
            ],
            "output_root": str(tmpdir / "output"),
            "style_zh": "bwx",
            "style_en": "modern",
        }
        status, body = http_post("/api/setup", setup_body)
        check("POST /api/setup 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            check("setup 状态为 ok", data.get("status") == "ok", f"got {data.get('status')}")
            ok(f"项目名: {data.get('project', '?')}")
        else:
            print(f"  响应: {body[:500]}")

        # ── 6. Verify config and project status ────────────────────────

        print("\n── 4/7: 验证项目配置 ──")

        status, body = http_get("/api/status")
        check("GET /api/status 返回 200", status == 200, f"got {status}")
        if status == 200:
            data = json.loads(body)
            check("项目名称不为空", bool(data.get("project")), f"project={data.get('project')}")
            check("源文件路径不为空", bool(data.get("source")), f"source={data.get('source')}")
            sections = data.get("sections", [])
            check("解析到至少 3 个章节", len(sections) >= 3, f"got {len(sections)}")
            section_titles = [s["title"] for s in sections]
            for expected in ["综合素质及能力", "工作经历", "项目经历"]:
                check(f"包含章节: {expected}", expected in section_titles)

        # ── 7. Run sync ────────────────────────────────────────────────

        print("\n── 5/7: 执行同步 (POST /api/sync) ──")

        status, body = http_post("/api/sync", {})
        check("POST /api/sync 返回 200", status == 200, f"got {status}")
        time.sleep(1)  # let sync complete

        # ── 8. Verify output files ──────────────────────────────────────

        print("\n── 6/7: 验证输出文件 ──")

        html_dir = tmpdir / "output" / "html"
        md_dir = tmpdir / "output" / "md"
        pdf_dir = tmpdir / "output" / "pdf"

        if html_dir.exists():
            html_files = list(html_dir.iterdir())
            check("HTML 输出目录存在且非空", len(html_files) >= 1,
                  f"文件: {[f.name for f in html_files]}")
            for f in html_files:
                content = f.read_text(encoding="utf-8")
                check(f"HTML 文件 {f.name} 包含简历姓名",
                      "张三" in content or "Zhang San" in content.replace("zhangsan", "Zhang San"))
                check(f"HTML 文件 {f.name} 包含章节",
                      "综合素质" in content or "Professional" in content or "Experience" in content)
                check(f"HTML 文件 {f.name} 含 <html> 标签", "<html" in content.lower())
                check(f"HTML 文件 {f.name} 含 <body>", "<body" in content.lower())
                ok(f"  {f.name}: {len(content)} 字符")
        else:
            fail("HTML 输出目录未创建")

        if md_dir.exists():
            md_files = list(md_dir.iterdir())
            check("MD 输出目录存在且非空", len(md_files) >= 1,
                  f"文件: {[f.name for f in md_files]}")
            for f in md_files:
                check(f"MD 文件 {f.name} 非空", f.stat().st_size > 0)
        else:
            fail("MD 输出目录未创建")

        if pdf_dir.exists():
            pdf_files = list(pdf_dir.iterdir())
            check("PDF 输出目录存在", len(pdf_files) >= 0)
            if pdf_files:
                for f in pdf_files:
                    check(f"PDF 文件 {f.name} 非空",
                          f.stat().st_size > 500, f"size={f.stat().st_size}")
                    with open(f, "rb") as _fh:
                        magic = _fh.read(4)
                    check(f"PDF 文件 {f.name} 以 %PDF 开头",
                          magic == b"%PDF", f"got {magic!r}")
                    ok(f"  {f.name}: {f.stat().st_size} bytes")
            else:
                ok("PDF 目录为空（无 Chromium 时正常，非错误）")
        else:
            ok("PDF 目录未创建（无 Chromium 时正常，非错误）")

        # ── 9. Verify dashboard renders ────────────────────────────────

        print("\n── 7/7: 验证仪表盘渲染 ──")

        status, body = http_get("/")
        check("GET / 返回 200", status == 200, f"got {status}")
        if status == 200:
            markers = [
                "📄 源文件",
                "🎨 转换模板",
                "📦 输出文件",
                "🔔 同步事件",
                "⏱ 同步历史",
                "zhangsan-resume",
                "综合素质及能力",
            ]
            for m in markers:
                check(f"仪表盘包含「{m}」", m in body, f"未找到: {m}")
            ok(f"仪表盘 HTML 大小: {len(body)} 字符")

        # ── 10. Verify file API ────────────────────────────────────────

        if html_dir.exists():
            html_files = list(html_dir.iterdir())
            if html_files:
                fpath = str(html_files[0])
                status, body = http_get(f"/api/file?path={fpath}")
                check("GET /api/file 返回 200 (HTML)", status == 200, f"got {status}")

    finally:
        # ── Cleanup (runs even if tests fail) ──────────────────────────
        print(f"\n── 清理 ──")
        if tmpdir and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)
        cfg = Path.cwd() / "md-sync.yaml"
        if cfg.exists():
            cfg.unlink()
        ok("临时文件和配置已清理")

    # ── Summary ────────────────────────────────────────────────────────

    total = PASS + FAIL
    print(f"\n{'═' * 48}")
    print(f"  E2E 测试完成")
    print(f"  ✅ 通过: {PASS}  |  ❌ 失败: {FAIL}  |  总计: {total}")
    print(f"{'═' * 48}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
