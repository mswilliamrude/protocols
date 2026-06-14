# WSLink Heartbeat Protocol Extension

**Date:** 2026-06-14
**Status:** Implemented in Skill_Multiagent, needs backport to protocols/
**Packet types added:** PACK_PING (b'P'), PACK_PONG (b'W')

---

## Problem

WSLink sessions die during idle periods because:
1. The `idle_timeout` (formerly 60s) kills the recv_loop when no packets arrive
2. MCP tool usage can be idle for minutes between interactions
3. WebSocket-level ping/pong proves TCP is alive but NOT that the WSLink session is processing
4. Without heartbeat, there's no way to distinguish "idle but alive" from "actually dead"

---

## Protocol Extension

### New Packet Types

| Type | Byte | Direction | Payload | Purpose |
|------|------|-----------|---------|---------|
| PING | `P` (0x50) | Either → Either | 8 bytes: `<d` (float64 LE timestamp) | Keepalive probe |
| PONG | `W` (0x57) | Responder → Initiator | Echo of PING payload | Confirms liveness + RTT |

### Wire Format

Same as all WSLink packets:
```
[4-byte LE length] [1-byte type 'P' or 'W'] [8-byte float64 timestamp] [4-byte LE CRC32]
```

Total frame size: 4 + 1 + 8 + 4 = **17 bytes per heartbeat**

### Behavior

1. **Sender:** Every `heartbeat_interval` seconds (default 20s), if no other packet was sent, transmit PING with current timestamp
2. **Receiver:** On receiving PING, immediately reply with PONG (echo payload)
3. **Timeout:** If no packet of ANY type received within `idle_timeout` seconds (default 60s = 3 missed heartbeats), consider connection dead and close
4. **RTT:** PONG payload contains the original PING timestamp — sender can measure round-trip time

### State Considerations

- PING/PONG are valid in **any** state (INIT, TRANSFERRING, or even after TRANSMIT_DONE)
- They do NOT reset file transfer state machines
- They DO reset the idle timeout counter (any received packet prevents timeout)
- Heartbeat loop runs as a separate asyncio task alongside recv_loop and send_loop

### Backward Compatibility

- Old peers that don't understand PING (`P`) will see it as an unknown packet type
- The security-patched WSLink code logs unknown types but doesn't crash:
  ```python
  # Packets not matching any handler fall through silently
  ```
- Old servers will ignore PING, meaning the client's heartbeat goes unanswered
- The client's idle_timeout still works as before — if server never responds to anything (including chat), connection dies after 60s
- **No handshake negotiation needed** — heartbeat is opt-in by the sender

---

## Configuration

### Router CLI Arguments

```
--heartbeat N          WSLink heartbeat interval in seconds (default: 20, 0=disable)
--heartbeat-timeout N  Drop connection after N seconds without any response (default: 60)
```

### Environment Variables

```
UNIMIND_HEARTBEAT_INTERVAL=20    # seconds between PINGs
UNIMIND_HEARTBEAT_TIMEOUT=60     # seconds before declaring connection dead
```

### WSLinkSession kwargs

```python
session = WSLinkSession(
    transport,
    heartbeat_interval=20.0,  # Send PING every 20s idle
    idle_timeout=60.0,        # Die after 60s silence (3 missed heartbeats)
)
```

Setting `heartbeat_interval=0` disables outgoing PINGs (but still responds to incoming PINGs).
Setting `idle_timeout=0` disables timeout entirely (connection lives until WebSocket closes).

---

## Implementation

### const.py Changes

```python
PACK_PING = b'P'        # Heartbeat ping (keepalive)
PACK_PONG = b'W'        # Heartbeat pong (response to ping)
```

### wslink.py — New heartbeat_loop task

```python
async def _heartbeat_loop(self):
    """Send periodic PING to keep the connection alive and detect dead peers."""
    while self.state != "DONE":
        await asyncio.sleep(self.heartbeat_interval)
        if self.state == "DONE":
            break
        try:
            payload = struct.pack('<d', time.time())
            await self.framer.send_packet_immediate(PACK_PING, payload)
        except Exception as e:
            log.warning(f"Heartbeat send failed: {e}")
            self.state = "DONE"
            break
```

### wslink.py — PING/PONG handlers (in _handle_packet, before all other handlers)

```python
if pkt_type == PACK_PING:
    await self.framer.send_packet_immediate(PACK_PONG, payload)
    return

if pkt_type == PACK_PONG:
    if len(payload) >= 8:
        ping_time = struct.unpack('<d', payload[:8])[0]
        rtt = time.time() - ping_time
        log.debug(f"Heartbeat PONG received (RTT: {rtt*1000:.1f}ms)")
    return
```

### wslink.py — Conditional idle timeout in recv_loop

```python
async def _recv_loop(self):
    while self.state != "DONE":
        try:
            if self.idle_timeout and self.idle_timeout > 0:
                packet = await asyncio.wait_for(
                    self.framer.read_packet(), timeout=self.idle_timeout
                )
            else:
                packet = await self.framer.read_packet()
        except asyncio.TimeoutError:
            log.warning(f"Idle timeout — no data in {self.idle_timeout}s. Dead connection.")
            self.state = "DONE"
            break
```

### loop() — Add heartbeat task to gather

```python
async def loop(self):
    await self.framer.send_packet_immediate(PACK_READY, b"")
    await self.framer.send_packet_immediate(PACK_READY_RECV, b"")
    
    recv_task = asyncio.create_task(self._recv_loop())
    send_task = asyncio.create_task(self._send_loop())
    heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    try:
        await asyncio.gather(recv_task, send_task, heartbeat_task)
    except Exception as e:
        self.state = "DONE"
```

---

## Interaction with Other Fixes

| Fix | Relationship |
|-----|-------------|
| Don't send Z without files (`batch_index > 0`) | Heartbeat keeps session alive even without Z |
| Don't transition to DONE on Z | Heartbeat continues after file transfer complete |
| Server `idle_timeout=0` | Server uses heartbeat for liveness, not timeout |
| WebSocket ping/pong (30s) | Lower layer — proves TCP. WSLink heartbeat proves session processing. |

---

## Testing

```python
def test_heartbeat_keeps_alive():
    """Connection survives 90s idle with heartbeat enabled."""
    # Start session with heartbeat_interval=20, idle_timeout=60
    # Send no application data for 90 seconds
    # Verify session still alive (recv_loop running, state != DONE)
    # Verify at least 4 PINGs were sent (90s / 20s = 4.5)

def test_heartbeat_detects_dead():
    """Connection dies after 60s without any response."""
    # Start session with heartbeat_interval=20, idle_timeout=60
    # Stop responding to ANY packets (simulate dead peer)
    # Verify session transitions to DONE after ~60s

def test_heartbeat_rtt():
    """RTT measurement from PING/PONG timestamps."""
    # Send PING with known timestamp
    # Receive PONG, verify payload matches
    # Measure RTT, verify reasonable (< 100ms for localhost)

def test_backward_compat():
    """Old peer ignores PING without crashing."""
    # Connect to server without heartbeat support
    # Send PINGs — verify no error, server stays alive
    # Chat messages still work
```

---

## TODO for protocols/ repo

1. Add `PACK_PING` and `PACK_PONG` to `protocols/wslink/const.py`
2. Add `_heartbeat_loop` to `protocols/wslink/protocol/wslink.py`
3. Add PING/PONG handlers to `_handle_packet`
4. Add conditional idle timeout to `_recv_loop`
5. Update Rust crate (`crates/pyprotocols-core/src/protocols/wslink.rs`) with heartbeat support
6. Add tests
