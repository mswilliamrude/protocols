# WS/Link: Next-Generation Transfer Protocol Architecture

WS/Link is an evolution of the 1994 HS/Link protocol. It takes the timeless concepts of the original (bidirectional transfer, multiplexing, sliding-window block sequence state machines, and resumability) and adapts them for 2026 clean-pipe networks like WebSockets, SSH tunnels, and raw TCP sockets.

## What changes from HS/Link?

### 1. The Framer (Length-Prefixed Clean Pipe)
**Old (HS/Link):** Relied on computational heavy UART byte-stuffing (`0x02` START, `0x1B` END, and `0x1E` DLE escaping) to prevent `XON`/`XOFF` control characters from dropping the serial line.
**New (WS/Link):** WebSockets are "8-bit clean" and self-framing. However, to support raw TCP and SSH interactive streams, we still need a framer. We replace byte-stuffing with a standard 4-byte length prefix:
`[4-byte Length L] [1-byte Packet Type] [L-1 bytes Payload] [4-byte CRC32]`
*Effort: Moderate deletion of the old `HSLinkFramer`, replacing it with a simple binary length-reader.*

### 2. Struct Upgrades (Modern Limitations)
The original `structs.py` was bound by MS-DOS 16-bit C compiler limits. WS/Link upgrades the memory layout for modern capacity:
- **File Sizes:** Upgraded from 32-bit `long` (2GB limit) to 64-bit unsigned `Q`. Supports Exabyte transfers.
- **Block Numbers:** Upgraded from 16-bit `short` to 32-bit unsigned `I`. At 4KB blocks, this pushes the max file size limit from 268MB to 16TB.
- **Timestamps:** Removed the crazy MS-DOS bit-shifting logic. Upgraded to a standard 64-bit IEEE 754 UNIX Epoch float (in milliseconds).
- **Filenames:** Removed the fixed 13-byte `char[13]` 8.3 MS-DOS limit. Upgraded to a dynamic length-prefixed UTF-8 string, filling the remainder of the Open (`O`) packet payload.
*Effort: Moderate edits to `structs.py` and deleting `tools.py`.*

### 3. Packet Type Simplifications
- **Drop `E` (Map+Data) & `F` (Data Only):** In HS/Link, these optimization blocks dropped Sequence Numbers to save 8 bytes per packet, which caused catastrophic shifts if a packet dropped in noise testing. WS/Link uses Type `D` (Seq+Data) exclusively to guarantee strict sequencing.
- **Drop Control Mapping:** WebSockets and SSH don't eat control characters. The Control Mapping header is removed.
- **Drop `M` (ExtNAK):** Legacy UART diagnostic fields (`errlsr` line status and `errcsip` memory pointers) are entirely removed from NAK packets.
*Effort: High deletion, Low friction.*

### 4. Bandwidth Shaping & Congestion Control
**Old (HS/Link):** Kept `window_size` static and assumed standard 14.4k / 56k baud rates.
**New (WS/Link):** WebSockets hide TCP window exhaustion from Python. To implement explicit bandwidth control and bufferbloat management:
- An RTT (Round Trip Time) monitor will be added to the `_pump_sender()` loop. It measures the time between sending a `D` block and receiving an `A` (ACK) block.
- **BBR-style logic:** If RTT spikes, `window_size` is halved to throttle bandwidth. If ACKs return instantly, `window_size` increments up to a max cap.
*Effort: Medium addition to the `_pump_sender` ARQ logic.*

### 5. Transport Abstraction (Asyncio)
The cooperative `select()` polling of `StdioTransport` will be replaced with native Python `asyncio` base classes:
- `AsyncWebSocketTransport` (For `fastapi` or `websockets` integrations).
- `AsyncStreamTransport` (For `asyncio.start_server` TCP or `asyncssh` interactive tunnels).
*Effort: New Transport implementations, minor `await` scattering in the core state machine.*

## Conclusion
By deleting the 1994 hardware cruft but preserving the genius sliding-window sequence logic, WS/Link emerges as a lightweight, error-correcting, state-resumable, multiplexed data tunnel that outperforms raw TCP streams for application-layer reliability.
