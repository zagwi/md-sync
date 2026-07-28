"""Project configuration management.

Reads ``md-sync.yaml`` from the project directory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def derive_output_path(
    output_root: Path,
    format: str,
    lang: str,
    name_map: Optional[dict] = None,
    source_stem: str = "",
    *,
    pdf: bool = False,
) -> str:
    """Build an output file path under the user-specified *output root*.

    Rules:
      - zh output: <name_map["zh"] or source_stem>.<ext>
      - en output: <name_map["en"]>.<ext>
      - directories follow the format (html/ pdf/ md/).
    The user only sets ONE directory (the root); the tool creates the format
    sub-directories and names files by language automatically.
    """
    base = (name_map or {}).get(lang) or source_stem
    # Guarantee a non-empty filename: fall back to the language code so we
    # never produce an extension-only path like "pdf/.pdf".
    if not base:
        base = lang or format
    if pdf:
        return str(output_root / "pdf" / f"{base}.pdf")
    return str(output_root / format / f"{base}.{format}")


# ── Config data classes ─────────────────────────────────────────────────────


@dataclass
class OutputConfig:
    """A single output target."""
    format: str              # "md" | "html"
    lang: str                # "zh" | "en" | …
    path: str = ""           # output file path (derived from source name if empty)
    theme: Optional[str] = None   # legacy theme name (backward compat)
    style: Optional[str] = None   # template style name (e.g. "bwx", "modern")
    pdf: bool = False
    pdf_path: Optional[str] = None


@dataclass
class WatchConfig:
    enabled: bool = True
    debounce: float = 1.5


@dataclass
class AiTranslationConfig:
    provider: str = "auto"
    model: Optional[str] = None


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
    # Language of the source document. The Markdown output for THIS language IS
    # the source file itself (not a generated copy), so it must point back at
    # the source path and be flagged as the source in the UI.
    source_lang: str = "zh"

    # Derived (set after load)
    config_path: Path = field(default_factory=Path)   # original YAML file path
    project_dir: Path = field(default_factory=Path)
    source_path: Path = field(default_factory=Path)

    @classmethod
    def load(cls, path: Path | str) -> "ProjectConfig":
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
    def output_root_path(self) -> Optional[Path]:
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

    def output_path(self, format: str, lang: str, *, pdf: bool = False) -> str:
        """Derive a single output path for (format, lang) from output_root.

        Returns "" when no output root is configured (i.e. unconfigured).
        """
        root = self.output_root_path
        if root is None:
            return ""
        return derive_output_path(root, format, lang, self.name_map, self.source_path.stem, pdf=pdf)
