#!/usr/bin/env bash
# Modified from skJack/research-workflow, 2026-09-22.
# Resolve the script directory BEFORE changing project directories.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || { echo "Python 3.10+ is required; on Windows use python scripts/workflow.py init ..." >&2; exit 1; }
exec "$PYTHON" "$SCRIPT_DIR/workflow.py" init "$@"
