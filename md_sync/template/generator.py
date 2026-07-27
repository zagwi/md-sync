"""Template generator — scaffold new templates and provide online preview.

Interactive CLI wizard:
    md-sync template create
    → Asks for: name, label, schema, base template
    → Generates: template.yaml, base.html.j2, style.css, section stubs

Web-based template editor (future):
    /template-editor route in web dashboard
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from md_sync.template.manager import TemplateManager


def interactive_create(project_dir: Optional[Path] = None) -> int:
    """Interactive CLI wizard to scaffold a new template."""
    mgr = TemplateManager(project_dir)

    print("=== md-sync Template Creator ===\n")

    # Step 1: Choose base or fresh
    print("Available base templates:")
    templates = mgr.list_templates(include_legacy=True)
    for i, t in enumerate(templates, 1):
        print(f"  {i}. {t.name:20s} {t.label}")
    print(f"  N. Start fresh (no base)")

    choice = input("\nBase template number (or N): ").strip()
    base = None
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(templates):
            base = templates[idx].name

    # Step 2: Template name
    name = input("Template name (e.g. my-brand): ").strip()
    while not name or not name.replace("-", "").replace("_", "").isalnum():
        name = input("  Invalid name. Use letters, numbers, hyphens: ").strip()

    # Step 3: Label
    label = input(f"Display label (e.g. My Brand Style) [{name}]: ").strip() or name

    # Step 4: Schema
    schema = input(f"Document schema (resume/article/docs) [resume]: ").strip() or "resume"

    # Generate
    try:
        dest = mgr.create_scaffold(name=name, label=label, schema=schema, base=base)
        print(f"\n✓ Template created: {dest}")
        print(f"  Edit: {dest / 'template.yaml'}")
        print(f"  Styling: {dest / 'style.css'}")
        print(f"  Layout: {dest / 'base.html.j2'}")
        print(f"  Sections: {dest / 'sections/'}")
        return 0
    except FileExistsError as e:
        print(f"\n✗ {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1
