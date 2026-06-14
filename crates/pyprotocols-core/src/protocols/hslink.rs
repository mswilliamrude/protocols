//! HSLink protocol — DLE-escaped framer with configurable CRC.
//!
//! Wire format:
//!   Frame: [STX][DLE-escaped: type + payload + CRC][ETX]
//!   DLE escaping: special byte → [DLE, byte ^ 0x80]
//!   Special bytes: STX(0x02), ETX(0x03), DLE(0x10), XON(0x11), XOFF(0x13), CAN(0x18)
//!
//! CRC is computed over unescaped (type + payload), appended little-endian,
//! then the whole (type + payload + CRC) region is DLE-escaped.
//!
//! This is the 1994 BBS protocol by Samuel H. Smith, faithful to the
//! original byte-level format for interop with vintage implementations.

use pyo3::prelude::*;
use pyo3::exceptions::{PyValueError, PyBufferError};
use crate::crc::{crc16, crc24, crc32_update};
use crate::framer::{Framer, FrameError, ParsedFrame, MAX_FRAME_SIZE};

// ─── Constants ───────────────────────────────────────────────────────

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
pub const XON_CHR: u8 = 0x11;
pub const XOFF_CHR: u8 = 0x13;
pub const CAN_CHR: u8 = 0x18;

/// Bytes that must be DLE-escaped on the wire.
#[cfg(test)]
const SPECIAL_BYTES: [u8; 6] = [STX_CHR, ETX_CHR, DLE_CHR, XON_CHR, XOFF_CHR, CAN_CHR];

/// DLE XOR mask for escaping/unescaping.
const DLE_XOR_MASK: u8 = 0x80;

/// Default CRC size in bytes (24-bit).
pub const DEF_CRC_SIZE: u8 = 3;

// ─── Helper functions ────────────────────────────────────────────────

/// Check if a byte requires DLE escaping.
#[inline]
fn is_special(byte: u8) -> bool {
    matches!(byte, STX_CHR | ETX_CHR | DLE_CHR | XON_CHR | XOFF_CHR | CAN_CHR)
}

/// DLE-escape a single byte: if special → [DLE, byte ^ 0x80], else → [byte].
#[inline]
fn escape_byte(byte: u8, out: &mut Vec<u8>) {
    if is_special(byte) {
        out.push(DLE_CHR);
        out.push(byte ^ DLE_XOR_MASK);
    } else {
        out.push(byte);
    }
}

/// Unescape a DLE-escaped byte: byte ^ 0x80.
#[inline]
fn unescape_byte(byte: u8) -> u8 {
    byte ^ DLE_XOR_MASK
}

/// Compute CRC over data with the given crc_size (2, 3, or 4 bytes).
/// Returns the CRC value.
fn compute_crc(data: &[u8], crc_size: u8) -> u32 {
    match crc_size {
        2 => crc16(data, 0) as u32,
        4 => crc32_update(0, data),
        _ => crc24(data, 0), // default: 3 bytes (24-bit)
    }
}

/// Append CRC as little-endian bytes.
fn append_crc_le(crc_val: u32, crc_size: u8, out: &mut Vec<u8>) {
    let bytes = crc_val.to_le_bytes();
    out.extend_from_slice(&bytes[..crc_size as usize]);
}

/// Read CRC from little-endian bytes.
fn read_crc_le(data: &[u8], crc_size: u8) -> u32 {
    let mut buf = [0u8; 4];
    let n = crc_size as usize;
    buf[..n].copy_from_slice(&data[..n]);
    u32::from_le_bytes(buf)
}

// ─── HSLink Framer Implementation ───────────────────────────────────

/// HSLink DLE-escaped framer with configurable CRC size.
#[derive(Debug, Clone)]
pub struct HSLinkFramerImpl {
    /// CRC size in bytes: 2 (CRC-16), 3 (CRC-24), or 4 (CRC-32).
    pub crc_size: u8,
}

