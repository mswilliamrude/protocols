# WSLink Architecture

## Overview

WSLink is a bidirectional channel multiplexer designed to tunnel multiple logical streams over a single WebSocket connection. It implements SSH-style credit-based flow control (RFC 4254 §5.2) with optional encryption and compression.

## Design Goals

1. **High throughput**: Pure Python ≤400 Mbps, Rust+PyO3 ≤25 Gbps
2. **Low latency**: Zero-copy where possible, minimal allocations
3. **Security**: CNSA-compliant cipher options, defense-in-depth
4. **Reliability**: Credit-based flow control prevents buffer exhaustion
5. **Flexibility**: Pluggable handlers for TCP, Unix sockets, SSH agent

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Application                              │
├─────────────────────────────────────────────────────────────────┤
│                      PooledProxy / Proxy                         │
│  - Channel lifecycle management                                  │
│  - High-level send/receive API                                   │
├─────────────────────────────────────────────────────────────────┤
│                      ConnectionPool                              │
│  - Multi-connection management                                   │
│  - Dispatch strategies (round-robin, least-loaded, least-latency)│
│  - Graceful drain and rebalancing                                │
├─────────────────────────────────────────────────────────────────┤
│                       ChannelMux                                 │
│  - Channel state machine (OPENING → OPEN → CLOSING → CLOSED)    │
│  - Credit-based flow control                                     │
│  - Packet framing and dispatch                                   │
├─────────────────────────────────────────────────────────────────┤
│                      TransformChain                              │
│  - Encryption (ChaCha20-Poly1305 / AES-256-GCM)                 │
│  - Compression (LZ4)                                             │
│  - Key derivation (HKDF-SHA256)                                  │
├─────────────────────────────────────────────────────────────────┤
│                       Handlers                                   │
│  - TCPHandler: TCP socket targets                                │
│  - UnixHandler: Unix domain sockets                              │
│  - SSHAgentHandler: SSH agent protocol                           │
├─────────────────────────────────────────────────────────────────┤
│                      WebSocket Transport                         │
└─────────────────────────────────────────────────────────────────┘
```

## Channel Lifecycle

```
Client                                    Server
  │                                         │
  │──── OPEN (channel_id, target, flags) ──►│
  │                                         │ create handler
  │◄─── OPEN_CONFIRM (channel_id, credit) ──│
  │                                         │
  │◄───────── DATA (channel_id, data) ──────│
  │──────── WINDOW (channel_id, bytes) ────►│ replenish credit
  │                                         │
  │──────── DATA (channel_id, data) ───────►│
  │◄─────── WINDOW (channel_id, bytes) ─────│
  │                                         │
  │──── CLOSE (channel_id, code, reason) ──►│
  │◄─── CLOSE (channel_id, code, reason) ───│
  │                                         │
```

### Channel ID Assignment

- **Client-initiated**: Odd IDs (1, 3, 5, ..., 65535)
- **Server-initiated**: Even IDs (2, 4, 6, ..., 65534)
- **Maximum**: 32K channels per side (65K total)
- **Wraparound**: Collision detection when IDs wrap

### Flow Control

Credit-based flow control prevents buffer exhaustion:

1. Each side starts with `INITIAL_CREDIT` (64KB default)
2. Sender decrements credit for each DATA packet sent
3. Receiver sends WINDOW packets to replenish credit
4. Sender blocks when credit reaches zero
5. `FlowControlViolation` raised if peer exceeds allowance

## Transform Pipeline

Data flows through transforms in order:

```
Outbound: plaintext → compress → encrypt → wire
Inbound:  wire → decrypt → decompress → plaintext
```

### Cipher Suites

| Suite | Algorithm | Key Size | Nonce | Tag | Use Case |
|-------|-----------|----------|-------|-----|----------|
| NONE | - | - | - | - | Testing only |
| CHACHA20_POLY1305 | ChaCha20-Poly1305 | 256-bit | 12B | 16B | Default, fast on CPU |
| AES_256_GCM | AES-256-GCM | 256-bit | 12B | 16B | CNSA compliance |

### Nonce Construction

Session-unique nonces prevent cross-session replay:

```
┌────────────┬────────────────────┐
│ session_id │     counter        │
│   4 bytes  │      8 bytes       │
└────────────┴────────────────────┘
```

- `session_id`: Random per-session, generated at init
- `counter`: Monotonic, incremented per packet
- Maximum messages per session: 2^32 (counter limit)

### Key Derivation

HKDF-SHA256 with per-session salt:

```python
derived_key = HKDF(
    algorithm=SHA256,
    length=32,
    salt=session_salt,  # Random 32 bytes
    info=b"wslink-session-key"
).derive(master_key)
```

## Connection Pool

### Dispatch Strategies

| Strategy | Selection Criteria | Best For |
|----------|-------------------|----------|
| ROUND_ROBIN | Rotate through connections | Equal distribution |
| LEAST_LOADED | Fewest active channels | Balanced load |
| LEAST_LATENCY | Lowest measured RTT | Latency-sensitive |
| STICKY | Hash of channel ID | Session affinity |
| RANDOM | Random selection | Simple distribution |

### Connection States

```
ACTIVE ─────► DRAINING ─────► (removed)
   │              │
   └──► FAILED ───┘
