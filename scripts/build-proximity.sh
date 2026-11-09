#!/bin/zsh
set -euo pipefail
TASK_ROOT="${0:A:h:h}"
source "$TASK_ROOT/scripts/macos-signing-env.sh"
BUILD_ROOT="$TASK_ROOT/.build/proximity"
APP="$BUILD_ROOT/ProxiMicPresence.app"
STAMP="$BUILD_ROOT/build-inputs.sha256"
DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-$(cat "$TASK_ROOT/packaging/macos-target.txt")}"
mkdir -p "$BUILD_ROOT"
# Ad-hoc Keychain authorization is tied to the actual code identity. Do not
# rebuild or re-sign an unchanged helper during unrelated UI/package work.
INPUT_HASH=$({
  shasum -a 256 "$TASK_ROOT/scripts/build-proximity.sh" "$TASK_ROOT/scripts/macos-signing-env.sh" "$TASK_ROOT"/native/ProxiMicPresence/*.swift "$TASK_ROOT/native/ProxiMicPresence/Info.plist"
  xcrun swiftc --version
  xcrun --show-sdk-path
  uname -m
  print -r -- "$DEPLOYMENT_TARGET"
  print -r -- "${PROXIMIC_SIGN_IDENTITY:--}"
} | shasum -a 256 | awk '{print $1}')
if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$INPUT_HASH" && -x "$APP/Contents/MacOS/ProxiMicPresence" ]] \
   && codesign --verify --strict "$APP" >/dev/null 2>&1; then
  print -r -- "距离锁屏组件未变化，复用现有签名和授权身份：$APP"
  exit 0
fi
# Build and sign away from the live helper path. The source app can restart its
# monitor during a build; signing an executable that it has opened can fail.
# A fresh staging bundle also avoids stale resources from an interrupted sign.
STAGING_ROOT="$(mktemp -d "$BUILD_ROOT/.build-XXXXXX")"
trap 'rm -rf "$STAGING_ROOT"' EXIT
STAGED_APP="$STAGING_ROOT/ProxiMicPresence.app"
mkdir -p "$STAGED_APP/Contents/MacOS" "$BUILD_ROOT/module-cache"
xcrun swiftc -swift-version 5 -O -target "$(uname -m)-apple-macosx$DEPLOYMENT_TARGET" \
  -module-cache-path "${PROXIMIC_MODULE_CACHE:-$BUILD_ROOT/module-cache}" \
  -framework AppKit -framework CoreBluetooth -framework Security -framework OpenDirectory -framework IOKit \
  "$TASK_ROOT/native/ProxiMicPresence/Policy.swift" "$TASK_ROOT/native/ProxiMicPresence/Calibration.swift" \
  "$TASK_ROOT/native/ProxiMicPresence/UnlockSecret.swift" \
  "$TASK_ROOT/native/ProxiMicPresence/ReturnWakePolicy.swift" \
  "$TASK_ROOT/native/ProxiMicPresence/main.swift" \
  -o "$STAGED_APP/Contents/MacOS/ProxiMicPresence"
cp "$TASK_ROOT/native/ProxiMicPresence/Info.plist" "$STAGED_APP/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string "$DEPLOYMENT_TARGET" "$STAGED_APP/Contents/Info.plist"
codesign "${MYTHLINK_CODESIGN_FLAGS[@]}" "$STAGED_APP"
codesign --verify --strict "$STAGED_APP"
rm -rf "$APP"
mv "$STAGED_APP" "$APP"
print -r -- "$INPUT_HASH" > "$STAMP"
print -r -- "已构建距离锁屏组件；尚未启用或锁屏：$APP"
