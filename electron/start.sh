#!/usr/bin/env bash
# Launch the md-sync desktop app (Electron shell + FastAPI backend).
set -e
cd "$(dirname "$0")"
exec electron .
