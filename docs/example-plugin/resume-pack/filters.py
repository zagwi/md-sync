"""Custom Jinja2 filters for the resume-pack plugin.

These filters are registered when the plugin is loaded and become
available in the Jinja2 rendering environment.

Usage in templates::
    {{ value | my_filter }}
"""

import re

_METRIC_RX = re.compile(
    r"(\d+[Kk]?\s*[+%]?|"
    r"\d+倍|"
    r"P\d+[<>]\d+ms|"
    r"\d+\.\d+%[+]?)"
)


def highlight_metric(text: str) -> str:
    """Wrap metric values in a styled span."""

    def _wrap(m):
        val = m.group(0).strip()
        return f'<span class="metric">{val}</span>'

    return _METRIC_RX.sub(_wrap, text)


def truncate(text: str, length: int = 100) -> str:
    """Truncate text to a given length."""
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "..."


# Registry of filters: {name: callable}
# This dict is discovered by DirectoryPlugin.register_filters()
filters = {
    "highlight_metric": highlight_metric,
    "truncate": truncate,
}
