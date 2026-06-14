//! HSLink protocol — DLE-escaped framer with CRC-24.
//!
//! Wire format:
//!   Frame: [STX][DLE-escaped: type + payload + CRC24][ETX]
//!   DLE escaping: 0x10 → 0x10 0x10 (doubled)
//!
//! This is the 1994 BBS protocol by Samuel H. Smith, faithful to the
//! original byte-level format for interop with vintage implementations.

// TODO: Implement HSLink framer
// For now, this module defines the interface that will be implemented.

/// HSLink packet type constants (single-byte identifiers).
pub const PACK_READY: u8 = b'R';
pub const PACK_READY_RECV: u8 = b'Q';
pub const PACK_DATA_BLOCK_SMD: u8 = b'D'; // Seq+Map+Data
pub const PACK_DATA_BLOCK_MD: u8 = b'E';  // Map+Data
pub const PACK_DATA_BLOCK_D: u8 = b'F';   // Data only
pub const PACK_ACK_BLOCK: u8 = b'A';
pub const PACK_NAK_BLOCK: u8 = b'N';
pub const PACK_EXTNAK_BLOCK: u8 = b'X';
pub const PACK_OPEN_FILE: u8 = b'O';
pub const PACK_CLOSE_FILE: u8 = b'C';
pub const PACK_SKIP_FILE: u8 = b'K';
pub const PACK_VERIFY_BLOCK: u8 = b'V';
pub const PACK_SEEK_BLOCK: u8 = b'S';
pub const PACK_CHAT_BLOCK: u8 = b'H';
pub const PACK_TRANSMIT_DONE: u8 = b'Z';

/// DLE escape/unescape constants.
pub const DLE_CHR: u8 = 0x10;
pub const STX_CHR: u8 = 0x02;
pub const ETX_CHR: u8 = 0x03;

// Implementation will follow in a subsequent commit.
// The HSLink framer needs:
// - DLE byte-stuffing (escape 0x10 as 0x10 0x10)
// - CRC-24 computation over unescaped data
// - STX/ETX frame delimiters
