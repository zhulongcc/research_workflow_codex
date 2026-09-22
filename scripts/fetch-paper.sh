#!/usr/bin/env bash
# Modified replacement for skJack/research-workflow, 2026-09-22.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || { echo "Python 3.10+ is required." >&2; exit 1; }
exec "$PYTHON" "$SCRIPT_DIR/fetch_paper.py" "$@"
