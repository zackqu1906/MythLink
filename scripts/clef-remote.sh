#!/usr/bin/env bash
# Reuse the project's Clef client over a private SSH forward.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEF_RUNTIME="$PROJECT_ROOT/.runtime/clef"
CLEF_CONFIG="${PROXIMIC_CLEF_CONNECTION_CONFIG:-$CLEF_RUNTIME/connection.env}"
if [[ ! -f "$CLEF_CONFIG" ]]; then
  echo "Missing Clef connection config: $CLEF_CONFIG" >&2
  exit 1
fi
source "$CLEF_CONFIG"
: "${PROXIMIC_CLEF_SSH_HOST:?}"
: "${PROXIMIC_CLEF_SSH_KEY:?}"
export PROXIMIC_CLEF_URL PROXIMIC_CLEF_MODEL PROXIMIC_CLEF_TIMEOUT_S
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
export no_proxy="127.0.0.1,localhost,${no_proxy:-}"
CLEF_CONTROL="$CLEF_RUNTIME/ssh.sock"
CLEF_SSH=(ssh -S "$CLEF_CONTROL" -o BatchMode=yes -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10 -i "$PROXIMIC_CLEF_SSH_KEY" -p "${PROXIMIC_CLEF_SSH_PORT:-20030}")
start_tunnel() {
  mkdir -p "$CLEF_RUNTIME"
  if ! "${CLEF_SSH[@]}" -O check "$PROXIMIC_CLEF_SSH_HOST" >/dev/null 2>&1; then
    "${CLEF_SSH[@]}" -M -fNT -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -L "127.0.0.1:${PROXIMIC_CLEF_LOCAL_PORT:-30000}:127.0.0.1:${PROXIMIC_CLEF_REMOTE_PORT:-30000}" \
      "$PROXIMIC_CLEF_SSH_HOST"
  fi
}
CLEF_ACTION="${1:-status}"
if [[ $# -gt 0 ]]; then shift; fi
case "$CLEF_ACTION" in
  start) start_tunnel; echo "$PROXIMIC_CLEF_URL" ;;
  status)
    "${CLEF_SSH[@]}" -O check "$PROXIMIC_CLEF_SSH_HOST"
    curl --noproxy '*' --fail --silent --show-error --max-time 10 "${PROXIMIC_CLEF_URL%/v1/systemone}/health"
    echo ;;
  stop) "${CLEF_SSH[@]}" -O exit "$PROXIMIC_CLEF_SSH_HOST" ;;
  check)
    start_tunnel
    exec "$PROJECT_ROOT/.runtime/venv/bin/python" "$PROJECT_ROOT/tools/check_clef_router.py" "$@" ;;
  compare)
    start_tunnel
    exec "$PROJECT_ROOT/.runtime/venv/bin/python" "$PROJECT_ROOT/tools/compare_clef_routing.py" "$@" ;;
  split)
    start_tunnel
    exec "$PROJECT_ROOT/.runtime/venv/bin/python" "$PROJECT_ROOT/tools/check_clef_split.py" "$@" ;;
  primary)
    start_tunnel
    exec "$PROJECT_ROOT/.runtime/venv/bin/python" "$PROJECT_ROOT/tools/check_clef_primary.py" "$@" ;;
  pipeline)
    start_tunnel
    exec "$PROJECT_ROOT/.runtime/venv/bin/python" "$PROJECT_ROOT/tools/check_clef_pipeline.py" "$@" ;;
  ui)
    start_tunnel
    exec bash "$PROJECT_ROOT/scripts/start-ui.sh" "$@" ;;
  *) echo "Usage: $0 {start|status|stop|primary|compare|split|check|pipeline|ui} [arguments]" >&2; exit 2 ;;
esac
