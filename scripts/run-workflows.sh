#!/usr/bin/env bash
set -euo pipefail

WORKFLOW_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW_PYTHON="${RING_AGENT_PYTHON:-}"
if [[ -z "$WORKFLOW_PYTHON" ]]; then
    WORKFLOW_COMMON_GIT="$(git -C "$WORKFLOW_PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    WORKFLOW_PRIMARY_ROOT="${WORKFLOW_COMMON_GIT%/.git}"
    for candidate in "$WORKFLOW_PROJECT_ROOT/.runtime/venv/bin/python" "$WORKFLOW_PROJECT_ROOT/.venv/bin/python" \
                     "$WORKFLOW_PRIMARY_ROOT/.runtime/venv/bin/python" "$WORKFLOW_PRIMARY_ROOT/.venv/bin/python"; do
        if [[ -x "$candidate" ]]; then WORKFLOW_PYTHON="$candidate"; break; fi
    done
fi
if [[ -z "$WORKFLOW_PYTHON" || ! -x "$WORKFLOW_PYTHON" ]]; then
    echo "未找到项目 Python 环境。请用 RING_AGENT_PYTHON=/完整路径/venv/bin/python 指定。" >&2
    exit 1
fi
export PYTHONPATH="$WORKFLOW_PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$WORKFLOW_PROJECT_ROOT"
exec "$WORKFLOW_PYTHON" -m proximic_ring.agent_workflow.extraction.workflows --root "$WORKFLOW_PROJECT_ROOT/agent-experience" "$@"
