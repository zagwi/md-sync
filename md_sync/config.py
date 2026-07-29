"""Project configuration management.

Reads ``md-sync.yaml`` from the project directory.
"""
from __future__ import annotations

import time as _time_module
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml


def _timestamp() -> str:
    """Return a compact millisecond timestamp string, e.g. ``20260728_143052_205``.

    Millisecond precision (3 digits) is sufficient to distinguish
    output files created in a single sync batch.
    """
    t = _time_module.time()
    ms = int((t - int(t)) * 1000)
    return (
        datetime.fromtimestamp(t).strftime("%Y%m%d_%H%M%S")
        + f"_{ms:03d}"
    )


def derive_output_path(
    output_root: Path,
    format: str,
    lang: str,
    name_map: dict | None = None,
    source_stem: str = "",
    *,
    pdf: bool = False,
    naming: str = "timestamp",
) -> str:
    """Build an output file path under the user-specified *output root*.

    ``naming`` controls how filename collisions are handled:
      - ``"timestamp"`` (default): append a millisecond timestamp so every
        derived name is unique, e.g. ``README-zh-20260728_143052_123.html``.
      - ``"overwrite"``: use a clean name with no timestamp, so repeated syncs
        overwrite the same file, e.g. ``README-zh.html``.
    """
    base = (name_map or {}).get(lang) or source_stem
    # Guarantee a non-empty filename: fall back to the language code so we
    # never produce an extension-only path like "pdf/.pdf".
    if not base:
        base = lang or format
    # Append millisecond timestamp to avoid filename collisions, unless the
    # user chose to overwrite (clean name, no suffix).
    if naming != "overwrite":
        ts = _timestamp()
        base = f"{base}-{ts}"
    ext_map = {
        "html": "html",
        "md": "md",
        "docx": "docx",
        "epub": "epub",
        "pdf": "pdf",
    }
    ext = ext_map.get(format, format)
    if pdf or format == "pdf":
        return str(output_root / "pdf" / f"{base}.pdf")
    return str(output_root / format / f"{base}.{ext}")


# ── Config data classes ─────────────────────────────────────────────────────


@dataclass
class OutputConfig:
    """A single output target."""
    format: str              # "md" | "html" | "docx" | "epub"
    lang: str                # "zh" | "en" | …
    path: str = ""           # output file path (derived from source name if empty)
    theme: str | None = None   # legacy theme name (backward compat)
    style: str | None = None   # template style name (e.g. "bwx", "modern")
    pdf: bool = False
    pdf_path: str | None = None
    page_size: str = "A4"      # PDF/DOCX page size, e.g. "A4", "Letter", "A5"
    page_margin: str = ""      # override margin; empty => standard margin for page_size


@dataclass
class WatchConfig:
    enabled: bool = True
    debounce: float = 1.5


@dataclass
class AiTranslationConfig:
    provider: str = "auto"
    model: str | None = None


@dataclass
class TranslationConfig:
    strategy: str = "mapping"
    mapping_file: str = ".translations.json"
    ai: AiTranslationConfig = field(default_factory=AiTranslationConfig)


@dataclass
class WebUIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8580


