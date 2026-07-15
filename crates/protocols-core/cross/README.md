# Cross-Compilation for MSYS2 UCRT64

This directory contains Docker-based tooling to cross-compile the `protocols` Python extension from Linux to Windows (MSYS2 UCRT64).

## Quick Start

```bash
# From the pyprotocols-core directory:
./cross/build-msys2.sh --zig
```

The wheel will be in `dist/`.

## Options

| Method | Command | Notes |
|--------|---------|-------|
| **Zig (recommended)** | `./cross/build-msys2.sh --zig` | Clean cross-compilation via cargo-zigbuild |
| mingw-w64 | `./cross/build-msys2.sh` | Traditional approach, uses gnullvm target |

## Installing on MSYS2

```bash
# In MSYS2 UCRT64 terminal:
pacman -S mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-pip

# Install the wheel
pip install protocols-1.0.0-cp39-abi3-win_amd64.whl

# Verify
python -c "import protocols; print(protocols.__file__)"
```

## How It Works

1. **abi3-py39**: The extension uses Python's stable ABI (PEP 384), so one wheel works for Python 3.9+
2. **x86_64-pc-windows-gnu**: Rust target for Windows with GNU toolchain (what MSYS2 uses)
3. **Zig**: Used as a drop-in C cross-compiler — bundles all necessary headers/libs

## Troubleshooting

### "DLL load failed" on import
The wheel might be missing the UCRT dependencies. In MSYS2:
```bash
pacman -S mingw-w64-ucrt-x86_64-gcc-libs
```

### Wrong Python (using system Python instead of MSYS2)
Make sure you're in a UCRT64 shell, not MSYS2 or MINGW64:
```bash
# Should show /ucrt64/bin/python
which python
```

### ABI mismatch / segfaults
If you get crashes, the wheel might have been built for MSVC Python. Rebuild with `--zig` flag which explicitly targets GNU ABI.

## Manual Docker Build

```bash
# Build image
docker build -f cross/Dockerfile.zigbuild -t pyprotocols-zig .

# Run and extract wheel
docker run --rm -v $(pwd)/dist:/out pyprotocols-zig
```
