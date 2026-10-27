#!/usr/bin/env bash
# Whole suite in parallel; run before committing.
# Extra arguments go to pytest, e.g.  scripts/test-full.sh --lf
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.runtime/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.runtime/venv/Scripts/python.exe"
[[ -x "$PYTHON" ]] || { echo "Project Python is missing. Run the setup script first." >&2; exit 1; }
CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)"
WORKERS=$(( CORES < 12 ? CORES : 12 ))
cd "$ROOT"
# One broken module must not hide the results of the other files.
exec "$PYTHON" -m pytest -n "$WORKERS" --dist load --continue-on-collection-errors -rfEs "$@"
