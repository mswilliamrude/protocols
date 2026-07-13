# WSLink v2 — Router Integration (before / after)

> Status: **PROPOSAL** (the protocol-side API it targets is IMPLEMENTED in v1.1.0;
> the router-side rewrite below is not yet applied — it lives in the
> `Skill_MultiAgent` repo, tracked for the integrating agent). Docs only here.
> Companion to [`WSLINK_V2_ARCHITECTURE.md`](./WSLINK_V2_ARCHITECTURE.md) (§3.7, §9) and
> [`WSLINK_EXTENSION_API.md`](./WSLINK_EXTENSION_API.md).
> Subject: `Skill_MultiAgent/unimind/mcp/router.py` (1533 lines). Citations: `path:line`,
> tree at branch point `42ab65f`.

The router is the primary WSLink consumer. This doc shows, concretely, how the
protocol changes let the router **delete** compensating glue. The litmus test from
architecture §9: *a good protocol change lets the consumer remove adapters, not add
them.* The estimated router diff below is **net-negative lines**.

---

## 1. How the router uses WSLink today

- **Primarily a chat channel.** MCP JSON-RPC rides `send_chat()` / `on_chat_received`
  (`router.py:309`, `:437`, `:479`, `:982`, `:1082`, `:1162`, `:1369`). File transfer
  (`add_files`) is secondary (`router.py:901`).
- **Monkeypatches the session** (`router.py:319-325`) — see architecture §3.7 / API §1.
- **Reaches into the framer** via `set_wslink_session()` (`router.py:327`).
- **Branches on an env var** to dodge the TRANSMIT_DONE/Z race, `router.py:141`:

  ```python
  WSLINK_DISABLED = os.environ.get("UNIMIND_WSLINK_DISABLED", "0") == "1"
  ```
  used at `router.py:300` / `:335`:
  ```python
  if not WSLINK_DISABLED:
      ... # set up WSLinkSession + monkeypatch
  else:
      self.logger.info("WSLink DISABLED — using plain text WebSocket mode")
  ```
- **Manually sequences the handshake** because proxy packets during handshake corrupt
  the stream, `router.py:397-402`:

  ```python
  # Start SSH agent forwarding AFTER WSLink handshake + MCP initialize
  # are complete — proxy packets would corrupt the byte stream if sent
  # during the handshake.
  if getattr(self, 'proxy_client', None):
      forwarding = await self.proxy_client.start_ssh_agent_forwarding()
  ```
- **Keeps only crude telemetry** (`record_bytes_sent(channel=…)` at `router.py:437`,
  `:478`; `record_bytes_recv` at `:526`) and **never pulls `get_link_stats()`** (grep of
  `router.py` → no match). Its own `health_snapshot()` at `router.py:998` is unrelated
  to protocol internals.

---

## 2. Mapping: each hack → the change that removes it

| Router smell | Root cause (protocol gap) | Change | Verdict |
|---|---|---|---|
| Monkeypatch `_handle_packet` (`:319-325`) | no handler registry | `register_packet_handler` (API §2) | **deleted** |
| `.framer` reach-through (`:327`) | no public egress | `session.send()` (API §1) | **deleted** |
| Proxy hand-rolls a framer (`socket_proxy/proxy.py:518-537`) | no shared framing source | Rust framer as source of truth (arch §4) | **deleted** |
| `WSLINK_DISABLED` env branch (`:141/:300/:335`) | Z chat-kill bug + no negotiation | fix Z + capability profile (arch §3.2, API §3) | **deleted** |
| Manual handshake ordering (`:397-402`) | no protocol state guard | "channels open only after READY" state (arch §4) | **mostly enforced by protocol** |
| No protocol visibility (`:437/:478` byte counters only) | telemetry computed but not exposed | `subscribe` + consume `get_link_stats` (API §4/§5) | **replaced with real signal** |
---

## 3. Before / after — the connection-setup block

### 3.1 Before (`router.py:311-333`, abridged)

```python
# Socket proxy: intercept proxy packets before WSLink drops them
try:
    from wslink_proxy_client import ProxyClient, is_socket_proxy_packet
    self.proxy_client = ProxyClient(
        send_fn=lambda data: self.ws_transport.write(data),
        client_logger=self.logger,
    )
    # Wrap WSLink's _handle_packet to intercept socket proxy types
    _original_handle = self.wslink_session._handle_packet
    async def _handle_with_proxy(pkt_type, payload):
        if is_socket_proxy_packet(pkt_type):
            await self.proxy_client.handle_packet(pkt_type, payload)
        else:
            await _original_handle(pkt_type, payload)
    self.wslink_session._handle_packet = _handle_with_proxy
    # Give proxy client access to WSLink framer for proper framing
    self.proxy_client.set_wslink_session(self.wslink_session)
    self.logger.info("Socket proxy client attached to WSLink session")
except Exception as _proxy_err:
    self.proxy_client = None
    self.logger.debug(f"Socket proxy not available: {_proxy_err}")
```

