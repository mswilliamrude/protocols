//! WSLink transport capability negotiation — byte-identical to the Python
//! reference (`protocols/wslink/protocol/capabilities.py`).
//!
//! Wire format of the READY payload (little-endian), 11 bytes:
//!   `[4 bytes magic b"WLC1"][1 byte version][2 bytes flags][4 bytes max_block_size]`
//!
//! Legacy peers send an empty READY payload and ignore any payload they receive,
//! so advertising is backward-compatible. Negotiation is fail-safe: a relaxing
//! capability (skip ARQ/CRC/reorder) is enabled only if BOTH peers advertise it,
//! and the wire CRC is disabled only if BOTH peers opt out.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Magic prefix identifying a capability advertisement in a READY payload.
pub const MAGIC: &[u8; 4] = b"WLC1";
/// Wire format version for the capability block.
pub const VERSION: u8 = 1;
/// Fixed header length: magic(4) + version(1) + flags(2) + max_block_size(4).
pub const HEADER_LEN: usize = 11;

pub const FLAG_RELIABLE: u16 = 0x0001;
pub const FLAG_PROVIDES_INTEGRITY: u16 = 0x0002;
pub const FLAG_ORDERED: u16 = 0x0004;
pub const FLAG_WIRE_CRC: u16 = 0x0008;

/// Negotiated (or advertised) transport capabilities for a WSLink session.
///
/// Defaults are conservative and equal to pre-v2 behaviour: reliability/integrity/
/// ordering are not assumed and the wire CRC stays on.
#[pyclass]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TransportCapabilities {
    #[pyo3(get)]
    pub is_reliable: bool,
    #[pyo3(get)]
    pub provides_integrity: bool,
    #[pyo3(get)]
    pub is_ordered: bool,
    #[pyo3(get)]
    pub wire_crc: bool,
    #[pyo3(get)]
    pub max_block_size: u32,
    #[pyo3(get)]
    pub version: u8,
}

#[pymethods]
impl TransportCapabilities {
    #[new]
    #[pyo3(signature = (is_reliable=false, provides_integrity=false, is_ordered=false,
                        wire_crc=true, max_block_size=4096, version=0))]
    pub fn new(
        is_reliable: bool,
        provides_integrity: bool,
        is_ordered: bool,
        wire_crc: bool,
        max_block_size: u32,
        version: u8,
    ) -> Self {
        Self {
            is_reliable,
            provides_integrity,
            is_ordered,
            wire_crc,
            max_block_size,
            version,
        }
    }

    /// The conservative default matching pre-v2 behaviour (CRC on, ARQ on).
    #[staticmethod]
    pub fn legacy() -> Self {
        Self::new(false, false, false, true, 4096, 0)
    }

    /// Pack the boolean capabilities into the u16 flag bitmask.
    pub fn to_flags(&self) -> u16 {
        let mut f = 0u16;
        if self.is_reliable {
            f |= FLAG_RELIABLE;
        }
        if self.provides_integrity {
            f |= FLAG_PROVIDES_INTEGRITY;
        }
        if self.is_ordered {
            f |= FLAG_ORDERED;
        }
        if self.wire_crc {
            f |= FLAG_WIRE_CRC;
        }
        f
    }

    /// Serialise to the 11-byte READY-payload wire format.
    pub fn encode<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new_bound(py, &self.encode_bytes())
    }

    /// Parse a READY payload.
    ///
    /// Returns `Some(caps)` for a valid advertisement, or `None` for a
    /// legacy/empty/unrecognised payload (caller falls back to `legacy()`).
    #[staticmethod]
    pub fn decode(payload: &[u8]) -> Option<TransportCapabilities> {
        if payload.len() < HEADER_LEN {
            return None;
        }
        if &payload[0..4] != MAGIC {
            return None;
        }
        let version = payload[4];
        let flags = u16::from_le_bytes([payload[5], payload[6]]);
        let max_block_size = u32::from_le_bytes([payload[7], payload[8], payload[9], payload[10]]);
        Some(TransportCapabilities {
            is_reliable: flags & FLAG_RELIABLE != 0,
            provides_integrity: flags & FLAG_PROVIDES_INTEGRITY != 0,
            is_ordered: flags & FLAG_ORDERED != 0,
            wire_crc: flags & FLAG_WIRE_CRC != 0,
            max_block_size,
            version,
        })
    }

    /// Combine local and remote advertisements into the effective capabilities.
    ///
    /// A relaxing capability is enabled only if BOTH peers advertise it. The wire
    /// CRC is disabled only if BOTH peers opt out (fail-safe: any side wanting it
    /// keeps it on). `max_block_size` is the minimum of both ceilings.
    #[staticmethod]
    pub fn negotiate(
        local: &TransportCapabilities,
        remote: &TransportCapabilities,
    ) -> TransportCapabilities {
        TransportCapabilities {
            is_reliable: local.is_reliable && remote.is_reliable,
            provides_integrity: local.provides_integrity && remote.provides_integrity,
            is_ordered: local.is_ordered && remote.is_ordered,
            // Fail-safe: CRC stays on unless BOTH peers opt out.
            wire_crc: local.wire_crc || remote.wire_crc,
            max_block_size: local.max_block_size.min(remote.max_block_size),
            version: if remote.version != 0 {
                local.version.min(remote.version)
            } else {
                local.version
            },
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "TransportCapabilities(is_reliable={}, provides_integrity={}, is_ordered={}, \
             wire_crc={}, max_block_size={}, version={})",
            self.is_reliable,
            self.provides_integrity,
            self.is_ordered,
            self.wire_crc,
            self.max_block_size,
            self.version
        )
    }

    fn __eq__(&self, other: &TransportCapabilities) -> bool {
        self == other
    }
}

