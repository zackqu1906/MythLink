#!/usr/bin/env bash
# Run on the Ascend container. Survives SSH logout; restart after container reboot.
set -euo pipefail
CLEF_DIR="${CLEF_DIR:-/root/clef-test}"
CLEF_PID_FILE="$CLEF_DIR/service.pid"
running() {
  [[ -s "$CLEF_PID_FILE" ]] || return 1
  CLEF_PID="$(cat "$CLEF_PID_FILE")"
  [[ "$CLEF_PID" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$CLEF_PID" 2>/dev/null || return 1
  [[ "$(ps -p "$CLEF_PID" -o args=)" == *"$CLEF_DIR/serve_clef_npu.py"* ]]
}
case "${1:-status}" in
  start)
    if running; then echo "Clef already running: $CLEF_PID"; exit 0; fi
    nohup env HF_HUB_OFFLINE=1 "$CLEF_DIR/venv/bin/python" -u "$CLEF_DIR/serve_clef_npu.py" \
      --model-path "$CLEF_DIR/model" --warmup-file "$CLEF_DIR/warmup.json" \
      >> "$CLEF_DIR/service.log" 2>&1 < /dev/null &
    echo "$!" > "$CLEF_PID_FILE"
    echo "Clef started: $(cat "$CLEF_PID_FILE"); log: $CLEF_DIR/service.log" ;;
  status)
    if running; then echo "Clef running: $CLEF_PID"; else echo "Clef stopped"; exit 1; fi
    curl --noproxy '*' --fail --silent --show-error --max-time 10 http://127.0.0.1:30000/health
    echo ;;
  stop)
    if running; then kill "$CLEF_PID"; echo "Clef stopping: $CLEF_PID"; else echo "Clef stopped"; fi ;;
  *) echo "Usage: $0 {start|status|stop}" >&2; exit 2 ;;
esac