### 3.2 After (extension API)

```python
try:
    from wslink_proxy_client import ProxyClient, SOCKET_PROXY_TYPES
    self.proxy_client = ProxyClient(send_fn=self.wslink_session.send, logger=self.logger)
    for t in SOCKET_PROXY_TYPES:                      # b's' b'd' b'c' b'e' b'w'
        self.wslink_session.register_packet_handler(t, self.proxy_client.handle_packet)
except Exception as _proxy_err:
    self.proxy_client = None
    self.logger.debug(f"Socket proxy not available: {_proxy_err}")
```

Gone: `_original_handle` capture, the wrapper closure, the `_handle_packet`
reassignment, and `set_wslink_session()` (the proxy now frames via
`session.send`). **~9 lines → ~3, and none of them touch private internals.**

---

## 4. Before / after — transport mode selection

### 4.1 Before — env-var branch (`router.py:300/335`)

```python
if not WSLINK_DISABLED:
    ... # WSLink + monkeypatch
else:
    self.logger.info("WSLink DISABLED — using plain text WebSocket mode")
```

### 4.2 After — negotiated capability profile

The plaintext fallback stops being a global env toggle and becomes the outcome of
negotiation (API §3). The session still supports a forced-plaintext profile for the
guaranteed-safe path, but the *default* is "negotiate, fall back gracefully":

```python
self.wslink_session = WSLinkSession(self.ws_transport, recv_dir=workspace_dir,
                                    heartbeat_interval=self.heartbeat_interval,
                                    idle_timeout=self.heartbeat_timeout)
# ... after loop() handshake completes:
caps = self.wslink_session.negotiated_capabilities
self.logger.info(f"WSLink caps: reliable={caps.is_reliable} crc={caps.wire_crc} "
                 f"block={caps.max_block_size}")
```

Once the Z chat-kill bug is fixed (arch §3.2), the *reason* for `WSLINK_DISABLED`
disappears and the env var + its branch can be deleted outright.

---

## 5. Before / after — observability

### 5.1 Before — bytes only (`router.py:437`, `:478` sent; `:526` recv)

```python
self.telemetry.record_bytes_sent(len(payload), channel="wslink")
...
self.telemetry.record_bytes_recv(len(payload), channel="wslink")
```

The router can see *how much* moved, never *how the protocol is behaving*.

### 5.2 After — consume stats + subscribe to events

Step zero (no protocol change) — pull the stats that already exist
(`…/wslink.py:91`) on the heartbeat tick and fold into `health_snapshot()`
(`router.py:998`):

```python
def health_snapshot(self) -> dict:
    snap = { ... existing ... }
    if self.wslink_session:
        snap["wslink"] = self.wslink_session.get_link_stats()   # ~40 fields, already computed
    return snap
```

Step one — subscribe for protocol events (API §4), routed into existing telemetry:

```python
class _RouterObserver:
    def __init__(self, telemetry, logger): self.t, self.log = telemetry, logger
    def on_event(self, e):
        if e.kind == "state_change":
            self.log.info(f"wslink {e.payload['old']}→{e.payload['new']} "
                          f"({e.payload['reason']})")           # would have caught the Z race
        elif e.kind == "integrity":
            self.t.incr("wslink.crc_fail")                       # 0 over TLS ⇒ CRC redundant
        elif e.kind == "channel" and e.payload["event"] == "credit_stall":
            self.t.incr("wslink.credit_stall")                   # proves 64 KB ceiling

self.wslink_session.subscribe(_RouterObserver(self.telemetry, self.logger), level="info")
```

---

## 6. Net effect

- **Deleted from `router.py`:** the monkeypatch block (`:319-325`), the `.framer`
  reach-through (`:327`), and — once the Z fix lands — the `WSLINK_DISABLED` var and
  its branch (`:141`, `:300`, `:335`).
- **Added to `router.py`:** ~3-line handler registration, a one-line `get_link_stats`
  merge, a small observer class. All against *public* API.
- **Net:** fewer lines, zero private-surface coupling, and — for the first time —
  real protocol-level visibility (state transitions, congestion, integrity, credit
  stalls) feeding the router's telemetry.

This is the litmus test passing: the protocol change let the consumer **delete**
adapters and **gain** signal, not write more glue.

---

## 7. Cross-repo note

`router.py` and the vendored WSLink copies live in the `Skill_MultiAgent` repo, while
the canonical protocol + Rust core live in `protocols` (this repo). The extension API
must land in the canonical protocol first, then propagate to the vendored copies
(`Skill_MultiAgent/unimind/mcp/wslink/`, `…/clients/common/wslink/`) — or, preferably,
the vendored copies are replaced by the single Rust source of truth (arch §4) so this
integration is written once.
