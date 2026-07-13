# WSLink v2 — Architecture, Decisions & Reasoning

> Status: **Phase 1 IMPLEMENTED** (v1.1.0). The extension API, observability,
> capability-negotiation plumbing, and the safe correctness fixes have landed in
> code (Python + congruent Rust). Behaviour-changing capability *flips* (actually
> skipping CRC/ARQ) remain deferred and gated — see §3.3 / §8. Original review was
> docs-only; this header updated on merge to `main`.
> Origin branch: `fix/wslink-extension-api-observability`
> Scope: the WSLink protocol (`protocols/wslink/`, `crates/pyprotocols-core/`) and its
> primary consumer, the Unimind MCP router (`Skill_MultiAgent/unimind/mcp/router.py`).

This document captures a design review of WSLink and the resulting architecture
proposal. It records not just *what* to change but *why*, so the reasoning survives
the people who had it. Companion docs:

- [`WSLINK_EXTENSION_API.md`](./WSLINK_EXTENSION_API.md) — the concrete `register_packet_handler` /
  `subscribe` / `negotiated_capabilities` interfaces + the observability event schema.
- [`ROUTER_INTEGRATION.md`](./ROUTER_INTEGRATION.md) — how the router's current jury-rigging
  collapses under this design (before/after).

> **Citation convention.** Code references use `path:line`. Excerpts are verbatim
> from the tree at branch point (`42ab65f`), trimmed with `…` where noted. Paths are
> relative to `/opt/git/`. The canonical protocol lives in `protocols/wslink/`; the
> Rust core in `crates/pyprotocols-core/`; the consumer + vendored copies in
> `Skill_MultiAgent/unimind/mcp/`.

---

## 0. TL;DR

WSLink is a faithful, well-built modernization of a 1994 file-transfer protocol
(HS/Link → ZMODEM lineage). Its core reliability machinery — per-block ACK/NAK,
CRC32, and a sliding-window congestion controller — is exactly the set of
guarantees that its real transport (WebSocket → TLS → TCP) **already provides**.
Re-implementing them in the application layer is redundant on a clean pipe and is
the throughput ceiling standing between WSLink and its "wire speed" goal.

The proposal, in one sentence: **make reliability, integrity, and delivery
discipline into *negotiated transport capabilities* rather than always-on layers,
expose a clean extension + observability API so consumers stop monkeypatching
internals, and measure everything before optimizing.**

Nothing here is a flag-day break. Every change defaults to today's behavior and is
gated behind capability negotiation with graceful fallback.

---

## 1. What WSLink is, and where it came from

WSLink is the clean-pipe evolution of HS/Link (a 1994 bidirectional file-transfer
protocol for MS-DOS over serial/UART) and shares a code family with a modernized
ZMODEM. That heritage is the key to understanding both its strengths and its
mismatches:

- Over a **1994 UART / serial line**, the link is *noisy and unreliable*. ARQ
  (per-block ACK/NAK + retransmit), CRC integrity, and windowed congestion control
  are **essential** — there is no lower layer to provide them.
- Over a **modern WebSocket (`wss://` → TLS → TCP)**, the link is *reliable,
  ordered, and integrity-checked* by the layers beneath. The same machinery is now
  **vestigial**.

The framer is literally named the "Clean Pipe Framer," and the design notes call
WSLink a "clean-pipe evolution" — yet it runs the full noisy-line ARQ over that
clean pipe. That is the central tension this document resolves.

### 1.1 What is genuinely excellent (keep all of it)

- **The framer.** `[4-byte LE length][1-byte type][payload][4-byte CRC32]`, with a
  `MAX_FRAME_SIZE` OOM guard and write coalescing. Clean and correct.
- **Resume / crash recovery** (VERIFY/SEEK). Transport-independent, genuinely
  valuable, and — critically — *not* dependent on ARQ (see §3.2).
- **The Rust core strategy.** Accelerating the hot primitives (CRC via `crc32fast`
  SIMD, frame build/parse, packet pack/unpack, `LinkStatsTracker`) via PyO3 while
  leaving the session FSM in Python is the disciplined way to do a hot-path port.
  Release profile (LTO fat, `codegen-units=1`, abi3) is properly tuned.
