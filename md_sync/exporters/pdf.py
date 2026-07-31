"""PDF export via Chromium headless.

Two backends live here:

* :func:`export_pdf` — the classic CLI path (``--print-to-pdf``). Fast and
  dependency-light, but Chromium's CLI cannot inject a custom footer, so there
  are no page numbers.
* :func:`export_pdf_cdp` — a DevTools-protocol path (driven over ``websockets``,
  which ``uvicorn[standard]`` already pulls in) that renders the page in a
  real headless browser and calls ``Page.printToPDF`` with an arbitrary
  header/footer template. This is what plugins use when they need special PDF
  behaviour — e.g. the gongwen plugin's GB/T 9704-2012 page numbers
  (``— 1 —``). Plugins *override* the built-in behaviour, never the reverse.

Plugins register a :class:`~md_sync.plugin.interface.PdfExporter`; the pipeline
prefers it over :func:`export_pdf` automatically.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

# Path to Chromium — auto-detect common locations
_CHROMIUM_CANDIDATES = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",
]


def _find_chromium() -> str | None:
    """Return the first available Chromium/Chrome binary."""
    for candidate in _CHROMIUM_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def export_pdf(
    html_path: Path | str,
    pdf_path: Path | str,
    chromium_path: str | None = None,
    page_margin: str = "15mm",
    page_size: str = "A4",
    extra_args: list[str] | None = None,
    style_name: str = "",
) -> bool:
    """Convert an HTML file to PDF using headless Chromium.

    Strips Chromium's default header/footer (URL, page number) and
    applies a configurable @page margin for proper print layout.

    Args:
        html_path: Path to source HTML file.
        pdf_path: Destination PDF path.
        chromium_path: Chromium binary path (auto-detect if None).
        page_margin: CSS @page margin value, e.g. "15mm", "20mm", "25mm".
        extra_args: Additional arguments to chromium.

    Returns:
        True on success, False on failure.
    """
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()

    from .page import normalize_page_size
    page_size = normalize_page_size(page_size)

    if not html_path.exists():
        print(f"[pdf] ERROR: HTML not found: {html_path}")
        return False

    chromium = chromium_path or _find_chromium()
    if not chromium:
        print("[pdf] ERROR: Chromium not found. Install chromium or set chromium_path.")
        return False

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Inject print CSS:
    #  - @page { margin: 0 } removes the default white page-edge margin band
    #    that Chromium prints regardless of background colors, so the theme
    #    background fills the whole page (no white border).
    #  - The original page_margin is converted to padding on the content
    #    container (#write for typora, .body for resume templates). Because a
    #    plain padding only applies at the element's first/last edge (so inner
    #    pages lose top/bottom margin), ``box-decoration-break: clone`` makes
    #    every page fragment repeat the padding — giving a consistent margin on
    #    all four sides of every page, including the bottom.
    #  - print-color-adjust: exact forces Chromium to KEEP background colors
    #    when printing (otherwise html/body/container backgrounds are dropped).
    #  - Header/footer (date, URL, page number) are suppressed via the CLI
    #    flags --no-pdf-header-footer / --no-print-header-footer (spellings
    #    differ across Chromium builds; both are passed harmlessly).
    html_text = html_path.read_text(encoding="utf-8")
    _margin_pad = (
        "-webkit-box-sizing: border-box; box-sizing: border-box; "
        "-webkit-box-decoration-break: clone; box-decoration-break: clone;"
    )
    pdf_css = (
        f"\n@page {{ size: {page_size}; margin: 0; }}\n"
        "@media print {\n"
        f"  #write, .body {{ padding: {page_margin}; {_margin_pad} }}\n"
        # Code blocks: themes set `overflow: auto`, which renders an ugly
        # horizontal scrollbar in print (looks like a raw HTML dump). In print
        # we kill the scroll container and wrap long lines (including unbreakable
        # tokens like long URLs) instead, so the block paginates cleanly with no
        # scrollbar. Applies to every template/theme uniformly.
        "  #write pre, .body pre,"
        "  #write pre code, .body pre code {\n"
        "    overflow: visible !important;\n"
        "    max-height: none !important;\n"
        "    white-space: pre-wrap !important;\n"
        "    word-break: break-word !important;\n"
        "    overflow-wrap: anywhere !important;\n"
        "  }\n"
        "}\n"
        "html, body, #write, .body, #write *, .body * {\n"
        "  -webkit-print-color-adjust: exact !important;\n"
        "  print-color-adjust: exact !important;\n"
        "}\n"
    )
    if "</style>" in html_text:
        html_text = html_text.replace("</style>", pdf_css + "</style>")
    elif "<head>" in html_text:
        html_text = html_text.replace("<head>", "<head><style>" + pdf_css + "</style>")

    # Render from a temp copy (same directory so relative URLs in the HTML
    # still resolve) — the source HTML artifact is left untouched.
    fd, tmp_html = tempfile.mkstemp(
        prefix=".md-sync-pdf-", suffix=".html", dir=str(html_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html_text)
    tmp_path = Path(tmp_html)

    cmd = [
        chromium,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-print-preview",
        # Suppress the default header/footer (date, URL, page number).
        # Two flag spellings are tried because support differs across
        # Chromium builds / distros:
        #  - --no-pdf-header-footer   : works on Arch Chromium 150 (and newer).
        #  - --no-print-header-footer : older builds / other distros.
        # Passing both is harmless — an unsupported flag is simply ignored.
        "--no-pdf-header-footer",
        "--no-print-header-footer",
        f"--print-to-pdf={pdf_path}",
        f"file://{tmp_path}",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[pdf] ERROR: {result.stderr.strip()}")
            return False
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            suffix = f" [{style_name}]" if style_name else ""
            print(f"[pdf] ✓ {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB){suffix}")
            return True
        print(f"[pdf] WARNING: output may be empty ({pdf_path})")
        return False
    except FileNotFoundError:
        print(f"[pdf] ERROR: Chromium binary not found: {chromium}")
        return False
    except subprocess.TimeoutExpired:
        print("[pdf] ERROR: Chromium timed out after 30s")
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


# ── CDP backend (custom header/footer, e.g. GB/T 9704-2012 页码) ─────────────


_DEVTOOLS_RE = re.compile(r"DevTools listening on ws://127\.0\.0\.1:(\d+)/")
_PAGE_WS_TIMEOUT = 30


def _parse_four_margins_mm(margin: str) -> tuple[float, float, float, float]:
    """Parse a CSS margin string into ``(top, right, bottom, left)`` mm.

    Accepts ``"37mm 26mm 35mm 28mm"`` (4 values), ``"15mm 20mm"``
    (vertical horizontal) or a single uniform value. Falls back to a
    conservative 20mm on garbage.
    """
    from .page import _to_mm

    parts = (margin or "").split()
    if len(parts) >= 4:
        return (_to_mm(parts[0]), _to_mm(parts[1]), _to_mm(parts[2]), _to_mm(parts[3]))
    if len(parts) >= 2:
        v, h = _to_mm(parts[0]), _to_mm(parts[1])
        return (v, h, v, h)
    if len(parts) == 1:
        v = _to_mm(parts[0])
        return (v, v, v, v)
    return (20.0, 20.0, 20.0, 20.0)


async def _read_devtools_port(proc: subprocess.Popen, timeout: int) -> int | None:
    """Read Chromium's stderr until it prints the DevTools ws port."""
    deadline = time.monotonic() + timeout
    line = ""
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.to_thread(proc.stderr.readline)
        except Exception:
            return None
        if not chunk:
            await asyncio.sleep(0.05)
            continue
        m = _DEVTOOLS_RE.search(chunk)
        if m:
            return int(m.group(1))
        line = chunk.strip()
    print(f"[pdf] CDP ERROR: DevTools port not found (last stderr: {line})")
    return None


