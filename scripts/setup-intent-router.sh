#!/usr/bin/env bash
# Clef runs on the server. This command checks the configured decision endpoint.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER_CLIENT_PYTHON="${PROXIMIC_PYTHON:-$PROJECT_ROOT/.runtime/venv/bin/python}"
if [[ ! -x "$ROUTER_CLIENT_PYTHON" ]]; then
    ROUTER_CLIENT_PYTHON="$(command -v python3)"
fi
exec "$ROUTER_CLIENT_PYTHON" "$PROJECT_ROOT/tools/check_clef_router.py" "$@"
