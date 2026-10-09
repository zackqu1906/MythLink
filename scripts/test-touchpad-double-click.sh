#!/usr/bin/env bash
set -euo pipefail
CLICK_TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLICK_TEST_PYTHON="$CLICK_TEST_ROOT/.runtime/venv/bin/python"
if [[ ! -x "$CLICK_TEST_PYTHON" ]]; then
    echo "未找到项目 Python，请先运行 scripts/setup-macos.sh。" >&2
    exit 1
fi
cd "$CLICK_TEST_ROOT"
exec "$CLICK_TEST_PYTHON" -u tools/test_touchpad_double_click.py "$@"
