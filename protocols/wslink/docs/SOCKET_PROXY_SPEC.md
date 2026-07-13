# WSLink Socket Proxy Extension — Protocol Specification

## Overview

Extends WSLink with bidirectional socket proxy channels, enabling tunneling
of arbitrary socket connections (SSH agent, Unix domain, TCP, VRF-scoped,
GRE, raw capture) over the existing WSLink WebSocket connection.

## Motivation

WSLink already carries two types of traffic over a single WebSocket:
- `PACK_CHAT_BLOCK` — JSON-RPC MCP tool calls
- `PACK_DATA_BLOCK` — Binary file transfers

Adding socket proxy channels enables:
- SSH agent forwarding (git authentication without key exposure)
- Unix domain socket proxying (Docker, D-Bus)
- TCP tunneling (database connections, service access)
- VRF-scoped access (network segmentation traversal)
- Raw interface capture (pcap-style traffic analysis)
- ATM/GRE/VXLAN tunnel tapping (encapsulated traffic inspection)

All multiplexed over the single existing WebSocket connection.

## Design Principles

1. **Backward compatible** — New packet types use lowercase ASCII, avoiding
   collision with existing uppercase types. Old implementations ignore
   unknown packet types.

2. **Channel multiplexed** — Multiple simultaneous socket connections, each
   identified by a 16-bit channel ID. Independent lifecycle per channel.

3. **Flow controlled** — Credit-based flow control prevents fast producer
   from overwhelming slow consumer.

4. **Zero new dependencies** — Uses existing WSLinkFramer (length-prefixed
   frames with CRC32). No changes to the framer.

## Frame Format

Uses the existing WSLink frame format:

```
[4-byte Length L][1-byte Type][Payload][4-byte CRC32]
```

## New Packet Types

Added to `const.py`:

| Type | Byte | Name | Direction | Purpose |
|------|------|------|-----------|---------|
| Socket Open | `b's'` | `PACK_SOCKET_OPEN` | Both | Open a proxy channel |
| Socket Data | `b'd'` | `PACK_SOCKET_DATA` | Both | Forward bytes on channel |
| Socket Close | `b'c'` | `PACK_SOCKET_CLOSE` | Both | Graceful channel close |
| Socket Error | `b'e'` | `PACK_SOCKET_ERROR` | Both | Channel error notification |
| Socket Window | `b'w'` | `PACK_SOCKET_WINDOW` | Both | Flow control credit grant |

## Packet Payloads

### PACK_SOCKET_OPEN (`b's'`)

Opens a new proxy channel. Sent by either side.

```
┌──────────────┬───────────┬──────────────────────────┐
│ Channel (2B) │ Flags (1B)│ Target (null-term string) │
│ uint16 LE    │ bitmask   │ UTF-8                     │
└──────────────┴───────────┴──────────────────────────┘
```

**Channel:** Unique 16-bit channel ID. Odd = client-initiated, Even = server-initiated.

**Flags:**
| Bit | Name | Description |
|-----|------|-------------|
| 0x01 | BIDIR | Bidirectional (default if not set) |
| 0x02 | READ_ONLY | Client can only read |
| 0x04 | WRITE_ONLY | Client can only write |
| 0x08 | COMPRESSED | LZ4 compress channel data |
| 0x10 | ENCRYPTED | Encrypt channel data (ChaCha20-Poly1305) |
| 0x20 | AUDIT | Log all traffic to audit trail |

**Target:** Null-terminated UTF-8 string specifying the proxy destination:

