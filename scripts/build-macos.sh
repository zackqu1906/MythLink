#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$PROJECT_ROOT/.build/pyinstaller-config}"

APP_ONLY=0
VERSION_ARGS=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --app-only) APP_ONLY=1; shift ;;
        --version)
            [[ "$#" -ge 2 ]] || { echo "--version requires X.Y.Z" >&2; exit 2; }
            VERSION_ARGS+=(--version "$2"); shift 2 ;;
        --auto-version) VERSION_ARGS+=(--auto-version); shift ;;
        *) echo "Usage: $0 [--app-only] [--version X.Y.Z | --auto-version]" >&2; exit 2 ;;
    esac
done

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "The macOS package must be built on Apple Silicon." >&2
    exit 1
fi
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
source "$PROJECT_ROOT/scripts/macos-signing-env.sh"
REQUIRE_SIGNED_RELEASE="${PROXIMIC_REQUIRE_SIGNED_RELEASE:-0}"
if [[ "$REQUIRE_SIGNED_RELEASE" == "1" ]]; then
    if [[ "$APPLE_SIGNING_IDENTITY" == "-" || -z "${APPLE_NOTARY_PROFILE:-}" ]]; then
        echo "A release build requires APPLE_SIGNING_IDENTITY and APPLE_NOTARY_PROFILE." >&2
        exit 1
    fi
fi
if [[ -n "${PROXIMIC_PYTHON:-}" ]]; then
    PYTHON_BIN="$PROXIMIC_PYTHON"
elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
else
    "$PROJECT_ROOT/scripts/install-python-macos.sh"
    PYTHON_BIN="$PROJECT_ROOT/.runtime/python-bin/python3.11"
fi
VENV_ROOT="$PROJECT_ROOT/.build/packaging-venv"
[[ -x "$VENV_ROOT/bin/python" ]] || "$PYTHON_BIN" -m venv "$VENV_ROOT"
PYTHON="$VENV_ROOT/bin/python"
# macOS ships Bash 3.2, where an empty array under nounset needs this guard.
APP_VERSION="$("$PYTHON" tools/build_version.py ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"})"
export MYTHLINK_BUILD_VERSION="$APP_VERSION"
echo "Building Mythlink $APP_VERSION"
if [[ "${PROXIMIC_SKIP_DEPENDENCY_INSTALL:-0}" != "1" ]]; then
    "$PYTHON" -m pip install --upgrade \
        "pip==26.2.1" "setuptools==81.0.0" "wheel==0.48.0"
    "$PYTHON" -m pip install -c requirements-macos.lock \
        ".[ring,touchpad,asr-streaming-sensevoice,asr-funasr-nano,asr-volcengine,ui]" \
        -r requirements-packaging.txt
fi
/bin/zsh scripts/build-input-method.sh
/bin/zsh scripts/build-proximity.sh
"$PYTHON" tools/build_browser_extension.py --identity "$APPLE_SIGNING_IDENTITY"
PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m PyInstaller --noconfirm --clean packaging/mythlink.spec

APP="$PROJECT_ROOT/dist/Mythlink.app"
# Keep the installable app bundle intact. PyInstaller's binary collection can
# flatten nested apps; copy/sign these helpers after it has built the host.
mkdir -p "$APP/Contents/Helpers"
ditto "$PROJECT_ROOT/.build/input-method/ProxiMicInput.app" "$APP/Contents/Helpers/ProxiMicInput.app"
cp "$PROJECT_ROOT/.build/input-method/InputMethodAdmin" "$APP/Contents/Helpers/InputMethodAdmin"
ditto "$PROJECT_ROOT/.build/proximity/ProxiMicPresence.app" "$APP/Contents/Helpers/ProxiMicPresence.app"
ditto "$PROJECT_ROOT/.build/browser-extension/MythlinkBrowser.app" "$APP/Contents/Helpers/MythlinkBrowser.app"
# Audited libraries only; leave the executable/Python archive and all features
# intact. Validate exports and linkage before the final bundle signature.
"$PYTHON" tools/strip_macos_runtime.py "$APP" --report "$PROJECT_ROOT/.build/macos-runtime-size.json"
"$PYTHON" tools/sign_macos_bundle.py "$APP" \
    --identity "$APPLE_SIGNING_IDENTITY" --team "$MYTHLINK_SIGNING_TEAM" \
    --entitlements packaging/macos.entitlements.plist \
    --report "$PROJECT_ROOT/.build/macos-signing-report.json"

