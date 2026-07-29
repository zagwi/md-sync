"""Web dashboard for md-sync — no config needed, all setup via browser."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from md_sync.config import OutputConfig, ProjectConfig, derive_output_path
from md_sync.core.pipeline import SyncPipeline
from md_sync.plugin.registry import PluginRegistry
from md_sync.template.manager import TemplateManager
from md_sync.translate.langdetect import detect_lang, lang_label
from md_sync.translate.fallback import _detect_provider


# ── Project history persistence ────────────────────────────────────────────


_HISTORY_DIR = Path.home() / ".md-sync"
_HISTORY_FILE = _HISTORY_DIR / "projects.json"


def _ensure_history_dir() -> None:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_history() -> list[dict]:
    """Load all saved projects from history file."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        return data.get("projects", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_to_history(entry: dict) -> None:
    """Add or update a project entry in history, then persist."""
    _ensure_history_dir()
    projects = _load_history()
    # Update existing entry with same config_path, or append
    # Preserve original created timestamp when updating
    for i, p in enumerate(projects):
        if p.get("config_path") == entry.get("config_path"):
            entry["created"] = p.get("created", entry.get("created", ""))
            projects[i] = entry
            break
    else:
        projects.append(entry)
    # Sort by last_opened descending
    projects.sort(key=lambda x: x.get("last_opened", ""), reverse=True)
    _HISTORY_FILE.write_text(
        json.dumps({"projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_history_entry(config: ProjectConfig) -> dict:
    """Build a history entry dict from an active project config."""
    fmt_parts = []
    seen = set()
    for o in config.outputs:
        s = f"{o.format.upper()}/{o.lang}"
        if o.pdf:
            s += "+PDF"
        if s not in seen:
            seen.add(s)
            fmt_parts.append(s)
    # Prefer the human-readable source file name over the internal project
    # key (e.g. "resume") so the history list shows something meaningful.
    src_label = Path(str(config.source)).name or config.project
    return {
        "name": src_label,
        "source": str(config.source),
        "config_path": str(config.config_path),
        "formats_summary": " | ".join(fmt_parts),
        "created": datetime.now().isoformat(timespec="seconds"),
        "last_opened": datetime.now().isoformat(timespec="seconds"),
    }


try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
except ImportError:
    FastAPI = None  # type: ignore


# ── File status helpers ────────────────────────────────────────────────────


def _file_status(path: str) -> dict:
    if not path:
        return {"exists": False, "size": 0, "mtime": 0}
    p = Path(path)
    if not p.exists():
        return {"exists": False, "size": 0, "mtime": 0}
    stat = p.stat()
    return {"exists": True, "size": stat.st_size, "mtime": stat.st_mtime}


def _status_color(status: dict, source_mtime: float = 0) -> str:
    if not status["exists"]:
        return "red"
    if source_mtime and source_mtime > status["mtime"]:
        return "#f59e0b"
    return "#22c55e"


# ── Format grouping helper ─────────────────────────────────────────────────


def _build_formats_data(outputs: list[dict], source_mtime: float, source_path: str = "") -> list[dict]:
    groups: dict[str, dict] = {}
    for o in outputs:
        fmt = o["format"]
        if fmt not in groups:
            groups[fmt] = {"format": fmt, "active": True, "languages": []}

        path = o["path"]
        unconfigured = not path
        st = _file_status(path)
        color = _status_color(st, source_mtime)
        size = f"{st['size'] // 1024}KB" if st["exists"] else "--"
        pdf_ok = bool(o.get("pdf") and o.get("pdf_path") and _file_status(o["pdf_path"]).get("exists"))

        if unconfigured:
            filename = ""
            rel_path = ""
        else:
            filename = Path(path).name
            # Build a short relative path for display (relative to CWD)
            try:
                rel_path = str(Path(path).relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(Path(path))

        groups[fmt]["languages"].append({
            "lang": o["lang"],
            "path": path,
            "unconfigured": unconfigured,
            "is_source": path == source_path,
            "rel_path": rel_path,
            "pdf": o.get("pdf", False),
            "pdf_path": o.get("pdf_path", ""),
            "style": o.get("style", "default"),
            "exists": st["exists"],
            "color": color,
            "size": size,
            "pdf_ok": pdf_ok,
            "filename": filename,
        })

    order = {"md": 0, "html": 1, "pdf": 2}
    result = list(groups.values())

    # PDF is surfaced as a first-class format. For every HTML output that has
    # PDF enabled, emit a real PDF file row pointing at its pdf_path, so the
    # user actually sees a PDF output in the file list instead of a hidden flag.
    pdf_langs = []
    for o in outputs:
        if o.get("format") == "html" and o.get("pdf") and o.get("pdf_path"):
            pp = Path(o["pdf_path"])
            st = _file_status(str(pp))
            pcolor = _status_color(st, source_mtime)
            psize = f"{st['size'] // 1024}KB" if st["exists"] else "--"
            try:
                prel = str(pp.relative_to(Path.cwd()))
            except ValueError:
                prel = str(pp)
            pdf_langs.append({
                "lang": o["lang"],
                "path": str(pp),
                "is_source": str(pp) == source_path,
                "rel_path": prel,
                "pdf": False,
                "pdf_path": "",
                "style": o.get("style", "default"),
                "exists": st["exists"],
                "color": pcolor,
                "size": psize,
                "pdf_ok": False,
                "filename": pp.name,
            })
    pdf_active = any(o.get("pdf") for o in outputs if o.get("format") == "html")
    result.append({"format": "pdf", "active": pdf_active, "languages": pdf_langs})
    return sorted(result, key=lambda x: (order.get(x["format"], 99), x["format"]))


# ── Setup page renderer ────────────────────────────────────────────────────


def _render_setup_page(
    project: str,
    templates: list[dict],
    history_projects: Optional[list[dict]] = None,
    plugins: Optional[list[dict]] = None,
    schemas: Optional[list[dict]] = None,
) -> str:
    """Render the project setup page (shown when no config exists)."""
    # History card
    hist_cards = ""
    if history_projects:
        for hp in history_projects:
            src = hp.get("source", "")
            src_path = Path(src).resolve() if src else Path()
            src_name = src_path.name if src_path.exists() else src
            exists = "✓" if src_path.exists() else "✗"
            exists_color = "#22c55e" if src_path.exists() else "#ef4444"
            # Escape single quotes for JS string
            cfg_path_escaped = hp.get("config_path", "").replace("'", "\\'")
            hist_cards += (
                f"<div class=\"hist-item\" onclick=\"loadHistory('{cfg_path_escaped}')\">"
                f"<div class=\"hist-name\">{hp.get('name', '?')}</div>"
                f"<div class=\"hist-src\"><span style=\"color:{exists_color};\">{exists}</span> {src_name}</div>"
                f"<div class=\"hist-fmt\">{hp.get('formats_summary', '')}</div>"
                f"</div>"
            )
    if not hist_cards:
        hist_cards = "<p style='color:#999;font-size:13px;'>暂无历史项目</p>"

    tpl_options = ""
    for t in templates:
        tpl_options += f"<option value=\"{t['name']}\">{t['label']}</option>"

    # Schema options
    schema_options = ""
    if schemas:
        for s in schemas:
            schema_options += f"<option value=\"{s['name']}\" {'selected' if s.get('default') else ''}>{s['label']}</option>"
    else:
        schema_options = "<option value=\"resume\">简历 (resume)</option>"

    # Plugin pack cards
    plugin_cards = ""
    if plugins:
        for p in plugins:
            has_template = bool(p.get("has_template"))
            tpl_btn = (
                f"<button class=\"btn btn-sm\" onclick=\"generateTemplate('{p['name']}')\" "
                f"style=\"background:#1a56db;\">生成 template.md</button>"
            ) if has_template else ""
            schema_tag = f"<code style=\"font-size:11px;color:#1a56db;background:#e8f0fe;padding:1px 6px;border-radius:3px;\">{p.get('parser_schema','-')}</code>"
            plugin_cards += (
                f"<div style=\"display:flex;align-items:center;gap:10px;padding:10px 0;"
                f"border-bottom:1px solid #f0f0f0;\">"
                f"<div style=\"flex:1;min-width:0;\">"
                f"<div style=\"font-size:13px;font-weight:500;color:#222;\">{p.get('name','?')}"
                f" <span style=\"font-size:11px;color:#999;font-weight:400;\">v{p.get('version','?')}</span></div>"
                f"<div style=\"font-size:12px;color:#666;word-break:break-all;\">{p.get('description','')}</div>"
                f"<div style=\"font-size:12px;color:#999;margin-top:2px;\">"
                f"类型: {p.get('plugin_type','?')} · schema: {schema_tag}</div>"
                f"</div>"
                f"{tpl_btn}</div>"
            )
    if not plugin_cards:
        plugin_cards = "<p style='color:#999;font-size:13px;'>无插件包</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>md-sync · 新项目</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#f5f5f5;color:#222;font-size:14px;padding:24px;}}
  .container{{max-width:600px;margin:40px auto}}
  h1{{font-size:26px;margin-bottom:4px;display:flex;align-items:center;gap:8px}}
  .sub{{color:#666;margin-bottom:24px;font-size:14px;}}
  .card{{background:#fff;border-radius:10px;padding:28px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
  .card h2{{font-size:16px;margin-bottom:16px;color:#333}}
  .field{{margin-bottom:16px}}
  .field label{{display:block;font-size:13px;font-weight:600;color:#555;margin-bottom:4px}}
  .field input,.field select{{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px;background:#fff}}
  .field input:focus,.field select:focus{{outline:2px solid #222;border-color:#222}}
  .field .hint{{font-size:12px;color:#999;margin-top:3px}}
  .btn{{padding:10px 28px;background:#222;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer}}
  .btn:hover{{background:#444}}
  .btn:disabled{{opacity:.5;cursor:not-allowed}}
  #result{{margin-top:12px;font-size:13px;color:#666}}
  .fmt-grid{{display:flex;gap:8px;flex-wrap:wrap}}
  .fmt-grid label{{display:inline-flex;align-items:center;gap:4px;padding:8px 16px;border:1px solid #ddd;border-radius:6px;cursor:pointer;font-size:13px;background:#fff;user-select:none}}
  .fmt-grid label:hover{{border-color:#999}}
  .fmt-grid input[type=checkbox]{{width:16px;height:16px;cursor:pointer}}
  .lang-grid{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
  .lang-grid label{{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border:1px solid #ddd;border-radius:6px;cursor:pointer;font-size:13px;background:#fff;user-select:none}}
  .hist-item{{padding:12px 16px;border:1px solid #eee;border-radius:8px;margin-bottom:8px;cursor:pointer;transition:background .15s;}}
  .hist-item:hover{{background:#f5f5f5;border-color:#ccc;}}
  .hist-item:active{{background:#eee;}}
  .hist-name{{font-size:14px;font-weight:600;color:#222;}}
  .hist-src{{font-size:12px;color:#666;margin-top:2px;}}
  .hist-fmt{{font-size:11px;color:#999;margin-top:2px;}}
  .btn-sm{{padding:6px 14px;font-size:12px;}}
</style>
</head>
<body>
<div class="container">

<h1>🚀 md-sync</h1>
<p class="sub">配置你的项目，一切在浏览器中完成</p>

<div class="card">
  <h2>📋 历史项目</h2>
  {hist_cards}
  <p style="margin-top:8px;font-size:12px;color:#999;">点击历史项目直接打开，或按以下步骤创建新项目</p>
</div>

<div class="card">
  <h2>📄 源文件</h2>
  <div id="sourceInputArea">
    <div class="field">
      <label for="sourcePath">输入 Markdown 源文件路径</label>
      <div style="display:flex;gap:8px;">
        <input type="text" id="sourcePath" placeholder="/path/to/your/document.md" style="flex:1;">
        <button class="btn" onclick="browseSource()" style="white-space:nowrap;background:#1a56db;color:#fff;">打开</button>
      </div>
      <div class="hint">输入 .md 文件的完整路径或相对路径</div>
    </div>
  </div>
  <div id="sourceDisplayArea" style="display:none;">
    <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8f8f8;border-radius:6px;">
      <span style="font-size:13px;color:#555;min-width:90px;">当前源文档：</span>
      <span id="sourceDisplayPath" style="font-size:13px;font-weight:500;color:#222;word-break:break-all;flex:1;"></span>
      <button class="btn btn-sm" onclick="showSourceInput()" style="background:#666;">更换</button>
    </div>
  </div>
</div>

<div class="card">
  <h2>🧩 文档格式 (Schema)</h2>
  <div class="field">
    <label for="schemaSelect">选择文档格式</label>
    <select id="schemaSelect" onchange="onSchemaChange()">{schema_options}</select>
    <div id="schemaHint" class="hint" style="margin-top:6px;font-weight:500;"></div>
    <div class="hint">选择解析器：内置简历 (resume) 或插件包的格式。不同 schema 对应不同的源 MD 模板格式。选择源文件后会自动检测推荐 schema。</div>
  </div>
</div>

<div class="card">
  <h2>🎨 模板风格</h2>
  <div class="field">
    <label for="tplZh">中文 HTML 模板</label>
    <select id="tplZh">{tpl_options}</select>
  </div>
  <div class="field">
    <label for="tplEn">English HTML Template</label>
    <select id="tplEn">{tpl_options}</select>
  </div>
</div>

<div class="card">
  <h2>📦 已安装插件包</h2>
  {plugin_cards}
  <p style="margin-top:8px;font-size:12px;color:#999;">
    插件包提供自定义文档格式 (schema) + 解析器 + HTML 模板。<br>
    从 CLI 安装: <code>md-sync plugin install /path/to/plugin-pack/</code>
  </p>
</div>

<div class="card">
  <h2>📦 输出格式</h2>
  <div class="field">
    <label>选择格式和语言版本</label>
    <div id="formatLangGrid" style="display:flex;flex-direction:column;gap:10px;">
      <div class="fmt-group" style="border:1px solid #eee;border-radius:8px;padding:10px 14px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:14px;font-weight:600;cursor:pointer;">
          <input type="checkbox" class="fmt-cb" value="html" checked onchange="toggleFmtLang(this)">
          HTML
        </label>
        <div class="fmt-langs" style="display:flex;gap:8px;margin-top:8px;margin-left:24px;">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="zh" checked> 中文
          </label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="en" checked> English
          </label>
        </div>
      </div>
      <div class="fmt-group" style="border:1px solid #eee;border-radius:8px;padding:10px 14px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:14px;font-weight:600;cursor:pointer;">
          <input type="checkbox" class="fmt-cb" value="md" checked onchange="toggleFmtLang(this)">
          MD（Markdown）
        </label>
        <div class="fmt-langs" style="display:flex;gap:8px;margin-top:8px;margin-left:24px;">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="zh" checked> 中文
          </label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="en" checked> English
          </label>
        </div>
      </div>
      <div class="fmt-group" style="border:1px solid #eee;border-radius:8px;padding:10px 14px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:14px;font-weight:600;cursor:pointer;">
          <input type="checkbox" class="fmt-cb" value="pdf" checked onchange="toggleFmtLang(this)">
          PDF（从 HTML 生成）
        </label>
        <div class="fmt-langs" style="display:flex;gap:8px;margin-top:8px;margin-left:24px;">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="zh" checked> 中文
          </label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;">
            <input type="checkbox" class="lang-cb" value="en" checked> English
          </label>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="card" style="margin-top:16px;">
  <label style="display:block;font-size:14px;font-weight:600;margin-bottom:10px;">输出目录</label>
  <div style="display:flex;gap:8px;">
    <input id="outputDir" type="text" placeholder="留空则输出到源文件所在目录" style="flex:1;padding:8px 10px;border:1px solid #ccc;border-radius:6px;font-size:14px;">
    <button type="button" class="btn btn-secondary" onclick="browseOutputDir()">浏览…</button>
  </div>
</div>

<button class="btn" onclick="createProject()">✨ 创建项目并同步</button>
<div id="result"></div>

</div>

<script>
var confirmedSource = '';

function confirmSource() {{
  var input = document.getElementById('sourcePath');
  var path = input.value.trim();
  if (!path) {{
    document.getElementById('result').textContent = '✗ 请填写源文件路径';
    document.getElementById('result').style.color = '#ef4444';
    return;
  }}
  confirmedSource = path;
  document.getElementById('sourceInputArea').style.display = 'none';
  document.getElementById('sourceDisplayPath').textContent = path;
  document.getElementById('sourceDisplayArea').style.display = 'block';
  input.value = '';
  document.getElementById('result').textContent = '';
  // Plug and Play: auto-detect schema from source content
  autoDetectSchema(path);
}}

async function browseSource() {{
  var input = document.getElementById('sourcePath');
  try {{
    if (window.mdSync && window.mdSync.openFile) {{
      var p = await window.mdSync.openFile();
      if (p) {{ input.value = p; confirmSource(); return; }}
    }}
  }} catch (e) {{}}
  input.focus();
}}

async function browseOutputDir() {{
  var input = document.getElementById('outputDir');
  try {{
    if (window.mdSync && window.mdSync.openDirectory) {{
      var p = await window.mdSync.openDirectory();
      if (p) {{ input.value = p; return; }}
    }}
  }} catch (e) {{}}
  input.focus();
}}

function toggleFmtLang(cb) {{
  var langs = cb.closest('.fmt-group').querySelectorAll('.lang-cb');
  langs.forEach(function(l) {{ l.disabled = !cb.checked; }});
}}

function showSourceInput() {{
  document.getElementById('sourceDisplayArea').style.display = 'none';
  document.getElementById('sourceInputArea').style.display = 'block';
  document.getElementById('sourcePath').value = confirmedSource;
}}

async function createProject() {{
  var btn = document.querySelector('.btn');
  btn.disabled = true;
  var r = document.getElementById('result');
  r.textContent = '正在创建…';
  r.style.color = '#666';

  var source = confirmedSource;
  if (!source) {{
    r.textContent = '✗ 请先确认源文件路径';
    r.style.color = '#ef4444';
    btn.disabled = false;
    return;
  }}

  var formats = [];
  document.querySelectorAll('.fmt-group').forEach(function(group) {{
    var fmtCb = group.querySelector('.fmt-cb');
    if (!fmtCb) return;
    if (fmtCb.checked) {{
      var langs = [];
      group.querySelectorAll('.lang-cb:checked').forEach(function(lcb) {{
        langs.push(lcb.value);
      }});
      if (langs.length > 0) {{
        formats.push({{format: fmtCb.value, langs: langs}});
      }}
    }}
  }});

  try {{
    var resp = await fetch('/api/setup', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{
        source: source,
        formats: formats,
        output_root: document.getElementById('outputDir').value.trim(),
        schema: document.getElementById('schemaSelect').value,
        style_zh: document.getElementById('tplZh').value,
        style_en: document.getElementById('tplEn').value,
      }})
    }});
    var data = await resp.json();
    if (data.status === 'ok') {{
      r.textContent = '✓ 项目已创建，正在生成文件…';
      r.style.color = '#22c55e';
      // 立即触发一次真正的转换，生成输出文件
      try {{
        await fetch('/api/sync', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}' }});
      }} catch(e) {{}}
      r.textContent = '✓ 文件已生成！正在跳转…';
      setTimeout(function(){{ location.href = '/'; }}, 1200);
    }} else {{
      r.textContent = '✗ ' + (data.error || '创建失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 请求失败';
    r.style.color = '#ef4444';
  }}
  btn.disabled = false;
}}

async function loadHistory(configPath) {{
  var r = document.getElementById('result');
  r.textContent = '正在加载…'; r.style.color = '#666';
  try {{
    var resp = await fetch('/api/history/load', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{config_path: configPath}})
    }});
    var data = await resp.json();
    if (data.status === 'ok') {{
      r.textContent = '✓ 已加载，正在跳转…';
      r.style.color = '#22c55e';
      setTimeout(function(){{ location.href = '/'; }}, 800);
    }} else {{
      r.textContent = '✗ ' + (data.error || '加载失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 加载失败'; r.style.color = '#ef4444';
  }}
}}

function onSchemaChange() {{
  var sel = document.getElementById('schemaSelect');
  var val = sel ? sel.value : 'resume';
  var r = document.getElementById('schemaHint');
  if (val !== 'resume') {{
    r.innerHTML = '💡 非内置 schema，确保你的源文件符合该插件包的 <code>template.md</code> 格式。';
    r.style.color = '#1a56db';
  }} else {{
    r.textContent = '';
  }}
}}

async function autoDetectSchema(sourcePath) {{
  var hint = document.getElementById('schemaHint');
  hint.textContent = '🔍 正在检测文档格式…';
  hint.style.color = '#666';
  try {{
    var resp = await fetch('/api/detect-schema', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{path: sourcePath}})
    }});
    var data = await resp.json();
    if (data.status === 'ok' && data.detected) {{
      var schemaName = data.detected.schema;
      var pluginName = data.detected.name;
      var confidence = data.detected.confidence || 'high';
      hint.innerHTML = '🎯 已自动检测: <code>' + schemaName + '</code> (由 ' + pluginName + ' 提供, 置信度: ' + confidence + ')';
      hint.style.color = '#1a56db';
      // Auto-select the detected schema in the dropdown
      var sel = document.getElementById('schemaSelect');
      if (sel) {{
        for (var i = 0; i < sel.options.length; i++) {{
          if (sel.options[i].value === schemaName) {{
            sel.selectedIndex = i;
            break;
          }}
        }}
      }}
    }} else if (data.status === 'ok') {{
      hint.textContent = '📄 未检测到特定插件 schema，将使用通用 Markdown 解析';
      hint.style.color = '#999';
    }} else {{
      hint.textContent = '✗ 检测失败: ' + (data.error || '未知错误');
      hint.style.color = '#ef4444';
    }}
  }} catch(e) {{
    hint.textContent = '✗ 检测出错: ' + e;
    hint.style.color = '#ef4444';
  }}
}}

async function generateTemplate(pluginName) {{
  var r = document.getElementById('result');
  r.textContent = '正在生成 template.md …';
  r.style.color = '#666';
  try {{
    var resp = await fetch('/api/plugins/template', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{name: pluginName}})
    }});
    var data = await resp.json();
    if (data.status === 'ok') {{
      r.innerHTML = '✓ 已生成: <code>' + data.path + '</code>';
      r.style.color = '#22c55e';
    }} else {{
      r.textContent = '✗ ' + (data.error || '生成失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 请求失败: ' + e;
    r.style.color = '#ef4444';
  }}
}}
</script>

</body>
</html>"""


# ── Dashboard renderer ─────────────────────────────────────────────────────


def _render_source_missing(config, missing_path: str) -> str:
    """Friendly page shown when the configured source file is missing."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>源文件缺失 · md-sync</title>
<style>
  body {{ font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:#f1f5f9;color:#1e293b;margin:0;padding:40px; }}
  .box {{ max-width:680px;margin:60px auto;background:#fff;border-radius:12px;
         padding:32px 36px;box-shadow:0 4px 20px rgba(0,0,0,.08); }}
  .err {{ color:#dc2626;font-size:15px;margin:16px 0;padding:12px 16px;
          background:#fef2f2;border-radius:8px;word-break:break-all; }}
  code {{ background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:13px; }}
  a {{ color:#2563eb; }}
</style></head>
<body><div class="box">
  <h2>⚠️ 源文件不存在</h2>
  <p>当前项目 <b>{config.project}</b> 配置的源文件未找到，请确认文件是否被移动或删除：</p>
  <div class="err">📄 {missing_path}</div>
  <p>解决方法：</p>
  <ol>
    <li>把简历源文件放回上述路径；或</li>
    <li>在「📄 源文件」卡片中重新选择 / 填写正确的源文件路径并保存；或</li>
    <li>回到 <a href="/">首页</a> 重新创建项目。</li>
  </ol>
  <p style="color:#64748b;font-size:13px;">恢复后刷新本页即可正常使用同步与翻译功能。</p>
</div></body></html>"""


def _render_dashboard(
    project: str,
    info: dict,
    outputs: list[dict],
    history: list[dict],
    source_mtime: float,
    templates: Optional[list[dict]] = None,
    formats_data: Optional[list[dict]] = None,
    source_missing: bool = False,
    source_path: str = "",
    output_root: str = "",
    project_history: Optional[list[dict]] = None,
) -> str:
    info = info or {}
    src_path = info.get("source", "") or source_path
    # Subtitle should be the human-readable source file, not the internal
    # project key (e.g. "resume") which is meaningless to the user.
    src_name = Path(src_path).name if src_path else (project or "未命名项目")
    LANG_LABELS = {"zh": "中文", "en": "English"}

    # Detected source language
    src_lang = info.get("source_lang", "")
    src_lang_label = LANG_LABELS.get(src_lang, src_lang or "未知")

    # Section badges
    sec_tags = ""
    for s in info.get("sections", []):
        sec_tags += (
            f"<span style=\"display:inline-block;background:#f0f0f0;"
            f"padding:2px 10px;border-radius:4px;margin:2px;\">"
            f"{s['title']} <strong>{s['items']}</strong></span>"
        )

    # Template card
    tpl_rows = ""
    html_outputs = [o for o in outputs if o["format"] == "html"]
    if templates and html_outputs:
        for oe in html_outputs:
            label = f"HTML / {oe['lang']}"
            current = oe["style"] or "default"
            opts = "".join(
                f"<option value=\"{t['name']}\" {'selected' if t['name'] == current else ''}>{t['label']}</option>"
                for t in templates
            )
            tpl_rows += f"""<tr>
  <td style="font-size:13px;font-weight:500;">{label}</td>
  <td><div style="display:flex;gap:6px;align-items:center;">
    <select id="style_{oe['format']}_{oe['lang']}"
            onchange="updateStyle('{oe['format']}','{oe['lang']}',this.value)"
            style="flex:1;padding:5px 8px;border:1px solid #ddd;border-radius:4px;font-size:13px;background:#fff;">
      {opts}
    </select>
    <span id="res_style_{oe['format']}_{oe['lang']}" style="font-size:12px;color:#22c55e;min-width:40px;"></span>
  </div></td>
</tr>"""
    if not tpl_rows:
        tpl_rows = "<tr><td colspan='2' style='color:#999;font-size:13px;padding:8px;'>HTML 输出可使用模板风格</td></tr>"

    # Output card — format checkboxes
    fmt_checkboxes = ""
    if formats_data:
        for fd in formats_data:
            fmt_checkboxes += (
                f"<label style=\"display:inline-flex;align-items:center;gap:4px;"
                f"padding:6px 14px;border:1px solid #ddd;border-radius:6px;"
                f"cursor:pointer;font-size:13px;user-select:none;background:#fff;\">"
                f"<input type=\"checkbox\" {'checked' if fd['active'] else ''} "
                f"onchange=\"toggleFormat('{fd['format']}',this.checked)\" "
                f"style=\"width:16px;height:16px;cursor:pointer;\">"
                f"{fd['format'].upper()}</label>"
            )

    # Output table
    fmt_rows = ""
    if formats_data:
        for fd in formats_data:
            fmt_label = fd['format'].upper()
            # Determine rowspan for this format
            langs = fd["languages"]
            rowspan = len(langs)
            for i, le in enumerate(langs):
                ll = LANG_LABELS.get(le["lang"], le["lang"])
                c = le["color"]
                if le.get("unconfigured"):
                    c = "#999"
                    st = "未配置"
                else:
                    st = {"red": "文件不存在", "#f59e0b": "待同步", "#22c55e": "已同步"}.get(c, "未知")

                fmt_cell = f"<td style=\"font-weight:600;font-size:13px;vertical-align:middle;\">{fmt_label}</td>" if i == 0 else ""
                if i == 0 and rowspan > 1:
                    fmt_cell = f"<td style=\"font-weight:600;font-size:13px;vertical-align:middle;\" rowspan=\"{rowspan}\">{fmt_label}</td>"

                fmt_rows += (
                    f"<tr>"
                    f"{fmt_cell}"
                    f"<td style=\"font-size:13px;\">{ll}</td>"
                    f"<td style=\"font-size:12px;color:#555;word-break:break-all;max-width:350px;\">"
                f"<code style=\"font-size:12px;color:#475569;\" title=\"{le['path'] or ''}\">{le['filename'] or '未配置'}</code></td>"
                    f"<td style=\"font-size:12px;color:#999;white-space:nowrap;\">{le['size']}</td>"
                    f"<td style=\"font-size:12px;white-space:nowrap;\">"
                    f"<span style=\"display:inline-block;width:8px;height:8px;border-radius:50%;background:{c};margin-right:4px;vertical-align:middle;\"></span>"
                    f"<span style=\"color:{c};vertical-align:middle;\">{st}</span>"
                    f"</td>"
                    f"<td style=\"font-size:12px;text-align:center;color:#999;\">{'是' if le.get('is_source') else '否'}</td>"
                    f"<td style=\"font-size:12px;text-align:center;\">"
                    f"{'<a href=\"/api/file?path=' + le['path'] + '\" target=\"_blank\" style=\"color:#2563eb;text-decoration:none;font-size:12px;\">打开</a>' if le['exists'] else '<span style=\"color:#ccc;\">-</span>'}"
                    f"</td></tr>"
                )
    if not fmt_rows:
        fmt_rows = "<tr><td colspan='7' style='color:#999;font-size:13px;padding:12px;text-align:center;'>未选择输出格式</td></tr>"

    # Legend
    legend = (
        "<div style=\"margin-top:8px;font-size:12px;color:#666;\">"
        "<span style=\"display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:4px;\"></span> 已同步"
        " &nbsp; "
        "<span style=\"display:inline-block;width:8px;height:8px;border-radius:50%;background:#f59e0b;margin-right:4px;\"></span> 待同步"
        " &nbsp; "
        "<span style=\"display:inline-block;width:8px;height:8px;border-radius:50%;background:red;margin-right:4px;\"></span> 文件不存在"
        "</div>"
    )

    # History
    hist_lines = ""
    for h in reversed(history[-20:]):
        cls = "#22c55e" if h.get("errors", 0) == 0 else "#ef4444"
        fi = " | ".join(h.get("files", [])) if h.get("files") else ""
        hist_lines += (
            f"<div style=\"color:{cls};font-size:13px;padding:3px 0;border-bottom:1px solid #f0f0f0;\">"
            f"<span style=\"color:#999;\">{h['time']}</span> &middot; {fi} &middot; {h.get('elapsed','0s')}</div>"
        )
    if not hist_lines:
        hist_lines = "<p style='color:#999;font-size:13px;'>暂无同步记录</p>"

    # Project history (historical projects, NOT the live sync log above)
    proj_hist_lines = ""
    for hp in (project_history or [])[:20]:
        name = hp.get("name", "未命名")
        src = hp.get("source", "")
        cfg_path = (hp.get("config_path") or "").replace("'", "\\'")
        fmts = hp.get("formats_summary", "")
        last = hp.get("last_opened", "")
        proj_hist_lines += (
            f"<div style=\"display:flex;align-items:center;gap:10px;padding:8px 0;"
            f"border-bottom:1px solid #f0f0f0;\">"
            f"<div style=\"flex:1;min-width:0;\">"
            f"<div style=\"font-size:13px;font-weight:500;color:#222;word-break:break-all;\">{name}</div>"
            f"<div style=\"font-size:12px;color:#999;word-break:break-all;\">{src}</div>"
            f"<div style=\"font-size:12px;color:#1a56db;margin-top:2px;\">{fmts} "
            f"<span style=\"color:#bbb;\">· 打开于 {last}</span></div>"
            f"</div>"
            f"<button class=\"btn btn-sm\" style=\"background:#1a56db;white-space:nowrap;\" "
            f"onclick=\"loadHistory('{cfg_path}', this)\">打开</button>"
            f"</div>"
        )
    if not proj_hist_lines:
        proj_hist_lines = "<p style='color:#999;font-size:13px;'>暂无历史项目</p>"

    # ── Assemble ─────────────────────────────────────────────────────
    banner = ""
    if source_missing:
        banner = (
            "<div style=\"margin:14px 0;padding:14px 18px;border-radius:8px;"
            "background:#fffbeb;border:1px solid #fde68a;color:#92400e;font-size:14px;\">"
            "⚠️ <b>未找到源文件</b>，翻译与同步功能暂不可用。"
            "请在下方「📄 源文件」卡片中指定简历文件路径后刷新页面。"
            f"<br><span style=\"font-size:12px;color:#b45309;\">配置路径：{src_path or '（未设置）'}</span>"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>md-sync · {src_name}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#f5f5f5;color:#222;font-size:14px;padding:24px;}}
  .container{{max-width:900px;margin:0 auto}}
  h1{{font-size:22px;margin-bottom:2px}}
  .sub{{color:#666;margin-bottom:20px}}
  .card{{background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .card h2{{font-size:15px;margin-bottom:12px;color:#333;display:flex;align-items:center;gap:8px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eee;font-size:13px}}
  th{{font-weight:600;color:#555;background:#fafafa}}
  select{{padding:5px 8px;border:1px solid #ddd;border-radius:4px;font-size:13px;background:#fff;cursor:pointer}}
  select:hover{{border-color:#999}}
  input[type=text]{{padding:6px 10px;border:1px solid #ddd;border-radius:4px;font-size:13px;width:100%}}
  .btn{{padding:8px 20px;background:#222;color:#fff;border:2px solid transparent;border-radius:6px;font-size:13px;cursor:pointer}}
  .btn:hover{{background:#444}}
  .btn:disabled{{opacity:.5;cursor:not-allowed}}
  .btn-danger{{background:#c0392b}}
  .btn-danger:hover{{background:#a93226}}
  .btn.syncing{{background:#555;border:2px solid #22c55e;opacity:1;}}
  .btn.syncing:hover{{background:#666}}
  .btn-sm{{padding:4px 12px;font-size:12px;margin-left:6px}}
  #result{{margin-left:12px;font-size:13px;color:#666}}
</style>
</head>
<body>
<div class="container">

<h1>md-sync</h1>
<p class="sub">{src_name}</p>

{banner}

<div class="card">
  <h2>📄 源文件</h2>
  <div id="sourceInputArea" style="display:none;">
    <div style="display:flex;gap:8px;align-items:center;">
      <input type="text" id="sourcePath" value=\"{src_path}\" style="flex:1;">
      <button class="btn btn-sm" onclick="browseSource()" style="background:#1a56db;">打开</button>
      <button class="btn btn-sm" onclick="cancelChange()" style="background:#999;">取消</button>
    </div>
  </div>
  <div id="sourceDisplayArea">
    <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:#f8f8f8;border-radius:6px;">
      <span style="font-size:13px;color:#555;min-width:90px;">当前源文档：</span>
      <span id="sourceDisplayPath" style="font-size:13px;font-weight:500;color:#222;word-break:break-all;flex:1;">{src_path}</span>
      <button class="btn btn-sm" onclick="showSourceInput()" style="background:#666;">更换</button>
    </div>
  </div>
  <p style="font-size:13px;color:#666;margin-top:8px;">
    <span style="display:inline-block;background:#e8f0fe;color:#1a56db;padding:2px 10px;border-radius:4px;margin-right:8px;">语言：{src_lang_label}</span>
    章节：{sec_tags}
  </p>
  <div style="margin-top:10px;display:flex;align-items:center;gap:8px;">
    <button class="btn" id="detectBtn" onclick="detectLang()">检测语言</button>
    <button class="btn" id="translateBtn" onclick="doTranslate()" style="background:#1a56db;">翻译</button>
    <span id="translateInfo" style="font-size:13px;color:#666;"></span>
  </div>
  <div id="translatePaths" style="display:none;margin-top:8px;padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;color:#475569;line-height:1.7;word-break:break-all;"></div>
</div>

<div class="card">
  <h2>🎨 转换模板</h2>
  <p style="font-size:13px;color:#999;margin-bottom:10px;">选择 HTML 输出的渲染风格</p>
  <table><tr><th style="width:130px;">输出</th><th>模板风格</th></tr>{tpl_rows}</table>
</div>

<div class="card">
  <h2>📦 输出文件</h2>
  <div style="margin-bottom:14px;">
    <div style="font-size:13px;color:#666;margin-bottom:8px;">目标格式（多选）：</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">{fmt_checkboxes}</div>
  </div>
  <div style="margin:10px 0 14px;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;">
    <div style="font-size:13px;color:#334155;margin-bottom:6px;">输出根目录（只需填写<b>一个</b>目录，工具会在其下自动建立 <code>pdf/</code>、<code>html/</code>、<code>md/</code> 子目录，并按语言命名文件）：</div>
    <div style="display:flex;gap:8px;align-items:center;">
      <input type="text" id="outputRoot" value="{output_root}" placeholder="例如 /home/你/Obsidian/简历" style="flex:1;padding:7px 10px;border:1px solid #cbd5e1;border-radius:4px;font-size:13px;" onkeydown="if(event.key==='Enter'){{saveOutputRoot();}}">
      <button class="btn" onclick="browseOutputRoot()">设置输出目录</button>
    </div>
  </div>
  <div style="margin-top:8px;">
    <table>
      <tr><th style="width:60px;">格式</th><th style="width:70px;">语言</th><th>文件名</th><th style="width:60px;">大小</th><th style="width:130px;">状态</th><th style="width:70px;">是否源文件</th><th style="width:50px;">操作</th></tr>
      {fmt_rows}
    </table>
  </div>
  {legend}
  <div style="margin-top:12px;display:flex;align-items:center;gap:8px;">
    <button class="btn" id="syncBtn" onclick="toggleSync()">同步</button>
    <span id="result"></span>
    <button class="btn btn-danger" style="margin-left:auto;" onclick="deleteAll()">全部删除</button>
  </div>
</div>

<div class="card">
  <h2>🔔 同步事件</h2>
  {hist_lines}
</div>

<div class="card">
  <h2>⏱ 同步历史</h2>
  {proj_hist_lines}
</div>

</div>

<script>
var _lastRunId = null;
var _detected = null;  // {{source_lang: 'zh', target_lang: 'en', missing: N}}

async function detectLang() {{
  var info = document.getElementById('translateInfo');
  info.style.color = '#666';
  info.textContent = '检测中…';
  try {{
    var resp = await fetch('/api/detect');
    var d = await resp.json();
    if (d.status !== 'ok') {{
      info.style.color = '#ef4444';
      info.textContent = '检测失败：' + (d.error || '');
      return;
    }}
    _detected = d;
    info.style.color = '#1a56db';
    info.textContent = `源语言：${{d.source_lang_label}} → 目标：${{d.target_lang_label}}，待翻译 ${{d.missing}} 条（provider: ${{d.provider}}）`;
  }} catch (e) {{
    info.style.color = '#ef4444';
    info.textContent = '检测出错：' + e;
  }}
}}

function refreshStatus() {{
  // dashboard 为全量重渲染，直接刷新页面以更新各输出状态/待译数
  setTimeout(function() {{ location.reload(); }}, 600);
}}

function showTranslatePaths(paths) {{
  var box = document.getElementById('translatePaths');
  if (!box) return;
  if (!paths || !paths.length) {{ box.style.display = 'none'; return; }}
  box.innerHTML = paths.join('<br>');
  box.style.display = 'block';
}}

async function doTranslate() {{
  var btn = document.getElementById('translateBtn');
  var info = document.getElementById('translateInfo');
  btn.disabled = true;
  info.style.color = '#666';
  info.textContent = '翻译中…';
  try {{
    var resp = await fetch('/api/translate', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{}})
    }});
    var d = await resp.json();
    btn.disabled = false;
    if (d.status !== 'ok') {{
      info.style.color = '#ef4444';
      info.textContent = '翻译失败：' + (d.error || '');
      return;
    }}
    var s = d.summary;
    info.style.color = '#22c55e';
    info.textContent = `翻译完成：${{s.translated}} 条新译 / ${{s.cached}} 条已缓存 / ${{s.failed}} 条失败（${{s.source_lang}}→${{s.target_lang}}）`;
    if (s.failed > 0) {{
      info.textContent += ' — 可能离线或被限流，稍后重试';
    }}
    // show where outputs WILL be written after a sync (translation itself
    // only fills the cache; it does NOT generate these files yet)
    var paths = (d.output_paths || []).map(function(p){{ return '🎯 同步后生成：' + p; }});
    paths.unshift('译文缓存：' + (d.mapping_path || '未知') + '（翻译仅更新缓存，未生成文件）');
    showTranslatePaths(paths);
  }} catch (e) {{
    btn.disabled = false;
    info.style.color = '#ef4444';
    info.textContent = '翻译出错：' + e;
  }}
}}

async function toggleSync() {{
  var btn = document.getElementById('syncBtn');
  var r = document.getElementById('result');
  var watching = btn.classList.contains('syncing');
  btn.disabled = true;
  try {{
    var resp = await fetch('/api/watch', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{action: watching ? 'stop' : 'start'}})
    }});
    var data = await resp.json();
    if (data.status === 'ok') {{
      if (data.watching) {{
        btn.innerHTML = '停止同步';
        btn.classList.add('syncing');
        r.textContent = '监听中，源文件改动将自动转换…';
        r.style.color = '#22c55e';
      }} else {{
        btn.innerHTML = '同步';
        btn.classList.remove('syncing');
        r.textContent = '✗ 已停止监听';
        r.style.color = '#f59e0b';
      }}
    }} else {{
      r.textContent = '✗ ' + (data.error || '失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 请求失败'; r.style.color = '#ef4444';
  }}
  btn.disabled = false;
  refreshState();
}}

async function refreshState() {{
  try {{
    var resp = await fetch('/api/status');
    var data = await resp.json();
    var btn = document.getElementById('syncBtn');
    var r = document.getElementById('result');
    if (data.watching) {{
      btn.innerHTML = '停止同步';
      btn.classList.add('syncing');
      if (_lastRunId !== null && data.run_id !== _lastRunId) {{
        // a new sync ran → reload to refresh outputs & history
        location.reload();
        return;
      }}
      if (r.textContent.indexOf('监听中') === 0 || r.textContent === '') {{
        r.textContent = '监听中，源文件改动将自动转换…';
        r.style.color = '#22c55e';
      }}
    }} else {{
      btn.innerHTML = '同步';
      btn.classList.remove('syncing');
    }}
    _lastRunId = data.run_id;
  }} catch(e) {{}}
}}

// poll every 2s to keep button state and refresh after a new sync
setInterval(refreshState, 2000);
refreshState();

async function deleteAll() {{
  if (!confirm('确定删除全部输出文件？此操作不可撤销。')) return;
  var r = document.getElementById('result');
  r.textContent = '删除中…'; r.style.color = '#666';
  try {{
    var resp = await fetch('/api/files/delete_all', {{method:'POST'}});
    var data = await resp.json();
    if (data.status === 'ok') {{
      r.textContent = '✓ 已删除 ' + data.deleted + ' 个文件' + (data.errors.length ? '（' + data.errors.length + ' 个失败）' : '');
      r.style.color = '#22c55e';
      setTimeout(function(){{ location.reload(); }}, 800);
    }} else {{
      r.textContent = '✗ ' + (data.error || '失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 请求失败'; r.style.color = '#ef4444';
  }}
}}

async function loadHistory(configPath, btn) {{
  if (btn) {{ btn.disabled = true; btn.textContent = '加载中…'; }}
  try {{
    var resp = await fetch('/api/history/load', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{config_path: configPath}})
    }});
    var data = await resp.json();
    if (data.status === 'ok') {{
      location.reload();
    }} else {{
      alert('加载失败: ' + (data.error || '未知错误'));
      if (btn) {{ btn.disabled = false; btn.textContent = '打开'; }}
    }}
  }} catch (e) {{
    alert('加载失败: ' + e);
    if (btn) {{ btn.disabled = false; btn.textContent = '打开'; }}
  }}
}}

function showSourceInput() {{
  document.getElementById('sourceDisplayArea').style.display = 'none';
  document.getElementById('sourceInputArea').style.display = 'block';
  document.getElementById('sourcePath').focus();
}}

function cancelChange() {{
  document.getElementById('sourceInputArea').style.display = 'none';
  document.getElementById('sourceDisplayArea').style.display = 'block';
  document.getElementById('sourcePath').value = document.getElementById('sourceDisplayPath').textContent;
}}

async function confirmSource() {{
  var input = document.getElementById('sourcePath');
  var path = input.value.trim();
  if (!path) return;
  var r = document.getElementById('result');
  r.textContent = '保存中…';
  r.style.color = '#666';
  try {{
    var resp = await fetch('/api/config', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{source: path}})}}
    );
    var data = await resp.json();
    if (data.status === 'ok') {{
      document.getElementById('sourceDisplayPath').textContent = data.source;
      document.getElementById('sourceInputArea').style.display = 'none';
      document.getElementById('sourceDisplayArea').style.display = 'block';
      input.value = '';
      r.textContent = '✓ 已更新';
      r.style.color = '#22c55e';
      setTimeout(function(){{ location.reload(); }}, 800);
    }} else {{
      r.textContent = '✗ ' + (data.error || '失败');
      r.style.color = '#ef4444';
    }}
  }} catch(e) {{
    r.textContent = '✗ 保存失败';
    r.style.color = '#ef4444';
  }}
}}

async function browseSource() {{
  var input = document.getElementById('sourcePath');
  try {{
    if (window.mdSync && window.mdSync.openFile) {{
      var p = await window.mdSync.openFile();
      if (p) {{ input.value = p; confirmSource(); return; }}
    }}
  }} catch (e) {{}}
  input.focus();
}}

async function updateStyle(format, lang, style) {{
  var res = document.getElementById('res_style_' + format + '_' + lang);
  res.textContent = '…'; res.style.color = '#999';
  try {{
    var resp = await fetch('/api/config/style', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{format, lang, style}})}}
    );
    var data = await resp.json();
    res.textContent = data.status === 'ok' ? '✓' : '✗';
    res.style.color = data.status === 'ok' ? '#22c55e' : '#ef4444';
  }} catch(e) {{ res.textContent = '✗'; res.style.color = '#ef4444'; }}
  setTimeout(function(){{ res.textContent = ''; }}, 3000);
}}

async function toggleFormat(format, checked) {{
  var r = document.getElementById('result');
  r.textContent = '保存中…';
  try {{
    var resp = await fetch('/api/config/toggle_format', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{format, active: checked}})}}
    );
    var data = await resp.json();
    r.textContent = data.status === 'ok' ? '✓ 已更新' : '✗ ' + (data.error || '');
    r.style.color = data.status === 'ok' ? '#22c55e' : '#ef4444';
    if (data.status === 'ok') setTimeout(function(){{ location.reload(); }}, 500);
  }} catch(e) {{ r.textContent = '✗ 保存失败'; r.style.color = '#ef4444'; }}
}}

async function saveOutputPath(el) {{
  var r = document.getElementById('result');
  r.textContent = '保存路径中…';
  try {{
    var resp = await fetch('/api/config/output_path', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{format: el.dataset.format, lang: el.dataset.lang, path: el.value}})}}
    );
    var data = await resp.json();
    r.textContent = data.status === 'ok' ? '✓ 路径已更新（下次同步生效）' : '✗ ' + (data.error || '');
    r.style.color = data.status === 'ok' ? '#22c55e' : '#ef4444';
    if (data.status === 'ok') el.value = data.path;
  }} catch(e) {{ r.textContent = '✗ 保存失败'; r.style.color = '#ef4444'; }}
}}

async function saveOutputRoot() {{
  var el = document.getElementById('outputRoot');
  var r = document.getElementById('result');
  r.textContent = '保存中…';
  try {{
    var resp = await fetch('/api/config/output_root', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{root: el.value}})}}
    );
    var data = await resp.json();
    r.textContent = data.status === 'ok' ? '✓ 输出根目录已保存（自动派生各格式路径）' : '✗ ' + (data.error || '');
    r.style.color = data.status === 'ok' ? '#22c55e' : '#ef4444';
    if (data.status === 'ok') setTimeout(function(){{ location.reload(); }}, 400);
  }} catch(e) {{ r.textContent = '✗ 保存失败'; r.style.color = '#ef4444'; }}
}}

// Open a native directory picker (Electron) and save the chosen output root.
// Falls back to focusing the text field for manual entry when running in a
// plain browser without the mdSync bridge.
async function browseOutputRoot() {{
  var el = document.getElementById('outputRoot');
  try {{
    if (window.mdSync && window.mdSync.openDirectory) {{
      var p = await window.mdSync.openDirectory();
      if (p) {{ el.value = p; await saveOutputRoot(); return; }}
    }}
  }} catch (e) {{}}
  el.focus();
}}
</script>

</body>
</html>"""


# ── FastAPI app ────────────────────────────────────────────────────────────


def create_app(
    config: Optional[ProjectConfig] = None,
    pipeline: Optional[SyncPipeline] = None,
) -> Optional[object]:
    if FastAPI is None:
        return None

    app = FastAPI(title="md-sync")
    sync_history: list[dict] = []
    _tpl_manager = TemplateManager()
    _plugin_registry = PluginRegistry()

    # ── Continuous watch state ────────────────────────────────────────────
    _watch = {"active": False}
    _watch_stop = {"event": None}      # threading.Event when watching
    _run_id = {"n": 0}                 # bumps on every sync (manual or watch)

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    def _do_sync(watch: bool = False) -> dict:
        """Run the pipeline once and record it in the history."""
        start = time.time()
        src = config.source_path
        src_mtime = _safe_mtime(src)
        print(f"[sync] {'watch' if watch else 'manual'} 触发同步 | 源: {src}"
              + (f" (修改于 {time.strftime('%H:%M:%S', time.localtime(src_mtime))})"
                 if src_mtime else " (不存在)"))
        try:
            stats = pipeline.run()
        except Exception as e:  # keep the watcher alive on transient errors
            import traceback as _tb
            _tb.print_exc()
            print(f"[sync] ✗ 同步失败: {e}")
            stats = {"outputs": [], "errors": [str(e)]}
        elapsed = time.time() - start
        now = time.strftime("%H:%M:%S")
        files = []
        for o in stats.get("outputs", []):
            if o.get("ok"):
                p = Path(o["path"])
                files.append(p.name)
                print(f"[sync]   → 已写入 {o.get('format','?')}/{o.get('lang','?')}: "
                      f"{p} (完成于 {now})")
            else:
                print(f"[sync]   ✗ 输出失败 {o.get('format','?')}/{o.get('lang','?')}: "
                      f"{o.get('error','')}")
        if stats.get("errors"):
            print(f"[sync] ✗ 同步完成但有错误，耗时 {elapsed:.1f}s: {stats['errors']}")
        else:
            print(f"[sync] ✓ 同步完成，耗时 {elapsed:.1f}s，生成 {len(files)} 个文件")
        sync_history.append({
            "time": now,
            "elapsed": f"{elapsed:.1f}s",
            "ok": len(files),
            "errors": len(stats.get("errors", [])),
            "files": files,
            "watch": watch,
            "src": str(src),
            "src_mtime": src_mtime,
        })
        _run_id["n"] += 1
        return stats

    def _watch_loop(stop_event: threading.Event) -> None:
        """Background loop: re-sync whenever the source file changes."""
        debounce = config.watch.debounce if (config and config.watch) else 1.5
        try:
            last_mtime = config.source_path.stat().st_mtime
        except OSError:
            last_mtime = 0.0
        while not stop_event.is_set():
            time.sleep(0.5)
            if stop_event.is_set():
                break
            try:
                mtime = config.source_path.stat().st_mtime
            except OSError:
                continue
            if mtime == last_mtime:
                continue
            # file changed → wait for further edits to settle (debounce)
            stop_event.wait(debounce)
            if stop_event.is_set():
                break
            try:
                mtime2 = config.source_path.stat().st_mtime
            except OSError:
                continue
            if mtime2 == last_mtime:
                continue
            changed = time.strftime("%H:%M:%S", time.localtime(mtime2))
            print(f"[watch] 检测到源文件修改: {config.source_path} (修改时间 {changed})")
            _do_sync(watch=True)
            last_mtime = mtime2

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        # No config → show setup page with history
        if config is None or pipeline is None:
            tpl_list = [{"name": t.name, "label": t.label} for t in _tpl_manager.list_templates()]
            history = _load_history()[:10]  # show last 10
            # Gather plugin and schema info
            plugin_list = []
            for p in _plugin_registry.plugins.values():
                m = p.manifest
                plugin_list.append({
                    "name": m.name, "version": m.version,
                    "description": m.description, "author": m.author,
                    "plugin_type": m.plugin_type,
                    "parser_schema": m.parser_schema or "",
                    "has_template": bool(m.template),
                    "templates": m.templates,
                })
            plugin_schemas = {s for s, _ in _plugin_registry.list_parsers()}
            schema_list = []
            if "resume" not in plugin_schemas:
                schema_list.append({"name": "resume", "label": "简历 (resume) — 内置", "default": True})
            for schema_name, parser in _plugin_registry.list_parsers():
                schema_list.append({
                    "name": schema_name,
                    "label": f"{parser.manifest.name} ({schema_name})",
                    "default": False,
                })
            html = _render_setup_page(
                "新项目", tpl_list, history_projects=history,
                plugins=plugin_list, schemas=schema_list,
            )
            return HTMLResponse(html)

        # Have config → update history and show dashboard
        _save_to_history(_build_history_entry(config))
        outputs_data = []
        for out in config.outputs:
            outputs_data.append({
                "format": out.format, "lang": out.lang, "path": out.path,
                "pdf": out.pdf, "pdf_path": out.pdf_path or "",
                "style": out.style or out.theme or "default",
            })
        src = config.source_path
        source_missing = not src.exists()

        # Only parse the source file when the user has actually provided one.
        # We never auto-load/parse a file on page load just to render the UI.
        info = {}
        src_mtime = 0
        if not source_missing:
            try:
                info = pipeline.run_dry()
                s = Path(info.get("source", ""))
                if s.exists():
                    src_mtime = s.stat().st_mtime
            except Exception:
                # Source present but unparseable — still render the UI.
                info = {}

        fmt_data = _build_formats_data(outputs_data, src_mtime, str(src))
        tpl_list = [{"name": t.name, "label": t.label} for t in _tpl_manager.list_templates()]

        html = _render_dashboard(config.project, info, outputs_data, sync_history,
                                  src_mtime, templates=tpl_list,
                                  formats_data=fmt_data, source_missing=source_missing,
                                  source_path=str(src), output_root=str(config.output_root),
                                  project_history=_load_history())
        return HTMLResponse(html)

    @app.post("/api/setup")
    async def api_setup(request: Request):
        """Create project from user-provided settings."""
        nonlocal config, pipeline

        body = await request.json()
        source = body.get("source", "").strip()
        formats = body.get("formats", [])  # [{format: "html", langs: ["zh","en"]}, ...]
        schema = body.get("schema", "")
        if not schema:
            # Plug and Play: auto-detect schema from source content
            try:
                src_path_check = Path(source).expanduser().resolve()
                if src_path_check.exists():
                    text = src_path_check.read_text(encoding="utf-8")
                    detected = _plugin_registry.detect_schema(text)
                    if detected:
                        schema = detected["schema"]
                    else:
                        schema = "resume"
                else:
                    schema = "resume"
            except Exception:
                schema = "resume"
        style_zh = body.get("style_zh", "bwx")
        style_en = body.get("style_en", "modern")
        output_root = (body.get("output_root") or "").strip()

        if not source:
            return {"status": "error", "error": "源文件路径不能为空"}

        src_path = Path(source).expanduser().resolve()
        if not src_path.exists():
            return {"status": "error", "error": f"文件不存在: {src_path}"}

        try:
            # Build outputs. The output root is the user-configured output
            # directory if already set; otherwise fall back to the source's
            # parent directory so paths are never placed inside the source file
            # itself. After the user sets the output directory, api_config_output_root
            # re-derives every path against that root.
            if output_root:
                root = Path(output_root).expanduser().resolve()
            else:
                root = config.output_root_path if (config and getattr(config, "output_root", None)) else src_path.parent
            outputs = []
            name_map = body.get("name_map") or {}
            for fmt_entry in formats:
                fmt = fmt_entry.get("format")
                langs = fmt_entry.get("langs", [])
                for lang in langs:
                    if fmt == "html":
                        style = style_zh if lang == "zh" else style_en
                        out = OutputConfig(
                            format="html", lang=lang,
                            path=derive_output_path(root, "html", lang, name_map, src_path.stem),
                            style=style,
                        )
                        outputs.append(out)
                    elif fmt == "md":
                        outputs.append(OutputConfig(
                            format="md", lang=lang,
                            path=derive_output_path(root, "md", lang, name_map, src_path.stem),
                        ))
                    elif fmt == "pdf":
                        # PDF is a flag on the HTML output of the same lang
                        html_out = next((o for o in outputs if o.format == "html" and o.lang == lang), None)
                        if html_out:
                            html_out.pdf = True
                            html_out.pdf_path = derive_output_path(root, "html", lang, name_map, src_path.stem, pdf=True)
                        else:
                            outputs.append(OutputConfig(
                                format="html", lang=lang,
                                path=derive_output_path(root, "html", lang, name_map, src_path.stem),
                                style=style_zh if lang == "zh" else style_en,
                                pdf=True,
                                pdf_path=derive_output_path(root, "html", lang, name_map, src_path.stem, pdf=True),
                            ))

            # Create config
            cfg = ProjectConfig(
                project=src_path.stem,
                source=str(src_path),
                schema=schema,
                outputs=outputs,
            )
            cfg.source_path = src_path.resolve()
            cfg.output_root = str(root)
            cfg.config_path = Path.cwd() / "md-sync.yaml"

            # Save to YAML
            raw = {
                "project": cfg.project,
                "source": str(src_path),
                "schema": schema,
                "output_root": str(root),
                "outputs": [],
                "watch": {"enabled": True, "debounce": 1.5},
                "web_ui": {"enabled": True, "host": "127.0.0.1", "port": 8580},
            }
            if name_map:
                raw["name_map"] = name_map
            for o in outputs:
                entry = {"format": o.format, "lang": o.lang, "path": o.path}
                if o.style:
                    entry["style"] = o.style
                if o.pdf:
                    entry["pdf"] = True
                    if o.pdf_path:
                        entry["pdf_path"] = o.pdf_path
                raw["outputs"].append(entry)

            with open(cfg.config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)

            # Initialize pipeline
            config = cfg
            pipeline = SyncPipeline(cfg)

            # Save to history
            _save_to_history(_build_history_entry(cfg))

            return {"status": "ok", "project": cfg.project}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.get("/api/status")
    async def api_status():
        if config is None:
            return {
                "project": "", "source": "", "sections": [],
                "pending_translations": [], "watching": False, "run_id": 0,
                "source_lang": "", "source_missing": True,
            }
        if not config.source_path.exists():
            return {
                "project": config.project, "source": str(config.source_path),
                "sections": [], "pending_translations": [],
                "source_lang": "", "source_missing": True,
                "watching": _watch["active"], "run_id": _run_id["n"],
            }
        info = pipeline.run_dry()
        return {
            "project": config.project,
            "source": info["source"],
            "sections": info["sections"],
            "pending_translations": info.get("pending_translations", []),
            "source_lang": info.get("source_lang", ""),
            "watching": _watch["active"],
            "run_id": _run_id["n"],
        }

    @app.get("/api/history")
    async def api_history():
        """Return project history list."""
        return {"projects": _load_history()}

    @app.post("/api/history/load")
    async def api_history_load(request: Request):
        """Load a project from its saved configuration file."""
        nonlocal config, pipeline

        body = await request.json()
        cfg_path = body.get("config_path", "")
        if not cfg_path:
            return {"status": "error", "error": "config_path required"}

        cfg_file = Path(cfg_path).resolve()
        if not cfg_file.exists():
            return {"status": "error", "error": f"配置文件不存在: {cfg_path}"}

        try:
            cfg = ProjectConfig.load(cfg_file)
            config = cfg
            pipeline = SyncPipeline(cfg)
            _save_to_history(_build_history_entry(cfg))
            return {"status": "ok", "project": cfg.project}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.get("/api/file")
    async def api_file(path: str):
        """Serve an output file for browser viewing."""
        full = Path(path).resolve()
        if not full.exists():
            return HTMLResponse("文件不存在", status_code=404)
        ext = full.suffix.lower()
        content_type = "text/html; charset=utf-8" if ext == ".html" else \
                       "application/pdf" if ext == ".pdf" else \
                       "text/plain; charset=utf-8"
        return HTMLResponse(full.read_bytes(), status_code=200, media_type=content_type)

    @app.get("/api/templates")
    async def api_templates():
        tpl_list = [{"name": t.name, "label": t.label, "schema": t.schema} for t in _tpl_manager.list_templates()]
        return {"templates": tpl_list}

    # ── Plugin API ─────────────────────────────────────────────────────

    @app.get("/api/plugins")
    async def api_plugins():
        """List all installed plugin packs and parsers with full info."""
        plugins = []
        for p in _plugin_registry.plugins.values():
            m = p.manifest
            plugins.append({
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "author": m.author,
                "plugin_type": m.plugin_type,
                "parser_schema": m.parser_schema or "",
                "templates": m.templates,
                "has_template": bool(m.template),
                "template": m.template or "",
            })
        return {"plugins": plugins}

    @app.post("/api/plugins/template")
    async def api_plugins_template(request: Request):
        """Generate a template.md file from a plugin pack."""
        body = await request.json()
        name = body.get("name", "")
        force = body.get("force", False)
        if not name:
            return {"status": "error", "error": "plugin name required"}

        content = _plugin_registry.get_template_source(name)
        if not content:
            return {"status": "error", "error": f"Plugin '{name}' has no template.md"}

        out_path = Path.cwd() / "template.md"
        if out_path.exists() and not force:
            return {"status": "confirm", "error": f"template.md 已存在，确定覆盖？", "path": str(out_path)}

        try:
            out_path.write_text(content, encoding="utf-8")
            return {"status": "ok", "path": str(out_path), "plugin": name}
        except OSError as e:
            return {"status": "error", "error": str(e)}

    @app.get("/api/schemas")
    async def api_schemas():
        """List all available schemas (built-in + plugin parsers)."""
        plugin_schemas = {s for s, _ in _plugin_registry.list_parsers()}
        schemas = []
        # Only add built-in resume if no plugin overrides it
        if "resume" not in plugin_schemas:
            schemas.append({"name": "resume", "label": "简历 (resume) — 内置", "default": True})
        for schema_name, parser in _plugin_registry.list_parsers():
            m = parser.manifest
            schemas.append({
                "name": schema_name,
                "label": f"{m.name} ({schema_name})",
                "default": False,
            })
        return {"schemas": schemas}

    # ── Plug and Play: auto-detect schema from source content ─────────

    @app.post("/api/detect-schema")
    async def api_detect_schema(request: Request):
        """Auto-detect the best schema for a given source file.

        Reads the file, runs ``detect()`` on all registered plugin parsers,
        and returns the best match (or None if no parser matches).
        """
        body = await request.json()
        path_str = body.get("path", "")
        if not path_str:
            return {"status": "error", "error": "path required"}

        src = Path(path_str)
        if not src.exists():
            return {"status": "error", "error": f"file not found: {src}"}

        try:
            text = src.read_text(encoding="utf-8")
            result = _plugin_registry.detect_schema(text)
            if result:
                return {"status": "ok", "detected": result}
            return {"status": "ok", "detected": None, "hint": "未检测到匹配的插件 schema，将使用通用 Markdown 解析"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/sync")
    async def api_sync():
        if config is None or pipeline is None:
            return {"status": "error", "error": "项目尚未配置，请先完成设置"}
        if not config.source_path.exists():
            return {"status": "error", "error": f"源文件不存在：{config.source_path}，请先在「📄 源文件」卡片指定"}
        _do_sync(watch=False)
        return {"status": "ok"}

    @app.get("/api/detect")
    async def api_detect():
        """Detect the source file's language (independent of sync)."""
        if config is None or pipeline is None:
            return {"status": "error", "error": "项目尚未配置，请先完成设置"}
        if not config.source_path.exists():
            return {"status": "error", "error": f"源文件不存在：{config.source_path}，请先在「📄 源文件」卡片指定"}
        try:
            src = pipeline._config.source_path
            text = Path(src).read_text(encoding="utf-8")
            lang = detect_lang(text)
            # Determine the translation target (the other supported language).
            target = "en" if lang == "zh" else "zh"
            # Count missing translations for that target.
            doc = pipeline._parser.parse_file(src, schema=pipeline._config.schema)
            info = pipeline.run_dry()
            missing = info.get("missing_translations", {}).get(target, 0)
            return {
                "status": "ok",
                "source_lang": lang,
                "source_lang_label": lang_label(lang),
                "target_lang": target,
                "target_lang_label": lang_label(target),
                "missing": missing,
                "provider": _detect_provider(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/translate")
    async def api_translate(request: Request = None):
        """Run ONLY the translation step (fill the cache), not full sync."""
        if config is None or pipeline is None:
            return {"status": "error", "error": "项目尚未配置，请先完成设置"}
        if not config.source_path.exists():
            return {"status": "error", "error": f"源文件不存在：{config.source_path}，请先在「📄 源文件」卡片指定"}
        target = None
        if request is not None:
            try:
                body = await request.json()
                target = body.get("target_lang")
            except Exception:
                target = None
        try:
            summary = pipeline.translate_only(target_lang=target)
            target_lang = summary.get("target_lang")
            out_paths = [o.path for o in config.outputs if o.lang == target_lang and o.path]
            map_path = str(pipeline._translator._path)
            return {
                "status": "ok",
                "summary": summary,
                "target_lang": target_lang,
                "output_paths": out_paths,
                "mapping_path": map_path,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/files/delete_all")
    async def api_delete_all():
        if config is None:
            return {"status": "error", "error": "项目尚未配置，请先完成设置"}
        deleted, errors = [], []
        for o in config.outputs:
            for p in (o.path, o.pdf_path):
                if not p:
                    continue
                fp = Path(p)
                if fp.exists():
                    try:
                        fp.unlink()
                        deleted.append(str(fp))
                    except OSError as e:
                        errors.append(f"{fp}: {e}")
        return {"status": "ok", "deleted": len(deleted), "errors": errors}

    @app.post("/api/watch")
    async def api_watch(request: Request):
        if config is None or pipeline is None:
            return {"status": "error", "error": "项目尚未配置，请先完成设置"}
        body = await request.json()
        action = body.get("action", "start")
        if action == "stop":
            if _watch_stop["event"] is not None:
                _watch_stop["event"].set()
                _watch_stop["event"] = None
            _watch["active"] = False
            return {"status": "ok", "watching": False}
        # start
        if _watch["active"]:
            return {"status": "ok", "watching": True}
        stop_event = threading.Event()
        _watch_stop["event"] = stop_event
        _watch["active"] = True
        # immediate first sync so outputs are fresh, then monitor for changes
        threading.Thread(target=_do_sync, kwargs={"watch": True}, daemon=True).start()
        threading.Thread(target=_watch_loop, args=(stop_event,), daemon=True).start()
        return {"status": "ok", "watching": True}

    @app.post("/api/config")
    async def api_config(request: Request):
        if config is None:
            return {"status": "error", "error": "项目尚未配置"}
        body = await request.json()
        source = body.get("source", "")
        if not source:
            return {"status": "error", "error": "source path required"}

        new_path = Path(source).resolve()
        if not new_path.exists():
            return {"status": "error", "error": f"file not found: {new_path}"}

        try:
            config.source_path = new_path
            config.source = str(new_path.relative_to(Path.cwd())) if new_path.is_relative_to(Path.cwd()) else str(new_path)

            cfg_file = config.config_path
            if cfg_file.exists():
                raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                raw["source"] = config.source
                cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")

            return {"status": "ok", "source": str(config.source_path)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/config/style")
    async def api_config_style(request: Request):
        if config is None:
            return {"status": "error", "error": "项目尚未配置"}
        body = await request.json()
        fmt, lang, style = body.get("format"), body.get("lang"), body.get("style")
        if not all([fmt, lang, style]):
            return {"status": "error", "error": "format, lang, style required"}

        try:
            for out in config.outputs:
                if out.format == fmt and out.lang == lang:
                    out.style = style
                    break
            else:
                return {"status": "error", "error": f"output not found: {fmt}/{lang}"}

            cfg_file = config.config_path
            if cfg_file.exists():
                raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                for o in raw.get("outputs", []):
                    if o.get("format") == fmt and o.get("lang") == lang:
                        o["style"] = style
                        o.pop("theme", None)
                        break
                cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            return {"status": "ok", "format": fmt, "lang": lang, "style": style}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/config/output_path")
    async def api_config_output_path(request: Request):
        if config is None:
            return {"status": "error", "error": "项目尚未配置"}
        body = await request.json()
        fmt, lang, new_path = body.get("format"), body.get("lang"), body.get("path")
        if not all([fmt, lang, new_path is not None]):
            return {"status": "error", "error": "format, lang, path required"}

        try:
            # Normalize: relative to project root / output root
            p = Path(new_path)
            if not p.is_absolute():
                p = (config.source_path.parent.parent if config.source_path.parent.name.lower() == "md"
                     else config.source_path.parent) / p
            resolved = str(p.resolve())

            # Update in-memory config
            for out in config.outputs:
                if out.format == fmt and out.lang == lang:
                    out.path = resolved
                    if out.format == "html" and out.pdf:
                        out.pdf_path = config.output_path("html", lang, pdf=True)
                    break
            else:
                return {"status": "error", "error": f"output not found: {fmt}/{lang}"}

            # Persist to YAML
            cfg_file = config.config_path
            if cfg_file.exists():
                raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                for o in raw.get("outputs", []):
                    if o.get("format") == fmt and o.get("lang") == lang:
                        o["path"] = resolved
                        if o.get("format") == "html" and o.get("pdf"):
                            o["pdf_path"] = config.output_path("html", o.get("lang"), pdf=True)
                        break
                cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")

            return {"status": "ok", "format": fmt, "lang": lang, "path": resolved}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/config/output_root")
    async def api_config_output_root(request: Request):
        if config is None:
            return {"status": "error", "error": "项目尚未配置"}
        body = await request.json()
        root = (body.get("root") or "").strip()
        try:
            cfg_file = config.config_path
            raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
            if root:
                raw["output_root"] = root
            else:
                raw.pop("output_root", None)
            cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            # Reload so every output derives from the new root
            new_cfg = ProjectConfig.load(cfg_file)
            config.outputs = new_cfg.outputs
            config.output_root = new_cfg.output_root
            return {"status": "ok", "output_root": config.output_root}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.post("/api/config/toggle_format")
    async def api_config_toggle_format(request: Request):
        if config is None:
            return {"status": "error", "error": "项目尚未配置"}
        body = await request.json()
        fmt = body.get("format")
        active = body.get("active", True)
        if not fmt:
            return {"status": "error", "error": "format required"}

        try:
            existing = set(o.format for o in config.outputs)

            if fmt == "pdf":
                # PDF is not a standalone format; it is a flag on HTML outputs
                for o in config.outputs:
                    if o.format == "html":
                        o.pdf = active
                        if active and not o.pdf_path:
                            o.pdf_path = config.output_path("html", o.lang, pdf=True)
                cfg_file = config.config_path
                if cfg_file.exists():
                    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                    for o in raw.get("outputs", []):
                        if o.get("format") == "html":
                            o["pdf"] = active
                            if active and not o.get("pdf_path"):
                                o["pdf_path"] = derive_output_path(
                                    config.output_root_path, "html", o["lang"], config.name_map, config.source_path.stem, pdf=True
                                )
                    cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
                return {"status": "ok", "format": fmt, "active": active}

            if active and fmt not in existing:
                for lang in ["zh", "en"]:
                    if fmt == "html":
                        config.outputs.append(OutputConfig(
                            format=fmt, lang=lang,
                            path=derive_output_path(config.output_root_path, "html", lang, config.name_map, config.source_path.stem),
                            style="bwx" if lang == "zh" else "modern",
                            pdf=True,
                            pdf_path=derive_output_path(config.output_root_path, "html", lang, config.name_map, config.source_path.stem, pdf=True),
                        ))
                    elif fmt == "md":
                        config.outputs.append(OutputConfig(
                            format=fmt, lang=lang,
                            path=derive_output_path(config.output_root_path, "md", lang, config.name_map, config.source_path.stem),
                        ))
            elif not active and fmt in existing:
                config.outputs = [o for o in config.outputs if o.format != fmt]

            cfg_file = config.config_path
            if cfg_file.exists():
                raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                if active and fmt not in existing:
                    raw.setdefault("outputs", [])
                    for lang in ["zh", "en"]:
                        if fmt == "html":
                            raw["outputs"].append({
                                "format": "html", "lang": lang,
                                "path": derive_output_path(config.output_root_path, "html", lang, config.name_map, config.source_path.stem),
                                "style": "bwx" if lang == "zh" else "modern",
                                "pdf": True,
                                "pdf_path": derive_output_path(config.output_root_path, "html", lang, config.name_map, config.source_path.stem, pdf=True),
                            })
                        elif fmt == "md":
                            raw["outputs"].append({
                                "format": "md", "lang": lang,
                                "path": derive_output_path(config.output_root_path, "md", lang, config.name_map, config.source_path.stem),
                            })
                else:
                    raw["outputs"] = [o for o in raw.get("outputs", []) if o.get("format") != fmt]
                cfg_file.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")

            return {"status": "ok", "format": fmt, "active": active}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    return app


def run_web_ui(config: Optional[object] = None, pipeline: Optional[object] = None) -> None:
    if FastAPI is None:
        print("[web] Install fastapi + uvicorn for Web UI")
        return

    host = "127.0.0.1"
    port = 8580
    if config and hasattr(config, "web_ui"):
        host = config.web_ui.host
        port = config.web_ui.port

    uvicorn.run(
        create_app(config, pipeline),
        host=host,
        port=port,
        log_level="warning",
    )
