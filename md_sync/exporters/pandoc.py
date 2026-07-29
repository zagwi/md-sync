"""Pandoc export — convert HTML/Markdown to .docx, .epub and other formats.

Uses the ``pandoc`` CLI (must be installed separately — ``apt install pandoc``
or ``brew install pandoc``). The pure-Python ``pypandoc`` wrapper is NOT used;
we call pandoc directly via subprocess, matching the pattern of
:mod:`md_sync.exporters.pdf`.

For Typora-themed documents, the recommended flow is:

    Markdown → (render HTML with Typora CSS) → pandoc → .docx / .epub

This preserves the Typora theme styling as much as possible in the target
format (heading hierarchy, basic formatting, inline CSS for epub).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _find_pandoc() -> str | None:
    """Return the pandoc binary path, or None if not found."""
    import shutil
    return shutil.which("pandoc")


def export_via_pandoc(
    input_path: Path | str,
    output_path: Path | str,
    to_format: str = "docx",
    from_format: str = "html",
    metadata: dict[str, str] | None = None,
    reference_doc: Path | str | None = None,
    page_size: str = "",
    margin: str = "",
    extra_args: list[str] | None = None,
) -> bool:
    """Convert a file to another format using pandoc CLI.

    Args:
        input_path: Source file path (e.g. rendered HTML or raw Markdown).
        output_path: Destination path (e.g. ``out.docx`` or ``out.epub``).
        to_format: Target format for ``-t`` (e.g. ``docx``, ``epub``, ``latex``).
        from_format: Source format for ``-f`` (default ``html``).
        metadata: Key-value metadata pairs passed as ``-M key=val``.
        reference_doc: Path to a reference ``.docx`` file for styling
                       (only meaningful for ``to_format=docx``).
        extra_args: Additional pandoc CLI arguments.

    Returns:
        True on success, False on failure.
    """
    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()

    if not input_path.exists():
        print(f"[pandoc] ERROR: Input not found: {input_path}")
        return False

    pandoc = _find_pandoc()
    if not pandoc:
        print("[pandoc] ERROR: pandoc not found. Install it via 'apt install pandoc' or 'brew install pandoc'.")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [pandoc, str(input_path), "-f", from_format, "-t", to_format, "-o", str(output_path)]

    # Metadata
    if metadata:
        for key, val in metadata.items():
            cmd.extend(["-M", f"{key}={val}"])

    temp_refs: list[Path] = []
    # Reference docx for styling
    if reference_doc:
        ref = Path(reference_doc)
        if ref.exists():
            cmd.extend(["--reference-doc", str(ref.resolve())])
        else:
            print(f"[pandoc] WARNING: reference-doc not found: {ref}")
    elif to_format == "docx" and page_size and (
        page_size != "A4" or (margin or "").strip()
    ):
        # No user reference doc but a non-default page size or an explicit margin
        # is requested: build a minimal reference docx carrying the page setup so
        # pandoc adopts it. The default A4 / auto-margin case is left untouched so
        # pandoc's own default reference styling is preserved.
        from .page import build_reference_docx
        ref = build_reference_docx(page_size, margin or "")
        temp_refs.append(ref)
        cmd.extend(["--reference-doc", str(ref.resolve())])

    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"[pandoc] ERROR: {result.stderr.strip()}")
            return False
        if output_path.exists() and output_path.stat().st_size > 0:
            size_kb = output_path.stat().st_size // 1024
            print(f"[pandoc] ✓ {output_path.name} ({size_kb} KB)")
            return True
        print(f"[pandoc] WARNING: output may be empty ({output_path})")
        return False
    except FileNotFoundError:
        print(f"[pandoc] ERROR: pandoc binary not found: {pandoc}")
        return False
    except subprocess.TimeoutExpired:
        print("[pandoc] ERROR: pandoc timed out after 60s")
        return False
    finally:
        for t in temp_refs:
            try:
                t.unlink(missing_ok=True)
            except OSError:
                pass
