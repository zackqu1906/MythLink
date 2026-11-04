#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Pin the macOS 11-compatible Node 22 LTS build, independently of Codex's runtime.
NODE_VERSION="22.23.4"
NODE_SHA256="037650d0839f457747396f052073fb48333d31bf5abd3c98b74da20d89ebb1ad"
NODE_NAME="node-v$NODE_VERSION-darwin-arm64"
NODE_ROOT="$PROJECT_ROOT/.runtime/node"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "The bundled Node runtime supports Apple Silicon macOS only." >&2
    exit 1
fi
if [[ -x "$NODE_ROOT/bin/node" && -f "$NODE_ROOT/archive.sha256" ]] \
    && [[ "$(cat "$NODE_ROOT/archive.sha256")" == "$NODE_SHA256" ]] \
    && [[ "$("$NODE_ROOT/bin/node" --version)" == "v$NODE_VERSION" ]]; then
    exit 0
fi
mkdir -p "$PROJECT_ROOT/.cache/downloads" "$PROJECT_ROOT/.runtime"
ARCHIVE="$PROJECT_ROOT/.cache/downloads/$NODE_NAME.tar.xz"
if [[ ! -f "$ARCHIVE" ]] || [[ "$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')" != "$NODE_SHA256" ]]; then
    curl --fail --location --retry 2 "https://nodejs.org/dist/v$NODE_VERSION/$NODE_NAME.tar.xz" -o "$ARCHIVE.part"
    [[ "$(shasum -a 256 "$ARCHIVE.part" | awk '{print $1}')" == "$NODE_SHA256" ]] \
        || { echo "Node archive checksum mismatch" >&2; exit 1; }
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
STAGE="$(mktemp -d "$PROJECT_ROOT/.runtime/node-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
tar -xJf "$ARCHIVE" -C "$STAGE" "$NODE_NAME/bin/node" "$NODE_NAME/LICENSE"
[[ "$("$STAGE/$NODE_NAME/bin/node" --version)" == "v$NODE_VERSION" ]]
printf '%s\n' "$NODE_SHA256" > "$STAGE/$NODE_NAME/archive.sha256"
mkdir -p "$NODE_ROOT/bin"
mv "$STAGE/$NODE_NAME/bin/node" "$NODE_ROOT/bin/node"
mv "$STAGE/$NODE_NAME/LICENSE" "$NODE_ROOT/LICENSE"
mv "$STAGE/$NODE_NAME/archive.sha256" "$NODE_ROOT/archive.sha256"
echo "Node: $NODE_ROOT/bin/node (v$NODE_VERSION)"
