//! ZMODEM protocol — ZDLE-escaped codec.
//!
//! ZMODEM uses ZDLE (0x18) as its escape character and encodes
//! control characters, XON/XOFF, and the ZDLE byte itself.
//!
//! This module provides the ZDLE encode/decode primitives and
//! header parsing, NOT the full session state machine (which
//! remains in Python for now due to its complexity).

// TODO: Implement ZMODEM ZDLE codec
// For now, this module defines the interface.

/// ZMODEM escape character.
pub const ZDLE: u8 = 0x18;

/// Characters that must be escaped in ZMODEM.
pub const MUST_ESCAPE: &[u8] = &[
    0x00, // NUL (some modems interpret)
    0x10, // DLE (XON/XOFF flow control)
    0x11, // DC1 (XON)
    0x13, // DC3 (XOFF)
    0x18, // CAN/ZDLE itself
    0x90, // High-bit DLE
    0x91, // High-bit XON
    0x93, // High-bit XOFF
];

/// ZMODEM header types.
pub const ZRQINIT: u8 = 0;
pub const ZRINIT: u8 = 1;
pub const ZSINIT: u8 = 2;
pub const ZACK: u8 = 3;
pub const ZFILE: u8 = 4;
pub const ZSKIP: u8 = 5;
pub const ZDATA: u8 = 10;
pub const ZEOF: u8 = 11;
pub const ZRPOS: u8 = 9;

// ZDLE encode/decode will be implemented here.
// The hot path is the per-byte escape check during data transmission.
