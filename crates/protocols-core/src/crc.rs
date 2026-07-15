//! CRC functions — SIMD-accelerated where possible.
//!
//! Provides CRC-16 (CCITT), CRC-24, and CRC-32 (ISO 3309 / zlib).
//! CRC-32 uses the `crc32fast` crate which auto-detects SSE4.2/CLMUL.

use pyo3::prelude::*;

/// CRC-32 (ISO 3309 polynomial, same as zlib).
/// SIMD-accelerated via crc32fast crate.
#[pyfunction]
#[pyo3(name = "crc32")]
pub fn py_crc32(data: &[u8]) -> u32 {
    crc32fast::hash(data)
}

/// CRC-32 with initial value (for incremental computation).
pub fn crc32_update(crc: u32, data: &[u8]) -> u32 {
    let mut hasher = crc32fast::Hasher::new_with_initial(crc);
    hasher.update(data);
    hasher.finalize()
}

/// CRC-16 CCITT (polynomial 0x1021).
/// Used by XMODEM/YMODEM/ZMODEM.
#[pyfunction]
#[pyo3(name = "crc16")]
pub fn py_crc16(data: &[u8]) -> u16 {
    crc16(data, 0)
}

/// CRC-16 with initial value.
pub fn crc16(data: &[u8], init: u16) -> u16 {
    let mut crc = init;
    for &byte in data {
        crc ^= (byte as u16) << 8;
        for _ in 0..8 {
            if crc & 0x8000 != 0 {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    crc
}

/// CRC-24 (polynomial 0x864CFB).
/// Used by HS/Link protocol.
#[pyfunction]
#[pyo3(name = "crc24")]
pub fn py_crc24(data: &[u8]) -> u32 {
    crc24(data, 0)
}

/// CRC-24 with initial value.
pub fn crc24(data: &[u8], init: u32) -> u32 {
    let mut crc = init;
    for &byte in data {
        crc ^= (byte as u32) << 16;
        for _ in 0..8 {
            if crc & 0x800000 != 0 {
                crc = (crc << 1) ^ 0x864CFB;
            } else {
                crc <<= 1;
            }
            crc &= 0xFFFFFF; // Keep 24 bits
        }
    }
    crc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_crc32_known_value() {
        // "hello world" CRC-32 = 0x0D4A1185
        let data = b"hello world";
        assert_eq!(py_crc32(data), 0x0D4A1185);
    }

    #[test]
    fn test_crc16_known_value() {
        // "123456789" CRC-16 CCITT (init=0) = 0x31C3
        let data = b"123456789";
        assert_eq!(py_crc16(data), 0x31C3);
    }

    #[test]
    fn test_crc24_zero() {
        assert_eq!(crc24(b"", 0), 0);
    }

    #[test]
    fn test_crc32_empty() {
        assert_eq!(py_crc32(b""), 0);
    }
}