impl HSLinkFramerImpl {
    /// Create a new HSLink framer with the given CRC size.
    pub fn new(crc_size: u8) -> Self {
        assert!(
            crc_size == 2 || crc_size == 3 || crc_size == 4,
            "crc_size must be 2, 3, or 4"
        );
        Self { crc_size }
    }
}

impl Default for HSLinkFramerImpl {
    fn default() -> Self {
        Self { crc_size: DEF_CRC_SIZE }
    }
}

impl Framer for HSLinkFramerImpl {
    /// Parse a DLE-escaped frame from the buffer.
    ///
    /// Scans for STX, unescapes bytes until ETX, verifies CRC.
    /// Returns ParsedFrame with bytes_consumed, pkt_type, payload.
    fn parse_frame(&self, buf: &[u8]) -> Result<ParsedFrame, FrameError> {
        // Find STX
        let stx_pos = match buf.iter().position(|&b| b == STX_CHR) {
            Some(pos) => pos,
            None => {
                return Err(FrameError::Incomplete {
                    needed: 1,
                    available: 0,
                });
            }
        };

        // State machine: unescape bytes between STX and ETX
        let mut unescaped = Vec::new();
        let mut escaped = false;
        let mut i = stx_pos + 1; // skip past STX
        let mut found_etx = false;

        while i < buf.len() {
            let b = buf[i];
            i += 1;

            if escaped {
                unescaped.push(unescape_byte(b));
                escaped = false;
            } else if b == DLE_CHR {
                escaped = true;
            } else if b == ETX_CHR {
                found_etx = true;
                break;
            } else if b == STX_CHR {
                // New STX restarts the frame — discard what we had
                unescaped.clear();
                escaped = false;
            } else if b == CAN_CHR {
                // Cancel — abort this frame
                return Err(FrameError::Invalid {
                    reason: "CAN byte received — frame aborted".to_string(),
                });
            } else {
                unescaped.push(b);
            }
        }

        if !found_etx {
            return Err(FrameError::Incomplete {
                needed: i + 1, // need at least one more byte (ETX)
                available: buf.len(),
            });
        }

        // bytes_consumed = everything up to and including ETX
        let bytes_consumed = i;

        // Validate minimum size: at least 1 (type) + crc_size
        let min_size = 1 + self.crc_size as usize;
        if unescaped.len() < min_size {
            return Err(FrameError::Invalid {
                reason: format!(
                    "frame too short: {} bytes unescaped, need at least {} (type + CRC)",
                    unescaped.len(),
                    min_size
                ),
            });
        }

        // Check for oversized frames
        if unescaped.len() > MAX_FRAME_SIZE {
            return Err(FrameError::TooLarge {
                size: unescaped.len(),
            });
        }

        // Split: [type + payload] [CRC]
        let crc_offset = unescaped.len() - self.crc_size as usize;
        let raw_data = &unescaped[..crc_offset];
        let received_crc = read_crc_le(&unescaped[crc_offset..], self.crc_size);

        // Compute expected CRC over (type + payload)
        let expected_crc = compute_crc(raw_data, self.crc_size);

        if received_crc != expected_crc {
            return Err(FrameError::CrcMismatch {
                expected: expected_crc,
                actual: received_crc,
            });
        }

        let pkt_type = raw_data[0];
        let payload = raw_data[1..].to_vec();

        Ok(ParsedFrame {
            bytes_consumed,
            pkt_type,
            payload,
        })
    }

    /// Build a DLE-escaped frame ready to send.
    ///
    /// Computes CRC over [pkt_type, ...payload], escapes all bytes
    /// (type + payload + CRC), wraps in [STX, escaped_data, ETX].
    fn build_frame(&self, pkt_type: u8, payload: &[u8]) -> Vec<u8> {
        // Compute CRC over unescaped (type + payload)
        let mut crc_input = Vec::with_capacity(1 + payload.len());
        crc_input.push(pkt_type);
        crc_input.extend_from_slice(payload);
        let crc_val = compute_crc(&crc_input, self.crc_size);

        // Append CRC as LE bytes to the data to be escaped
        let mut raw_with_crc = crc_input;
        append_crc_le(crc_val, self.crc_size, &mut raw_with_crc);

        // Build frame: STX + escaped(type + payload + CRC) + ETX
        // Worst case: every byte is escaped (doubled), so max size = 2 + 2*raw_len
        let mut frame = Vec::with_capacity(2 + raw_with_crc.len() * 2);
        frame.push(STX_CHR);
        for &b in &raw_with_crc {
            escape_byte(b, &mut frame);
        }
        frame.push(ETX_CHR);

        frame
    }

