#!/usr/bin/env bash
set -euo pipefail
WORKFLOW_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW_SETUP_PYTHON="${RING_AGENT_PYTHON:-}"
if [[ -z "$WORKFLOW_SETUP_PYTHON" ]]; then
    WORKFLOW_GIT="$(git -C "$WORKFLOW_PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    for candidate in "$WORKFLOW_PROJECT_ROOT/.venv/bin/python" "$WORKFLOW_PROJECT_ROOT/.runtime/venv/bin/python" \
                     "${WORKFLOW_GIT%/.git}/.venv/bin/python" "${WORKFLOW_GIT%/.git}/.runtime/venv/bin/python"; do
        if [[ -x "$candidate" ]]; then WORKFLOW_SETUP_PYTHON="$candidate"; break; fi
    done
fi
if [[ -z "$WORKFLOW_SETUP_PYTHON" ]]; then
    echo '请通过 RING_AGENT_PYTHON 指定 Python 3.11+ 的路径。' >&2
    exit 1
fi
export PYTHONPATH="$WORKFLOW_PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
WORKFLOW_RUNTIME="$("$WORKFLOW_SETUP_PYTHON" -c 'from proximic_ring.agent_workflow.operations.operation_embeddings import runtime_directory; print(runtime_directory())')"
"$WORKFLOW_SETUP_PYTHON" -m proximic_ring.agent_workflow.operations.operation_embedding_worker --check-runtime
"$WORKFLOW_SETUP_PYTHON" -m proximic_ring.agent_workflow.operations.operation_embedding_worker --download "$WORKFLOW_RUNTIME/models/bge-m3"
