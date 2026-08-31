"""CLI entry point.

Usage:
    md-sync init         Create a default md-sync.yaml
    md-sync sync         Run a one-shot sync
    md-sync status       Show project status
    md-sync dry-run      Show what would change
    md-sync gui          Launch the native Qt GUI
    md-sync start        Serve the web dashboard (static/index.html) at :8580
    md-sync template     Manage template styles
    md-sync plugin       Manage plugins
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from md_sync.config import ProjectConfig
from md_sync.core.pipeline import SyncPipeline
from md_sync.plugin.interface import DirectoryPlugin
from md_sync.plugin.loader import remove_plugin
from md_sync.plugin.registry import PluginRegistry
from md_sync.template.manager import TemplateManager


def _add_common_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "-c",
        "--config",
        default="md-sync.yaml",
        help="Path to project config (default: md-sync.yaml)",
    )


def _extract_config(args: argparse.Namespace) -> str:
    cfg = getattr(args, "config", None)
    return cfg if cfg else "md-sync.yaml"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="md-sync",
        description="Markdown → multi-format, multi-language sync engine",
    )
    parser.add_argument("-c", "--config", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd, help_text in [
        ("init", "Create a default md-sync.yaml"),
        ("sync", "Run a one-shot sync"),
        ("status", "Show project status"),
        ("dry-run", "Show what would change without writing"),
        ("gui", "Launch the native Qt GUI"),
        ("start", "Serve the web dashboard (index.html) at :8580"),
        ("ipc", "Serve the desktop backend over a Unix socket (no network port)"),
    ]:
        sp = sub.add_parser(cmd, help=help_text)
        _add_common_args(sp)

    # template subcommand
    tpl_parser = sub.add_parser("template", help="Manage template styles")
    _add_common_args(tpl_parser)
    tpl_sub = tpl_parser.add_subparsers(dest="template_action", required=True)
    tpl_sub.add_parser("list")
    tpl_show = tpl_sub.add_parser("show")
    tpl_show.add_argument("name")

    # plugin subcommand
    plg_parser = sub.add_parser("plugin", help="Manage plugins")
    _add_common_args(plg_parser)
    plg_sub = plg_parser.add_subparsers(dest="plugin_action", required=True)
    plg_sub.add_parser("list")
    plg_install = plg_sub.add_parser("install")
    plg_install.add_argument("source")
    plg_remove = plg_sub.add_parser("remove")
    plg_remove.add_argument("name")
    plg_show = plg_sub.add_parser("show")
    plg_show.add_argument("name")
    plg_template = plg_sub.add_parser(
        "template", help="Generate source template.md from a plugin pack"
    )
    plg_template.add_argument("plugin_name", help="Name of the installed plugin pack")
    plg_template.add_argument(
        "-o",
        "--output",
        default="template.md",
        help="Output path for template.md (default: template.md)",
    )

    args = parser.parse_args(argv)
    cfg_path = _extract_config(args)

    handlers = {
        "init": lambda: _cmd_init(),
        "sync": lambda: _cmd_sync(cfg_path),
        "status": lambda: _cmd_status(cfg_path),
        "dry-run": lambda: _cmd_dry_run(cfg_path),
        "template": lambda: _cmd_template(args),
        "plugin": lambda: _cmd_plugin(args),
        "gui": lambda: _cmd_gui(),
        "start": lambda: _cmd_start(),
        "ipc": lambda: _cmd_ipc(),
    }

    handler = handlers.get(args.command)
    if handler:
        return handler()

    parser.print_help()
    return 1


# ── Init ────────────────────────────────────────────────────────────────────


def _cmd_init() -> int:
    cwd = Path.cwd()
    cfg_path = cwd / "md-sync.yaml"
    if cfg_path.exists():
        print(f"[init] Already exists: {cfg_path}")
        return 0

    template = """# md-sync project configuration
project: my-project
source: README.md
schema: resume

outputs:
  - format: html
    lang: zh
    path: output/zh.html
    style: bwx
    pdf: true
    pdf_path: output/zh.pdf

  - format: md
    lang: en
    path: output/en.md

  - format: html
    lang: en
    path: output/en.html
    style: modern
    pdf: true
    pdf_path: output/en.pdf

watch:
  enabled: true
  debounce: 1.5

translation:
  strategy: mapping
  mapping_file: .translations.json
  ai:
    provider: auto