- **The socket-proxy channel mux** (`socket_proxy/`). SSH-style per-channel,
  credit-based flow control (RFC 4254 §5.2), per-channel LZ4 + ChaCha20-Poly1305 /
  AES-256-GCM transforms, and real SSRF hardening (private-IP blocking, cloud
  metadata blocking, DNS-rebinding re-resolve, allowlist, Unix-path validation).
- **Security hardening throughout:** path traversal (realpath + prefix), frame
  length cap, VERIFY count cap, credit bounds, flow-control violation detection.
- **Institutional-memory comments.** The code explains *why* (the TRANSMIT_DONE/Z
  chat-mode race, the `add_files` wake-event deadlock, the selective-ACK
  reasoning). Rare and worth preserving.

---

## 2. The core critique: reliability over a reliable pipe

Three concrete consequences of running ARQ/CRC/congestion-control on top of TCP.

### 2.1 The application window is the wire-speed ceiling

Throughput ≤ `window_bytes / RTT`. The default window is `256 blocks × 4096 B = 1 MB`.

The defaults, `protocols/wslink/protocol/wslink.py:41-43`:

```python
self.block_size = kwargs.get('block_size', 4096)
self.window_size = kwargs.get('initial_window', 16)
self.max_window_size = kwargs.get('max_window', 256)
```

- On a 100 Gb link at 1 ms RTT, the bandwidth-delay product (BDP) is ~12.5 MB.
  A 1 MB window caps throughput at ~1 MB/ms = **8 Gb/s — under 10% of the wire**.
- The layer meant to *ensure* delivery is what *prevents* saturation.

**Lever, not architecture:** `MAX_BLOCK_SIZE` is already 64 KB in the Rust core.
Raising the *actual* `block_size` from 4096 → 65536 makes the same 256-window hold
**16 MB** in flight (~128 Gb/s at 1 ms). So the *default block size is a bigger
throughput limiter than the architecture is.*

**But the honest cost:** `unacked_blocks` holds a full copy of every in-flight
block. `256 × 128 KB = 32 MB per session`; across 256 sessions, **8 GB of RAM** for
retransmit buffers that a clean pipe never uses. That memory exists *only because of
ARQ*. See §3 for why the answer is to shed ARQ, not to compress the buffer.

The copy, `protocols/wslink/protocol/wslink.py:395` (inside `_pump_sender`):

```python
self.unacked_blocks[self.next_block_num] = payload   # full payload retained until ACK
```

Optimal block size is also link-dependent (a 128 KB atomic write is ~10 µs on
100 Gb but ~1 ms of head-of-line jitter on 1 Gb), so `block_size` should be
**negotiated at handshake**, not hardcoded.

### 2.2 Stacked congestion control fights TCP

The BBR-lite window reacts to RTT and "ARQ timeouts." But over TCP the application
never sees real loss — TCP hides it behind its own retransmit. So the app-level
controller shrinks its window in response to *TCP's* latency spikes/bufferbloat.
Two controllers oscillate against each other; net throughput is worse than either
alone. (Over serial this is correct; over TCP it is counterproductive.)

The controller, `protocols/wslink/protocol/wslink.py:423-430` (in `_update_rtt`):

```python
# BBR-style naive scale: if link is fast and window is full, increase window.
if avg_rtt < 0.1 and len(self.unacked_blocks) >= self.window_size * 0.8:
    self.window_size = min(self.max_window_size, self.window_size + 1)
    self._stats_window_grows += 1
elif avg_rtt > 0.5:
    # Bufferbloat detected, scale back gently
    self.window_size = max(1, int(self.window_size * 0.9))
```

`avg_rtt` here is measured *over TCP*, so "bufferbloat" is often TCP's own
retransmit latency — the app controller throttles in response to the layer below it.

### 2.3 CRC32 is redundant over authenticated/TLS transports

- Over `wss://`, TLS provides a cryptographic AEAD MAC (GCM/Poly1305) — *stronger*
  than CRC32. The CRC adds zero integrity and burns CPU (the CPU that caps pure
  Python near USB2 speeds).
