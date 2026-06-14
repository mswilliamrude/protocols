# HS/Link Protocol Specification

This document details the packet-level specification of the HS/Link protocol. It is the interop contract required to build a compatible implementation of the proprietary Samuel H. Smith HS/Link bidirectional transfer protocol.

## 1. Protocol Design Overview

HS/Link is a **bidirectional, sliding-window, CRC-checked** file transfer protocol. Both the sender and receiver can operate simultaneously over a single connection by interleaving transmit and receive operations in an idle pump loop. 

Data is framed into packets using distinct lead-in and end markers. Control characters are escaped so the stream survives flow-control-sensitive serial links. Packets are acknowledged by sequence number, and packet loss triggers a NAK / extended-NAK for selective resend (rather than dropping the entire transfer).

## 2. Framing and Escaping

Packets are framed at the byte level with special control characters:

| Name | Hex Value | Meaning |
|------|-----------|---------|
| `START_PACKET_CHR` | `0x02` | Packet lead-in |
| `END_PACKET_CHR` | `0x1B` | Packet end marker |
| `DLE_CHR` | `0x1E` | Data "escape" prefix |
| `XON_CHR` | `0x11` | Flow control (XON) |
| `XOFF_CHR` | `0x13` | Flow control (XOFF) |
| `CAN_CHR` | `0x18` | Cancel (Ctrl-X) |

**Packet Structure:**
A packet begins with `START_PACKET_CHR`, followed by a **type byte** (character), a type-specific payload, a CRC, and concludes with `END_PACKET_CHR`. 

**Escaping:**
Any occurrence of the 6 special characters *inside* the payload or the CRC must be escaped using `DLE_CHR`. In the original `HSRECV.C` reference, the escaped character is toggled (e.g., `byte ^ 0x40` or `byte ^ 0x80`) to ensure the byte transmitted is not one of the control characters. The receiver reverses this mapping.

## 3. Packet Types

The single type byte dictates the payload of the packet. 

| Byte | Name | Purpose |
|------|------|---------|
| `A` | `PACK_ACK_BLOCK` | Acknowledges a specific sequence block. |
| `C` | `PACK_CLOSE_FILE` | Indicates the end of a file. |
| `D` | `PACK_DATA_BLOCK_SMD`| Full data block: sequence + control mapping + data payload. |
| `E` | `PACK_DATA_BLOCK_MD` | Data block with control mapping + data payload. |
| `F` | `PACK_DATA_BLOCK_D` | Minimal overhead data block: data payload only. |
| `H` | `PACK_CHAT_BLOCK` | Contains chat text. |
| `K` | `PACK_SKIP_FILE` | Instructs peer to skip the current file. |
| `M` | `PACK_EXTNAK_BLOCK` | Extended NAK containing error reasons and sub-block CRCs. |
| `N` | `PACK_NAK_BLOCK` | Negative acknowledgment for a specific block sequence. |
| `O` | `PACK_OPEN_FILE` | Contains file metadata (name, size, timestamp). |
| `P` | `PACK_RESET_FILE` | Reset signal. |
| `Q` | `PACK_READY_RECV` | Ready to receive handshake. |
| `R` | `PACK_READY` | Ready handshake. |
| `S` | `PACK_SEEK_BLOCK` | Seek signal for resuming. |
| `V` | `PACK_VERIFY_BLOCK` | Verify block packet for resuming crashed transfers. |
| `Z` | `PACK_TRANSMIT_DONE` | All files have been transmitted. |

*Note on Data Packets:* `D`, `E`, and `F` act as an efficiency cascade. A full packet (`D`) establishes the sequence and mapping. Once established, the sender drops to mapping+data (`E`) or bare data (`F`) to reduce overhead.

## 4. Key Payload Structures

All multi-byte fields (like integers) must be processed carefully according to their original fixed-width 16-bit/32-bit DOS representations.

