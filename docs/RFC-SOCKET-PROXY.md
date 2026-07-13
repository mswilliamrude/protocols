# WSLink Socket Proxy Extension — Feature Request

**Date:** 2026-07-12
**Author:** williamrude
**Status:** PROPOSED
**Branch:** feature/wslink-socket-proxy-rfc

---

## Summary

Extend WSLink with bidirectional socket proxy channels, enabling tunneling of arbitrary connections (SSH agent, Unix domain, TCP, UDP, VRF, USB, PCIe, raw capture) over the existing WSLink WebSocket connection. Python handles negotiation, Rust handles data plane.

---

## Motivation

WSLink currently carries two traffic types:
- `PACK_CHAT_BLOCK` (b'H') — JSON-RPC MCP tool calls
- `PACK_DATA_BLOCK` (b'D') — Binary file transfers

Real-world use cases require proxying other connection types through the same tunnel:
- **SSH agent forwarding** — git authentication without key exposure
- **Unix domain sockets** — Docker, D-Bus proxying
- **TCP/UDP tunneling** — database access, service connectivity
- **VRF traversal** — network segmentation bridging for security assessments
- **USB device proxy** — YubiKey, SDR dongle, JTAG, serial console
- **Raw packet capture** — pcap over WSLink with BPF filtering
- **PCIe/Thunderbolt** — device control plane access (Rust data plane)

All multiplexed over the single existing WebSocket connection.

---

## Background

### The Unimind Use Case

Unimind is an MCP knowledge system that orchestrates multiple specialized sidecar MCP servers (Ghost OSINT, CGA code intelligence, Ghidra binary analysis). These sidecars need access to resources on the client's machine:
- SSH keys (for git clone in CGA sidecar)
- Local USB devices (for hardware security assessment)
- VRF-scoped networks (for network security assessment)
- Raw interfaces (for traffic capture and analysis)

Currently, credentials must be deployed into containers. Socket proxy eliminates this by tunneling access through the client's WSLink connection. Keys never leave the client machine.

### Protocol Heritage

WSLink evolved from HS/Link (1994 BBS protocol). The frame format — length-prefixed with CRC32 integrity — is transport-agnostic and extensible. Adding new packet types requires zero changes to the framer. The existing Rust implementation (`crates/pyprotocols-core`) provides SIMD-accelerated CRC32 and PyO3 bindings.

---

## Protocol Changes

### New Packet Types

Five new types using lowercase ASCII (backward compatible — existing types are uppercase):

| Type | Byte | Name | Purpose |
|------|------|------|---------|
| Socket Open | `b's'` | `PACK_SOCKET_OPEN` | Open a proxy channel |
| Socket Data | `b'd'` | `PACK_SOCKET_DATA` | Forward bytes on channel |
| Socket Close | `b'c'` | `PACK_SOCKET_CLOSE` | Graceful channel close |
| Socket Error | `b'e'` | `PACK_SOCKET_ERROR` | Channel error notification |
| Socket Window | `b'w'` | `PACK_SOCKET_WINDOW` | Flow control credit grant |

### Frame Format

Unchanged. Uses existing WSLink frame:
```
[4-byte LE length L][1-byte type][payload][4-byte LE CRC32]
```

### Packet Payloads

**PACK_SOCKET_OPEN (b's'):**
```
[Channel: u16 LE][Flags: u8][Target: null-terminated UTF-8]
```

Channel IDs: odd = client-initiated, even = server-initiated.

Flags bitmask:
- 0x01 BIDIR (bidirectional, default)
- 0x02 READ_ONLY
- 0x04 WRITE_ONLY
- 0x08 COMPRESSED (LZ4 per-channel)
- 0x10 ENCRYPTED (ChaCha20-Poly1305)
- 0x20 AUDIT (log all traffic)
- 0x40 DATAGRAM (preserve message boundaries — for UDP)

**PACK_SOCKET_DATA (b'd'):**
```
[Channel: u16 LE][Data: rest of payload]
```

**PACK_SOCKET_CLOSE (b'c'):**
```
[Channel: u16 LE][Code: u16 LE]
```
Codes: 0=normal, 1=refused, 2=unreachable, 3=auth_failed, 4=timeout, 5=admin_close

