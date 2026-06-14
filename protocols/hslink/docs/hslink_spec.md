# HS/Link Protocol Specification

The packet-level specification of the HS/Link protocol, extracted from
the recovered source (primarily `HDK/HSPRIV.H`, cross-referenced with
`HSRECV.C` for receive and `HSTRANS.C` for transmit). This is the
document the 32-bit port must conform to **byte-for-byte**.

> **This is the interop contract.** A 32-bit HS/Link interoperates with
> the original 16-bit HS/Link if and only if it produces and consumes
> exactly these byte sequences. The bit-width of the program is
> invisible across the wire; the byte format is everything. Every
> structure that goes on the wire must be emitted with **pinned,
> fixed-width fields** (`stdint.h`) regardless of the host's native
> `int` size — the original was written assuming a 16-bit `int`, so a
> 32-bit build must not let wider native types change field sizes or
> packing.

All constants below are from the February 1994 DOS edition.

## Design in one paragraph

HS/Link is a **bidirectional, sliding-window, CRC-checked** file
transfer protocol. Both ends may send files simultaneously; an idle
pump interleaves transmit and receive so the two directions progress
together over one connection. Data is framed into packets with a
distinct lead-in and end marker, with control characters escaped so the
stream survives flow-control-sensitive links. Blocks are acknowledged by
sequence number; losses trigger NAK / extended-NAK and selective
resend. A chat channel rides the same framing as a dedicated packet
type.

## Framing

Special characters at the byte level:

| Name | Value | Meaning |
|------|------:|---------|
| `START_PACKET_CHR` | `0x02` | Packet lead-in |
| `END_PACKET_CHR` | `0x1B` | Packet end marker |
| `DLE_CHR` | `0x1E` | Data "escape" prefix |
| `XON_CHR` | `0x11` | Flow control |
| `XOFF_CHR` | `0x13` | Flow control |
| `CAN_CHR` | `0x18` | Cancel (Ctrl-X) |

A packet begins with `START_PACKET_CHR`, carries a **type byte**, a
type-specific payload, a CRC, and ends with `END_PACKET_CHR`. Any
occurrence of a special character *inside* payload/CRC is escaped with
`DLE_CHR` so it can't be mistaken for framing or eaten by flow control.

### Control-character mapping

Beyond DLE escaping, HS/Link can **remap** the five framing-sensitive
codes per-stream, via a `control_mapping` block optionally carried in a
data packet:

```
control_mapping {
    uchar xon_map;    // substitute code for XON
    uchar xoff_map;   // substitute code for XOFF
    uchar dle_map;    // substitute code for DLE
    uchar start_map;  // substitute code for START
    uchar end_map;    // substitute code for END
}
```

This let HS/Link adapt to links that swallowed particular control bytes.
Over a transparent TCP transport most of this can be negotiated to
identity mappings — but the negotiation must still be implemented for
compatibility with a real HS/Link peer, which *will* exercise it.

> **Telnet note:** when the transport is telnet/TCP (e.g. via NetFoss
> on the BBS side), the byte `0xFF` (telnet IAC) is handled by the
> **telnet layer beneath** HS/Link, not by HS/Link itself. Keep the two
> layers separate: strip/handle IAC in the transport, hand clean
> application bytes to the HS/Link framer. See `build-and-test.md`.

## Packet types

The type byte (`enum message_types`); "payload" notes the associated
structure:

| Byte | Name | Payload |
|------|------|---------|
| `A` | `PACK_ACK_BLOCK` | sequence_packet |
| `C` | `PACK_CLOSE_FILE` | — |
| `D` | `PACK_DATA_BLOCK_SMD` | sequence + mapping + data |
| `E` | `PACK_DATA_BLOCK_MD` | mapping + data |
| `F` | `PACK_DATA_BLOCK_D` | data only |
| `H` | `PACK_CHAT_BLOCK` | text (**the chat feature**) |
| `K` | `PACK_SKIP_FILE` | — |
| `M` | `PACK_EXTNAK_BLOCK` | extnak_packet |
| `N` | `PACK_NAK_BLOCK` | sequence_packet |
| `O` | `PACK_OPEN_FILE` | file_header_packet |
| `P` | `PACK_RESET_FILE` | — |
| `Q` | `PACK_READY_RECV` | — |
| `R` | `PACK_READY` | ready/handshake |
| `S` | `PACK_SEEK_BLOCK` | block_spec_packet |
| `V` | `PACK_VERIFY_BLOCK` | resume_verify_packet |
| `Z` | `PACK_TRANSMIT_DONE` | — |

