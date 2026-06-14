# PyZMODEM

A pure-Python 3 implementation of the ZMODEM file transfer protocol, designed for modern terminal wrapper workflows (like iTerm2/SecureCRT's ZMODEM integration) without requiring compiled C binaries (like `lrzsz`).

## Goal & Intent

The primary intent of this project is to provide a fully functional, cross-platform ZMODEM sender and receiver that can operate seamlessly over SSH or serial connections. Because it is written in pure Python, it can run on Linux, MSYS2, Git for Windows, macOS, or any environment with Python 3, without needing to compile C extensions.

The ultimate workflow looks like this:
1. **Wrap your session:** Execute `rz.py --directory ~/Downloads -- ssh user@remotehost` on your local machine. This spawns a PTY (pseudo-terminal) wrapper that actively snoops the terminal stream.
2. **Download from remote:** On the remote host, execute `sz.py ~/Documents/file.tgz`. The local `rz.py` wrapper detects the ZMODEM signature (`**B01`), pauses stdout, and seamlessly captures the file into your local `~/Downloads` folder while rendering a beautiful progress bar (showing KB/s, CRC errors, and retransmits).
3. **Upload to remote:** On the remote host, execute `rz.py --request file.txt`. The local `rz.py` wrapper detects the upload request and automatically sends `file.txt` from your local machine to the remote host.

## New Features

- **Transparent ZLIB Compression**: Pass `-c` or `--compress` to `sz.py` and `rz.py` to enable inline ZLIB stream compression. If both sides agree, data chunks are compressed on-the-fly, vastly speeding up text file transfers over slow serial lines or SSH links. Automatically resyncs dictionary state upon mid-stream CRC errors.
- **Forced Overwrites**: Pass `-y` or `--overwrite` to `sz.py` to instruct the receiver to clobber any existing destination files, bypassing the standard ZMODEM crash-recovery resumption mechanism.
- **Robust Cancellation**: Press `Ctrl+C` (in wrapper mode) or `Ctrl+X` 5 times to abort a transfer cleanly. Both sides will exchange `CAN` (`0x18`) bytes and gracefully exit without leaving the terminal in a deadlocked state.
- **Debug Logging**: Pass `--debug` to write detailed packet negotiation logs to `/tmp/pyzmodem_rz_debug.log` and `/tmp/pyzmodem_sz_debug.log` without corrupting the `stdout` binary stream.

## Development Checklist

- [x] Understand ZMODEM send protocol mechanics (ZRQINIT, ZRINIT, ZFILE, ZRPOS, ZDATA, ZEOF, ZFIN) and CRC16/CRC32 calculation.
- [x] Patch the `modem` module's `ZMODEM` class to include functional `send()` and `_send_16_data()` methods.
- [x] Fix Python 3 type mismatch issues (`TypeError: unsupported operand type(s) for ^: 'int' and 'str'`) in the CRC16/CRC32 byte calculations.
- [x] Ensure `ZDATA` file chunks correctly use ZDLE escaping for control characters (`XON`, `XOFF`, `ZDLE`) to prevent SSH/TTY corruption.
- [x] Update `sz.py` (sender) to emit the standard `rz**B01...` signature block so the receiver can auto-detect the incoming transfer.
- [x] Redesign `rz.py` into a PTY wrapper (e.g., `rz.py -- ssh user@host`) so it can actively snoop the terminal stream, pause stdout, and take over the session.
- [x] Add a rich UI to `rz.py` featuring a progress bar, KB/s rate, CRC error counters, and retransmit indicators.
- [x] Implement ZMODEM reverse-request capability (sz/rz asking the local wrapper to UPLOAD a file to the remote host).
- [x] Verify bidirectional transfers (upload and download) work seamlessly over the PTY wrapper against both Python and C (`lrzsz`) clients.
- [x] Clean up the `modem` package to be a standalone, robust Python 3 library.
