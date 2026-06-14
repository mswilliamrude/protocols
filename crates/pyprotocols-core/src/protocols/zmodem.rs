//! ZMODEM protocol — ZDLE-escaped codec.
//!
//! ZMODEM uses ZDLE (0x18) as its escape character and encodes
//! control characters, XON/XOFF, and the ZDLE byte itself.
//!
//! This module provides the ZDLE encode/decode primitives and
//! header parsing, NOT the full session state machine (which
//! remains in Python for now due to its complexity).

use pyo3::prelude::*;
use pyo3::types::PyDict;
use thiserror::Error;

use crate::crc::crc16;

// ─── Constants ───────────────────────────────────────────────────────────────

/// ZMODEM escape character.
pub const ZDLE: u8 = 0x18;

/// Padding character '*'.
pub const ZPAD: u8 = 0x2A;

/// Hex header indicator 'B'.
pub const ZHEX: u8 = 0x42;

/// Binary header (CRC-16) indicator 'A'.
pub const ZBIN: u8 = 0x41;

/// Binary header (CRC-32) indicator 'C'.
pub const ZBIN32: u8 = 0x43;

// Frame end types
/// End of frame, no ACK expected.
pub const ZCRCE: u8 = 0x68; // 'h'
/// End of frame, continue (streaming).
pub const ZCRCG: u8 = 0x69; // 'i'
/// End of frame, ACK required.
pub const ZCRCQ: u8 = 0x6A; // 'j'
/// End of frame, wait for answer.
pub const ZCRCW: u8 = 0x6B; // 'k'

// Header types
pub const ZRQINIT: u8 = 0;
pub const ZRINIT: u8 = 1;
pub const ZSINIT: u8 = 2;
pub const ZACK: u8 = 3;
pub const ZFILE: u8 = 4;
pub const ZSKIP: u8 = 5;
pub const ZRPOS: u8 = 9;
pub const ZDATA: u8 = 10;
pub const ZEOF: u8 = 11;
pub const ZCAN: u8 = 18;

/// XON flow control.
const XON: u8 = 0x11;
/// XOFF flow control.
const XOFF: u8 = 0x13;
/// DLE (data link escape).
const DLE: u8 = 0x10;
/// CR (carriage return).
const CR: u8 = 0x0D;

// ─── Error type ──────────────────────────────────────────────────────────────

/// Errors from ZDLE decode and header parsing.
#[derive(Error, Debug, Clone, PartialEq)]
pub enum ZdleError {
    /// Data is truncated mid-escape sequence.
    #[error("truncated: ZDLE at end of data")]
    Truncated,

    /// CRC mismatch in header.
    #[error("CRC mismatch: expected {expected:#06x}, got {actual:#06x}")]
    CrcMismatch { expected: u16, actual: u16 },

    /// Invalid header format.
    #[error("invalid header: {reason}")]
    InvalidHeader { reason: String },

    /// Invalid hex digit in hex header.
    #[error("invalid hex digit: {byte:#04x}")]
    InvalidHexDigit { byte: u8 },
}

// ─── Header struct ───────────────────────────────────────────────────────────

/// Parsed ZMODEM header (type + 4 position bytes as little-endian u32).
#[derive(Debug, Clone, PartialEq)]
pub struct ZmodemHeader {
    pub header_type: u8,
    pub position: u32, // p0..p3 assembled as little-endian u32
}

// ─── ZDLE Encode ─────────────────────────────────────────────────────────────

/// Returns true if a byte must always be ZDLE-escaped.
#[inline]
fn must_escape(byte: u8) -> bool {
    matches!(byte, ZDLE | XON | XOFF | DLE | 0x90 | 0x91 | 0x93)
}

