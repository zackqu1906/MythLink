#!/usr/bin/env bash
set -euo pipefail

TRIAL_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRIAL_PYTHON="${RING_AGENT_PYTHON:-}"
if [[ -z "$TRIAL_PYTHON" ]]; then
    TRIAL_COMMON_GIT="$(git -C "$TRIAL_PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    TRIAL_PRIMARY_ROOT="${TRIAL_COMMON_GIT%/.git}"
    for candidate in "$TRIAL_PROJECT_ROOT/.runtime/venv/bin/python" "$TRIAL_PROJECT_ROOT/.venv/bin/python" \
                     "$TRIAL_PRIMARY_ROOT/.runtime/venv/bin/python" "$TRIAL_PRIMARY_ROOT/.venv/bin/python"; do
        if [[ -x "$candidate" ]]; then TRIAL_PYTHON="$candidate"; break; fi
    done
fi
if [[ -z "$TRIAL_PYTHON" || ! -x "$TRIAL_PYTHON" ]]; then
    echo "未找到项目 Python 环境。请用 RING_AGENT_PYTHON=/完整路径/venv/bin/python 指定。" >&2
    exit 1
fi
export PYTHONPATH="$TRIAL_PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$TRIAL_PROJECT_ROOT"
exec "$TRIAL_PYTHON" -m proximic_ring.agent_workflow.execution.workflow_trial_cli "$@"
