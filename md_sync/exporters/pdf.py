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
    margin: str = "5mm,8mm",
    extra_args: Optional[list[str]] = None,
) -> bool:
    """Convert an HTML file to PDF using headless Chromium.

    Args:
        html_path: Path to source HTML file.
        pdf_path: Destination PDF path.
        chromium_path: Chromium binary path (auto-detect if None).
        margin: @page margin values (CSS format).
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

    cmd = [
        chromium,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
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
