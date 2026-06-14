# PyZMODEM Utilities

This directory contains utility scripts to help build, manage, and deploy the PyZMODEM package.

## `make_aio.py` (All-In-One Generator)

The `make_aio.py` script automatically compiles the modular PyZMODEM package (located inside `rzsz/`) into standalone, single-file executables: `szaio.py` and `rzaio.py`.

Because Python modules typically require a folder structure to resolve imports, deploying PyZMODEM to a remote system (like an embedded device or an isolated SSH server) would normally require transferring the entire `rzsz/` directory. The AIO generator solves this by reading all the internal protocol files, intelligently stripping their relative `import` statements, and flattening the entire architecture into a single monolithic script.

### How to Generate
From the root directory of the repository, simply run:
```bash
python3 utils/make_aio.py
```
This will generate (or overwrite) `szaio.py` and `rzaio.py` in the root of the repository.

*Note: These generated files are excluded from source control via `.gitignore` to prevent duplicate code in the repository.*

### How to Use

Once generated, these scripts are 100% self-contained and only rely on the Python 3 standard library.

1. **Deploy:** Transfer `szaio.py` or `rzaio.py` to your remote system (e.g., via `scp`, `wget`, or copy-pasting the raw text).
2. **Execute:** Run them exactly as you would the standard tools.
   ```bash
   # On the remote server:
   ./szaio.py some_file.tar.gz
   ```
3. **Capture:** If you are running the `rz.py` PTY wrapper on your local machine, it will transparently detect the ZMODEM signature emitted by `szaio.py` and seamlessly download the file!