/// ZDLE-encode a data buffer.
///
/// Escapes all bytes requiring ZDLE escaping per the ZMODEM spec:
/// - ZDLE itself, XON, XOFF, DLE, and their high-bit variants (0x90, 0x91, 0x93)
/// - CR (0x0D) when preceded by '@' (0x40) — prevents modem command injection
///
/// `prev_byte` is the byte immediately before `data[0]` (for the '@'+CR rule
/// at chunk boundaries). Pass 0 for the start of a stream.
pub fn zdle_encode(data: &[u8], prev_byte: u8) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() * 2);
    let mut prev = prev_byte;

    for &byte in data {
        if must_escape(byte) {
            out.push(ZDLE);
            out.push(byte ^ 0x40);
        } else if byte == CR && prev == b'@' {
            out.push(ZDLE);
            out.push(byte ^ 0x40);
        } else {
            out.push(byte);
        }
        prev = byte;
    }

    out
}

// ─── ZDLE Decode ─────────────────────────────────────────────────────────────

/// ZDLE-decode a data subpacket stream.
///
/// Consumes ZDLE-escaped bytes until a frame-end sequence `[ZDLE, 0x68..0x6B]`
/// is encountered. Returns (decoded_data, bytes_consumed) where `bytes_consumed`
/// includes up to and including the frame-end marker (but not the CRC that follows).
///
/// # Errors
/// - `ZdleError::Truncated` if data ends mid-escape (ZDLE at last position).
pub fn zdle_decode(data: &[u8]) -> Result<(Vec<u8>, usize), ZdleError> {
    let mut out = Vec::with_capacity(data.len());
    let mut i = 0;

    while i < data.len() {
        let byte = data[i];
        if byte == ZDLE {
            if i + 1 >= data.len() {
                return Err(ZdleError::Truncated);
            }
            let next = data[i + 1];
            // Frame end types terminate the subpacket
            if (ZCRCE..=ZCRCW).contains(&next) {
                // consumed includes [ZDLE, frame_end]
                return Ok((out, i + 2));
            }
            // Normal escape: XOR with 0x40 to recover original
            out.push(next ^ 0x40);
            i += 2;
        } else {
            out.push(byte);
            i += 1;
        }
    }

    // Reached end of data without a frame-end marker
    Err(ZdleError::Truncated)
}

// ─── Hex Header Parse ────────────────────────────────────────────────────────

/// Decode a single hex ASCII digit to its value.
#[inline]
fn hex_digit(b: u8) -> Result<u8, ZdleError> {
    match b {
        b'0'..=b'9' => Ok(b - b'0'),
        b'a'..=b'f' => Ok(b - b'a' + 10),
        b'A'..=b'F' => Ok(b - b'A' + 10),
        _ => Err(ZdleError::InvalidHexDigit { byte: b }),
    }
}

/// Decode a hex ASCII pair to a byte.
#[inline]
fn hex_pair(hi: u8, lo: u8) -> Result<u8, ZdleError> {
    Ok((hex_digit(hi)? << 4) | hex_digit(lo)?)
}