impl TransportCapabilities {
    /// GIL-free serialiser used by `encode()` and by cross-language parity tests.
    /// Produces the exact 11-byte little-endian wire form.
    pub fn encode_bytes(&self) -> Vec<u8> {
        let mut buf = Vec::with_capacity(HEADER_LEN);
        buf.extend_from_slice(MAGIC);
        buf.push(VERSION);
        buf.extend_from_slice(&self.to_flags().to_le_bytes());
        buf.extend_from_slice(&self.max_block_size.to_le_bytes());
        buf
    }
}

/// Register capability flag constants in the Python module (parity with
/// `capabilities.py`).
pub fn register_constants(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("CAP_VERSION", VERSION)?;
    m.add("CAP_FLAG_RELIABLE", FLAG_RELIABLE)?;
    m.add("CAP_FLAG_PROVIDES_INTEGRITY", FLAG_PROVIDES_INTEGRITY)?;
    m.add("CAP_FLAG_ORDERED", FLAG_ORDERED)?;
    m.add("CAP_FLAG_WIRE_CRC", FLAG_WIRE_CRC)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_matches_python_layout() {
        // Known-answer vector: reliable+integrity+ordered, crc OFF, block 65536.
        // flags = 0x0001|0x0002|0x0004 = 0x0007 (crc bit clear)
        let c = TransportCapabilities::new(true, true, true, false, 65536, 0);
        // encode() needs the GIL; test the raw byte layout via to_flags instead.
        assert_eq!(c.to_flags(), 0x0007);

        // Build the 11-byte payload by hand and decode it.
        let mut payload = Vec::new();
        payload.extend_from_slice(MAGIC);
        payload.push(VERSION);
        payload.extend_from_slice(&0x0007u16.to_le_bytes());
        payload.extend_from_slice(&65536u32.to_le_bytes());
        assert_eq!(payload.len(), HEADER_LEN);

        let d = TransportCapabilities::decode(&payload).unwrap();
        assert!(d.is_reliable && d.provides_integrity && d.is_ordered);
        assert!(!d.wire_crc);
        assert_eq!(d.max_block_size, 65536);
        assert_eq!(d.version, VERSION);
    }

    #[test]
    fn decode_rejects_legacy_and_junk() {
        assert!(TransportCapabilities::decode(b"").is_none());
        assert!(TransportCapabilities::decode(b"junkjunkjun").is_none()); // 11 bytes, bad magic
        assert!(TransportCapabilities::decode(b"WLC1").is_none()); // too short
    }

    #[test]
    fn negotiate_is_fail_safe() {
        let on = TransportCapabilities::new(false, false, false, true, 4096, 0);
        let off = TransportCapabilities::new(false, false, false, false, 4096, 0);
        // CRC stays on unless BOTH opt out.
        assert!(TransportCapabilities::negotiate(&on, &off).wire_crc);
        assert!(TransportCapabilities::negotiate(&off, &on).wire_crc);
        assert!(!TransportCapabilities::negotiate(&off, &off).wire_crc);

        // Reliable only if both.
        let rel = TransportCapabilities::new(true, false, false, true, 8192, 1);
        let norel = TransportCapabilities::new(false, false, false, true, 4096, 1);
        let n = TransportCapabilities::negotiate(&rel, &norel);
        assert!(!n.is_reliable);
        assert_eq!(n.max_block_size, 4096); // min of both ceilings
    }
}
