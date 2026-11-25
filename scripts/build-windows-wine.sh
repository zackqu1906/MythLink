#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo 'The Wine build requires a Linux x86-64 runner.' >&2
    exit 1
fi
for tool in wine wineserver curl unzip sha256sum; do
    if ! command -v "$tool" >/dev/null; then
        echo "Missing build tool $tool" >&2
        exit 1
    fi
done
wine --version

build_tools=$(mktemp -d "${RUNNER_TEMP:-/tmp}/mythlink-wine.XXXXXX")
export WINEPREFIX="$build_tools/prefix" WINEARCH=win64
export WINEDEBUG="${WINEDEBUG:--all}" WINEDLLOVERRIDES='mscoree,mshtml='
export PYTHONIOENCODING=utf-8 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
cleanup() {
    wineserver -k >/dev/null 2>&1 || true
    rm -rf -- "$build_tools"
}
trap cleanup EXIT

curl -fsSL --retry 3 -o "$build_tools/python.nupkg" \
    https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg
printf '%s  %s\n' 9283876d58c017e0e846f95b490da3bca0fc0a6ee1134b2870677cfb7eec3c67 \
    "$build_tools/python.nupkg" | sha256sum --check
unzip -q "$build_tools/python.nupkg" 'tools/*' -d "$build_tools"
windows_python="$build_tools/tools/python.exe"
wine "$windows_python" -c 'import platform, sys; assert sys.platform == "win32" and platform.machine().lower() == "amd64"; print(sys.version)'

curl -fsSL --retry 3 -o "$build_tools/innosetup.exe" \
    https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe
printf '%s  %s\n' a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c \
    "$build_tools/innosetup.exe" | sha256sum --check
wine "$build_tools/innosetup.exe" /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /PORTABLE=1 '/DIR=C:\InnoSetup'
iscc="$WINEPREFIX/drive_c/InnoSetup/ISCC.exe"
if [[ ! -f "$iscc" ]]; then
    echo 'Inno Setup did not install. Check that Wine can run 32-bit Windows programs.' >&2
    exit 1
fi

version="${MYTHLINK_BUILD_VERSION:-}"
if [[ -z "$version" ]]; then
    version=$(wine "$windows_python" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])' | tr -d '\r')
fi
MYTHLINK_BUILD_VERSION=$(wine "$windows_python" tools/build_version.py --version "$version" | tr -d '\r')
export MYTHLINK_BUILD_VERSION

wine "$windows_python" -m pip install --upgrade 'pip==26.2.1' 'setuptools==81.0.0' 'wheel==0.48.0'
wine "$windows_python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.11.0' 'torchaudio==2.11.0'
wine "$windows_python" -m pip install -c requirements-windows.lock \
    '.[ring,touchpad,asr-streaming-sensevoice,asr-funasr-nano,asr-volcengine,ui]' -r requirements-packaging.txt
wine "$windows_python" tools/install_r1_sdk.py
wine "$windows_python" -m PyInstaller --noconfirm --clean 'packaging\mythlink.spec'

if ! PROXIMIC_DATA_HOME='C:\Mythlink-build-check' wine dist/Mythlink/Mythlink.exe --self-check-package; then
    diagnostic_log="$WINEPREFIX/drive_c/Mythlink-build-check/logs/diagnostic.log"
    if [[ -f "$diagnostic_log" ]]; then
        tail -n 80 "$diagnostic_log" >&2
    fi
    exit 1
fi
wine "$iscc" "/DMyAppVersion=$MYTHLINK_BUILD_VERSION" 'packaging\windows\Mythlink.iss'
installer="dist/installer/Mythlink-$MYTHLINK_BUILD_VERSION-windows-x64-setup.exe"
test -s "$installer"
sha256sum "$installer"
