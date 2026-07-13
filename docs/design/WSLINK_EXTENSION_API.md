# WSLink v2 — Extension & Observability API

> Status: **IMPLEMENTED** (v1.1.0) — `register_packet_handler`, `subscribe`,
> `negotiated_capabilities`, `send()`, and the event schema below are live in
> `protocols/wslink/protocol/{wslink,events,capabilities}.py` with congruent Rust
> in `crates/pyprotocols-core/src/{capabilities,events}.rs`.
> Companion to [`WSLINK_V2_ARCHITECTURE.md`](./WSLINK_V2_ARCHITECTURE.md) (§3.7, §7).
> Citation convention: `path:line`, verbatim excerpts trimmed with `…`, paths
> relative to `/opt/git/`, tree at branch point `42ab65f`.

This document specifies the single seam that (a) replaces the router's monkeypatch
with a supported extension point and (b) opens the protocol's internal decisions for
observation. Control and observability share one interface because they attach at the
same place.

---

## 1. Why this exists — the coupling it removes

The router today reaches into WSLink's **private internals** to add behavior. It wraps
the private `_handle_packet` at runtime and reassigns it,
`Skill_MultiAgent/unimind/mcp/router.py:319-325`:

```python
_original_handle = self.wslink_session._handle_packet
async def _handle_with_proxy(pkt_type, payload):
    if is_socket_proxy_packet(pkt_type):
        await self.proxy_client.handle_packet(pkt_type, payload)
    else:
        await _original_handle(pkt_type, payload)
self.wslink_session._handle_packet = _handle_with_proxy
```

It also grabs the framer via `set_wslink_session()` (`router.py:327`) so the proxy can
frame its own packets. Both are private-surface coupling: any refactor of the session
silently breaks the proxy. The fix is to make these first-class:

```
session.register_packet_handler(pkt_type, handler)   # control
session.subscribe(observer)                           # observability
session.negotiated_capabilities                       # §3.1 result, read post-handshake
session.send(pkt_type, payload)                       # supported egress (no framer reach-through)
session.get_link_stats()                              # already exists — consume it
```

---

## 2. Control API — `register_packet_handler`

### 2.1 Interface

```python
class WSLinkSession:
    def register_packet_handler(
        self,
        pkt_type: bytes,          # single-byte type, e.g. b's' (socket-open)
        handler: Callable[[bytes, bytes], Awaitable[None]],  # (pkt_type, payload) -> await
        *,
        override: bool = False,   # replace a builtin handler (default: refuse collision)
    ) -> None: ...

    def unregister_packet_handler(self, pkt_type: bytes) -> None: ...
```

### 2.2 Dispatch contract

`_handle_packet` becomes a thin dispatcher:

1. Control/keepalive types (`PING`/`PONG`/`CHAT`) handled first, as today
   (`protocols/wslink/protocol/wslink.py:434-455`).
2. **Registered handlers** consulted next — this is where the socket-proxy's lowercase
   types (`s d c e w`, `socket_proxy/const.py:19-23`) attach *without wrapping the FSM*.
3. Builtin file-transfer types (`O D A N …`) fall through to the existing state machine.

Registration refuses to shadow a builtin unless `override=True`, preventing the
"selective ACK vs receiver" class of silent surprise.

### 2.3 What the router deletes

The 7-line monkeypatch block above collapses to:

```python
self.proxy_client = ProxyClient(send_fn=self.wslink_session.send, logger=self.logger)
for t in (b's', b'd', b'c', b'e', b'w'):
    self.wslink_session.register_packet_handler(t, self.proxy_client.handle_packet)
```

No `_handle_packet` reassignment, no `_original_handle` capture, no `.framer`
reach-through. See [`ROUTER_INTEGRATION.md`](./ROUTER_INTEGRATION.md) for the full diff.

---

## 3. Capability API — `negotiated_capabilities`

Negotiation piggybacks on the currently-empty `READY` payload
(`…/wslink.py:227-228`, handled without reading payload at `…/wslink.py:458-462` — see
architecture §4). After the handshake completes, the result is a read-only property:

```python
@dataclass(frozen=True)
class TransportCapabilities:
    is_reliable: bool          # skip ARQ when True
    provides_integrity: bool   # skip wire CRC when True (TLS or channel AEAD)
    is_ordered: bool           # no reorder buffer needed when True
    max_block_size: int        # negotiated, BDP-aware
    wire_crc: bool             # effective CRC state after negotiation (default True)
    version: int               # protocol version (0 = legacy/no caps advertised)

session.negotiated_capabilities  # -> TransportCapabilities
```

**Backward-compatible defaults:** a peer that sends empty `READY` (legacy) yields
`version=0, wire_crc=True, is_reliable=…` derived conservatively → today's behavior.
A capability is only *acted on* when both peers advertise it AND the transport
supports it.

**Subsumes the env-var hack.** The router's `WSLINK_DISABLED`
(`router.py:141`, `:300`, `:335`) becomes one capability profile ("plaintext/no-mux")
rather than a global branch — with plain-text mode preserved as the guaranteed-safe
fallback the team already relies on.

---

## 4. Observability API — `subscribe`

### 4.1 Design constraints (non-negotiable)

