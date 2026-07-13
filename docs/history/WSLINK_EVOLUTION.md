# Project PyZMODEM → WSLink: The Evolution of a Binary Protocol

**Project span:** May 27 – June 14, 2026  
**Sessions:** 55 | **Messages:** 3,920  
**Repository:** `Triitus_Hyperclient` (pyprotocols)  
**Outcome:** The WSLink binary protocol — backbone of all Unimind communication

---

## The Lineage

```
ZMODEM (1986, Chuck Forsberg)     HS/Link (1993, Samuel H. Smith)
   │  CRC32 framing                   │  Full-duplex bidirectional
   │  Crash recovery                   │  Sliding window ARQ
   │  Block-level transfer             │  Out-of-band chat channel
   └────────────┐              ┌───────┘
                │              │
                ▼              ▼
            ┌──────────────────────┐
            │   WSLink Protocol    │
            │   (May-June 2026)    │
            │                      │
            │  ZMODEM framing DNA  │
            │  + HS/Link bidir     │
            │  + BBR congestion    │
            │  + WebSocket clean   │
            │    pipe transport    │
            └──────────┬───────────┘
                       │
                       ▼
            ┌──────────────────────┐
            │   Unimind MCP Wire   │
            │   Protocol (Live)    │
            └──────────────────────┘
```

---

## Act I: The Genesis — "Python zmodem send/receive on Rocky9"

**Date:** May 27, 2026  
**Session:** 28 messages  
**The ask:** Transfer files between systems without installing packages.

The very first message was simple: *"Can I do zmodem file transfers using only Python standard library on Rocky 9?"*

The AI responded that while possible, the ZMODEM protocol is "complex and not something you'd typically implement from scratch." It suggested `pyzmodem` as a library.

The user pushed back: *"lets see what you can do without pyzmodem...."*

This challenge — build it from nothing — became the project's DNA. Two skeleton files were created (`rz.py` and `sz.py`) with the ZMODEM state machine represented as placeholder comments. The user then asked for a demo of a 1MB file transfer, and crucially, asked:

> "how would that work say.... if i wanted to rz on a local side of a ssh session and sz within the ssh session"

This question — cross-system file transfer over SSH — defined the entire architecture that followed. The protocol needed to survive terminal emulation, PTY allocation, and `sudo use_pty` corruption.

---

## Act II: The Wall — "sz hangs at B0"

**Date:** May 30, 2026  
**Session:** "sz hangs at B0 during PyZMODEM transfer" — 484 messages

The pure-Python ZMODEM implementation hit a hard wall. The symptom: `sz` would hang at the `B0` phase — the initial baud rate negotiation sequence. The problem was fundamental:

1. **PTY corruption:** `sudo`'s `Defaults use_pty` places a pseudo-terminal in the data path. ZMODEM needs an 8-bit clean pipe, but PTY drivers process control characters (XON/XOFF), translate `\n` to `\r\n`, and echo bytes before raw mode engages.

2. **Initialization race:** Between process start and `tcsetattr(raw_mode)`, the PTY driver applies default line discipline. If ZMODEM's `**\x18B00...` init sequence hits during this window, it's corrupted.

3. **Double-echo:** The PTY echoes input bytes back to the sender before raw mode, causing protocol desynchronization.

The `-e` (escape all control characters) workaround was documented, but it was a band-aid. The real insight was deeper:

> **ZMODEM was designed for direct serial lines, not multiplexed transport layers.**

The protocol's 1986 assumptions — that it owns the wire, that bytes flow unmodified — were violated by every modern system between the sender and receiver (SSH, PTY, terminal emulators, flow control).

---

## Act III: The Discovery — HS/Link

**Date:** May 30, 2026  
**Session:** "Research HS/Link protocol specification by Samuel H. Smith" — 83 messages  
**Session:** "hslink status update" — 360 messages

While fighting ZMODEM's limitations, research into BBS-era file transfer protocols led to Samuel H. Smith's **HS/Link** (1993). This was the breakthrough insight.

HS/Link solved problems ZMODEM never attempted:

| Feature | ZMODEM | HS/Link |
|---------|--------|---------|
| Direction | Half-duplex (send OR receive) | Full-duplex (simultaneous send AND receive) |
| Flow control | XON/XOFF software | Sliding window ARQ |
| Recovery | File-level restart | Block-level crash recovery |
| Side channel | None | Chat/status messages during transfer |
| Framing | DLE-escaped bytes | DLE-framed blocks with CRC |

