# pyretroprotocols

A collection of retro file-transfer protocols (ZMODEM, HS/Link) modernized for Python
and clean-pipe streams — plus **WSLink**, a WebSocket/asyncio-native evolution with a
bidirectional **Socket Proxy** channel multiplexer — backed by an optional
high-performance **Rust/PyO3 core** (`pyprotocols_core`).

## Layout

| Path | What |
|---|---|
| `protocols/wslink/` | WSLink — clean-pipe framer, resume (VERIFY/SEEK), heartbeat, congestion control, extension API + observability (v1.1.0), and the Socket Proxy channel mux (`channel/handlers/pool/proxy/transforms`) |
| `protocols/hslink/` | HS/Link (1994) — pure-Python, socket/pipe-compatible |
| `protocols/zmodem/` | ZMODEM / X/Y-MODEM |
| `crates/pyprotocols-core/` | Rust/PyO3 accelerator: SIMD CRC, framers, packet structs, `LinkStatsTracker`, `ChannelMux`, `TransportCapabilities` |
| `docs/design/` | WSLink architecture, v2 extension/observability API, router integration, security model |

## Components

### WSLink Socket Proxy

Bidirectional channel multiplexer over WebSocket with SSH-style flow control:

- **Pure-Python path**: ≤400 Mbps (SSH agent, serial, USB 2.0)
- **Rust+PyO3 path**: high-throughput (USB 3.x, PCIe, Thunderbolt, 10G capture)
- Credit-based flow control per SSH RFC 4254 §5.2
- Channel IDs: odd = client-initiated, even = server-initiated (32K each side)
- Max 256 concurrent channels, 64 KB initial credit per direction
- PSK encryption (ChaCha20-Poly1305 default, AES-256-GCM for CNSA), LZ4 compression
- SSRF hardening (private-IP + metadata blocking, DNS-rebinding re-resolve, allowlist)
- Connection pooling with multiple dispatch strategies

### WSLink v2 — extension API & observability (v1.1.0)

A supported extension seam and protocol observability, added **without any
wire-breaking change** (all additions default to prior behaviour):

```python
session.register_packet_handler(b'x', handler)   # consumer packet types (no monkeypatch)
session.subscribe(observer, sample_rate=1)        # state_change / congestion / rtt / integrity ...
session.get_link_stats()                          # ~40 live metrics
caps = session.negotiated_capabilities            # opt-in READY-handshake negotiation (default off)
```

## Python + Rust: graceful acceleration

The pure-Python packages work standalone. Where a hot path has a Rust twin, consumers
opportunistically use it and fall back automatically:

```python
try:
    from pyprotocols_core import ChannelMux, TransportCapabilities, LinkStatsTracker
except ImportError:
    ...  # pure-Python fallback
```

The Rust crate builds both a Python extension (`cdylib`) and a Rust `rlib`. Capability
wire-encoding is **byte-identical** across the two implementations (verified by
`crates/pyprotocols-core/examples/parity.rs`).

## Installation

```bash
pip install -e .
```

For Rust acceleration:

```bash
pip install maturin
cd crates/pyprotocols-core && cargo test   # Rust unit tests
maturin build --release                    # Python extension wheel (see note)
```

> **Note:** the crate's `pyproject.toml` currently sets `python-source = "python"`,
> which points at a directory that does not exist — `maturin build` will fail until
> that line is removed (pure-Rust extension) or the directory is created. `cargo
> build` / `cargo test` are unaffected.

## Testing

```bash
python -m pytest tests/wslink/ -v            # socket proxy suite
python -m pytest tests/wslink/test_transforms.py -v   # crypto transforms
python -m pytest tests/wslink/test_channel_mux.py -v  # channel multiplexing
python -m pytest tests/wslink/test_pool.py -v         # connection pooling
python -m pytest tests/wslink/test_handlers.py -v     # target handlers
python -m pytest tests/wslink/test_proxy.py -v        # proxy integration
cd crates/pyprotocols-core && cargo test              # Rust core (incl. capability parity)
```

## Documentation

- [WSLink Architecture](docs/design/WSLINK_ARCHITECTURE.md) — system design & component overview
- [WSLink v2 Architecture](docs/design/WSLINK_V2_ARCHITECTURE.md) — extension API, capabilities, observability (decisions + reasoning)
- [Extension & Observability API](docs/design/WSLINK_EXTENSION_API.md)
- [Router Integration](docs/design/ROUTER_INTEGRATION.md)
- [Socket Proxy Spec](protocols/wslink/docs/SOCKET_PROXY_SPEC.md)
- [Heartbeat Protocol](docs/WSLINK_HEARTBEAT.md)
- [WSLink Evolution](docs/history/WSLINK_EVOLUTION.md) — history
- [`CHANGELOG.md`](CHANGELOG.md) — release history

## License

MIT.