- **Zero-cost when off.** At 10 Gb, a per-frame callback that always fires becomes the
  bottleneck. Always-on layer = integer counter increments only; rich callbacks are
  **opt-in and sampled**. Mirrors Unimind's interoception/thinking toggles.
- **Counters vs events.** Counters are cheap and always available via `get_link_stats()`
  (already computed, `…/wslink.py:91`). Events (`on_*`) are for debug/troubleshooting
  mode and may be sampled (every Nth frame).
- **Never block the data path.** Observers run best-effort; an observer exception is
  logged and swallowed, never propagated into the session loop.

### 4.2 Interface

```python
class SessionObserver(Protocol):
    def on_event(self, event: "ProtocolEvent") -> None: ...   # sync, non-blocking, best-effort

class WSLinkSession:
    def subscribe(self, observer: SessionObserver, *,
                  sample_rate: int = 1,        # 1 = every event; N = every Nth frame-level event
                  level: str = "info") -> None: ...
    def unsubscribe(self, observer: SessionObserver) -> None: ...
```

### 4.3 Event schema

```python
@dataclass(frozen=True)
class ProtocolEvent:
    kind: str          # see table below
    ts_monotonic: float
    session_id: str
    payload: dict      # kind-specific (schemas below)
```

| `kind` | When | Payload | Diagnoses | Source today |
|---|---|---|---|---|
| `state_change` | FSM transition | `{old, new, reason}` | the TRANSMIT_DONE/Z chat-kill race | `…/wslink.py:461` sets `TRANSFERRING`; `:657-662` Z handling |
| `congestion` | window grows/shrinks, ARQ timeout, NAK | `{event, window_before, window_after, rtt_ms, reason}` | stacked-CC oscillation vs TCP (arch §2.2) | `…/wslink.py:423-430`, `:372-382` |
| `rtt_sample` | each RTT measurement | `{rtt_ms, jitter_ms, inflight, window}` | jitter trend → deadline-drop (arch §3.4/§3.5) | `…/wslink.py:418` `_update_rtt` |
| `frame` (sampled) | per frame in/out | `{dir, type, size}` | type histogram, size distribution | `framer.py:26-53`, `:62-71` |
| `integrity` | CRC/length failure | `{reason, expected, actual}` | **CRC-fail=0 over TLS proves redundancy** (arch §3.3) | `framer.py:49-51` returns `b'?'`; counted at `…/wslink.py:286-289` |
| `buffer` (sampled) | recv/send buffer depth | `{recv_bytes, send_bytes, recv_hwm}` | unbounded-buffer risk (arch §5.7) | `websocket_transport.py:20-24`, framer `_write_buf` |
| `channel` | open/close/credit-stall/flow-violation | `{channel_id, event, credit, target?}` | **credit-stall proves 64 KB ceiling** (arch §3.6); SSRF blocks | `socket_proxy/channel.py` credit logic; `const.py:52` |
| `transfer` | file open/close/skip/resume | `{file, blocks, resumed_at?}` | resume correctness | `…/wslink.py:508-559` (VERIFY/SEEK) |

### 4.4 The `state_change` example in detail

The single highest-value hook. The FSM sets state in-place with no notification —
`protocols/wslink/protocol/wslink.py:461`:

```python
self.state = "TRANSFERRING"
```

Under this API that becomes `self._set_state("TRANSFERRING", reason="handshake")`, and
`_set_state` emits `state_change`. Had this existed, the chat-kill race would have
surfaced as a logged `state_change{old=TRANSFERRING, new=DONE, reason=TRANSMIT_DONE}`
instead of a multi-session hunt that ended in the `WSLINK_DISABLED` workaround.

---

## 5. Folding the existing counters in — `get_link_stats`

No new computation needed for the counter layer; it already returns ~40 fields
(`…/wslink.py:91-219`). The router's heartbeat loop should pull it each tick and merge
into `router.py:998 health_snapshot()`. The Rust equivalent
(`crates/pyprotocols-core/src/protocols/wslink.rs:605 snapshot`) is key-for-key
compatible (documented divergence: `state` is Python-only), so the same consumer code
works against either backend.

---

## 6. Threading / async notes

- `register_packet_handler` handlers are `async` and run inside the recv loop — same
  context as today's `_handle_packet`, so no new concurrency surface.
- `subscribe` observers are **sync and best-effort**; if an observer needs to do async
  work it must hand off to its own queue. This keeps the data path free of observer
  latency.
- Counter reads (`get_link_stats`) are safe from any coroutine (already documented as
  such at `…/wslink.py:95`).

---

## 7. Minimal implementation footprint

| Piece | Est. protocol change | Risk |
|---|---|---|
| `_set_state()` + `state_change` event | ~10 lines, replace 3 in-place assignments | low |
| `register_packet_handler` + dispatcher | ~20 lines in `_handle_packet` | low |
| `subscribe` + counter events | ~30 lines, gated behind `sample_rate` | low |
| `negotiated_capabilities` (READY payload) | ~40 lines, backward-compatible | medium (wire) |
| `send()` public egress | ~5 lines wrapping the framer | low |

All additive; all default to today's behavior. See architecture §8 for sequencing.