- Over **plaintext** `ws://` / raw TCP, TCP's 16-bit checksum is genuinely too weak
  for large transfers (Stone & Partridge, *"When the CRC and TCP Checksum
  Disagree,"* SIGCOMM 2000 — errors slip past as often as ~1 in 16M–10B packets).
  Here CRC32 *does* real work.
- Over serial/PTY (ZMODEM/HS-Link), CRC is **mandatory** — no lower layer.

Conclusion: CRC integrity is a **transport property**, not a constant. See §3.3.

The wire CRC is computed unconditionally today. Python,
`protocols/wslink/protocol/framer.py:55-60`:

```python
def _build_frame(self, pkt_type: bytes, payload: bytes) -> bytes:
    """Build a complete wire frame (no I/O)."""
    data = pkt_type + payload
    crc = zlib.crc32(data) & 0xFFFFFFFF
    length = len(data) + 4
    return struct.pack('<I', length) + data + struct.pack('<I', crc)
```

Rust core, `crates/pyprotocols-core/src/protocols/wslink.rs:197` — SIMD, effectively
free (this is why the CPU rationale for dropping it evaporates; see §3.3):

```rust
let crc = crc32fast::hash(&frame[4..4 + data_len]);
```

---

## 3. Decisions

### 3.1 `TransportCapabilities` — reliability/integrity/ordering become negotiated

Introduce a capability object, negotiated once at handshake, that drives behavior:

```
TransportCapabilities:
    is_reliable      # transport guarantees delivery (TCP/wss/ssh) → skip ARQ
    provides_integrity  # transport authenticates data (TLS or channel AEAD) → skip wire CRC
    is_ordered       # transport guarantees ordering (TCP) → no reorder buffer needed
    max_block_size   # negotiated, BDP-aware
    ... (extensible)
```

The same flag resolves ARQ on/off, CRC on/off, buffer-or-not, and (future)
delivery mode. It makes the retro heritage **configurable rather than mandatory.**

### 3.2 Drop ARQ entirely; keep resume

**ARQ and resume are different machinery** and are frequently conflated:

- **ARQ** recovers *in-flight* loss *during* a live transfer (the `unacked_blocks`
  window, per-block ACK/NAK).
- **Resume** (VERIFY/SEEK) restarts an *interrupted* transfer by hashing what is
  already on disk and seeking the sender forward. It rebuilds state **from the
  file**, not from in-flight buffers.

Therefore you can **drop ARQ in all cases and keep 100% of resume.** Resume works
over a bare clean pipe: reconnect → receiver reports N verified blocks → sender
`seek`s → streams the rest → TCP carries the bytes.

Consequences:
- Reliable transport (TCP/wss/ssh): TCP handles in-flight loss; resume handles
  connection-drop; **no ARQ needed**. The `unacked_blocks` copy buffer disappears
  (fixes §2.1's 8 GB memory problem for free).
- Lossy datagram transport (UDP/WebRTC), *only if ever needed*: enable ARQ then —
  but the receiver must gain a real reorder buffer (see §5, "known bug") and fast
  NAK. Honestly, at that point adopt **QUIC** and get per-stream reliability,
  no-HOL, congestion control, and migration for free rather than hand-maintaining
  ARQ.

**Two CRCs, two fates:** the wire-frame CRC (§2.3) is droppable on TLS; the
**resume-content CRC** (VERIFY hashing on-disk chunks) is *always kept* — it
validates disk state from a prior session and is transport-independent.

The resume-content CRC, `protocols/wslink/protocol/wslink.py:548` (receiver hashing
its partial file) and `:623` (sender verifying) — note this is `zlib.crc32` over
*file chunks*, unrelated to the wire frame CRC:

```python
crc_val = zlib.crc32(chunk) & 0xFFFFFFFF        # :548 build VERIFY from disk
...
if (zlib.crc32(chunk) & 0xFFFFFFFF) == expected_crc:   # :623 confirm resume point
```

### 3.3 CRC becomes a negotiated capability, default ON

Per §2.3, CRC integrity is transport-dependent. But it is *not* a "safe win" to
drop, because it is embedded in the **wire format** shared by 6 hand-copied
implementations (see §4). Therefore:

| Transport / channel | Lower-layer integrity | CRC verdict |
|---|---|---|
| `wss://` (TLS) or AEAD channel (ChaCha20-Poly1305 / AES-GCM) | cryptographic MAC | redundant → *may* skip |
| plaintext `ws://` / raw TCP | TCP 16-bit checksum (too weak) | **keep** |
| serial / PTY | none | **mandatory** |

**Reversal from the naive "drop it on TLS" position:** once framing lives in Rust,
`crc32fast` is SIMD (tens of GB/s) — effectively free. The CPU rationale for
dropping evaporates; only redundancy remains, weighed against wire-compat risk
across 6 lockstep implementers carrying live MCP traffic. **Net decision: keep the
wire CRC default-ON; make it a negotiable capability that CAN be disabled only on
paths where it is measured to matter.** Default = today's behavior = zero compat
risk. (See §4 and companion "measure first" theme.)

### 3.4 Delivery modes: reliable-stream + deadline-drop

Reliable-ARQ and low-latency-isochronous are **opposite delivery disciplines**:

- **Reliable stream:** deliver everything, stall if needed. (File transfer, JSON-RPC.)
- **Deadline-drop:** deliver *on time or discard* — never stall. (USB3/display-class
  isochronous over routed links: a late frame is useless; you want the next fresh
  frame, not a stale retransmit. This is how RTP/media transports behave.)

You cannot serve isochronous traffic with the ARQ path that serves file transfer.
Latency-critical frames carry a **presentation deadline**; if measured one-way delay
(≈ RTT/2 + jitter margin) says a frame will miss it, **drop it** rather than
retransmit. This is the difference between *graceful degradation* and *catastrophic
timeout* when running demanding protocols over routed links.

The current FSM has no deadline concept — the `TRANSMIT_DONE (Z)` handler and its
chat-mode carve-out show it is tuned for reliable file/chat only,
`protocols/wslink/protocol/wslink.py:657-662`:

```python
elif pkt_type == PACK_TRANSMIT_DONE:
    log.info("Peer signaled all files transmitted (Z).")
    # NOTE: Do NOT transition to DONE here. The chat channel (used for
    # JSON-RPC MCP traffic in the Unimind router) must remain active ...
```

That `NOTE` is scar tissue from the chat-kill race that produced the router's
`WSLINK_DISABLED` escape hatch (§4, §8).

### 3.5 Timing-telemetry channel

A shared, in-protocol timing reference so inner protocols can degrade gracefully
instead of failing hard:

- Extend the heartbeat with a `T` (telemetry) frame carrying
  `{monotonic_ts, rtt_ewma, jitter, inflight_bytes, window, budget_state}`.
- Each peer maintains a `TimingView`; inner protocols read `session.timing()` as
  their shared reference clock.
- **Track deltas, not absolute clocks.** Do not attempt wall-clock sync (needs
  PTP/NTP). Graceful mitigation only needs *trends* (rising RTT/jitter), which are
  measurable relatively.
- **Use `time.monotonic()`** for all interval math. The current RTT uses
  `time.time()` (wall clock), which an NTP step can move backward → garbage/negative
  RTT. (Also listed as a bug in §5.)
- **Consequence of §3.2:** dropping ARQ removes the free RTT samples that block
  ACKs provided. The timing channel therefore needs its **own** timestamped probe
  (a lightweight data-plane marker, or the existing WS ping/pong).
- **Trust caveat:** telemetry must be high-priority / out-of-band, or it is
  HOL-blocked behind bulk data and goes stale exactly during congestion — the one
  moment it is needed. On a single-TCP mux this is a real limit (see §6).

### 3.6 Channel credit windows must be BDP-sized

The socket-proxy uses `CHANNEL_INITIAL_CREDIT = 65536` (64 KB). Per-channel
throughput ≤ `credit / RTT`:

- At 1 ms RTT: `64 KB / 1 ms ≈ 512 Mb/s` per channel until a WINDOW grant replenishes.
- At 0.2 ms LAN RTT: ~2.6 Gb/s.

A single channel therefore **cannot hit 10 Gb** on the initial credit — the same
BDP trap as §2.1, one layer up. For >1 Gb channels, make `CHANNEL_INITIAL_CREDIT`
**negotiable and BDP-sized (MBs, not 64 KB)**, and ensure WINDOW grants are large
and timely. 64 KB is a Mbps-class window; SSH-agent forwarding (the current live use)
is fine, fat channels are not.

### 3.7 Extension + observability API (the keystone)

The router integrates by **monkeypatching private internals**
(`wslink_session._handle_packet`, `.framer`) and branching on a `WSLINK_DISABLED`
env var. That coupling — not the protocol changes — is what makes every other change
risky. Therefore the **highest-leverage first move** is a clean seam:

```
session.register_packet_handler(pkt_type, handler)  # control — replaces the monkeypatch
session.subscribe(observer)                          # observability — protocol events
session.negotiated_capabilities                      # the §3.1 result, read post-handshake
session.get_link_stats()                             # already exists — just consume it
```

Detailed in [`WSLINK_EXTENSION_API.md`](./WSLINK_EXTENSION_API.md). "Make the change
easy, then make the easy change" — this seam is what lets every subsequent change be
a clean plug-in instead of a re-patch. It is also where the observability hooks live
(§7).

---

## 4. Wire compatibility & capability negotiation

**The CRC is in the wire format, and the wire format is duplicated across 6
hand-copied implementations:** canonical (`protocols/wslink/`), the two vendored
Unimind copies (`mcp`, `clients`), a docs copy, an `.opencode` copy, and a *seventh*
hand-rolled framer in `socket_proxy/proxy.py`. All parse `payload =
packet[1:-4]` and validate the trailing 4 bytes. Dropping the CRC on one side makes
the peer eat 4 bytes of real payload → every frame mis-parses. **This is a
wire-breaking change and must be negotiated.**

**The escape hatch:** the current handshake carries *no* version/capability field —
`READY`/`READY_RECV` send `b""`, and the handler *ignores the payload* (it only
transitions state). That lets us piggyback a capability blob backward-compatibly:

Sent with empty payload, `protocols/wslink/protocol/wslink.py:227-228`:

```python
await self.framer.send_packet_immediate(PACK_READY, b"")
await self.framer.send_packet_immediate(PACK_READY_RECV, b"")
```

Handled without ever reading the payload, `…/wslink.py:458-462` — so a capability
blob is safely ignored by old peers:

```python
if pkt_type in (PACK_READY, PACK_READY_RECV):
    if self.state == "INIT":
        log.info("Handshake sync complete. Connection established.")
        self.state = "TRANSFERRING"
        self._send_event.set()  # Wake sender to start transfer
```

- Old peer → sends `READY b""` → new peer sees empty → assumes **legacy: CRC ON, ARQ
  ON**.
- New peer → sends `READY {caps}` → old peer ignores payload → stays legacy.
- Only when **both** advertise a capability *and* the transport supports it does
  either side change behavior. Graceful fallback, no flag day.

**Consolidation:** the 6-copy duplication is itself the risk. The Rust
`ChannelMux` + framer should become the **single source of truth**; every other
copy imports it. Then a CRC toggle or block-size change is one edit, not six, and
the `socket_proxy` hand-roll is retired. Until then, any wire change must touch all
copies atomically.

The 7th (hand-rolled) framer,
`Skill_MultiAgent/unimind/mcp/wslink/socket_proxy/proxy.py:518-538` — same wire
format, independently re-implemented, must move in lockstep with the other six:

```python
"""Frame a packet with length, type, and CRC.
Format: [4-byte LE length][1-byte type][payload][4-byte LE CRC32]
...
crc = zlib.crc32(packet) & 0xFFFFFFFF          # :537
packet += struct.pack("<I", crc)
```

Ordering constraint (from the router): capability negotiation must complete *within
the WSLink handshake phase*, before MCP `initialize` and before any channel/proxy
traffic — which is exactly the sequence the router already enforces
(`router.py:397-399`).

---

## 5. Known correctness issues (independent of the architecture)

1. **Go-Back-N masquerading as Selective Repeat (latent bug).** The sender comments
   claim "Selective ACK" and delete individual acked blocks, but the receiver has
   **no reorder buffer** — it accepts only `block == recv_expected_block` and
   NAKs+discards anything higher. That is Go-Back-N. Over TCP this branch never
   executes (in-order guaranteed), so it is untested latent code that would
   misbehave the instant it ran over a lossy datagram transport — the exact case
   ARQ exists for. Fix when/if a datagram transport is added: give the receiver a
   real reorder buffer, or embrace GBN honestly and correct the comments + cumulative
   ACK.

   The sender *claims* selective repeat, `protocols/wslink/protocol/wslink.py:480-486`:

   ```python
   # Selective ACK: only clear the specific block acknowledged.
   # ... Cumulative clearing is UNSAFE because ...
   if ack_block in self.unacked_blocks:
       del self.unacked_blocks[ack_block]
   ```

   …but the receiver has no reorder buffer — it NAKs and *discards* anything ahead of
   `recv_expected_block`, `…/wslink.py:575-578` (that is Go-Back-N):

   ```python
   elif seq['block'] > self.recv_expected_block:
       log.warning(f"Out of order block ... Sending NAK.")
       nak_payload = SequencePacket.pack(seq['batch'], self.recv_expected_block)
       await self.framer.send_packet_immediate(PACK_NAK_BLOCK, nak_payload)
   ```
2. **`WebSocketTransport.read_exactly` reslices the buffer every read**
   (`self.buffer = self.buffer[n:]`) → O(n) copy per read, O(n²) under load. Fix
   regardless of everything else; it caps throughput today. Use an offset cursor /
   `memoryview` / deque of chunks. `protocols/wslink/websocket_transport.py:52-54`:

   ```python
   data = bytes(self.buffer[:n])
   self.buffer = self.buffer[n:]   # reallocates + copies the entire remaining buffer
   return data
   ```
3. **`mark_closed` is not loop-thread-safe** despite the docstring — uses
   `asyncio.ensure_future` from a sync/other-thread context; should be
   `loop.call_soon_threadsafe`.
4. **Immediate vs buffered write ordering.** `send_packet_immediate` bypasses
   `_write_buf`, so a control frame can hit the wire ahead of data frames queued
   before it. Benign for ACKs, latent hazard for order-sensitive traffic. One
   ordered egress queue removes the footgun.
5. **CRC failure → silent discard, no NAK.** On a truly lossy link, recovery waits
   for the 2 s ARQ timer instead of fast retransmit. (Moot over TCP; matters for
   datagram.)
6. **RTT uses wall clock (`time.time()`), not `time.monotonic()`** — NTP steps
   corrupt RTT samples. (See §3.5.)
7. **Unbounded receive buffer.** `WebSocketTransport.feed_data` grows `self.buffer`
   with no high-water mark → a fast peer can drive memory without limit. Add
   backpressure / a cap. `protocols/wslink/websocket_transport.py:20-24`:

   ```python
   async def feed_data(self, data: bytes):
       """Called externally when the WebSocket receives binary data."""
       async with self.cond:
           self.buffer.extend(data)   # no bound — grows as fast as the peer sends
           self.cond.notify_all()
   ```
8. **Blocking file I/O in the async loop.** `_pump_sender` calls
   `current_fd.read()` synchronously inside the event loop; at high throughput this
   stalls the loop. Use a thread executor / read-ahead.
9. **`batch_index` is `u8`** — wraps after 256 files in a long-lived session and can
   collide with a stale batch in ACK matching.
10. **`MAX_BLOCK_SIZE` const drift** — `socket_proxy/const.py` = 4096 vs Rust core =
    65536. Consolidate (see §4).

---

## 6. The head-of-line reality (single most important caveat)

The socket-proxy gives per-channel *credit* flow control, which buys **fairness and
backpressure** — but all channels are still multiplexed over **one** WebSocket = one
TCP connection. This faithfully inherits SSH multiplexing's most famous limitation:
a single lost segment stalls **every** channel behind it (TCP head-of-line), because
TCP delivers in order. Credits prevent app-layer buffer starvation; they do **not**
provide **latency isolation under loss.**

- On a **single-switch LAN** (near-zero loss — the stated primary scenario): fine.
  HOL is negligible.
- Over **routed links** (the demanding USB/DP-class use cases): HOL re-emerges and
  defeats the per-channel QoS. The real fixes are **QUIC** (independent per-stream
  delivery) or **N TCP connections** (OS scheduling + a shaper).

Write this down so nobody assumes the credits buy latency isolation they don't.

---

## 7. Observability (measure before optimizing)

The protocol already computes rich telemetry (`get_link_stats()`,
`LinkStatsTracker.snapshot()`), and the router **never reads it** — it keeps only
crude byte counters. Step zero is to *consume what exists*. Full hook catalog and
event schema in [`WSLINK_EXTENSION_API.md`](./WSLINK_EXTENSION_API.md) §Observability.

The rich source already exists — `protocols/wslink/protocol/wslink.py:91` — and
returns ~40 fields (RTT, window, in-flight, ARQ timeouts, CRC failures, error rate,
throughput, ETA):

```python
def get_link_stats(self) -> dict:
    """Query connection statistics: throughput, error rates, congestion state."""
```

The consumer has its own unrelated snapshot,
`Skill_MultiAgent/unimind/mcp/router.py:998` (`def health_snapshot`), and a grep of
`router.py` for `get_link_stats` returns **nothing** — the protocol's own telemetry
is computed every call and thrown away.

The strategic point: instrumentation is not just troubleshooting — it is the
**measurement instrument that de-risks every other change here**. You cannot
honestly justify dropping CRC, bumping block size, resizing the credit window, or
removing ARQ without before/after numbers:

- CRC-fail-rate = 0 over TLS → empirical proof the CRC is redundant (§3.3).
- Credit-stall count → proof the 64 KB window is the ceiling (§3.6).
- Window-oscillation traces → proof of stacked-CC fighting TCP (§2.2).
- Buffer high-water → justification for backpressure (§5.7).

**Instrument first, measure, then optimize with evidence.**

---

## 8. Roadmap / sequencing

Ordered so each step de-risks the next. Nothing here pushes; nothing breaks defaults.

0. **Consume existing telemetry.** Router pulls `get_link_stats()` on the heartbeat
   tick; fold into `router.py:998 health_snapshot()`. No protocol change.
1. **Extension + observability API** (§3.7): `register_packet_handler`, `subscribe`,
   `negotiated_capabilities`. Unblocks everything; removes the monkeypatch.
2. **`on_state_change` hook** — smallest hook, highest diagnostic value (would have
   caught the TRANSMIT_DONE/Z chat-kill race that produced `WSLINK_DISABLED`).
3. **Capability negotiation in `READY`** (§4), default = today's behavior.
4. **Fix chat-mode Z + drop/simplify ARQ** (§3.2); then retire `WSLINK_DISABLED`.
5. **Counters:** CRC-fail-rate, credit-stall — the evidence base for the toggles.
6. **Negotiated `block_size` (default → 64 KB)** and **BDP-sized channel credit**
   (§2.1, §3.6).
7. **`monotonic()` clock**, **`read_exactly` fix**, **`feed_data` backpressure**
   (§5) — pure-upside correctness/perf, independent of the above.
8. **Timing-telemetry channel + delivery modes** (§3.4, §3.5).
9. **Consolidate framing on the Rust core** (§4); retire the 6 copies.
10. **(If routed-link demand is real) QUIC transport** for true per-stream delivery
    (§6).

---

## 9. Litmus test

A good protocol change should let the consumer **delete** glue, not write more. The
estimated diff to `router.py` under this design is **net-negative lines** — the
extension API deletes the monkeypatch wrapper, and fixing the Z race deletes the
`WSLINK_DISABLED` path. If any proposed change here required *more* router glue, it
would be wrong. See [`ROUTER_INTEGRATION.md`](./ROUTER_INTEGRATION.md).
