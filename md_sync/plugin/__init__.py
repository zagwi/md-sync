"""Plugin system for md-sync — pluggable rendering templates and pipeline hooks.

Core concepts:
    Plugin          A Python package or directory that provides render templates
    PluginRegistry  Auto-discovers, loads, and manages plugins
    RenderPlugin    Base class for rendering plugins
    Hook            Pipeline extension points for plugins to inject behavior
"""