/// Parse a ZMODEM hex header.
///
/// Expected input format (starting from the first ZPAD):
///   `ZPAD ZPAD ZDLE ZHEX type_hi type_lo p0_hi p0_lo p1_hi p1_lo p2_hi p2_lo p3_hi p3_lo crc_hi_hi crc_hi_lo crc_lo_hi crc_lo_lo CR LF [XON]`
///
/// All data bytes (type, p0-p3, crc) are encoded as two hex ASCII characters each.
///
/// # Errors
/// - `InvalidHeader` if the prefix/structure is wrong
/// - `InvalidHexDigit` if non-hex characters are found
/// - `CrcMismatch` if CRC-16 doesn't match
pub fn parse_hex_header(data: &[u8]) -> Result<ZmodemHeader, ZdleError> {
    // Minimum: ZPAD ZPAD ZDLE ZHEX + 5 bytes * 2 hex chars + 2 CRC bytes * 2 hex chars + CR LF = 4 + 10 + 4 + 2 = 20
    if data.len() < 20 {
        return Err(ZdleError::InvalidHeader {
            reason: format!("hex header too short: {} bytes", data.len()),
        });
    }

    // Validate prefix: ** ZDLE B
    if data[0] != ZPAD || data[1] != ZPAD || data[2] != ZDLE || data[3] != ZHEX {
        return Err(ZdleError::InvalidHeader {
            reason: "expected ZPAD ZPAD ZDLE ZHEX prefix".into(),
        });
    }

    // Decode the 5 data bytes (type + p0..p3) from hex
    let hex_start = 4;
    let header_type = hex_pair(data[hex_start], data[hex_start + 1])?;
    let p0 = hex_pair(data[hex_start + 2], data[hex_start + 3])?;
    let p1 = hex_pair(data[hex_start + 4], data[hex_start + 5])?;
    let p2 = hex_pair(data[hex_start + 6], data[hex_start + 7])?;
    let p3 = hex_pair(data[hex_start + 8], data[hex_start + 9])?;

    // Decode CRC-16 from hex (2 bytes = 4 hex chars)
    let crc_start = hex_start + 10;
    if data.len() < crc_start + 4 {
        return Err(ZdleError::InvalidHeader {
            reason: "hex header truncated at CRC".into(),
        });
    }
    let crc_hi = hex_pair(data[crc_start], data[crc_start + 1])?;
    let crc_lo = hex_pair(data[crc_start + 2], data[crc_start + 3])?;
    let received_crc = ((crc_hi as u16) << 8) | (crc_lo as u16);

    // Compute CRC-16 over [type, p0, p1, p2, p3]
    let crc_data = [header_type, p0, p1, p2, p3];
    let computed_crc = crc16(&crc_data, 0);

    if received_crc != computed_crc {
        return Err(ZdleError::CrcMismatch {
            expected: computed_crc,
            actual: received_crc,
        });
    }

    let position = u32::from_le_bytes([p0, p1, p2, p3]);

    Ok(ZmodemHeader {
        header_type,
        position,
    })
}

// ─── Binary Header Parse ─────────────────────────────────────────────────────

/// Parse a ZMODEM binary (CRC-16) header.
///
/// Expected input format (starting from the first ZPAD):
///   `ZPAD ZDLE ZBIN <ZDLE-escaped: type p0 p1 p2 p3 crc_lo crc_hi>`
///
/// The type and position bytes (and CRC) are ZDLE-escaped. CRC-16 is computed
/// over [type, p0, p1, p2, p3].
///
/// # Errors
/// - `InvalidHeader` if the prefix/structure is wrong
/// - `Truncated` if data ends mid-escape
/// - `CrcMismatch` if CRC-16 doesn't match
pub fn parse_bin_header(data: &[u8]) -> Result<ZmodemHeader, ZdleError> {
    // Minimum: ZPAD ZDLE ZBIN + at least 7 unescaped bytes (type+p0..p3+crc_lo+crc_hi)
    if data.len() < 10 {
        return Err(ZdleError::InvalidHeader {
            reason: format!("binary header too short: {} bytes", data.len()),
        });
    }

    // Validate prefix: * ZDLE A
    if data[0] != ZPAD || data[1] != ZDLE || data[2] != ZBIN {
        return Err(ZdleError::InvalidHeader {
            reason: "expected ZPAD ZDLE ZBIN prefix".into(),
        });
    }

    // ZDLE-decode 7 bytes: type + p0 + p1 + p2 + p3 + crc_hi + crc_lo
    let mut decoded = Vec::with_capacity(7);
    let mut i = 3; // start after prefix

    while decoded.len() < 7 {
        if i >= data.len() {
            return Err(ZdleError::Truncated);
        }
        if data[i] == ZDLE {
            if i + 1 >= data.len() {
                return Err(ZdleError::Truncated);
            }
            decoded.push(data[i + 1] ^ 0x40);
            i += 2;
        } else {
            decoded.push(data[i]);
            i += 1;
        }
    }

    let header_type = decoded[0];
    let p0 = decoded[1];
    let p1 = decoded[2];
    let p2 = decoded[3];
    let p3 = decoded[4];
    let crc_hi = decoded[5];
    let crc_lo = decoded[6];

    let received_crc = ((crc_hi as u16) << 8) | (crc_lo as u16);

    // CRC-16 over [type, p0, p1, p2, p3]
    let crc_data = [header_type, p0, p1, p2, p3];
    let computed_crc = crc16(&crc_data, 0);

    if received_crc != computed_crc {
        return Err(ZdleError::CrcMismatch {
            expected: computed_crc,
            actual: received_crc,
        });
    }

    let position = u32::from_le_bytes([p0, p1, p2, p3]);

    Ok(ZmodemHeader {
        header_type,
        position,
    })
}