| Target Format | Description |
|---------------|-------------|
| `ssh-agent` | SSH agent socket (platform-specific path auto-detected) |
| `unix:<path>` | Unix domain socket |
| `tcp:<host>:<port>` | TCP connection |
| `udp:<host>:<port>` | UDP datagram relay |
| `tls:<host>:<port>` | TLS-wrapped TCP (terminated at router) |
| `vrf:<name>:tcp:<host>:<port>` | VRF-scoped TCP |
| `vrf:<name>:udp:<host>:<port>` | VRF-scoped UDP |
| `pipe:<name>` | Windows named pipe |
| `abstract:<name>` | Linux abstract namespace socket |
| `gre:<tunnel-id>:<remote-ip>` | GRE tunnel tap |
| `vxlan:<vni>:<vtep-ip>` | VXLAN overlay tap |
| `ipsec:<sa-name>` | IPsec SA tap (encrypted passthrough) |
| `ipsec:ike:<peer-ip>` | IKE negotiation monitor |
| `mpls:<label-stack>` | MPLS LSP tap |
| `atm:<if>:<vpi>:<vci>` | ATM cell stream |
| `atm:<if>:oam` | ATM OAM cells |
| `raw:<interface>` | Raw interface capture |
| `raw:<interface>:bpf:<filter>` | BPF-filtered capture |
| `socks5:<host>:<port>` | SOCKS5 proxy passthrough |
| `http-connect:<host>:<port>` | HTTP CONNECT tunnel |

### PACK_SOCKET_DATA (`b'd'`)

Forward bytes on an open channel.

```
┌──────────────┬───────────────────────────────────────┐
│ Channel (2B) │ Data (rest of payload)                 │
│ uint16 LE    │ raw bytes                              │
└──────────────┴───────────────────────────────────────┘
```

Maximum data per frame: `MAX_BLOCK_SIZE - 2` (4094 bytes at default block size).
Larger transfers are automatically fragmented across multiple DATA frames.

### PACK_SOCKET_CLOSE (`b'c'`)

Graceful channel close. Sent by either side.

```
┌──────────────┬───────────┐
│ Channel (2B) │ Code (2B) │
│ uint16 LE    │ uint16 LE │
└──────────────┴───────────┘
```

**Close codes:**
| Code | Name | Description |
|------|------|-------------|
| 0x0000 | NORMAL | Normal close |
| 0x0001 | TARGET_REFUSED | Target refused connection |
| 0x0002 | TARGET_UNREACHABLE | Target not reachable |
| 0x0003 | AUTH_FAILED | Authentication failed |
| 0x0004 | TIMEOUT | Connection timed out |
| 0x0005 | ADMIN_CLOSE | Closed by administrator |
| 0x0006 | PROTOCOL_ERROR | Protocol violation |

### PACK_SOCKET_ERROR (`b'e'`)

Channel error notification. Does not close the channel.

```
┌──────────────┬───────────┬──────────────────────────┐
│ Channel (2B) │ Code (2B) │ Message (rest, UTF-8)     │
│ uint16 LE    │ uint16 LE │                           │
└──────────────┴───────────┴──────────────────────────┘
```

### PACK_SOCKET_WINDOW (`b'w'`)

Flow control credit grant (credit-based, similar to SSH RFC 4254 §5.2).

```
┌──────────────┬────────────┐
│ Channel (2B) │ Credit (4B)│
│ uint16 LE    │ uint32 LE  │
└──────────────┴────────────┘
```

Initial credit: 65536 bytes per direction.
When consumer processes data, it sends WINDOW to grant more credit.
Producer MUST NOT send more DATA than available credit.

## Channel Lifecycle

```
Initiator                          Responder
    │                                  │
    │  SOCKET_OPEN (chan=1, target)     │
    │─────────────────────────────────►│
    │                                  │ connect to target
    │                                  │
    │  SOCKET_WINDOW (chan=1, 65536)   │ (initial credit)
    │◄─────────────────────────────────│
    │                                  │
    │  SOCKET_DATA (chan=1, bytes)      │
    │─────────────────────────────────►│ write to target
    │                                  │
    │  SOCKET_DATA (chan=1, bytes)      │ read from target
    │◄─────────────────────────────────│
    │                                  │
    │  SOCKET_WINDOW (chan=1, 4096)     │ grant more credit
    │─────────────────────────────────►│
    │                                  │
    │         ... bidirectional ...     │
    │                                  │
    │  SOCKET_CLOSE (chan=1, NORMAL)    │
    │─────────────────────────────────►│
    │                                  │ close local socket
    │  SOCKET_CLOSE (chan=1, NORMAL)    │
    │◄─────────────────────────────────│
    │                                  │
```