The three data-packet variants (`D`/`E`/`F`) are an efficiency device: a
full packet carries sequence + control-mapping + data, but once those
are established the sender drops to mapping+data (`E`) or bare data (`F`)
to cut per-packet overhead. The `data_block_parts` flags (`SEQ_BLOCK=1`,
`MAP_BLOCK=2`, `DATA_BLOCK=0`) indicate which parts are present.

## Key structures

> Field widths shown are the original Turbo C types. In the 32-bit port,
> pin each on-the-wire field to a fixed width: `char`→`int8`,
> 16-bit `int`→`int16`, `long`→`int32`, `block_number`→its original
> width. Do **not** let a 32-bit native `int` widen these.

### File header (`PACK_OPEN_FILE`, type `O`)

```
file_header_packet {
    char  name[13];      // 8.3 filename
    long  size;          // bytes (32-bit)
    block_number blocks; // size in blocks
    int   BlockSize;     // data block size for this transfer (16-bit)
    ftime time;          // modification timestamp (DOS ftime layout)
    file_number batch;   // index within the batch (uchar)
    char  spare[20];
}
```

`struct ftime` is a packed 16-bit DOS date/time. The port must either
reproduce that exact packed layout on the wire or convert to/from it at
the boundary — a real 16-bit peer expects the DOS-format bytes.

> Treat this field as a **wire encoding only**: convert to/from a 64-bit
> host `time_t` at the packet boundary, and never carry the packed DOS
> form around as your working time type. This keeps the implementation
> Year-2038-safe regardless of 32- vs 64-bit build. See the time-handling
> note in `build-and-test.md`. (The DOS packed format's own range runs to
> ~2107 — unrelated to 2038, but it is still a narrow encoding, not a
> clock.)

### Sequence (used by ACK/NAK and data)

```
sequence_packet {
    file_number batch;   // which file in the batch (uchar)
    block_number block;  // which block within the file
}
```

### Data packet

```
data_packet {
    sequence_packet seq; // file + block (present if SEQ_BLOCK)
    control_mapping map; // remaps (present if MAP_BLOCK)
    uchar data[MAX_BLOCK_SIZE];  // up to 4096 bytes
}
```

### Extended NAK (`PACK_EXTNAK_BLOCK`, type `M`)

Reports an error plus CRCs of up to 8 partial sub-blocks, so the sender
can resend only the damaged spans rather than the whole block:

```
extnak_packet {
    sequence_packet seq;
    uchar nak_reason;
    uchar errlsr;             // UART line-status bits at error (legacy)
    long  errcsip;            // CS:IP at error (legacy diagnostic, 32-bit)
    CRC_type check[8];        // CRCs of partial blocks
}
```

> `errlsr`/`errcsip` are DOS-era diagnostics (serial line status and a
> real-mode code pointer). The port keeps the field layout for wire
> compatibility but can zero/ignore their meaning.

### Resume verification (`PACK_VERIFY_BLOCK`, type `V`)

Crash-recovery / resume: an array of up to 100 block CRCs from a base
block, so a resumed transfer can verify which blocks already arrived
intact and continue from there.

```
resume_verify_packet {
    block_number base_block;
    int count;                // 16-bit
    CRC_type check[100];      // CRCs to verify
}
```

## Sizes, windows, and CRC

