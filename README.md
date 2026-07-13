# pyretroprotocols

A collection of retro file-transfer protocols (ZMODEM, HS/Link) modernized and ported
for Python and clean-pipe streams — plus **WSLink**, a WebSocket/asyncio-native
evolution — with an optional high-performance **Rust/PyO3 core** (`pyprotocols_core`).

## Layout

| Path | What |
|---|---|
| `protocols/wslink/` | WSLink — modern clean-pipe framer, resume (VERIFY/SEEK), heartbeat, congestion control, extension API + observability (v1.1.0) |
| `protocols/hslink/` | HS/Link (1994) — pure-Python, socket/pipe-compatible |
| `protocols/zmodem/` | ZMODEM / X/Y-MODEM |
| `crates/pyprotocols-core/` | Rust/PyO3 accelerator: SIMD CRC, framers, packet structs, `LinkStatsTracker`, `TransportCapabilities` |
| `docs/design/` | WSLink v2 architecture, extension/observability API, router integration |

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

### Build

```bash
# Rust core (unit tests)
cd crates/pyprotocols-core && cargo test

# Python extension wheel (see note below)
maturin build --release
```

> **Note:** the crate's `pyproject.toml` currently sets `python-source = "python"`,
> which points at a directory that does not exist — `maturin build` will fail until
> that line is removed (pure-Rust extension) or the directory is created. `cargo
> build`/`cargo test` are unaffected.

## WSLink v2 (v1.1.0)

WSLink gained a supported **extension seam** and **protocol observability** without
any wire-breaking change (all additions default to prior behaviour):

```python
session.register_packet_handler(b'x', handler)   # consumer packet types (no monkeypatch)
session.subscribe(observer, sample_rate=1)        # state_change / congestion / rtt / integrity ...
session.get_link_stats()                          # ~40 live metrics
caps = session.negotiated_capabilities            # opt-in READY-handshake negotiation
```

See [`docs/design/WSLINK_V2_ARCHITECTURE.md`](docs/design/WSLINK_V2_ARCHITECTURE.md)
for the full design, decisions, and reasoning, and
[`CHANGELOG.md`](CHANGELOG.md) for release history.

## License

MIT.
