#!/bin/bash
# Build an MSYS2 pacman package from the pre-built wheel
# Run from Linux after cross-compiling the wheel
#
# Usage: ./msys2/build-pkg.sh
#
# Output: msys2/mingw-w64-ucrt-x86_64-python-protocols-1.0.0-1-any.pkg.tar.zst

set -e
cd "$(dirname "$0")/.."

PKG_NAME="python-protocols"
PKG_VER="1.0.0"
PKG_REL="1"
MINGW_PREFIX="/ucrt64"
ARCH="any"

# Full package name following MSYS2 conventions
FULL_NAME="mingw-w64-ucrt-x86_64-${PKG_NAME}-${PKG_VER}-${PKG_REL}-${ARCH}"

WHEEL="dist/protocols-${PKG_VER}-cp39-abi3-win_amd64.whl"
if [[ ! -f "$WHEEL" ]]; then
    echo "ERROR: Wheel not found: $WHEEL"
    echo "Run ./cross/build-msys2.sh --zig first"
    exit 1
fi

echo "==> Building MSYS2 package from: $WHEEL"

# Create package directory structure
PKG_DIR="msys2/pkg"
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR${MINGW_PREFIX}/lib/python3.11/site-packages"

# Extract wheel contents
echo "==> Extracting wheel..."
unzip -q "$WHEEL" -d "$PKG_DIR${MINGW_PREFIX}/lib/python3.11/site-packages/"

# Remove dist-info (pacman tracks files differently)
# Actually, keep it - Python needs it for importlib.metadata
# rm -rf "$PKG_DIR${MINGW_PREFIX}/lib/python3.11/site-packages/"*.dist-info

# Create .PKGINFO
echo "==> Creating package metadata..."
cat > "$PKG_DIR/.PKGINFO" <<EOF
pkgname = mingw-w64-ucrt-x86_64-${PKG_NAME}
pkgver = ${PKG_VER}-${PKG_REL}
pkgdesc = High-performance file transfer protocols (ZMODEM, HS/Link, WS/Link) with Rust acceleration
url = https://github.com/your-org/protocols
builddate = $(date +%s)
packager = cross-build
size = $(du -sb "$PKG_DIR" | cut -f1)
arch = ${ARCH}
license = MIT
depend = mingw-w64-ucrt-x86_64-python
EOF

# Create .MTREE (file manifest with checksums)
echo "==> Creating file manifest..."
cd "$PKG_DIR"
find . -type f -o -type l | while read f; do
    if [[ "$f" == "./.PKGINFO" ]] || [[ "$f" == "./.MTREE" ]]; then
        continue
    fi
    # Format: path mode uid gid size sha256
    stat_out=$(stat -c "%a 0 0 %s" "$f" 2>/dev/null || echo "644 0 0 0")
    sha=$(sha256sum "$f" | cut -d' ' -f1)
    echo ".${f#.} $stat_out sha256=$sha"
done > .MTREE
cd - > /dev/null

# Create the package archive
echo "==> Creating package archive..."
OUTPUT="msys2/${FULL_NAME}.pkg.tar.zst"
cd "$PKG_DIR"
# Use tar with zstd compression (pacman expects this format)
tar --zstd -cf "../${FULL_NAME}.pkg.tar.zst" .PKGINFO .MTREE *
cd - > /dev/null

# Cleanup
rm -rf "$PKG_DIR"

echo ""
echo "==> Package created: $OUTPUT"
ls -lh "$OUTPUT"

echo ""
echo "==> Install on MSYS2 UCRT64 with:"
echo "    pacman -U ${FULL_NAME}.pkg.tar.zst"
