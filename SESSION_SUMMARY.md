# Session State: pyretroprotocols (formerly pyzmodem)

## Current Status
- **Date:** May 31, 2026
- **Current Branch:** `feature/wslink`
- **Legacy Branch:** `legacy_protocol/hslink` (Renamed from `feature/hslink`)

## Accomplishments This Session
1. **Legacy HS/Link Finalization**: 
   - Completed a pure Python, socket/pipe-compatible implementation of the 1994 bidirectional HS/Link protocol.
   - Tested and verified Crash Recovery (V/S packets), File Skipping (K packets), and selective ARQ over noisy pipes.
   - Moved all legacy code and MS-DOS specs to `protocols/hslink/`.

2. **WS/Link Architecture & Implementation**:
   - Outlined and fully implemented **WS/Link**—a modernized, clean-pipe evolution of HS/Link designed for `asyncio`, WebSockets, and SSH.
   - **Struct Upgrades**: Eliminated 16-bit MS-DOS limits. WS/Link now uses 64-bit file sizes (Exabytes), 32-bit block numbers (16TB max at 4KB MTU), 64-bit IEEE 754 floats for timestamps, and dynamic UTF-8 length-prefixed filenames.
   - **Framer**: Replaced `DLE` UART byte-stuffing with a fast `[4-byte Length][Type][Payload][CRC32]` clean-pipe frame.
   - **Congestion Control**: Built a dynamic BBR-lite sliding window into the `_pump_sender()` loop that tracks RTT to organically swell bandwidth and aggressively throttle on ARQ timeouts.
   - **Configurability**: Purged all magic numbers. `block_size` (MTU alignment), `window_size`, `arq_timeout`, and chunking limits are all completely configurable via `**kwargs`.
   - **Chaos Testing**: Passed 5% drop / 5% bit-flip chaos simulations with flying colors, reconstructing perfect MD5 payloads over flaky connections.

## Next Steps for the User
1. **Rename the Repository**:
   - Go to GitHub -> Settings -> Rename to `pyretroprotocols`.
   - Locally run:
     ```bash
     cd ../
     mv pyzmodem pyretroprotocols
     cd pyretroprotocols
     git remote set-url origin <new-github-url>
     ```
2. **Push Branches**:
   - `git push -u origin legacy_protocol/hslink`
   - `git push -u origin feature/wslink`
3. **Integration**:
   - Hook `protocols.wslink.transport.AsyncStreamTransport` up to a FastAPI WebSocket endpoint or `asyncssh` server to test real-world tunneling!
---

## WSLink Bug Fix Required (2026-06-14)

**Bug:** TRANSMIT_DONE (Z) sent when `batch_index == 0` (no files transferred) kills
the peer's WSLink session. Chat channel dies because the receiver transitions to DONE
on receiving Z, even though no file transfer occurred.

**Root cause:** Security patch changed sender from `if batch_index > 0: send Z` to
`if not _sent_z: send Z` (unconditional). This breaks chat-only sessions (MCP router).

**Fix needed in protocols/ repo:**
1. `protocols/wslink/protocol/wslink.py` — sender: only send Z when `batch_index > 0`
2. `protocols/wslink/protocol/wslink.py` — receiver: don't transition to DONE on Z
   (or add `keep_alive` parameter that prevents DONE transition)

**Fix already applied in:** `Skill_Multiagent/unimind/clients/common/wslink/` and
`Skill_Multiagent/unimind/mcp/wslink/` (commit ee22bea)

**Also relevant for Rust rewrite:** The Rust WSLink implementation must NOT send Z
in chat-only mode. Add a `ChatMode` vs `FileTransferMode` enum to make this explicit.
