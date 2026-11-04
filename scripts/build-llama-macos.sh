#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAMA_TAG="b10516"
SOURCE_SHA256="bd13fa381488aa59ae4081aca24d368a956ad6e393c54931510a94d240590e33"
BUILD_ROOT="$PROJECT_ROOT/.build/llama-macos"
DESTINATION="$PROJECT_ROOT/.runtime/llama-cpp-$LLAMA_TAG-macos-arm64"
DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-$(cat "$PROJECT_ROOT/packaging/macos-target.txt")}"
CMAKE="${PROXIMIC_CMAKE:-$PROJECT_ROOT/.build/packaging-venv/bin/cmake}"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "The local LLM runtime supports Apple Silicon macOS only." >&2; exit 1
fi
if [[ ! -x "$CMAKE" ]]; then
    CMAKE="$(command -v cmake || true)"
    [[ -n "$CMAKE" ]] || { echo "Install requirements-packaging.txt to build the macOS LLM runtime." >&2; exit 1; }
fi
STAMP="$( { shasum -a 256 "$0"; printf '%s\n' "$SOURCE_SHA256" "$DEPLOYMENT_TARGET"; xcrun clang --version; } | shasum -a 256 | awk '{print $1}')"
if [[ -x "$DESTINATION/llama-server" && -f "$DESTINATION/build.sha256" ]] \
    && [[ "$(cat "$DESTINATION/build.sha256")" == "$STAMP" ]]; then exit 0; fi
mkdir -p "$PROJECT_ROOT/.cache/downloads" "$BUILD_ROOT" "$PROJECT_ROOT/.runtime"
ARCHIVE="$PROJECT_ROOT/.cache/downloads/llama-$LLAMA_TAG-source.tar.gz"
if [[ ! -f "$ARCHIVE" ]] || [[ "$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')" != "$SOURCE_SHA256" ]]; then
    curl --fail --location --retry 2 "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/$LLAMA_TAG" -o "$ARCHIVE.part"
    [[ "$(shasum -a 256 "$ARCHIVE.part" | awk '{print $1}')" == "$SOURCE_SHA256" ]] \
        || { echo "llama.cpp source checksum mismatch" >&2; exit 1; }
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
if [[ ! -d "$BUILD_ROOT/source/llama.cpp-$LLAMA_TAG" ]]; then
    mkdir -p "$BUILD_ROOT/source"
    tar -xzf "$ARCHIVE" -C "$BUILD_ROOT/source"
fi
SOURCE="$BUILD_ROOT/source/llama.cpp-$LLAMA_TAG"
# Keep CPU and Metal inference. The optional BLAS backend unconditionally uses
# Accelerate's 13.3-only ILP64 ABI. Downloads/HTTPS are owned by Python, and the
# app uses the local HTTP API rather than llama.cpp's web UI.
"$CMAKE" -S "$SOURCE" -B "$BUILD_ROOT/build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON '-DCMAKE_INSTALL_RPATH=@loader_path' \
    -DGGML_NATIVE=OFF -DGGML_CPU_KLEIDIAI=OFF -DGGML_BLAS=OFF \
    -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_OPENMP=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_APP=OFF \
    -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_NUMBER=10516
"$CMAKE" --build "$BUILD_ROOT/build" --target llama-server --parallel "${PROXIMIC_BUILD_JOBS:-4}"
STAGE="$(mktemp -d "$PROJECT_ROOT/.runtime/llama-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp "$BUILD_ROOT/build/bin/llama-server" "$STAGE/"
cp -R "$BUILD_ROOT/build/bin/"*.dylib "$STAGE/"
cp "$SOURCE/LICENSE" "$STAGE/"
"$STAGE/llama-server" --version
printf '%s\n' "$STAMP" > "$STAGE/build.sha256"
mkdir -p "$DESTINATION"
cp -R "$STAGE/." "$DESTINATION/"
echo "Local LLM runtime: $DESTINATION"
