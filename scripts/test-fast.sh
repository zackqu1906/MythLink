#!/usr/bin/env bash
# Stroke, touchpad and trial tests in parallel (about 1-2 minutes).
# Extra arguments go to pytest, e.g.  scripts/test-fast.sh --lf   or   -k menu
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.runtime/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.runtime/venv/Scripts/python.exe"
[[ -x "$PYTHON" ]] || { echo "Project Python is missing. Run the setup script first." >&2; exit 1; }
CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)"
WORKERS=$(( CORES < 12 ? CORES : 12 ))
cd "$ROOT"
FILES=()
for file in tests/test_*.py; do
    case "$(basename "$file")" in
        test_stroke_*|test_touchpad_*|test_ui_touchpad*|test_native_touchpad*|test_complete_stroke_input*) FILES+=("$file") ;;
    esac
done
exec "$PYTHON" -m pytest -n "$WORKERS" --dist load "${FILES[@]}" "$@"