### File Header (`PACK_OPEN_FILE`, Type `O`)
```c
struct file_header_packet {
    char  name[13];      // 8.3 filename
    int32 size;          // File size in bytes
    int16 blocks;        // Size in blocks (original block_number type)
    int16 BlockSize;     // Data block size for this transfer
    uint16 time;         // DOS packed ftime timestamp
    uint8 batch;         // Index within the batch
    char  spare[20];
}
```

### Sequence Packet
Used by ACK, NAK, and Data packets.
```c
struct sequence_packet {
    uint8 batch;         // Which file in the batch
    int16 block;         // Which block within the file
}
```

### Data Packet
Data payloads can hold up to 4096 bytes.
```c
struct data_packet {
    sequence_packet seq; // File + block (Present if type 'D')
    control_mapping map; // Remaps (Present if type 'D' or 'E')
    uint8 data[MAX_BLOCK_SIZE]; // Up to 4096 bytes of data
}
```

### Extended NAK (`PACK_EXTNAK_BLOCK`, Type `M`)
Reports an error plus CRCs of up to 8 partial sub-blocks. This allows the sender to resend only damaged spans rather than the whole block.
```c
struct extnak_packet {
    sequence_packet seq;
    uint8 nak_reason;
    uint8 errlsr;        // legacy UART line-status (keep layout, can ignore)
    int32 errcsip;       // legacy CS:IP pointer (keep layout, can ignore)
    uint32 check[8];     // CRCs of partial blocks
}
```

### Resume Verify (`PACK_VERIFY_BLOCK`, Type `V`)
Used for crash recovery. Contains an array of CRCs so a resumed transfer can identify intact blocks.
```c
struct resume_verify_packet {
    int16 base_block;
    int16 count;
    uint32 check[100];   // Up to 100 block CRCs
}
```

## 5. CRC and Constants

- **CRC Types:** HS/Link negotiates 16-bit, 24-bit, or 32-bit CRCs. The default is **24-bit**. The CRC spans the Type byte + Payload. The escaping process (DLE) occurs *after* the CRC is calculated.
- **MAX_BLOCK_SIZE:** 4096 bytes.
- **MAX_PENDING (Window Depth):** Scales based on line speed (`EffSpeed / 16`). This sliding window controls how many unacknowledged packets can be in flight.
- **Timeouts:**
  - `ACK_TIMEOUT`: 20,000 ms.
  - `RCV_TIMEOUT_N`: 22,000 ms (Idle before rx timeout).
  - `RCV_TIMEOUT_P`: 11,000 ms (Idle for partial packet).

## 6. Connection Lifecycle & State Machine

The protocol relies on a cooperative event loop mapping closely to a state machine:

1. **Initialization:** The protocol attempts to identify the peer.
2. **Handshake:** Loop exchanging `PACK_READY` (`R`) and `PACK_READY_RECV` (`Q`) until both ends synchronize state.
3. **Transmit Loop:**
   - Send `PACK_OPEN_FILE` (`O`).
   - Stream data packets (`D`, `E`, `F`) using the sliding window.
   - Wait for `PACK_ACK_BLOCK` (`A`).
   - React to `PACK_NAK_BLOCK` (`N`) and `PACK_EXTNAK_BLOCK` (`M`) by re-queueing blocks.
   - Send `PACK_CLOSE_FILE` (`C`) at EOF.
4. **Interleaved Processing:** A cooperative `ComIdle` loop pumps both directions, receiving incoming bytes, sending outgoing bytes, and checking for chat/keyboard inputs simultaneously.
5. **Drain:** Send `PACK_TRANSMIT_DONE` (`Z`).
6. **Termination:** Close connection cleanly.

## 7. The Chat Channel

A unique feature of HS/Link is the ability to send interactive chat messages during a file transfer.
- Sent via `PACK_CHAT_BLOCK` (`H`).
- Rate-limited to one block every 2 seconds (`CHAT_TIMEOUT`).
- Idle window closes after 30 seconds (`CHAT_CLOSE`).