// ─── PyO3 Wrapper ────────────────────────────────────────────────────────────

/// Python-facing ZMODEM codec with static methods.
#[pyclass]
#[pyo3(name = "ZmodemCodec")]
pub struct PyZmodemCodec;

#[pymethods]
impl PyZmodemCodec {
    #[new]
    fn new() -> Self {
        Self
    }

    /// ZDLE-encode binary data.
    ///
    /// Returns the escaped byte sequence. Uses 0 as the initial prev_byte.
    #[staticmethod]
    fn encode(data: &[u8]) -> Vec<u8> {
        zdle_encode(data, 0)
    }

    /// ZDLE-decode a data subpacket.
    ///
    /// Returns (decoded_bytes, bytes_consumed). Raises ValueError on truncation.
    #[staticmethod]
    fn decode(data: &[u8]) -> PyResult<(Vec<u8>, usize)> {
        zdle_decode(data).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(e.to_string())
        })
    }

    /// Parse a ZMODEM header (auto-detects hex vs binary format).
    ///
    /// Returns a dict with keys: "type" (int), "position" (int).
    /// Raises ValueError on parse failure.
    #[staticmethod]
    fn parse_header<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyDict>> {
        let header = if data.len() >= 4 && data[0] == ZPAD && data[1] == ZPAD && data[2] == ZDLE && data[3] == ZHEX {
            parse_hex_header(data)
        } else if data.len() >= 3 && data[0] == ZPAD && data[1] == ZDLE && data[2] == ZBIN {
            parse_bin_header(data)
        } else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "unrecognized header format: expected hex (** ZDLE B) or binary (* ZDLE A) prefix",
            ));
        };

        let header = header.map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(e.to_string())
        })?;

        let dict = PyDict::new_bound(py);
        dict.set_item("type", header.header_type)?;
        dict.set_item("position", header.position)?;
        Ok(dict.into())
    }
}

