# pyretroprotocols

A collection of retro file transfer protocols (ZMODEM, HS/Link) modernized for Python, plus the WSLink Socket Proxy Extension for bidirectional channel multiplexing over WebSocket.

## Components

### WSLink Socket Proxy

Bidirectional channel multiplexer over WebSocket with SSH-style flow control. Designed for:

- **Pure Python path**: ≤400 Mbps (SSH agent, serial, USB 2.0)
- **Rust+PyO3 path**: ≤25 Gbps (USB 3.x, PCIe, Thunderbolt, 10G capture)

Features:
- Credit-based flow control per SSH RFC 4254 §5.2
- Channel IDs: odd=client-initiated, even=server-initiated (32K each side)
- Max 256 concurrent channels, 64KB initial credit per direction
- PSK-based encryption (ChaCha20-Poly1305 default, AES-256-GCM for CNSA)
- LZ4 compression with configurable thresholds
- Connection pooling with multiple dispatch strategies

## Installation

```bash
pip install -e .
```

For Rust acceleration:
```bash
pip install maturin
cd rust && maturin develop --release
```

## Documentation

- [WSLink Architecture](docs/design/WSLINK_ARCHITECTURE.md) - System design and component overview
- [Security Model](docs/design/SECURITY.md) - Security hardening and threat mitigations
- [RFC: Socket Proxy](docs/RFC-SOCKET-PROXY.md) - Protocol specification
- [Heartbeat Protocol](docs/WSLINK_HEARTBEAT.md) - Keep-alive and health monitoring

## Testing

Tests are organized under `tests/` with per-protocol subdirectories:

```bash
# Run all WSLink tests
python -m pytest tests/wslink/ -v

# Run specific test modules
python -m pytest tests/wslink/test_transforms.py -v   # Crypto transforms (60 tests)
python -m pytest tests/wslink/test_channel_mux.py -v  # Channel multiplexing (76 tests)
python -m pytest tests/wslink/test_pool.py -v         # Connection pooling (29 tests)
python -m pytest tests/wslink/test_handlers.py -v     # Target handlers (23 tests)
python -m pytest tests/wslink/test_proxy.py -v        # Proxy integration (23 tests)
```

### Test Categories

| Module | Tests | Coverage |
|--------|-------|----------|
| `test_transforms.py` | 60 | Cipher suites, nonce safety, compression, key derivation |
| `test_channel_mux.py` | 76 | Channel lifecycle, flow control, packet formats |
| `test_pool.py` | 29 | Connection management, dispatch strategies, rebalancing |
| `test_handlers.py` | 23 | Target parsing, policy enforcement, SSRF protection |
| `test_proxy.py` | 23 | End-to-end proxy operations, error handling |

## Project Structure

```
protocols/
├── wslink/
│   ├── __init__.py
│   ├── transforms.py    # Crypto: ChaCha20, AES-GCM, LZ4, HKDF
│   ├── channel.py       # Channel state machine and flow control
│   ├── handlers.py      # TCP/Unix/SSH-agent target handlers
│   ├── pool.py          # Connection pooling and dispatch
│   └── proxy.py         # High-level proxy API
├── zmodem/              # ZMODEM implementation
└── hslink/              # HS/Link implementation

tests/
└── wslink/
    ├── test_transforms.py
    ├── test_channel_mux.py
    ├── test_pool.py
    ├── test_handlers.py
    └── test_proxy.py

docs/
├── design/
│   ├── WSLINK_ARCHITECTURE.md
│   └── SECURITY.md
├── RFC-SOCKET-PROXY.md
└── WSLINK_HEARTBEAT.md
```

## License

MIT
