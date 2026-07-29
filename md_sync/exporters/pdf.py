"""PDF export via Chromium headless."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


# Path to Chromium — auto-detect common locations
_CHROMIUM_CANDIDATES = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",
]


def _find_chromium() -> Optional[str]:
    """Return the first available Chromium/Chrome binary."""
    for candidate in _CHROMIUM_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def export_pdf(
    html_path: Path | str,
    pdf_path: Path | str,
    chromium_path: Optional[str] = None,
    page_margin: str = "15mm",
    extra_args: Optional[list[str]] = None,
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
    #  - The original page_margin is converted to #write padding. Because a
    #    plain padding only applies at the element's first/last edge (so inner
    #    pages lose top/bottom margin), box-decoration-break: clone makes every
    #    page fragment repeat the padding — giving a consistent margin on all
    #    four sides of every page, including the bottom.
    #  - print-color-adjust: exact forces Chromium to KEEP background colors
    #    when printing (otherwise html/body/#write backgrounds are dropped).
    #  - Header/footer (date, URL, page number) are suppressed via the CLI
    #    flags --no-pdf-header-footer / --no-print-header-footer (spellings
    #    differ across Chromium builds; both are passed harmlessly).
    html_text = html_path.read_text(encoding="utf-8")
    pdf_css = (
        "\n@page { margin: 0; }\n"
        "@media print {\n"
        f"  #write {{ padding: {page_margin}; "
        "-webkit-box-sizing: border-box; box-sizing: border-box; "
        "-webkit-box-decoration-break: clone; box-decoration-break: clone; }}\n"
        # Code blocks: themes set `overflow: auto`, which renders an ugly
        # horizontal scrollbar in print (looks like a raw HTML dump). In print
        # we kill the scroll container and wrap long lines (including unbreakable
        # tokens like long URLs) instead, so the block paginates cleanly with no
        # scrollbar. Applies to every template/theme uniformly.
        "  #write pre, #write pre code {\n"
        "    overflow: visible !important;\n"
        "    max-height: none !important;\n"
        "    white-space: pre-wrap !important;\n"
        "    word-break: break-word !important;\n"
        "    overflow-wrap: anywhere !important;\n"
        "  }\n"
        "}\n"
        "html, body, #write, #write * {\n"
        "  -webkit-print-color-adjust: exact !important;\n"
        "  print-color-adjust: exact !important;\n"
        "}\n"
    )
    if "</style>" in html_text:
        html_text = html_text.replace("</style>", pdf_css + "</style>")
    elif "<head>" in html_text:
        html_text = html_text.replace("<head>", "<head><style>" + pdf_css + "</style>")
    html_path.write_text(html_text, encoding="utf-8")

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
        f"file://{html_path}",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[pdf] ERROR: {result.stderr.strip()}")
            return False
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            print(f"[pdf] ✓ {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")
            return True
        print(f"[pdf] WARNING: output may be empty ({pdf_path})")
        return False
    except FileNotFoundError:
        print(f"[pdf] ERROR: Chromium binary not found: {chromium}")
        return False
    except subprocess.TimeoutExpired:
        print("[pdf] ERROR: Chromium timed out after 30s")
        return False