The search for HS/Link's actual specification was itself epic — scouring textfiles.com, Simtel archives, CD-ROM indexes on archive.org, and BBS mirrors. The original `HSL121.ZIP` from Samuel H. Smith's "Tool Shop BBS" proved elusive, but enough documentation was found to understand the protocol's design philosophy:

> **Data and control travel on the same wire, distinguished by framing — not by separate channels.**

This was the architectural insight that would become WSLink's core principle.

---

## Act IV: The Birth — "All-in-one Python script"

**Date:** May 31 – June 1, 2026  
**Session:** 754 messages (the largest single session in the project)

The "All-in-one Python script for sz" session was where ZMODEM, HS/Link, and modern async Python fused into WSLink.

Key engineering decisions made in this marathon session:

### The Frame Format
```
[4-byte LE length L] [1-byte type] [payload] [4-byte LE CRC32]
L = 1 + len(payload) + 4
```

This is pure ZMODEM DNA — length-prefixed with CRC32 integrity — but running over a WebSocket clean pipe instead of a byte-stuffed serial line. No DLE escaping needed. No control character conflicts.

### The Packet Types (HS/Link heritage)
```
R (Ready)      Q (Ready_Recv)     — Handshake
O (Open_File)  C (Close_File)     — File lifecycle
D (Data_Block) A (Ack_Block)      — Transfer
N (Nak_Block)  S (Seek_Block)     — Error recovery
V (Verify)     K (Skip_File)      — Resume/dedup
H (Chat)       Z (Transmit_Done)  — Control
```

This is HS/Link's alphabet — bidirectional, windowed, with an out-of-band chat channel — but with modern semantics (64-bit file sizes, UTF-8 names, f64 timestamps).

### The Congestion Control (BBR-lite)
- Sliding window: starts at 16, max 256
- Window halved on timeout or NAK
- Growth when RTT < 100ms AND utilization > 80%
- Reduction to 90% when RTT > 500ms (bufferbloat)

This is neither ZMODEM (no congestion control) nor HS/Link (fixed window) — it's a simplified BBR model adapted for the characteristics of WebSocket-over-TCP.

### The `KeyboardInterrupt` Bug
A critical bug was found and fixed during this session: `Ctrl+C` would escape the pass-through loop and crash the PTY wrapper, killing the SSH session. The fix: trap `KeyboardInterrupt` inside the `select` loop and forward it as `\x03` to the child process.

---

## Act V: The Crossover — "Connect to unimind-mcp"

**Date:** June 3, 2026  
**Session:** 121 messages  
**The same day as Unimind's own genesis.**

On June 3, the file transfer protocol crossed over into something entirely different. The session "Connect to unimind-mcp and kick tires" marks the moment WSLink stopped being a file transfer tool and became an **AI communication protocol**.

The first connection attempt to the MCP server at `10.0.10.4` failed:
- Redis wasn't running (connection refused on port 6379)
- The environment was AlmaLinux, not Ubuntu
- `systemctl` didn't work (containerized environment)

The solution: SSH into the machine, manually install and start Redis, then verify the MCP server could reach it. This bootstrapping sequence — SSH → Redis → MCP → WSLink — established the deployment pattern that Unimind still uses.

The key architectural decision was that WSLink would carry **MCP tool calls and responses** — not files. The frame format designed for file blocks could carry JSON-RPC messages just as easily. The sliding window that prevented network congestion during file transfer now prevented context window exhaustion during AI deliberation.

---

## Act VI: The June 14 Explosion

**Date:** June 14, 2026  
**Session:** "Morning greeting and requirements review"  
**Scale:** 1,665 messages in "Unimind data security brainstorming" alone

June 14 was the most productive single day in the project's history. Five major things happened simultaneously:

### 1. The Security Council
A multi-model council (3 tracks: security deep-dive, correctness, performance) reviewed all three protocols:
- HS/Link: 20-message security fix session
- ZMODEM: 15-message security fix session  
- Cross-protocol: 14-message architecture session

