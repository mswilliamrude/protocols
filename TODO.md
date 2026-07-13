# Protocols — TODO

**Last Updated:** 2026-07-12

---

## WSLink Socket Proxy Extension (Feature Request)

**RFC:** `docs/RFC-SOCKET-PROXY.md`
**Priority:** HIGH
**Branch:** `feature/wslink-socket-proxy-rfc`

### Phase 1: Core Protocol

- [x] Add socket proxy constants to `protocols/wslink/const.py` (5 new packet types)
- [x] Add Rust packet structs to `crates/pyprotocols-core/src/protocols/wslink.rs`
  - [x] SocketOpenPacket (channel + flags + target)
  - [x] SocketDataPacket (channel + data)
  - [x] SocketClosePacket (channel + code)
  - [x] SocketErrorPacket (channel + code + message)
  - [x] SocketWindowPacket (channel + credit)
- [x] New `crates/pyprotocols-core/src/channel.rs`
  - [x] ChannelMux (demux frames to channels, mux data to frames)
  - [x] Credit-based flow control (per SSH RFC 4254 §5.2)
  - [x] Channel lifecycle (OPEN → DATA → CLOSE)
  - [x] Channel table (max 256 concurrent)
- [x] Unit tests for all new packet types (17 tests)
- [ ] Unit tests for channel lifecycle (open, data, close, error, window) — Rust tests written, need Python integration
- [x] Unit tests for CRC32 integrity on new frame types
- [x] Python bindings (PyO3) for ChannelMux

### Phase 2: Compression + Encryption

- [ ] Per-channel LZ4 compression (flag 0x08)
  - [ ] `lz4_flex` crate integration
  - [ ] Compress on send, decompress on receive (transparent to channel consumer)
  - [ ] Benchmark: verify LZ4 doesn't bottleneck at target throughput
- [ ] Per-channel ChaCha20-Poly1305 encryption (flag 0x10)
  - [ ] X25519 ECDH key exchange in OPEN payload metadata
  - [ ] Encrypt after compress (standard order)
  - [ ] Nonce management (counter-based, per-channel)

### Phase 3: Parallel Connection Pool

- [ ] Multi-WebSocket transport (N connections to same endpoint)
- [ ] Channel affinity (same channel → same connection)
- [ ] Dynamic scaling (grow on demand, shrink on idle)
- [ ] Per-connection QoS (TCP_NODELAY, TOS, SO_PRIORITY)
- [ ] Connection health monitoring (dead connection detection + failover)
- [ ] Benchmark: verify linear scaling up to NIC wire speed

### Phase 4: Target Handlers (Python — control plane)

- [ ] SSH agent handler
  - [ ] Linux: connect to `$SSH_AUTH_SOCK` Unix socket
  - [ ] Windows: connect to `\\.\pipe\openssh-ssh-agent` named pipe
  - [ ] macOS: connect to `$SSH_AUTH_SOCK` or Keychain agent
- [ ] TCP proxy handler
- [ ] UDP proxy handler (DATAGRAM mode flag 0x40)
- [ ] Unix domain socket handler
- [ ] Windows named pipe handler
- [ ] Serial port handler (`pyserial`)
- [ ] Target allow-list validation + security policy enforcement

### Phase 5: Target Handlers (Rust — data plane)

- [ ] USB device proxy (`rusb` crate)
  - [ ] Bulk transfer forwarding
  - [ ] Interrupt endpoint support
  - [ ] Device hotplug detection
  - [ ] VID:PID allow-list enforcement
- [ ] Raw packet capture (`AF_PACKET` + BPF)
  - [ ] BPF filter compilation
  - [ ] Ring buffer capture
  - [ ] pcap header generation
- [ ] VRF-scoped sockets (`ip vrf exec` wrapper)
- [ ] PCIe control plane (VFIO/mmap BAR access)
- [ ] io_uring transport option (kernel bypass)

### Phase 6: Advanced

- [ ] GRE/VXLAN/MPLS tunnel tapping
- [ ] ATM cell stream proxy
- [ ] IPsec SA tap (encrypted passthrough + IKE monitoring)
- [ ] Bluetooth RFCOMM/L2CAP
- [ ] Multicast group relay
- [ ] DisplayPort framebuffer capture (compressed H.264 stream)
- [ ] DPDK integration for 25G+ wire rate

---

## Bandwidth Targets

| Implementation | Target | Verified |
|---------------|--------|----------|
| Python single connection | ~100 Mbps | [ ] |
| Python 4x parallel | ~400 Mbps | [ ] |
| Rust single connection | ~5 Gbps | [ ] |
| Rust 4x parallel (10G NIC) | ~10 Gbps | [ ] |
| Rust + io_uring | ~10 Gbps single conn | [ ] |
| Rust + DPDK (25G NIC) | ~25 Gbps | [ ] |

---

## Language Boundary

```
Python: ALL control plane (negotiation, OPEN/CLOSE, auth, audit, errors)
Python: Data plane ≤400 Mbps (SSH agent, serial, DNS, USB 2.0)
Rust:   Data plane >400 Mbps (USB 3.x, PCIe, Thunderbolt, 10G capture)
Rust:   Framing + CRC32 + LZ4 + encryption (always, for all channels)
```

---

## Other TODOs

### Existing

- [ ] BBR-style congestion control in sender pump (designed, not implemented)
- [ ] QUIC transport option (alternative to parallel TCP WebSockets)
- [ ] Formal protocol specification document (IETF-style)

### Backlog: WSLink over PTY

- [ ] COBS transport layer (Consistent Overhead Byte Stuffing)
  - [ ] `crates/pyprotocols-core/src/cobs.rs` — encode/decode
  - [ ] Zero-byte frame delimiter (unambiguous, no escaping)
  - [ ] 0.4% worst-case overhead (1 byte per 254)
  - [ ] Unit tests for edge cases (all zeros, all 0xFF, empty)
- [ ] PTY raw-mode helper
  - [ ] Eliminate race window between exec() and tcsetattr()
  - [ ] Parent-side: setraw() before fork
  - [ ] Child-side: setraw() as first operation
- [ ] SSH stdin/stdout tunnel mode
  - [ ] `ssh user@host 'wslink-server' | wslink-client`
  - [ ] No port forwarding required
  - [ ] Useful for locked-down environments

### Maintenance

- [ ] Update `WSLINK_ARCHITECTURE.md` with socket proxy extension
- [ ] Update README with new capabilities
- [ ] CI: add Rust crate build + test to GitHub Actions
- [ ] Release: publish `pyprotocols-core` wheel with socket proxy support
