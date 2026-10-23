#!/usr/bin/env bash
set -euo pipefail

DOUBLE_TAP_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOUBLE_TAP_PYTHON="$DOUBLE_TAP_PROJECT_ROOT/.runtime/venv/bin/python"
if [[ ! -x "$DOUBLE_TAP_PYTHON" ]]; then
    echo "未找到项目 Python，请先运行 scripts/setup-macos.sh。" >&2
    exit 1
fi
cd "$DOUBLE_TAP_PROJECT_ROOT"
exec "$DOUBLE_TAP_PYTHON" -u tools/test_double_tap.py "$@"