### 2. Six Security Fixes Applied
From the `fix/wslink-error-handling` branch:
- Exception handling around `asyncio.gather` (task exceptions no longer kill sessions silently)
- Try/except in `_recv_loop` (bad packets don't terminate receive)
- Try/except in `_send_loop` (disk errors logged, session transitions cleanly)
- Stdlib `logging` replacing `syslog` for portability
- Dead code removal (`_open_next_file` sync method)
- Error propagation audit

### 3. The Rust Port Begins
Five parallel Rust subagent sessions:
- `Cargo.toml + lib.rs` skeleton
- CRC + file_safety modules
- Framer + transport traits
- WSLink protocol structs
- HSLink DLE framer impl
- ZMODEM ZDLE codec impl

### 4. Five Skills Born
The security cascade produced reusable skills:
- `security-cascade-fix` — systematic protocol hardening
- `rust-traits-first` — trait-based Rust protocol design
- `contextual-commit` — commits that carry reasoning
- `recall` — session context reconstruction
- `self-improvement` — continuous learning capture

### 5. The Council Architecture Formalized
The council deliberation pattern — dispatch → execute → evaluate → learn → retry — was refined and integrated into Unimind itself, running *over WSLink*.

---

## The Technical DNA

### What WSLink Inherited from ZMODEM (1986)
- **CRC32 frame integrity** — every frame is checksummed
- **Block-level transfer** — files divided into numbered blocks
- **Crash recovery** — resume from last-known-good block
- **File skip detection** — don't retransfer what already exists

### What WSLink Inherited from HS/Link (1993)
- **Full-duplex operation** — send and receive simultaneously
- **Sliding window ARQ** — multiple blocks in flight
- **Out-of-band chat** — control messages during transfer
- **Cumulative acknowledgment** — one ACK clears multiple blocks

### What WSLink Added (2026)
- **BBR-lite congestion control** — RTT-adaptive window sizing
- **WebSocket clean pipe** — no byte-stuffing, no escape sequences
- **Async Python** — `asyncio` event loops, non-blocking I/O
- **64-bit file sizes** — no 4GB limit
- **UTF-8 filenames** — international character support
- **Batch operations** — multiple files in a single session
- **MCP transport** — carries AI tool calls, not just file data

---

## Known Issues at End of Project

From production deployment in Unimind:

1. **Busy-wait anti-pattern:** `_send_loop` polls with `asyncio.sleep(0.01)`
2. **No backpressure:** When receiver is slower than sender
3. **Inaccurate RTT:** Cumulative ACKs confuse per-block timing
4. **Sequential verify:** Resume reads blocks sequentially (no parallelism)
5. **No keepalive:** Silent connection death undetected until ARQ timeout
6. **Not truly BBR:** No bandwidth estimation, purely delay-based
7. **Dead code:** `_open_next_file` sync never removed

These are documented as the starting point for the Rust rewrite.

---

## The Numbers

| Metric | Value |
|--------|-------|
| Total sessions | 55 |
| Total messages | 3,920 |
| Largest session | 754 msgs ("All-in-one Python script") |
| Timeline | May 27 – June 14 (19 days) |
| Languages | Python → Rust (in progress) |
| Files in final impl | 4 (framer.py, wslink.py, structs.py, const.py) |
| Lines of code | ~500 (Python), Rust port in progress |
| Security fixes | 6 (applied June 14) |
| Skills produced | 5 |
| Protocol packet types | 12 |

---

## The Story in One Sentence

A challenge to implement 1986 ZMODEM "without libraries" on Rocky Linux evolved through a PTY corruption wall, a discovery of 1993 HS/Link's bidirectional design, a marathon 754-message coding session, and a same-day crossover into AI communication — becoming the binary protocol that carries every thought between Unimind's distributed AI agents.

---

## Timeline

```
May 27  ──  "Can I do zmodem using only Python?"
            First rz.py/sz.py skeletons
            
May 30  ──  "sz hangs at B0" — ZMODEM hits the PTY wall
            HS/Link discovered (Samuel H. Smith, 1993)
            
May 31  ──  754-message marathon: WSLink is born
            Frame format, packet types, congestion control
            KeyboardInterrupt bug found and fixed
            
Jun  1  ──  Async rewrite (szaio.py, rzaio.py)
            BBR-lite congestion control added
            
Jun  3  ──  THE CROSSOVER: "Connect to unimind-mcp"
            WSLink becomes MCP transport protocol
            Same day as Unimind genesis
            
Jun  5  ──  Multi-agent skill born
            
Jun  8  ──  Production deployment in Unimind
            
Jun 14  ──  THE EXPLOSION:
            • Security council (3 tracks)
            • 6 bug fixes applied
            • Rust port begins (6 parallel tracks)
            • 5 skills created
            • Council architecture formalized
            • 1,665 messages in one session
```

---

*Mined from opencode.db on July 6, 2026. This document preserves the archaeological record of how a 1986 file transfer protocol became the nervous system of an AI knowledge platform.*