/// Register ZMODEM constants and classes into a Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyZmodemCodec>()?;
    m.add("ZMODEM_ZDLE", ZDLE)?;
    m.add("ZMODEM_ZPAD", ZPAD)?;
    m.add("ZMODEM_ZHEX", ZHEX)?;
    m.add("ZMODEM_ZBIN", ZBIN)?;
    m.add("ZMODEM_ZBIN32", ZBIN32)?;
    m.add("ZMODEM_ZCRCE", ZCRCE)?;
    m.add("ZMODEM_ZCRCG", ZCRCG)?;
    m.add("ZMODEM_ZCRCQ", ZCRCQ)?;
    m.add("ZMODEM_ZCRCW", ZCRCW)?;
    Ok(())
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_zdle_encode_special_chars() {
        // ZDLE itself → [ZDLE, 0x18 ^ 0x40] = [0x18, 0x58]
        assert_eq!(zdle_encode(&[ZDLE], 0), vec![0x18, 0x58]);

        // XON (0x11) → [ZDLE, 0x11 ^ 0x40] = [0x18, 0x51]
        assert_eq!(zdle_encode(&[XON], 0), vec![0x18, 0x51]);

        // XOFF (0x13) → [ZDLE, 0x13 ^ 0x40] = [0x18, 0x53]
        assert_eq!(zdle_encode(&[XOFF], 0), vec![0x18, 0x53]);

        // DLE (0x10) → [ZDLE, 0x10 ^ 0x40] = [0x18, 0x50]
        assert_eq!(zdle_encode(&[DLE], 0), vec![0x18, 0x50]);

        // High-bit variants
        assert_eq!(zdle_encode(&[0x90], 0), vec![0x18, 0x90 ^ 0x40]);
        assert_eq!(zdle_encode(&[0x91], 0), vec![0x18, 0x91 ^ 0x40]);
        assert_eq!(zdle_encode(&[0x93], 0), vec![0x18, 0x93 ^ 0x40]);

        // Normal byte passes through
        assert_eq!(zdle_encode(&[0x41], 0), vec![0x41]);
    }

    #[test]
    fn test_zdle_roundtrip() {
        // Encode then decode should recover the original data
        let original: Vec<u8> = (0..=255).collect();
        let encoded = zdle_encode(&original, 0);

        // Append a frame end so decode can terminate
        let mut stream = encoded.clone();
        stream.push(ZDLE);
        stream.push(ZCRCE);

        let (decoded, consumed) = zdle_decode(&stream).unwrap();
        assert_eq!(decoded, original);
        assert_eq!(consumed, stream.len());
    }

    #[test]
    fn test_cr_after_at_escaped() {
        // CR after '@' must be escaped
        let data = [b'@', CR];
        let encoded = zdle_encode(&data, 0);
        // '@' passes through, CR is escaped
        assert_eq!(encoded, vec![b'@', ZDLE, CR ^ 0x40]);

        // CR NOT after '@' passes through
        let data2 = [b'A', CR];
        let encoded2 = zdle_encode(&data2, 0);
        assert_eq!(encoded2, vec![b'A', CR]);

        // prev_byte = '@' at chunk boundary
        let data3 = [CR];
        let encoded3 = zdle_encode(&data3, b'@');
        assert_eq!(encoded3, vec![ZDLE, CR ^ 0x40]);
    }

    #[test]
    fn test_hex_header_parse() {
        // Build a valid hex header for ZRINIT (type=1) at position 0
        let header_type: u8 = ZRINIT;
        let p0: u8 = 0;
        let p1: u8 = 0;
        let p2: u8 = 0;
        let p3: u8 = 0;

        let crc_data = [header_type, p0, p1, p2, p3];
        let crc_val = crc16(&crc_data, 0);

        let mut hex_body = String::new();
        for &b in &crc_data {
            hex_body.push_str(&format!("{:02x}", b));
        }
        hex_body.push_str(&format!("{:02x}{:02x}", (crc_val >> 8) as u8, (crc_val & 0xFF) as u8));

        let mut raw: Vec<u8> = vec![ZPAD, ZPAD, ZDLE, ZHEX];
        raw.extend_from_slice(hex_body.as_bytes());
        raw.push(b'\r');
        raw.push(b'\n');

        let header = parse_hex_header(&raw).unwrap();
        assert_eq!(header.header_type, ZRINIT);
        assert_eq!(header.position, 0);
    }

    #[test]
    fn test_hex_header_parse_with_position() {
        // ZDATA header at position 0x00001000 (4096)
        let header_type: u8 = ZDATA;
        let pos: u32 = 4096;
        let [p0, p1, p2, p3] = pos.to_le_bytes();

        let crc_data = [header_type, p0, p1, p2, p3];
        let crc_val = crc16(&crc_data, 0);

        let mut hex_body = String::new();
        for &b in &crc_data {
            hex_body.push_str(&format!("{:02x}", b));
        }
        hex_body.push_str(&format!("{:02x}{:02x}", (crc_val >> 8) as u8, (crc_val & 0xFF) as u8));

        let mut raw: Vec<u8> = vec![ZPAD, ZPAD, ZDLE, ZHEX];
        raw.extend_from_slice(hex_body.as_bytes());
        raw.push(b'\r');
        raw.push(b'\n');

        let header = parse_hex_header(&raw).unwrap();
        assert_eq!(header.header_type, ZDATA);
        assert_eq!(header.position, 4096);
    }

    #[test]
    fn test_hex_header_bad_crc() {
        let mut raw: Vec<u8> = vec![ZPAD, ZPAD, ZDLE, ZHEX];
        // type=0, p0..p3=0, crc=0xFFFF (wrong)
        raw.extend_from_slice(b"0000000000ffff");
        raw.push(b'\r');
        raw.push(b'\n');

        let result = parse_hex_header(&raw);
        assert!(matches!(result, Err(ZdleError::CrcMismatch { .. })));
    }

    #[test]
    fn test_bin_header_parse() {
        // Build binary header: ZPAD ZDLE ZBIN <escaped: type p0 p1 p2 p3 crc_hi crc_lo>
        let header_type: u8 = ZRQINIT;
        let pos: u32 = 0;
        let [p0, p1, p2, p3] = pos.to_le_bytes();

        let crc_data = [header_type, p0, p1, p2, p3];
        let crc_val = crc16(&crc_data, 0);
        let crc_hi = (crc_val >> 8) as u8;
        let crc_lo = (crc_val & 0xFF) as u8;

        // ZDLE-encode the 7 payload bytes
        let payload = [header_type, p0, p1, p2, p3, crc_hi, crc_lo];
        let escaped_payload = zdle_encode(&payload, 0);

        let mut raw: Vec<u8> = vec![ZPAD, ZDLE, ZBIN];
        raw.extend_from_slice(&escaped_payload);

        let header = parse_bin_header(&raw).unwrap();
        assert_eq!(header.header_type, ZRQINIT);
        assert_eq!(header.position, 0);
    }

    #[test]
    fn test_bin_header_with_escaped_bytes() {
        // Position = 0x00001811 — p0=0x11 (XON, must be escaped)
        let header_type: u8 = ZDATA;
        let pos: u32 = 0x00001811;
        let [p0, p1, p2, p3] = pos.to_le_bytes();
        assert_eq!(p0, 0x11); // XON — will be escaped
        assert_eq!(p1, 0x18); // ZDLE — will be escaped

        let crc_data = [header_type, p0, p1, p2, p3];
        let crc_val = crc16(&crc_data, 0);
        let crc_hi = (crc_val >> 8) as u8;
        let crc_lo = (crc_val & 0xFF) as u8;

        let payload = [header_type, p0, p1, p2, p3, crc_hi, crc_lo];
        let escaped_payload = zdle_encode(&payload, 0);

        // Verify that the escaped payload is longer (some bytes got escaped)
        assert!(escaped_payload.len() > payload.len());

        let mut raw: Vec<u8> = vec![ZPAD, ZDLE, ZBIN];
        raw.extend_from_slice(&escaped_payload);

        let header = parse_bin_header(&raw).unwrap();
        assert_eq!(header.header_type, ZDATA);
        assert_eq!(header.position, 0x00001811);
    }

    #[test]
    fn test_decode_truncated() {
        // ZDLE at end of data — should error
        let data = [0x41, ZDLE];
        let result = zdle_decode(&data);
        assert_eq!(result, Err(ZdleError::Truncated));
    }

    #[test]
    fn test_decode_no_frame_end() {
        // No ZDLE+frame_end — should error
        let data = [0x41, 0x42, 0x43];
        let result = zdle_decode(&data);
        assert_eq!(result, Err(ZdleError::Truncated));
    }
}