**PACK_SOCKET_ERROR (b'e'):**
```
[Channel: u16 LE][Code: u16 LE][Message: UTF-8]
```

**PACK_SOCKET_WINDOW (b'w'):**
```
[Channel: u16 LE][Credit: u32 LE]
```
Credit-based flow control (per SSH RFC 4254 §5.2). Initial credit: 65536 bytes.

### Channel Target Types

```
ssh-agent                      SSH agent socket (auto-detect platform)
unix:<path>                    Unix domain socket
tcp:<host>:<port>              TCP connection
udp:<host>:<port>              UDP datagram relay
tls:<host>:<port>              TLS-terminated TCP
vrf:<name>:tcp:<host>:<port>   VRF-scoped TCP
vrf:<name>:udp:<host>:<port>   VRF-scoped UDP
pipe:<name>                    Windows named pipe
abstract:<name>                Linux abstract namespace socket
serial:<dev>:<baud>            Serial port
usb:<vid>:<pid>:<endpoint>     USB device endpoint
gre:<tunnel-id>:<remote-ip>    GRE tunnel tap
vxlan:<vni>:<vtep-ip>          VXLAN overlay tap
ipsec:<sa-name>                IPsec SA tap
ipsec:ike:<peer-ip>            IKE negotiation monitor
mpls:<label-stack>             MPLS LSP tap
atm:<if>:<vpi>:<vci>           ATM cell stream
raw:<interface>                Raw interface capture
raw:<interface>:bpf:<filter>   BPF-filtered capture
raw-ip:<if>:<proto-num>        Raw IP protocol (unnumbered: ICMP, OSPF, VRRP, etc.)
socks5:<host>:<port>           SOCKS5 proxy passthrough
http-connect:<host>:<port>     HTTP CONNECT tunnel
mcast:<group>:<port>           Multicast group relay
bt:rfcomm:<addr>:<channel>     Bluetooth RFCOMM
pcie:<bdf>:bar<n>              PCIe config/MMIO (control plane only)
```

---

## Bandwidth Estimates

### Python Data Plane

| Configuration | Throughput | Notes |
|--------------|-----------|-------|
| Single connection | ~100 Mbps | GIL-bound, asyncio overhead |
| 4 parallel connections | ~400 Mbps | 4x independent TCP streams |
| 8 parallel connections | ~600-700 Mbps | Diminishing returns, CPU saturated |
| Theoretical max | ~800 Mbps | Full CPU core dedicated |
| With LZ4 compression | ~300-2000 Mbps effective | Depends on compressibility |

### Rust Data Plane (existing crate: pyprotocols-core)

| Configuration | Throughput | Notes |
|--------------|-----------|-------|
| Single connection | ~5-8 Gbps | Zero-copy, SIMD CRC32, no GIL |
| Single + LZ4 | ~3-5 Gbps | LZ4 at 2+ GB/s |
| Single + io_uring | ~10 Gbps | Kernel bypass for I/O |
| 4 parallel connections | ~10 Gbps | Wire rate on 10G NIC |
| DPDK (kernel bypass) | 25+ Gbps | Line rate on 25G NIC |

### Protocol-Specific Requirements

| Protocol | Bandwidth | Python adequate? | Rust required? |
|----------|----------|-----------------|---------------|
| SSH agent | <1 Mbps | Yes | No |
| Serial console | <1 Mbps | Yes | No |
| UDP DNS/SNMP | <10 Mbps | Yes | No |
| VRF TCP tunnels | 1-100 Mbps | Marginal at 100M | Preferred |
| USB 1.x (Low/Full Speed) | 1.5/12 Mbps | Yes | No |
| USB 2.0 (High Speed) | 480 Mbps | Needs 4+ parallel | Single connection |
| USB 3.0 (SuperSpeed) | 5 Gbps | **NO** | **Required** |
| USB 3.1 Gen 2 | 10 Gbps | **NO** | Required + parallel |
| USB 3.2 Gen 2x2 | 20 Gbps | **NO** | Required + io_uring |
| PCIe config space | <1 Mbps | Yes (but latency) | Preferred (μs latency) |
| PCIe DMA | 1-32 GB/s | **NO** | Required + DPDK |
| Thunderbolt networking | 10-40 Gbps | **NO** | Required |
| DisplayPort (H.264 compressed) | 5-20 Mbps | Yes | Preferred (encode speed) |
| Raw capture (1G link) | ~940 Mbps | Borderline | Comfortable |
| Raw capture (10G link) | ~10 Gbps | **NO** | Required |

