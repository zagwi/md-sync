"""Web dashboard entry: ``python -m md_sync.web`` (and md-sync-web binary).

Starts the FastAPI server on 127.0.0.1:8580 serving the Dioxus wasm UI
from ``static/`` plus the /api/* endpoints.
"""

from md_sync.web.app import main

if __name__ == "__main__":
    main()