    /// Build a frame directly into an existing buffer.
    ///
    /// Returns the number of bytes written into `out`.
    fn build_frame_into(&self, pkt_type: u8, payload: &[u8], out: &mut [u8]) -> usize {
        // For DLE-escaped frames, the output size is variable.
        // Build into a temporary vec and copy — this is the safe path.
        let frame = self.build_frame(pkt_type, payload);
        let len = frame.len();
        out[..len].copy_from_slice(&frame);
        len
    }

    /// Calculate the maximum possible wire size for a frame.
    ///
    /// For DLE-escaped frames this is the worst case where every byte
    /// needs escaping: STX + 2*(1 + payload_len + crc_size) + ETX.
    fn frame_size_for(&self, payload_len: usize) -> usize {
        // Worst case: every byte in (type + payload + CRC) is special
        let raw_len = 1 + payload_len + self.crc_size as usize;
        2 + raw_len * 2 // STX + worst-case escaped data + ETX
    }
}

// ─── PyO3 Wrapper ────────────────────────────────────────────────────

/// Register HSLink constants in the Python module.
pub fn register_constants(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("HSLINK_PACK_READY", PACK_READY)?;
    m.add("HSLINK_PACK_READY_RECV", PACK_READY_RECV)?;
    m.add("HSLINK_PACK_DATA_BLOCK_SMD", PACK_DATA_BLOCK_SMD)?;
    m.add("HSLINK_PACK_DATA_BLOCK_MD", PACK_DATA_BLOCK_MD)?;
    m.add("HSLINK_PACK_DATA_BLOCK_D", PACK_DATA_BLOCK_D)?;
    m.add("HSLINK_PACK_ACK_BLOCK", PACK_ACK_BLOCK)?;
    m.add("HSLINK_PACK_NAK_BLOCK", PACK_NAK_BLOCK)?;
    m.add("HSLINK_PACK_EXTNAK_BLOCK", PACK_EXTNAK_BLOCK)?;
    m.add("HSLINK_PACK_OPEN_FILE", PACK_OPEN_FILE)?;
    m.add("HSLINK_PACK_CLOSE_FILE", PACK_CLOSE_FILE)?;
    m.add("HSLINK_PACK_SKIP_FILE", PACK_SKIP_FILE)?;
    m.add("HSLINK_PACK_VERIFY_BLOCK", PACK_VERIFY_BLOCK)?;
    m.add("HSLINK_PACK_SEEK_BLOCK", PACK_SEEK_BLOCK)?;
    m.add("HSLINK_PACK_CHAT_BLOCK", PACK_CHAT_BLOCK)?;
    m.add("HSLINK_PACK_TRANSMIT_DONE", PACK_TRANSMIT_DONE)?;
    m.add("DLE_CHR", DLE_CHR)?;
    m.add("STX_CHR", STX_CHR)?;
    m.add("ETX_CHR", ETX_CHR)?;
    m.add("XON_CHR", XON_CHR)?;
    m.add("XOFF_CHR", XOFF_CHR)?;
    m.add("CAN_CHR", CAN_CHR)?;
    m.add("DEF_CRC_SIZE", DEF_CRC_SIZE)?;
    Ok(())
}

/// Python-facing HSLink DLE-escaped framer.
///
/// Provides static methods for frame parsing and building, mirroring
/// the WSLinkFramer API pattern.
#[pyclass]
#[derive(Debug, Clone)]
pub struct HSLinkFramer {
    crc_size: u8,
}