### Architecture Decision

```
Control plane: ALWAYS Python
  - Channel negotiation (OPEN/CLOSE)
  - Flow control decisions
  - Target validation + auth + audit
  - Error handling

Data plane ≤400 Mbps: Python acceptable
  - SSH agent, serial, DNS, USB 2.0

Data plane >400 Mbps: Rust required
  - USB 3.x, PCIe, Thunderbolt, 10G capture

Hybrid handoff:
  - Python opens channel, validates target
  - Rust takes over data forwarding
  - Python resumes on CLOSE/ERROR
```

---

## Parallel Connection Pool

Multiple WebSocket connections to the same endpoint, striped by channel:

```
router.py ──── WS 1 ────── Server    (MCP chat, low latency)
           ├── WS 2 ──────┤          (file transfer)
           ├── WS 3 ──────┤          (USB bulk transfer)
           └── WS 4 ──────┘          (raw capture)
```

Channel affinity: same channel always uses same connection (preserves ordering).
Dynamic scaling: grow on demand, shrink on idle.
Per-connection QoS: TCP_NODELAY + TOS marking for latency-sensitive channels.

---

## Security Considerations

1. **Target allow-list:** Router MUST validate targets against config. Default: only `ssh-agent` and `tcp:localhost:*`.
2. **Network taps:** `raw:`, `vrf:`, `gre:` targets require explicit `--allow-network-tap` flag.
3. **USB access:** Requires `--allow-usb` flag and optionally `--usb-allow-list <vid>:<pid>`.
4. **Channel limits:** Max 256 concurrent channels per session.
5. **Credit enforcement:** Producer MUST NOT exceed credit. Violation = channel close.
6. **Audit flag:** Channels with 0x20 flag log all traffic for compliance.
7. **E2E encryption:** 0x10 flag enables ChaCha20-Poly1305. Keys via X25519 ECDH.

---

## Implementation Plan

### Phase 1: Core Protocol (Rust + Python)

| File | Change | Lines |
|------|--------|-------|
| `protocols/wslink/const.py` | Add 5 packet constants | ~10 |
| `crates/pyprotocols-core/src/protocols/wslink.rs` | Add socket proxy packet types + channel structs | ~200 |
| `crates/pyprotocols-core/src/channel.rs` | New — ChannelMux, flow control, compression | ~300 |
| Tests | Unit tests for all new packet types + channel lifecycle | ~200 |

### Phase 2: Python Client + Server

| Component | Lines |
|-----------|-------|
| Router.py: SocketProxyClient (connects to local targets) | ~200 |
| Server: Socket relay (creates local sockets for sidecars) | ~200 |
| SSH agent handler (platform-specific agent path detection) | ~100 |

### Phase 3: Rust Data Plane Acceleration

| Component | Lines |
|-----------|-------|
| LZ4 per-channel compression (`lz4_flex` crate) | ~50 |
| ChaCha20-Poly1305 per-channel encryption | ~100 |
| Parallel connection pool (tokio) | ~200 |
| USB proxy via `rusb` crate | ~300 |

### Phase 4: Advanced Targets

| Component | Lines |
|-----------|-------|
| VRF-scoped socket (ip vrf exec wrapper) | ~50 |
| Raw capture (AF_PACKET + BPF) | ~200 |
| Serial port proxy | ~100 |
| PCIe control plane (VFIO/mmap) | ~200 |

---

## Compatibility

- **Wire compatible:** Lowercase ASCII packet types. No collision with existing uppercase types.
- **Framer unchanged:** Same `[length][type][payload][CRC32]` format.
- **Graceful degradation:** Old implementations ignore unknown packet types.
- **No version negotiation required** for basic operation.
