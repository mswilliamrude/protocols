#!/bin/bash
# Build pyprotocols-core wheel for MSYS2 UCRT64
# Usage: ./cross/build-msys2.sh [--zig]
#
# Options:
#   --zig    Use cargo-zigbuild (recommended, cleaner cross-compilation)
#   (none)   Use mingw-w64 directly

set -e
cd "$(dirname "$0")/.."

SCRIPT_DIR="cross"
DIST_DIR="dist"
mkdir -p "$DIST_DIR"

if [[ "$1" == "--zig" ]]; then
    echo "==> Building with Zig cross-compiler (recommended)"
    DOCKERFILE="$SCRIPT_DIR/Dockerfile.zigbuild"
    TAG="pyprotocols-zigbuild"
else
    echo "==> Building with mingw-w64"
    DOCKERFILE="$SCRIPT_DIR/Dockerfile.msys2-ucrt64"
    TAG="pyprotocols-msys2"
fi

echo "==> Building Docker image: $TAG"
docker build -f "$DOCKERFILE" -t "$TAG" .

echo "==> Running cross-compilation"
docker run --rm -v "$(pwd)/$DIST_DIR:/out" "$TAG"

echo ""
echo "==> Wheels available in $DIST_DIR:"
ls -la "$DIST_DIR"/*.whl 2>/dev/null || echo "(no wheels found)"

echo ""
echo "==> To install on MSYS2 UCRT64:"
echo "    # In MSYS2 UCRT64 shell:"
echo "    pip install protocols-1.0.0-cp39-abi3-win_amd64.whl"