| Constant | Value | Meaning |
|----------|------:|---------|
| `MAX_BLOCK_SIZE` | 4096 | Max data bytes per packet |
| `MAX_PENDING` | `EffSpeed/16` | Sliding-window depth (scales with line speed) |
| `DEF_CRC_SIZE` | 3 | Default CRC width = **24-bit** (16/24/32 supported) |
| `CANCEL_COUNT` | 4 | CANs needed to cancel a transfer |
| `EXTNAK_COUNT` | 8 | Partial-block CRCs per extended NAK |
| `MAX_VERIFY_COUNT` | 100 | Block CRCs per verify packet |

`CRC_type` is a 32-bit unsigned value; the negotiated width (16/24/32)
selects how many bits are significant. Window depth scaling with
effective speed is what let HS/Link keep fast links full.

## Timeouts (milliseconds)

| Constant | Value | Meaning |
|----------|------:|---------|
| `ACK_TIMEOUT` | 20000 | Before an ACK/FLOW timeout |
| `MAX_TIMEOUT` | 4 | ACK timeouts before cancelling the link |
| `RCV_TIMEOUT_N` | 22000 | Idle before rx timeout (no packet seen) |
| `RCV_TIMEOUT_P` | 11000 | Idle before rx timeout (partial packet) |
| `READY_TIMEOUT` | 120000 | Ready-handshake timeout |
| `ENQ_TIMEOUT` | 5000 | Before re-sending a ready/ENQ |
| `VERIFY_TIMEOUT` | 60000 | Extra time for verify/resume |
| `TERMINATE_TIMEOUT` | 500 | Idle before exit |
| `CHAT_TIMEOUT` | 2000 | Between outgoing chat blocks |
| `CHAT_CLOSE` | 30000 | Idle before the chat window closes |
| `KEYBOARD_POLL_TIME` | 110 | Between keyboard polls |

## Handshake & transfer flow (from `HSLINK.C` main)

1. **Open & identify.** `ComOpen()`, then send an identification string
   (registered vs. unregistered + sender name).
2. **Ready handshake.** Loop `wait_for_ready()` exchanging `PACK_READY` /
   `PACK_READY_RECV` until both ends agree (states in
   `ready_pending_codes`: INITIAL, FINAL, SEND_VERIFY, SEND_FILE).
3. **Transmit loop.** For each outgoing file: `PACK_OPEN_FILE`, then a
   stream of data packets (`D`/`E`/`F`) acknowledged by `PACK_ACK_BLOCK`,
   with `PACK_NAK_BLOCK` / `PACK_EXTNAK_BLOCK` driving selective resend;
   `PACK_CLOSE_FILE` at end. **Throughout, `service_receive()` runs on
   every idle tick**, so inbound files and chat are handled concurrently.
4. **Drain.** `PACK_TRANSMIT_DONE`, then `finish_receive()` until the
   peer is satisfied.
5. **Terminate.** `terminate_link()`, `ComClose()`.

## The chat channel

Chat is simply `PACK_CHAT_BLOCK` (type `H`) carrying text, interleaved
with data packets on the same framed stream. Keyboard input that isn't a
command initiates chat mode ("all other keyboard input initiates chat
mode" → `display_chatout()` in the source). Outgoing chat is rate-limited
by `CHAT_TIMEOUT` (2 s between blocks) and the window auto-closes after
`CHAT_CLOSE` (30 s) idle.

## Notes for the 32-bit implementation

  - The engine is a **cooperative state machine pumped by idle time** —
    no threads. Maps onto a `select()`/`poll()` socket loop, or an
    async model.
  - Order of work: framing (escape/unescape) → packet codec (the
    structs above, fixed-width) → the two state machines (`HSRECV.C` /
    `HSTRANS.C` as behavioral reference) → chat.
  - CRC: implement 16/24/32-bit with negotiation; default 24.
  - Control-mapping: implement negotiation; collapse to identity over a
    transparent transport, but be ready for a real peer to use it.
  - Legacy diagnostic fields (`errlsr`, `errcsip`) — preserve layout,
    ignore semantics.
  - **Validate byte-for-byte against a live 16-bit peer** before
    trusting the implementation (see `build-and-test.md`).
