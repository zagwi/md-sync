"""Pipeline hook system — extension points for plugins.

Hooks are emitted at key points in the sync pipeline:
  - ``before_parse``     Before source MD is parsed
  - ``after_parse``      After Document is created
  - ``before_translate`` Before translation step
  - ``before_render``    Before rendering starts
  - ``after_render``     After output file is written
  - ``before_export``    Before PDF export
  - ``after_export``     After PDF export
  - ``before_sync``      Before full sync cycle
  - ``after_sync``       After full sync cycle
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


HookHandler = Callable[..., None]


@dataclass
class HookEvent:
    """Data passed to hook handlers."""
    name: str
    doc: Optional[Any] = None
    config: Optional[dict] = None
    output_path: Optional[Path] = None
    error: Optional[str] = None
    context: dict = field(default_factory=dict)


class HookManager:
    """Manage pipeline hook registration and emission."""

    def __init__(self):
        self._handlers: dict[str, list[HookHandler]] = {}

    def register(self, hook_name: str, handler: HookHandler) -> None:
        """Register a handler for a hook."""
        if hook_name not in self._handlers:
            self._handlers[hook_name] = []
        self._handlers[hook_name].append(handler)

    def unregister(self, hook_name: str, handler: HookHandler) -> None:
        """Remove a handler."""
        if hook_name in self._handlers:
            self._handlers[hook_name] = [h for h in self._handlers[hook_name] if h is not handler]

    def emit(self, hook_name: str, **kwargs) -> None:
        """Emit a hook event, calling all registered handlers."""
        if hook_name not in self._handlers:
            return
        event = HookEvent(name=hook_name, **kwargs)
        for handler in self._handlers[hook_name]:
            try:
                handler(event)
            except Exception as e:
                print(f"[hook] Handler error on {hook_name}: {e}")

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()

    @property
    def registered_hooks(self) -> list[str]:
        return list(self._handlers.keys())


# Singleton instance (used by pipeline and plugins)
_hook_manager: Optional[HookManager] = None


def get_hook_manager() -> HookManager:
    """Get or create the global HookManager singleton."""
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager()
    return _hook_manager


def reset_hook_manager() -> None:
    """Reset the singleton (useful for testing)."""
    global _hook_manager
    _hook_manager = None