```

- **ACTIVE**: Accepting new channels
- **DRAINING**: No new channels, waiting for existing to close
- **FAILED**: Connection lost, channels orphaned

### Rebalancing

Automatic rebalancing moves channels from overloaded connections:

```python
pool = ConnectionPool(rebalance_threshold=0.2)
# Triggers when load imbalance exceeds 20%
moved = await pool.rebalance()
```

## Handler Architecture

### Target Parsing

```
tcp:hostname:port     → TCPHandler
unix:/path/to/socket  → UnixHandler  
ssh-agent:            → SSHAgentHandler
[::1]:8080            → TCPHandler (IPv6)
```

### Policy Enforcement

Two-phase policy check:

1. **Deny rules**: Checked first, reject if matched
2. **Allow rules**: Must match at least one

Default policy:
- Allow: localhost, 127.0.0.1, ::1, /tmp/*, SSH_AUTH_SOCK
- Deny: All remote hosts

### SSRF Protection

Built-in protection against Server-Side Request Forgery:

- Block private IP ranges (10.x, 172.16-31.x, 192.168.x)
- Block cloud metadata endpoints (169.254.169.254, metadata.google.internal)
- DNS rebinding protection (resolve before connect, verify IP)

## Error Handling

### Error Categories

| Category | Handling | Example |
|----------|----------|---------|
| Protocol | Close channel with code | Invalid packet format |
| Transport | Mark connection failed | WebSocket disconnect |
| Handler | Close channel, log | Target unreachable |
| Security | Reject silently | SSRF attempt |

### Sanitized Error Messages

Production errors never leak internal details:

```python
SANITIZED_ERRORS = {
    "connection_refused": "Target connection refused",
    "timeout": "Connection timed out",
    "internal": "Internal error",
    # ... pre-defined messages only
}
```

## Performance Considerations

### Zero-Copy Path

When using Rust acceleration:
- Direct buffer sharing between Python and Rust
- No intermediate copies for encryption/decryption
- Memory-mapped I/O for large transfers

### Buffer Management

- Pre-allocated packet buffers
- Credit prevents unbounded buffering
- Configurable high-water marks

### Compression Heuristics

LZ4 compression enabled when:
- Payload > MIN_COMPRESS_SIZE (512 bytes default)
- Not already compressed (detected by entropy)
- Compression ratio > MIN_COMPRESS_RATIO (1.1x)

## Testing Strategy

### Unit Tests

Each component tested in isolation:
- `test_transforms.py`: Crypto correctness, edge cases
- `test_channel_mux.py`: State machine transitions
- `test_pool.py`: Dispatch strategies, rebalancing
- `test_handlers.py`: Target parsing, policy

### Integration Tests

- `test_proxy.py`: End-to-end channel operations
- Rust/Python parity tests (same test cases, both implementations)

### Security Tests

- Nonce uniqueness verification
- Credit exhaustion handling
- SSRF protection validation
- Path traversal rejection
