#!/usr/bin/env bash
set -euo pipefail
DOUBLE_PINCH_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$DOUBLE_PINCH_PROJECT_ROOT/scripts/test-firmware-gestures.sh" --double-pinch "$@"
