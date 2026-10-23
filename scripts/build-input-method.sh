#!/bin/zsh
set -euo pipefail
TASK_ROOT="${0:A:h:h}"
if [[ "$#" == "0" ]]; then
  BUILD_PYTHON="$TASK_ROOT/.runtime/venv/bin/python"
  [[ -x "$BUILD_PYTHON" ]] || BUILD_PYTHON="${PROXIMIC_PYTHON:-python3}"
  exec "$BUILD_PYTHON" "$TASK_ROOT/tools/build_input_method.py"
fi
if [[ "$#" != "1" || "$1" != "--prepared" || -z "${MYTHLINK_IME_BUILD_STAGE:-}" ]]; then
  print -u2 -- "Usage: $0"
  exit 2
fi
source "$TASK_ROOT/scripts/macos-signing-env.sh"
BUILD_ROOT="$TASK_ROOT/.build/input-method"
APP_BUNDLE="$MYTHLINK_IME_BUILD_STAGE/ProxiMicInput.app"
mkdir -p "$BUILD_ROOT/module-cache" "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
export CLANG_MODULE_CACHE_PATH="$BUILD_ROOT/module-cache"
export SWIFT_MODULECACHE_PATH="$BUILD_ROOT/module-cache"
TARGET_ARCH="$(uname -m)"
DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
xcrun swiftc -swift-version 5 -O -module-cache-path "$BUILD_ROOT/module-cache" \
  -target "$TARGET_ARCH-apple-macosx$DEPLOYMENT_TARGET" \
  -framework AppKit -framework InputMethodKit -framework Carbon \
  "$TASK_ROOT"/native/ProxiMicInput/Sources/*.swift \
  -o "$APP_BUNDLE/Contents/MacOS/ProxiMicInput"
# Info.plist was stamped by the locked build wrapper before signing.
if [[ -d "$TASK_ROOT/native/ProxiMicInput/Resources" ]]; then
  # A rebuild must remove retired artwork as well as copy new resources.
  rsync -a --delete "$TASK_ROOT/native/ProxiMicInput/Resources/" "$APP_BUNDLE/Contents/Resources/"
fi
plutil -lint "$APP_BUNDLE/Contents/Info.plist"
codesign "${MYTHLINK_CODESIGN_FLAGS[@]}" "$APP_BUNDLE"
codesign --verify --strict "$APP_BUNDLE"
xcrun swiftc -swift-version 5 -O -module-cache-path "$BUILD_ROOT/module-cache" \
  -target "$TARGET_ARCH-apple-macosx$DEPLOYMENT_TARGET" \
  -parse-as-library -framework Carbon -framework AppKit \
  "$TASK_ROOT/scripts/InputMethodAdmin.swift" "$TASK_ROOT/scripts/InputSourceSelection.swift" -o "$MYTHLINK_IME_BUILD_STAGE/InputMethodAdmin"
codesign "${MYTHLINK_CODESIGN_FLAGS[@]}" --identifier com.proximic.InputMethodAdmin "$MYTHLINK_IME_BUILD_STAGE/InputMethodAdmin"
codesign --verify --strict "$MYTHLINK_IME_BUILD_STAGE/InputMethodAdmin"