@dataclass
class ProjectConfig:
    """Top-level project configuration."""
    project: str
    source: str                     # path to source MD file
    schema: str = "resume"          # parsing schema
    outputs: list[OutputConfig] = field(default_factory=list)
    watch: WatchConfig = field(default_factory=WatchConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    web_ui: WebUIConfig = field(default_factory=WebUIConfig)
    # lang code -> output base filename (no extension).
    # zh defaults to the source stem; en needs a "simple translation"
    # (manual mapping) since content/name translation is not automated yet.
    name_map: dict = field(default_factory=dict)
    # Single user-specified output directory. All outputs derive from it
    # (pdf/ html/ md/ sub-directories + language-based file names). Empty means
    # "unconfigured" — the user must set it before outputs can be generated.
    output_root: str = ""
    # How to handle output filename collisions: "timestamp" (default) appends a
    # millisecond timestamp; "overwrite" uses a clean name (no suffix).
    output_naming: str = "timestamp"
    # Language of the source document. The Markdown output for THIS language IS
    # the source file itself (not a generated copy), so it must point back at
    # the source path and be flagged as the source in the UI.
    source_lang: str = "zh"

    # Derived (set after load)
    config_path: Path = field(default_factory=Path)   # original YAML file path
    project_dir: Path = field(default_factory=Path)
    source_path: Path = field(default_factory=Path)

    @classmethod
    def load(cls, path: Path | str) -> ProjectConfig:
        """Load and validate a project YAML file."""
        path = Path(path).resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))

        cfg = cls(
            project=raw["project"],
            source=raw["source"],
            schema=raw.get("schema", "resume"),
            outputs=[OutputConfig(**o) for o in raw.get("outputs", [])],
            watch=WatchConfig(**(raw.get("watch", {}))),
            translation=cls._parse_translation(raw.get("translation", {})),
            web_ui=WebUIConfig(**(raw.get("web_ui", {}))),
            name_map=raw.get("name_map", {}),
            output_root=raw.get("output_root", ""),
            output_naming=raw.get("output_naming", "timestamp"),
            source_lang=raw.get("source_lang", "zh"),
            config_path=path,
        )
        # Use CWD as the base for relative paths (user runs from project root)
        cwd = Path.cwd().resolve()
        cfg.source_path = (cwd / cfg.source).resolve()

        # Output file names are derived from the source name, not hardcoded:
        #   zh  → <source_stem>.<ext>   (just a suffix swap)
        #   en  → <name_map["en"]>.<ext> (simple translation of the file name)
        # Directories follow the format (html/ pdf/ md/), siblings of the
        # source's own md/ directory.
        for out in cfg.outputs:
            # The source-language Markdown output IS the source file itself.
            # Bind it to the source path (not a copy under output_root) so the
            # UI recognises it as "the source file" instead of a missing output.
            if out.format == "md" and out.lang == cfg.source_lang:
                out.path = str(cfg.source_path)
                out.pdf = False
                out.pdf_path = None
                continue

            # Normally the path is derived from the single user-configured
            # output root (pdf/ html/ md/ sub-dirs + language-based name). A
            # per-output `path` is only kept when the user explicitly overrides.
            if out.path:
                out.path = str((cwd / out.path).resolve())
            else:
                derived = cfg.output_path(out.format, out.lang)
                if derived:
                    out.path = derived
                # else: stays empty -> "unconfigured" in the UI

            # PDF path: when not set explicitly, derive it under the dedicated
            # pdf/ sub-directory (NOT as a sibling of the html file), so the
            # html/ folder stays html-only.
            if out.pdf and out.format == "html":
                if out.pdf_path:
                    out.pdf_path = str((cwd / out.pdf_path).resolve())
                else:
                    pdf_derived = cfg.output_path(out.format, out.lang, pdf=True)
                    if pdf_derived:
                        out.pdf_path = pdf_derived

        return cfg

    @staticmethod
    def _parse_translation(raw: dict) -> TranslationConfig:
        ai_raw = raw.get("ai", {})
        return TranslationConfig(
            strategy=raw.get("strategy", "mapping"),
            mapping_file=str(raw.get("mapping_file", ".translations.json")),
            ai=AiTranslationConfig(**ai_raw) if ai_raw else AiTranslationConfig(),
        )

    def translation_path(self) -> Path:
        """Resolved path to the translation mapping file."""
        p = Path(self.translation.mapping_file)
        if not p.is_absolute():
            p = self.project_dir / p
        return p.resolve()

    # ── Output path derivation ───────────────────────────────────────────

    @property
    def output_root_path(self) -> Path | None:
        """Resolved output root, or None when the user hasn't configured one.

        When unconfigured it returns None (NOT a guessed directory) so the UI
        can prompt for it instead of silently writing somewhere unexpected.
        """
        if not self.output_root:
            return None
        p = Path(self.output_root)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p.resolve()

    def base_name(self, lang: str) -> str:
        """Output base name (no extension) for a language."""
        return self.name_map.get(lang) or self.source_path.stem

    def output_path(self, format: str, lang: str, *, pdf: bool = False, naming: str | None = None) -> str:
        """Derive a single output path for (format, lang) from output_root.

        Returns "" when no output root is configured (i.e. unconfigured).
        """
        root = self.output_root_path
        if root is None:
            return ""
        if naming is None:
            naming = self.output_naming
        return derive_output_path(root, format, lang, self.name_map, self.source_path.stem, pdf=pdf, naming=naming)
