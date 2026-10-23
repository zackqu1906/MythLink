#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RING_AGENT_PYTHON="${RING_AGENT_PYTHON:-$PROJECT_ROOT/.runtime/venv/bin/python}"
if [[ ! -x "$RING_AGENT_PYTHON" ]]; then
    RING_AGENT_PYTHON="$PROJECT_ROOT/.venv/bin/python"
fi
if [[ ! -x "$RING_AGENT_PYTHON" ]]; then
    echo "未找到 Mythlink Python 环境。请先运行 ./scripts/setup-macos.sh。" >&2
    exit 1
fi
exec "$RING_AGENT_PYTHON" "$PROJECT_ROOT/experiments/ring_desktop_agent/launcher.py" "$@"
