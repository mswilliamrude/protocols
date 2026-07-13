# Changelog

All notable changes to **pyretroprotocols** / `pyprotocols-core` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/). Versions map to git tags.

## [1.1.0] — 2026-07-13

WSLink v2, Phase 1: a supported extension seam, protocol observability, and
capability-negotiation plumbing — plus safe correctness/efficiency fixes. **Every
change defaults to prior behaviour; nothing is wire-breaking.** Python and the Rust
core (`pyprotocols_core`) are kept congruent (byte-identical capability wire form,
verified by a parity harness).

### Added
- **Extension API** (`WSLinkSession`): `register_packet_handler()` /
  `unregister_packet_handler()` (dispatches consumer packet types; refuses to shadow
  the 14 builtin types without `override=True`), `subscribe()` / `unsubscribe()` for
  protocol-event observation, and public `send(pkt_type, payload)` egress. This is the
  supported replacement for consumers monkeypatching `_handle_packet` / reaching into
  `.framer`.
- **Observability** (`protocols/wslink/protocol/events.py`): `EventKind`,
  `ProtocolEvent`, `SessionObserver` (ABC), `CallbackObserver`, and `Subscription`
  (per-observer frame sampling). Events emitted: `state_change`, `congestion`
  (arq_timeout / nak / window grow-shrink), `rtt_sample`, `integrity`, `transfer`.
  Zero-cost when no observer is subscribed; observer exceptions are isolated.
- **Capability negotiation** (`protocols/wslink/protocol/capabilities.py`):
  `TransportCapabilities` with `encode`/`decode`/`negotiate`, advertised in the
  `READY` handshake payload. **Opt-in (`advertise_capabilities=False` default) so the
  wire stays byte-identical to legacy peers**, which ignore the payload. Negotiation
  is fail-safe: relaxations (skip ARQ / CRC / reorder) require *both* peers (AND);
  `wire_crc` uses OR (stays on unless both opt out); `max_block_size` is `min(both)`.
- **Observable state machine**: `WSLinkSession.state` is now a read-only property;
  all transitions go through `_set_state(new, reason)` and emit `state_change` (the
  hook that would have surfaced the historical TRANSMIT_DONE/Z chat-kill race).
- **Rust core** (`pyprotocols_core`): `TransportCapabilities` PyO3 class
  (`src/capabilities.rs`) with a byte-identical wire encoder/decoder to Python, and
  `CAP_*` / `EVENT_*` parity constants (`src/events.rs`). 3 new unit tests + a
  cross-language byte-parity example (`examples/parity.rs`).
- **Design docs** under `docs/design/`: `WSLINK_V2_ARCHITECTURE.md`,
  `WSLINK_EXTENSION_API.md`, `ROUTER_INTEGRATION.md` (all claims cited by `path:line`).

### Changed
- `WebSocketTransport.read_exactly` now uses a read cursor with periodic compaction
  instead of `self.buffer = self.buffer[n:]` — **O(n²) under load → amortised O(1)**.
- `WebSocketTransport.feed_data` applies real backpressure (blocks at a high-water
  mark, default 128 MB, clamped ≥ 2× `MAX_FRAME_SIZE`) instead of unbounded growth.
- All interval/RTT math uses `time.monotonic()` (was wall-clock `time.time()`, which
  an NTP step could move backward → garbage RTT). Wall clock retained only for file
  mtime.

### Fixed
- `WebSocketTransport.mark_closed` is now loop-thread-safe
  (`loop.call_soon_threadsafe`, was `asyncio.ensure_future`).

### Security
- Capability wire-parsing is length- and magic-checked with no unbounded
  allocation/loops; malformed payloads fall back to legacy. A hostile peer cannot
  weaken integrity/reliability or force larger blocks (AND-gated relaxations,
  OR-gated CRC, min-gated block size). Reviewed by multi-model council (approved).

### Deferred (intentionally not in this release)
- Behaviour *flips* driven by negotiated capabilities (actually skipping the wire
  CRC / ARQ) — plumbing only for now; flips are gated and will land with measurement
  counters ("measure first").
- Propagation of the extension API into the vendored copies and the router
  monkeypatch rewrite (cross-repo, `Skill_MultiAgent`) — see
  `docs/design/ROUTER_INTEGRATION.md §7`.

## [1.0.0] — 2026 (tag `v1.0`)

- Security-hardened protocols (WSLink, HSLink, ZMODEM): path-traversal prevention,
  frame-length caps, VERIFY count caps, selective-ACK bookkeeping.
- Introduced the Rust/PyO3 core `pyprotocols-core` (SIMD CRC, framers, packet structs,
  `LinkStatsTracker`), with a pure-Python fallback pattern.
- WSLink heartbeat keepalive, connection-lifecycle fixes, chat-mode handling, and the
  `WebSocketTransport` stream-over-message adapter.

## [0.9] — 2026 (tag `v0.9`)

- Initial modernization of the retro file-transfer protocols: HS/Link (1994)
  finalized in pure Python; WSLink clean-pipe framer (`[len][type][payload][CRC32]`),
  64-bit sizes, BBR-lite sliding window, configurable via `**kwargs`; chaos-tested
  under 5% drop / 5% bit-flip.

[1.1.0]: https://github.com/mswilliamrude/protocols/releases/tag/v1.1.0
[1.0.0]: https://github.com/mswilliamrude/protocols/releases/tag/v1.0
[0.9]: https://github.com/mswilliamrude/protocols/releases/tag/v0.9