## SSH Agent Forwarding Example

```
Client (router.py)                 Server (Unimind)        Sidecar (CGA)
    │                                  │                      │
    │                                  │  CGA needs git auth  │
    │                                  │◄─────────────────────│
    │                                  │  SSH_AUTH_SOCK needed │
    │                                  │                      │
    │  SOCKET_OPEN(chan=2,              │                      │
    │    target="ssh-agent")           │                      │
    │◄─────────────────────────────────│                      │
    │                                  │                      │
    │  (connect to local ssh-agent)    │                      │
    │                                  │                      │
    │  SOCKET_WINDOW(chan=2, 65536)     │                      │
    │─────────────────────────────────►│                      │
    │                                  │  create unix socket   │
    │                                  │  SSH_AUTH_SOCK=/tmp/  │
    │                                  │  forwarded-agent.sock │
    │                                  │─────────────────────►│
    │                                  │                      │
    │                                  │  git clone git@...   │
    │                                  │◄─────────────────────│
    │                                  │                      │
    │  SOCKET_DATA(chan=2,              │  agent request        │
    │    SSH_AGENTC_REQUEST_IDENTITIES)│                      │
    │◄─────────────────────────────────│                      │
    │                                  │                      │
    │  (forward to local agent)        │                      │
    │  (agent returns public keys)     │                      │
    │                                  │                      │
    │  SOCKET_DATA(chan=2,              │                      │
    │    SSH_AGENT_IDENTITIES_ANSWER)  │                      │
    │─────────────────────────────────►│─────────────────────►│
    │                                  │                      │
    │  SOCKET_DATA(chan=2,              │  sign challenge      │
    │    SSH_AGENTC_SIGN_REQUEST)      │                      │
    │◄─────────────────────────────────│◄─────────────────────│
    │                                  │                      │
    │  (agent signs with private key)  │                      │
    │  (KEY NEVER LEAVES THIS MACHINE) │                      │
    │                                  │                      │
    │  SOCKET_DATA(chan=2,              │                      │
    │    SSH_AGENT_SIGN_RESPONSE)      │                      │
    │─────────────────────────────────►│─────────────────────►│
    │                                  │                      │
    │                                  │  auth succeeds       │
    │                                  │  clone proceeds      │
```

## Security Considerations

1. **Target validation:** The responder (router.py) MUST validate the target
   against an allow-list. Default: only `ssh-agent` and `tcp:localhost:*`.
   Network targets require explicit configuration.

2. **Channel limits:** Maximum 256 concurrent channels per session.

3. **Credit limits:** Producer MUST NOT send more than credited bytes.
   Violation = protocol error → channel close.

4. **Audit:** Channels with AUDIT flag log all traffic (encrypted if
   sensitive). Useful for compliance and forensic analysis.

5. **Encryption:** Channels with ENCRYPTED flag use ChaCha20-Poly1305
   for end-to-end encryption. Key exchange via initial OPEN handshake
   (X25519 ECDH in target metadata).

6. **VRF/raw access:** Network tap targets (raw, gre, atm) require
   explicit `--allow-network-tap` flag on router.py. Disabled by default.

## Implementation Files

| File | Changes |
|------|---------|
| `const.py` | Add 5 packet type constants |
| `protocol/structs/channel.py` | New — channel packet structs |
| `protocol/wslink.py` | Add ChannelManager to WSLinkSession |
| Router client (external) | SocketProxyClient — connects to local targets |
| Server handler (external) | Socket relay — creates local sockets for sidecars |

## Compatibility

- **Wire compatible:** New packet types use lowercase ASCII bytes (`s`, `d`,
  `c`, `e`, `w`). Existing types use uppercase. No collision possible.
- **Framer unchanged:** Same `[length][type][payload][CRC32]` format.
- **Graceful degradation:** Old implementations ignore unknown packet types.
  No version negotiation required for basic operation.