#[pymethods]
impl HSLinkFramer {
    /// Create a new HSLinkFramer with configurable CRC size.
    ///
    /// Args:
    ///     crc_size: CRC size in bytes (2, 3, or 4). Default: 3 (CRC-24).
    #[new]
    #[pyo3(signature = (crc_size=3))]
    pub fn new(crc_size: u8) -> PyResult<Self> {
        if crc_size != 2 && crc_size != 3 && crc_size != 4 {
            return Err(PyValueError::new_err(
                "crc_size must be 2, 3, or 4",
            ));
        }
        Ok(Self { crc_size })
    }

    /// Parse a complete DLE-escaped frame from a buffer.
    ///
    /// Returns: (bytes_consumed, pkt_type, payload)
    /// Raises: ValueError on CRC mismatch or invalid frame.
    ///         BufferError if data is incomplete (no STX..ETX).
    #[staticmethod]
    #[pyo3(signature = (data, crc_size=3))]
    pub fn parse_frame(data: &[u8], crc_size: u8) -> PyResult<(usize, u8, Vec<u8>)> {
        let framer = HSLinkFramerImpl::new(crc_size);
        Self::result_to_py(framer.parse_frame(data))
    }

    /// Build a complete DLE-escaped frame ready to send.
    ///
    /// Returns: bytes [STX][escaped type+payload+CRC][ETX]
    #[staticmethod]
    #[pyo3(signature = (pkt_type, payload, crc_size=3))]
    pub fn build_frame(pkt_type: u8, payload: &[u8], crc_size: u8) -> PyResult<Vec<u8>> {
        if crc_size != 2 && crc_size != 3 && crc_size != 4 {
            return Err(PyValueError::new_err("crc_size must be 2, 3, or 4"));
        }
        let framer = HSLinkFramerImpl::new(crc_size);
        Ok(Framer::build_frame(&framer, pkt_type, payload))
    }

    /// Instance method: parse using this framer's configured CRC size.
    pub fn parse(&self, data: &[u8]) -> PyResult<(usize, u8, Vec<u8>)> {
        let framer = HSLinkFramerImpl::new(self.crc_size);
        Self::result_to_py(framer.parse_frame(data))
    }

    /// Instance method: build using this framer's configured CRC size.
    pub fn build(&self, pkt_type: u8, payload: &[u8]) -> Vec<u8> {
        let framer = HSLinkFramerImpl::new(self.crc_size);
        Framer::build_frame(&framer, pkt_type, payload)
    }

    /// Escape a single byte using DLE byte-stuffing.
    ///
    /// Returns: escaped bytes (1 or 2 bytes).
    #[staticmethod]
    pub fn escape(byte: u8) -> Vec<u8> {
        let mut out = Vec::with_capacity(2);
        escape_byte(byte, &mut out);
        out
    }

    /// Unescape a DLE-escaped byte.
    ///
    /// Returns: the original byte (input ^ 0x80).
    #[staticmethod]
    pub fn unescape(byte: u8) -> u8 {
        unescape_byte(byte)
    }

    /// Check if a byte is a special byte that requires DLE escaping.
    #[staticmethod]
    pub fn is_special(byte: u8) -> bool {
        is_special(byte)
    }

    /// CRC-24 computation (polynomial 0x864CFB).
    #[staticmethod]
    pub fn crc24(data: &[u8]) -> u32 {
        crc24(data, 0)
    }
}

impl HSLinkFramer {
    /// Convert a parse result to PyResult.
    fn result_to_py(result: Result<ParsedFrame, FrameError>) -> PyResult<(usize, u8, Vec<u8>)> {
        match result {
            Ok(frame) => Ok((frame.bytes_consumed, frame.pkt_type, frame.payload)),
            Err(FrameError::Incomplete { needed, available }) => {
                Err(PyBufferError::new_err(format!(
                    "incomplete frame: need {} bytes, have {}",
                    needed, available
                )))
            }
            Err(FrameError::CrcMismatch { expected, actual }) => {
                Err(PyValueError::new_err(format!(
                    "CRC mismatch: expected {:#010x}, got {:#010x}",
                    expected, actual
                )))
            }
            Err(e) => Err(PyValueError::new_err(e.to_string())),
        }
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_roundtrip() {
        let framer = HSLinkFramerImpl::default(); // CRC-24
        let payload = b"hello world";
        let frame = framer.build_frame(PACK_DATA_BLOCK_SMD, payload);

        // Frame must start with STX and end with ETX
        assert_eq!(frame[0], STX_CHR);
        assert_eq!(*frame.last().unwrap(), ETX_CHR);

        // Parse it back
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_DATA_BLOCK_SMD);
        assert_eq!(parsed.payload, payload);
        assert_eq!(parsed.bytes_consumed, frame.len());
    }