codesign --verify --deep --strict --verbose=2 "$APP"
plutil -lint "$APP/Contents/Info.plist"
"$PYTHON" tools/verify_macos_bundle.py "$APP" --minimum-macos 15.0
APP_EXECUTABLE="$APP/Contents/MacOS/Mythlink"
if ! file "$APP_EXECUTABLE" | grep -q "arm64"; then
    echo "Packaged executable is not Apple Silicon arm64: $APP_EXECUTABLE" >&2
    exit 1
fi

# Building a DMG proves only that files were collected.  Start the frozen app
# on the macOS builder as well so platform-only imports, native libraries and
# missing QML modules fail the build instead of failing on the user's Mac.
SMOKE_DATA_ROOT="$PROJECT_ROOT/.build/macos-smoke-data"
rm -rf "$SMOKE_DATA_ROOT"
mkdir -p "$SMOKE_DATA_ROOT"
SMOKE_IME_DIR="$(mktemp -d /tmp/proximic-build-ime.XXXXXX)"
trap 'rm -rf "$SMOKE_IME_DIR"' EXIT
PROXIMIC_DATA_HOME="$SMOKE_DATA_ROOT" \
    PROXIMIC_IME_SOCKET="$SMOKE_IME_DIR/bridge.sock" \
    "$APP_EXECUTABLE" --self-check-package
SMOKE_LOG="$SMOKE_DATA_ROOT/logs/diagnostic.log"
if [[ ! -f "$SMOKE_LOG" ]] \
    || ! grep -q "bundled ADPCM decoder ready" "$SMOKE_LOG" \
    || ! grep -q "bundled QML files ready" "$SMOKE_LOG" \
    || ! grep -q "macOS input method transport ready" "$SMOKE_LOG" \
    || ! grep -q "bundled input method installer ready" "$SMOKE_LOG" \
    || ! grep -q "macOS permission backends ready (read-only)" "$SMOKE_LOG" \
    || ! grep -q "macOS browser scene backend ready (read-only)" "$SMOKE_LOG" \
    || ! grep -q "QML root window ready" "$SMOKE_LOG" \
    || ! grep -q "Agent workspace QML ready" "$SMOKE_LOG" \
    || ! grep -q "Ring voice gesture endpoints ready" "$SMOKE_LOG" \
    || ! grep -q "Computer History recommendation runtime ready" "$SMOKE_LOG" \
    || ! grep -q "packaged ASR imports ready" "$SMOKE_LOG"; then
    echo "macOS packaged application did not complete its startup probe." >&2
    [[ -f "$SMOKE_LOG" ]] && cat "$SMOKE_LOG" >&2
    exit 1
fi
echo "macOS packaged startup probe passed."

if [[ "$APP_ONLY" == "1" ]]; then
    echo "App: $APP"
    echo "App-only build complete; existing DMG unchanged."
    exit 0
fi

DMG_ROOT="$PROJECT_ROOT/.build/dmg"
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "$APP" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
cp "$PROJECT_ROOT/packaging/macos-installation.txt" "$DMG_ROOT/安装说明.txt"
DMG="$PROJECT_ROOT/dist/Mythlink-$APP_VERSION-macos-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "Mythlink" -srcfolder "$DMG_ROOT" \
    -ov -format UDZO "$DMG"
if [[ "$APPLE_SIGNING_IDENTITY" != "-" ]]; then
    DMG_SIGN_FLAGS=(--force --timestamp --sign "$APPLE_SIGNING_IDENTITY")
    if [[ -n "${APPLE_SIGNING_KEYCHAIN:-}" ]]; then
        DMG_SIGN_FLAGS+=(--keychain "$APPLE_SIGNING_KEYCHAIN")
    fi
    codesign "${DMG_SIGN_FLAGS[@]}" "$DMG"
    codesign --verify --strict "$DMG"
fi

if [[ -n "${APPLE_NOTARY_PROFILE:-}" && "$APPLE_SIGNING_IDENTITY" != "-" ]]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
elif [[ "$APPLE_SIGNING_IDENTITY" != "-" || -n "${APPLE_NOTARY_PROFILE:-}" ]]; then
    echo "warning: both APPLE_SIGNING_IDENTITY and APPLE_NOTARY_PROFILE are required for notarization." >&2
fi

echo "DMG: $DMG"