async def _page_target_ws(port: int, timeout: int) -> str | None:
    """Find a page target's websocket URL via the /json/list endpoint."""
    import httpx

    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"http://127.0.0.1:{port}/json/list", timeout=1)
                if r.status_code == 200:
                    for t in r.json():
                        if t.get("type") != "page":
                            continue
                        url = t.get("url", "")
                        if url.startswith("about:blank") or url.startswith("file://"):
                            return t["webSocketDebuggerUrl"]
                    # Headless may report only extension pages first; keep polling.
            except Exception:
                pass
            await asyncio.sleep(0.2)
    print(f"[pdf] CDP ERROR: no page target on port {port}")
    return None


async def _cdp_print(
    html_path: Path,
    pdf_path: Path,
    chromium: str,
    page_margin: str,
    page_size: str,
    footer_template: str,
    header_template: str,
    timeout: int,
) -> bool:
    """Run the actual CDP session (must be called inside a running loop)."""
    import websockets

    from .page import PAGE_SIZES_MM, normalize_page_size

    page_size = normalize_page_size(page_size)

    # Chromium 150 的行为（实测）：
    #  - ``displayHeaderFooter: true`` 会完全忽略 printToPDF 的 margin 参数，
    #    也忽略 CSS ``@page`` 边距 → 内容铺满整页（边距丢失）。
    #  - 关闭页脚后 margin 参数才生效，但此时 CSS ``@page`` 规则（尤其带
    #    ``!important``）会反过来覆盖 margin 参数。
    # 因此这里关闭页脚，并把 CSS ``@page`` 边距显式声明为与 margin 参数一致
    # 的值（单一事实来源 = page_margin）。需要自定义页脚的调用方（如 gongwen
    # 页码）应事后用 PDF 库叠加，而不是依赖 displayHeaderFooter。
    # Render from a temp copy (same directory so relative URLs resolve) — the
    # source HTML artifact is left untouched.
    html = html_path.read_text(encoding="utf-8")
    _page_rule = f"\n@page {{ size: {page_size}; margin: {page_margin}; }}\n"
    if "</style>" in html:
        html = html.replace("</style>", _page_rule + "</style>")
    elif "<head>" in html:
        html = html.replace("<head>", "<head><style>" + _page_rule + "</style>")
    else:
        html = "<style>" + _page_rule + "</style>" + html
    fd, tmp_html = tempfile.mkstemp(
        prefix=".md-sync-cdp-", suffix=".html", dir=str(html_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    tmp_path = Path(tmp_html)

    user_data_dir = tempfile.mkdtemp(prefix="md-sync-cdp-")
    proc = subprocess.Popen(
        [
            chromium,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--remote-allow-origins=*",
            "--remote-debugging-port=0",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = await _read_devtools_port(proc, timeout)
        if not port:
            return False
        page_ws = await _page_target_ws(port, timeout)
        if not page_ws:
            return False

        async with websockets.connect(page_ws, max_size=2**28, open_timeout=timeout) as ws:
            seq = 0

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal seq
                seq += 1
                req = {"id": seq, "method": method, "params": params or {}}
                await ws.send(json.dumps(req))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    msg = json.loads(raw)
                    if msg.get("id") == seq:
                        if msg.get("error"):
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result") or {}

            await call("Page.enable")
            await call("Page.navigate", {"url": f"file://{tmp_path}"})
            # Wait for the document to load.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    ready = await call("Runtime.evaluate", {
                        "expression": "document.readyState", "returnByValue": True,
                    })
                    if ready.get("result", {}).get("value") == "complete":
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.2)

            mm = lambda v: v / 25.4
            w_mm, h_mm = PAGE_SIZES_MM[page_size]
            t, r, b, l = _parse_four_margins_mm(page_margin)
            result = await call("Page.printToPDF", {
                "landscape": False,
                "displayHeaderFooter": bool(header_template or footer_template),
                "printBackground": True,
                "paperWidth": round(mm(w_mm), 4),
                "paperHeight": round(mm(h_mm), 4),
                "marginTop": round(mm(t), 4),
                "marginRight": round(mm(r), 4),
                "marginBottom": round(mm(b), 4),
                "marginLeft": round(mm(l), 4),
                "headerTemplate": header_template or "",
                "footerTemplate": footer_template or "",
                "preferCSSPageSize": False,
            })
            data = result.get("data")
            if not data:
                print(f"[pdf] CDP ERROR: printToPDF returned no data ({result})")
                return False
            pdf_path.write_bytes(base64.b64decode(data))
            return True
    except Exception as e:
        print(f"[pdf] CDP ERROR: {e}")
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        import shutil

        shutil.rmtree(user_data_dir, ignore_errors=True)
        tmp_path.unlink(missing_ok=True)


def export_pdf_cdp(
    html_path: Path | str,
    pdf_path: Path | str,
    chromium_path: str | None = None,
    page_margin: str = "15mm",
    page_size: str = "A4",
    footer_template: str = "",
    header_template: str = "",
    extra_args: list[str] | None = None,
    style_name: str = "",
    timeout: int = 30,
) -> bool:
    """Export a PDF via the DevTools protocol so a custom footer can be injected.

    Unlike :func:`export_pdf`, this backend drives a real headless browser over
    ``websockets`` and calls ``Page.printToPDF`` with ``displayHeaderFooter``,
    letting callers provide an arbitrary ``footer_template`` (e.g. gongwen's
    GB/T 9704-2012 page number ``— 1 —``).

    The page margins are enforced by the printToPDF margin params, with a
    matching CSS ``@page`` rule injected so both agree (single source of truth
    = ``page_margin``). ``displayHeaderFooter`` is only enabled when a
    template is actually provided — Chromium 150 ignores all margins when it
    is enabled without one.

    Returns True on success, False on any failure.
    """
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()

    if not html_path.exists():
        print(f"[pdf] ERROR: HTML not found: {html_path}")
        return False

    chromium = chromium_path or _find_chromium()
    if not chromium:
        print("[pdf] ERROR: Chromium not found. Install chromium or set chromium_path.")
        return False

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ok = asyncio.run(
            _cdp_print(
                html_path,
                pdf_path,
                chromium,
                page_margin,
                page_size,
                footer_template,
                header_template,
                timeout,
            )
        )
    except Exception as e:
        print(f"[pdf] ERROR (CDP): {e}")
        return False
    if ok and pdf_path.exists() and pdf_path.stat().st_size > 1000:
        suffix = f" [{style_name}]" if style_name else ""
        print(f"[pdf] ✓ {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB){suffix}")
        return True
    print(f"[pdf] WARNING: output may be empty ({pdf_path})")
    return False