    #[test]
    fn test_frame_roundtrip_crc16() {
        let framer = HSLinkFramerImpl::new(2);
        let payload = b"CRC-16 test data";
        let frame = framer.build_frame(PACK_READY, payload);
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_READY);
        assert_eq!(parsed.payload, payload);
    }

    #[test]
    fn test_frame_roundtrip_crc32() {
        let framer = HSLinkFramerImpl::new(4);
        let payload = b"CRC-32 test data";
        let frame = framer.build_frame(PACK_ACK_BLOCK, payload);
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_ACK_BLOCK);
        assert_eq!(parsed.payload, payload);
    }

    #[test]
    fn test_dle_escape_special_bytes() {
        let framer = HSLinkFramerImpl::default();

        // Build a payload containing ALL 6 special bytes
        let payload = vec![STX_CHR, ETX_CHR, DLE_CHR, XON_CHR, XOFF_CHR, CAN_CHR];
        let frame = framer.build_frame(PACK_CHAT_BLOCK, &payload);

        // Verify: the frame interior (between STX and ETX) must NOT contain
        // any raw special bytes — they should all be escaped as [DLE, byte^0x80]
        let inner = &frame[1..frame.len() - 1]; // strip STX/ETX
        for (i, &b) in inner.iter().enumerate() {
            if SPECIAL_BYTES.contains(&b) {
                // Only DLE_CHR is allowed (as escape prefix), never raw specials
                assert_eq!(b, DLE_CHR, "raw special byte {:#04x} found at position {}", b, i);
                // The byte after DLE must be the escaped form (original ^ 0x80)
                // and must NOT itself be a raw special byte
                assert!(i + 1 < inner.len(), "DLE at end of frame");
                let next = inner[i + 1];
                // next should be (original ^ 0x80), verify it's > 0x80 range
                assert_ne!(next & DLE_XOR_MASK, 0, "escaped byte missing 0x80 bit");
            }
        }

        // Parse must recover the original payload
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.payload, payload);
    }

    #[test]
    fn test_crc_mismatch_detected() {
        let framer = HSLinkFramerImpl::default();
        let frame = framer.build_frame(PACK_DATA_BLOCK_SMD, b"test data");

        // Corrupt a byte in the middle of the escaped content
        let mut corrupted = frame.clone();
        // Find a non-special byte in the middle to corrupt
        let mid = corrupted.len() / 2;
        corrupted[mid] ^= 0x01;

        let result = framer.parse_frame(&corrupted);
        // Should either fail with CrcMismatch or Invalid (if corruption
        // breaks the framing structure)
        assert!(
            matches!(result, Err(FrameError::CrcMismatch { .. }) | Err(FrameError::Invalid { .. })),
            "expected CRC mismatch or invalid frame, got: {:?}",
            result
        );
    }

    #[test]
    fn test_incomplete_frame() {
        let framer = HSLinkFramerImpl::default();
        let frame = framer.build_frame(PACK_READY, b"hello");

        // Remove the ETX (last byte) — should report incomplete
        let partial = &frame[..frame.len() - 1];
        let result = framer.parse_frame(partial);
        assert!(
            matches!(result, Err(FrameError::Incomplete { .. })),
            "expected Incomplete, got: {:?}",
            result
        );

        // Empty buffer — also incomplete
        let result = framer.parse_frame(&[]);
        assert!(matches!(result, Err(FrameError::Incomplete { .. })));

        // Just STX, no content — incomplete
        let result = framer.parse_frame(&[STX_CHR]);
        assert!(matches!(result, Err(FrameError::Incomplete { .. })));
    }

    #[test]
    fn test_multiple_frames_in_buffer() {
        let framer = HSLinkFramerImpl::default();

        let frame1 = framer.build_frame(PACK_READY, b"first");
        let frame2 = framer.build_frame(PACK_ACK_BLOCK, b"second");
        let frame3 = framer.build_frame(PACK_CHAT_BLOCK, b"third");

        // Concatenate all frames
        let mut buffer = Vec::new();
        buffer.extend_from_slice(&frame1);
        buffer.extend_from_slice(&frame2);
        buffer.extend_from_slice(&frame3);

        // Parse first frame
        let parsed1 = framer.parse_frame(&buffer).unwrap();
        assert_eq!(parsed1.pkt_type, PACK_READY);
        assert_eq!(parsed1.payload, b"first");

        // Parse second frame from remainder
        let rest = &buffer[parsed1.bytes_consumed..];
        let parsed2 = framer.parse_frame(rest).unwrap();
        assert_eq!(parsed2.pkt_type, PACK_ACK_BLOCK);
        assert_eq!(parsed2.payload, b"second");

        // Parse third frame from remainder
        let rest = &rest[parsed2.bytes_consumed..];
        let parsed3 = framer.parse_frame(rest).unwrap();
        assert_eq!(parsed3.pkt_type, PACK_CHAT_BLOCK);
        assert_eq!(parsed3.payload, b"third");

        // Verify total bytes consumed
        assert_eq!(
            parsed1.bytes_consumed + parsed2.bytes_consumed + parsed3.bytes_consumed,
            buffer.len()
        );
    }

    #[test]
    fn test_garbage_before_stx_is_skipped() {
        let framer = HSLinkFramerImpl::default();
        let frame = framer.build_frame(PACK_READY, b"clean");

        // Prepend garbage bytes before the frame
        let mut buf = vec![0xAA, 0xBB, 0xCC, 0xDD];
        buf.extend_from_slice(&frame);

        let parsed = framer.parse_frame(&buf).unwrap();
        assert_eq!(parsed.pkt_type, PACK_READY);
        assert_eq!(parsed.payload, b"clean");
        // bytes_consumed includes the garbage prefix + frame
        assert_eq!(parsed.bytes_consumed, 4 + frame.len());
    }

    #[test]
    fn test_empty_payload() {
        let framer = HSLinkFramerImpl::default();
        let frame = framer.build_frame(PACK_TRANSMIT_DONE, b"");
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_TRANSMIT_DONE);
        assert_eq!(parsed.payload, b"");
    }

    #[test]
    fn test_escape_unescape_roundtrip() {
        // Every byte should survive escape → unescape
        for b in 0u8..=255 {
            let mut escaped = Vec::new();
            escape_byte(b, &mut escaped);
            if is_special(b) {
                assert_eq!(escaped.len(), 2);
                assert_eq!(escaped[0], DLE_CHR);
                assert_eq!(unescape_byte(escaped[1]), b);
            } else {
                assert_eq!(escaped.len(), 1);
                assert_eq!(escaped[0], b);
            }
        }
    }

    #[test]
    fn test_binary_payload_all_bytes() {
        let framer = HSLinkFramerImpl::default();
        // Payload with every possible byte value
        let payload: Vec<u8> = (0..=255).collect();
        let frame = framer.build_frame(PACK_DATA_BLOCK_D, &payload);
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_DATA_BLOCK_D);
        assert_eq!(parsed.payload, payload);
    }

    #[test]
    fn test_frame_size_for_worst_case() {
        let framer = HSLinkFramerImpl::default();
        // frame_size_for returns max possible size
        let max_size = framer.frame_size_for(10);
        // Actual frame with non-special payload should be smaller
        let frame = framer.build_frame(PACK_READY, &[0x41; 10]); // 'A' is not special
        assert!(frame.len() <= max_size);
    }
}
