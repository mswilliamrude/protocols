//! Framer traits — define the interface for frame parsing and building.
//!
//! Each protocol implements this trait differently:
//! - WSLink: [4-byte length][1-byte type][payload][4-byte CRC32]
//! - HSLink: [STX][DLE-escaped data][ETX] with CRC-24
//! - ZMODEM: ZDLE-escaped with hex/binary header variants

use thiserror::Error;

/// Maximum frame size (1MB) — prevents OOM from malicious length fields.
pub const MAX_FRAME_SIZE: usize = 1 * 1024 * 1024;

/// Errors that can occur during frame parsing.
#[derive(Error, Debug, Clone, PartialEq)]
pub enum FrameError {
    /// Buffer doesn't contain a complete frame yet — need more data.
    #[error("incomplete frame: need at least {needed} bytes, have {available}")]
    Incomplete { needed: usize, available: usize },

    /// CRC mismatch — frame is corrupted.
    #[error("CRC mismatch: expected {expected:#010x}, got {actual:#010x}")]
    CrcMismatch { expected: u32, actual: u32 },

    /// Frame exceeds maximum allowed size.
    #[error("frame size {size} exceeds maximum {}", MAX_FRAME_SIZE)]
    TooLarge { size: usize },

    /// Invalid frame structure (e.g., length < minimum).
    #[error("invalid frame: {reason}")]
    Invalid { reason: String },
}

/// Result of successfully parsing a frame from a buffer.
#[derive(Debug, Clone)]
pub struct ParsedFrame {
    /// Number of bytes consumed from the input buffer.
    pub bytes_consumed: usize,
    /// Single-byte packet type identifier.
    pub pkt_type: u8,
    /// Payload bytes (excluding type and CRC).
    pub payload: Vec<u8>,
}

/// Trait for stateless frame parsing and building.
///
/// Implementations are protocol-specific but share this interface.
/// The framer does NO I/O — it operates on byte buffers.
pub trait Framer {
    /// Attempt to parse one complete frame from the front of `buf`.
    ///
    /// Returns:
    /// - `Ok(ParsedFrame)` — frame successfully parsed, consume `bytes_consumed` from buf
    /// - `Err(FrameError::Incomplete)` — need more data, don't consume anything
    /// - `Err(FrameError::CrcMismatch)` — frame corrupted, caller decides what to do
    /// - `Err(FrameError::TooLarge)` — frame exceeds size limit, reject
    fn parse_frame(&self, buf: &[u8]) -> Result<ParsedFrame, FrameError>;

    /// Build a complete frame from a packet type and payload.
    ///
    /// Returns the wire-ready bytes: header + type + payload + CRC.
    fn build_frame(&self, pkt_type: u8, payload: &[u8]) -> Vec<u8>;

    /// Build a frame directly into an existing buffer (zero-alloc fast path).
    ///
    /// Returns the number of bytes written into `out`.
    /// `out` must be large enough (caller ensures via `frame_size_for`).
    fn build_frame_into(&self, pkt_type: u8, payload: &[u8], out: &mut [u8]) -> usize;

    /// Calculate the exact wire size for a frame with the given payload length.
    fn frame_size_for(&self, payload_len: usize) -> usize;
}
