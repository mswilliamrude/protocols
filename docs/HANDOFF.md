# Handoff / Checkpoint — WSLink v1.1.0 (2026-07-13)

Durable session checkpoint. (Written as a repo file because the Unimind MCP
checkpoint tool was unavailable at write time — this is the cross-agent-durable
equivalent.)

## Shipped to `main` (tag `v1.1.0`, merge `97beee6`)

Two work streams merged:

1. **Socket Proxy stack** (concurrent agent) — ChannelMux + credit flow control,
   target handlers, connection pool, per-channel LZ4 + ChaCha20-Poly1305 /
   AES-256-GCM, SSRF hardening (private-IP/metadata block, DNS-rebind re-resolve),
   session-unique nonces, sanitized errors, Rust `channel.rs`; docs
   (`WSLINK_ARCHITECTURE.md`, `SECURITY.md`, `RFC-SOCKET-PROXY.md`, `WSLINK_EVOLUTION.md`).
2. **WSLink v2** (this session) — extension API (`register_packet_handler` /
   `subscribe` / `send()` / `negotiated_capabilities`), observability
   (`events.py`), capability negotiation (`capabilities.py`, opt-in/default-off),
   observable state machine (`_set_state`), safe fixes (O(1) `read_exactly`,
   `feed_data` backpressure, monotonic clock, thread-safe close); congruent Rust
   (`capabilities.rs`, `events.rs`), byte-parity verified. Design docs under
   `docs/design/WSLINK_V2_*` + `CHANGELOG.md`.

Verified: Rust lib builds; Python + integration + byte-parity green.

## ⚠️ KNOWN ISSUE — for the socket-proxy author (option-1 handoff, untouched)

`crates/pyprotocols-core/src/channel.rs` **unit tests do not compile** — 20 errors,
signature drift: the `#[cfg(test)]` calls invoke methods (e.g. `handle_close`,
`handle_data`) **without the `py: Python<'_>` argument** the methods now require.
- Shipping library builds fine (`cargo build --lib` OK).
- `cargo test --lib` is RED until fixed.
- Pre-existing on `origin/main` (byte-identical to origin); NOT introduced by the
  v2 merge.
- **Fix:** wrap the affected test bodies in `Python::with_gil(|py| { ... })` and
  pass `py` to the mux methods.

## Deferred (gated / cross-repo — "measure first")

- **CRC/ARQ behavior flips** via negotiated caps — plumbing only today; flip behind
  the measurement counters (CRC-fail=0 over TLS proves redundancy; credit-stall
  proves the 64 KB window ceiling). See `WSLINK_V2_ARCHITECTURE.md §3.3, §8`.
- **Router de-hacking** — extension API is live in the protocol; the router
  monkeypatch rewrite + vendored-copy propagation live in `Skill_MultiAgent`
  (`ROUTER_INTEGRATION.md §7`).
- **TRANSMIT_DONE/Z chat-kill fix** — now *observable* via `state_change`, but the
  fix + retiring `WSLINK_DISABLED` is roadmap step 4.
- **maturin packaging** — `pyproject.toml` `python-source = "python"` points at a
  missing dir; `maturin build` fails until removed/created (cargo unaffected).
- **HOL blocking** — per-channel credits buy fairness, not latency isolation under
  loss over a single TCP mux; QUIC / N-connections is the routed-link answer.

## Library entries persisted this session (Unimind)

Lessons (pending validation): transport-layering / don't-duplicate-TCP;
API-change-lets-consumer-delete-glue; instrument-before-optimize; preserve-behavior
+ fail-safe wire negotiation; write-the-cross-peer-integration-harness-early.

Council skills: **Transport Layering Analysis** (architect), **PyO3 Python-Rust
Congruence** (pragmatist).