"""
    cfg_path.write_text(template, encoding="utf-8")
    print(f"[init] ✓ Created: {cfg_path}")
    return 0


# ── Sync / Status / Dry-run ─────────────────────────────────────────────────


def _cmd_sync(config_path: str) -> int:
    cfg = _load_config(config_path)
    pipeline = SyncPipeline(cfg)
    stats = pipeline.run()
    if stats.get("errors"):
        print(f"[sync] {len(stats['errors'])} error(s):")
        for e in stats["errors"]:
            print(f"  ✗ {e}")
        return 1
    return 0


def _cmd_status(config_path: str) -> int:
    cfg = _load_config(config_path)
    pipeline = SyncPipeline(cfg)
    info = pipeline.run_dry()

    print(f"Project: {cfg.project}")
    print(f"Source:  {info['source']}")
    print("Sections:")
    for sec in info["sections"]:
        print(f"  - {sec['title']} ({sec['items']} items)")
    if info.get("pending_translations"):
        print("\nPending translations:")
        for pt in info["pending_translations"]:
            print(f"  [{pt['section']}] {pt['preview']}...")
    print("\nOutputs:")
    for out in cfg.outputs:
        exists = Path(out.path).exists()
        style_name = out.style or out.theme or "default"
        print(f"  {'✓' if exists else ' '} {out.format} ({out.lang}) [{style_name}] → {out.path}")
    return 0


def _cmd_dry_run(config_path: str) -> int:
    cfg = _load_config(config_path)
    pipeline = SyncPipeline(cfg)
    info = pipeline.run_dry()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


# ── Template subcommands ────────────────────────────────────────────────────


def _cmd_template(args: argparse.Namespace) -> int:
    mgr = TemplateManager()

    if args.template_action == "list":
        print(f"{'Name':20s} {'Schema':12s} {'Label'}")
        print("-" * 80)
        for t in mgr.list_templates():
            tag = f" [{t.author}]" if "plugin" in t.author else ""
            print(f"{t.name:20s} {t.schema:12s} {t.label}{tag}")
        print("\nTip: 'md-sync template show <name>' for details")

    elif args.template_action == "show":
        try:
            cat = mgr.resolve(args.name)
            t = cat.info
            print(f"Name:        {t.name}")
            print(f"Label:       {t.label}")
            print(f"Description: {t.description}")
            print(f"Schema:      {t.schema}")
            print(f"Author:      {t.author}")
            print(f"Version:     {t.version}")
            print(f"Directory:   {t.directory}")
            print(f"Engine:      {t.engine}")
            print(f"Sections:    {', '.join(cat.section_ids)}")
            if t.tags:
                print(f"Tags:        {', '.join(t.tags)}")
        except FileNotFoundError as e:
            print(f"✗ {e}")

    return 0


# ── Plugin subcommands ──────────────────────────────────────────────────────


def _cmd_plugin(args: argparse.Namespace) -> int:
    if args.plugin_action == "list":
        registry = PluginRegistry()
        plugins = registry.list_plugins()
        if not plugins:
            print("No plugins installed.")
            print("  Install one: md-sync plugin install <path|git-url|package>")
        else:
            print(f"{'Name':20s} {'Type':12s} {'Schema':16s} {'Templates':20s} {'Author'}")
            print("-" * 80)
            for p in plugins:
                tpls = ", ".join(p.templates) if p.templates else "—"
                schema = p.parser_schema or "—"
                print(f"{p.name:20s} {p.plugin_type:12s} {schema:16s} {tpls:20s} {p.author}")

    elif args.plugin_action == "install":
        from md_sync.plugin.loader import install_plugin as ip

        try:
            ip(args.source)
        except Exception as e:
            print(f"✗ Install failed: {e}")
            return 1

    elif args.plugin_action == "remove":
        ok = remove_plugin(args.name)
        return 0 if ok else 1

    elif args.plugin_action == "template":
        """Generate source template.md from a plugin pack."""
        registry = PluginRegistry()
        source = registry.get_template_source(args.plugin_name)
        if source is None:
            pack_info = registry.get_pack_info(args.plugin_name)
            if pack_info is None:
                print(f"✗ Plugin '{args.plugin_name}' not found or is not a pack.")
                print("  Installed plugins:")
                for p in registry.list_plugins():
                    print(f"    - {p.name} ({p.plugin_type})")
                return 1
            print(f"✗ Plugin '{args.plugin_name}' has no source template (template.md).")
            return 1
        out_path = Path(args.output)
        out_path.write_text(source, encoding="utf-8")
        print(f"✓ Generated template.md from '{args.plugin_name}' → {out_path}")
        print("  Edit this file to write your document, then run 'md-sync sync'.")
        return 0

    elif args.plugin_action == "show":
        registry = PluginRegistry()
        plugin = registry.get(args.name)
        if plugin:
            m = plugin.manifest
            print(f"Name:        {m.name}")
            print(f"Version:     {m.version}" if m.version else "Version:     1.0")
            print(f"Type:        {m.plugin_type}")
            print(f"Description: {m.description}")
            print(f"Author:      {m.author}")
            print(f"Directory:   {m.directory}")
            if m.templates:
                print(f"Templates:   {', '.join(m.templates)}")
            if m.plugin_type in ("pack", "parser"):
                print(f"Schema:      {m.parser_schema or '—'}")
                print(f"Parser:      {m.parser_class or '—'}")
                print(f"Template:    {m.template or '—'}")
                # Show source template preview
                if isinstance(plugin, DirectoryPlugin):
                    src = plugin.get_template_source()
                    if src:
                        preview = src[:300].replace("\n", "\n              ")
                        print("\n  Template.md preview (first 300 chars):\n")
                        print(f"  {preview}...")
            if m.hooks:
                print(f"Hooks:       {', '.join(m.hooks)}")
            if m.dependencies:
                print(f"Dependencies: {', '.join(m.dependencies)}")
        else:
            print(f"Plugin not loaded: {args.name}")
            print("  Check: md-sync plugin list")

    return 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_config(config_path: str) -> ProjectConfig:
    path = Path(config_path)
    if not path.exists():
        print(f"[error] Config not found: {path}")
        print("  Run 'md-sync init' to create one, or use the Qt GUI ('md-sync gui').")
        sys.exit(1)
    return ProjectConfig.load(path)


def _cmd_gui() -> int:
    """Launch the native Qt GUI (no HTTP server)."""
    from md_sync.qt_app import main as gui_main

    gui_main()
    return 0


def _cmd_start() -> int:
    """Serve the web dashboard (static/index.html) on localhost:8580."""
    from md_sync.web.app import main as web_main

    web_main()
    return 0


def _cmd_ipc() -> int:
    """Serve the desktop backend over a Unix socket (no network port)."""
    from md_sync.web.ipc import main as ipc_main

    ipc_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
