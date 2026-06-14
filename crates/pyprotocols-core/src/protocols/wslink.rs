//! WSLink protocol — modern length-prefixed clean-pipe framer.
//!
//! Wire format:
//!   Frame: [4-byte LE length L][1-byte type][payload][4-byte LE CRC32]
//!   where L = 1 + len(payload) + 4
//!   CRC32 computed over (type_byte + payload)
//!
//! Packet types:
//!   A=ACK, C=Close, D=Data, H=Chat, K=Skip, N=NAK,
//!   O=Open, Q=ReadyRecv, R=Ready, S=Seek, V=Verify,
//!   Z=TransmitDone (all files sent — NOT session termination)
//!
//! IMPORTANT: Z (TRANSMIT_DONE) signals "I have no more files to send."
//! It does NOT mean "session is over." The chat channel (H packets) must
//! remain active after Z for MCP JSON-RPC traffic. Z should only be sent
//! when batch_index > 0 (files were actually transferred). Chat-only
//! sessions NEVER send Z.

use pyo3::prelude::*;
use pyo3::exceptions::{PyValueError, PyBufferError};
use pyo3::types::PyDict;
use crate::crc;
use crate::framer::{Framer, FrameError, ParsedFrame, MAX_FRAME_SIZE};

// ─── Constants ───────────────────────────────────────────────────────

pub const PACK_ACK_BLOCK: u8 = b'A';
pub const PACK_CLOSE_FILE: u8 = b'C';
pub const PACK_DATA_BLOCK: u8 = b'D';
pub const PACK_CHAT_BLOCK: u8 = b'H';
pub const PACK_SKIP_FILE: u8 = b'K';
pub const PACK_NAK_BLOCK: u8 = b'N';
pub const PACK_OPEN_FILE: u8 = b'O';
pub const PACK_READY_RECV: u8 = b'Q';
pub const PACK_READY: u8 = b'R';
pub const PACK_SEEK_BLOCK: u8 = b'S';
pub const PACK_VERIFY_BLOCK: u8 = b'V';
pub const PACK_TRANSMIT_DONE: u8 = b'Z';

pub const MAX_BLOCK_SIZE: usize = 65536; // 64KB max (negotiable)

/// Register WSLink constants in the Python module.
pub fn register_constants(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("PACK_ACK_BLOCK", PACK_ACK_BLOCK)?;
    m.add("PACK_CLOSE_FILE", PACK_CLOSE_FILE)?;
    m.add("PACK_DATA_BLOCK", PACK_DATA_BLOCK)?;
    m.add("PACK_CHAT_BLOCK", PACK_CHAT_BLOCK)?;
    m.add("PACK_SKIP_FILE", PACK_SKIP_FILE)?;
    m.add("PACK_NAK_BLOCK", PACK_NAK_BLOCK)?;
    m.add("PACK_OPEN_FILE", PACK_OPEN_FILE)?;
    m.add("PACK_READY_RECV", PACK_READY_RECV)?;
    m.add("PACK_READY", PACK_READY)?;
    m.add("PACK_SEEK_BLOCK", PACK_SEEK_BLOCK)?;
    m.add("PACK_VERIFY_BLOCK", PACK_VERIFY_BLOCK)?;
    m.add("PACK_TRANSMIT_DONE", PACK_TRANSMIT_DONE)?;
    m.add("MAX_BLOCK_SIZE", MAX_BLOCK_SIZE)?;
    Ok(())
}

// ─── WSLink Framer ───────────────────────────────────────────────────

/// WSLink length-prefixed framer with CRC-32 integrity.
#[pyclass]
#[derive(Debug, Clone, Default)]
pub struct WSLinkFramer;

#[pymethods]
impl WSLinkFramer {
    #[new]
    pub fn new() -> Self {
        Self
    }

    /// Parse a complete frame from a buffer.
    ///
    /// Returns: (bytes_consumed, pkt_type, payload)
    /// Raises: ValueError on CRC mismatch, BufferError if data is too short.
    #[staticmethod]
    pub fn parse_frame(data: &[u8]) -> PyResult<(usize, u8, Vec<u8>)> {
        let framer = WSLinkFramerImpl;
        match framer.parse_frame(data) {
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

    /// Build a complete frame ready to send.
    ///
    /// Returns: bytes containing [4-byte len][1-byte type][payload][4-byte CRC32]
    #[staticmethod]
    pub fn build_frame(pkt_type: u8, payload: &[u8]) -> Vec<u8> {
        let framer = WSLinkFramerImpl;
        Framer::build_frame(&framer, pkt_type, payload)
    }

    /// Fast CRC-32 (SIMD-accelerated).
    #[staticmethod]
    pub fn crc32(data: &[u8]) -> u32 {
        crc::py_crc32(data)
    }
}

/// Internal implementation (not exposed to Python).
#[derive(Debug, Clone)]
pub(crate) struct WSLinkFramerImpl;

impl Framer for WSLinkFramerImpl {
    fn parse_frame(&self, buf: &[u8]) -> Result<ParsedFrame, FrameError> {
        // Need at least 4 bytes for length prefix
        if buf.len() < 4 {
            return Err(FrameError::Incomplete {
                needed: 4,
                available: buf.len(),
            });
        }

        // Read length (little-endian u32)
        let length = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]) as usize;

        // Validate length bounds
        if length < 5 {
            return Err(FrameError::Invalid {
                reason: format!("frame length {} < minimum 5", length),
            });
        }
        if length > MAX_FRAME_SIZE {
            return Err(FrameError::TooLarge { size: length });
        }

        // Need 4 (length prefix) + length (type + payload + CRC)
        let total_frame_size = 4 + length;
        if buf.len() < total_frame_size {
            return Err(FrameError::Incomplete {
                needed: total_frame_size,
                available: buf.len(),
            });
        }

        // Extract fields
        let frame_data = &buf[4..total_frame_size];
        let pkt_type = frame_data[0];
        let payload = &frame_data[1..frame_data.len() - 4];
        let expected_crc = u32::from_le_bytes([
            frame_data[frame_data.len() - 4],
            frame_data[frame_data.len() - 3],
            frame_data[frame_data.len() - 2],
            frame_data[frame_data.len() - 1],
        ]);

        // Verify CRC (over type + payload, excluding CRC itself)
        let actual_crc = crc32fast::hash(&frame_data[..frame_data.len() - 4]);
        if actual_crc != expected_crc {
            return Err(FrameError::CrcMismatch {
                expected: expected_crc,
                actual: actual_crc,
            });
        }

        Ok(ParsedFrame {
            bytes_consumed: total_frame_size,
            pkt_type,
            payload: payload.to_vec(),
        })
    }

    fn build_frame(&self, pkt_type: u8, payload: &[u8]) -> Vec<u8> {
        let data_len = 1 + payload.len(); // type + payload
        let length = data_len + 4; // + CRC
        let total = 4 + length; // + length prefix

        let mut frame = Vec::with_capacity(total);

        // Length prefix (LE u32)
        frame.extend_from_slice(&(length as u32).to_le_bytes());
        // Type byte
        frame.push(pkt_type);
        // Payload
        frame.extend_from_slice(payload);
        // CRC-32 over (type + payload)
        let crc = crc32fast::hash(&frame[4..4 + data_len]);
        frame.extend_from_slice(&crc.to_le_bytes());

        frame
    }

    fn build_frame_into(&self, pkt_type: u8, payload: &[u8], out: &mut [u8]) -> usize {
        let data_len = 1 + payload.len();
        let length = data_len + 4;
        let total = 4 + length;

        // Length prefix
        out[0..4].copy_from_slice(&(length as u32).to_le_bytes());
        // Type
        out[4] = pkt_type;
        // Payload
        out[5..5 + payload.len()].copy_from_slice(payload);
        // CRC
        let crc = crc32fast::hash(&out[4..4 + data_len]);
        out[4 + data_len..4 + data_len + 4].copy_from_slice(&crc.to_le_bytes());

        total
    }

    fn frame_size_for(&self, payload_len: usize) -> usize {
        4 + 1 + payload_len + 4 // length_prefix + type + payload + CRC
    }
}

// ─── Packet Structs ──────────────────────────────────────────────────

/// FileHeaderPacket: carries file metadata in OPEN_FILE (O) packets.
///
/// Wire format (little-endian):
///   [u64 size][u32 blocks][u32 block_size][f64 mtime][u8 batch][utf8 name...]
#[pyclass]
#[derive(Debug, Clone)]
pub struct FileHeaderPacket;

/// Fixed header size: 8 + 4 + 4 + 8 + 1 = 25 bytes (name is variable-length suffix)
pub const FILE_HEADER_FIXED_SIZE: usize = 25;

#[pymethods]
impl FileHeaderPacket {
    /// Pack file header into bytes.
    #[staticmethod]
    pub fn pack(name: &str, size: u64, blocks: u32, block_size: u32, time_float: f64, batch: u8) -> Vec<u8> {
        let name_bytes = name.as_bytes();
        let mut buf = Vec::with_capacity(FILE_HEADER_FIXED_SIZE + name_bytes.len());

        buf.extend_from_slice(&size.to_le_bytes());       // u64
        buf.extend_from_slice(&blocks.to_le_bytes());     // u32
        buf.extend_from_slice(&block_size.to_le_bytes()); // u32
        buf.extend_from_slice(&time_float.to_le_bytes()); // f64
        buf.push(batch);                                   // u8
        buf.extend_from_slice(name_bytes);                // utf8 name

        buf
    }

    /// Unpack bytes into a dict: {name, size, blocks, block_size, time, batch}
    #[staticmethod]
    pub fn unpack(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
        if data.len() < FILE_HEADER_FIXED_SIZE {
            return Err(PyValueError::new_err(format!(
                "FileHeaderPacket requires at least {} bytes, got {}",
                FILE_HEADER_FIXED_SIZE,
                data.len()
            )));
        }

        let size = u64::from_le_bytes(data[0..8].try_into().unwrap());
        let blocks = u32::from_le_bytes(data[8..12].try_into().unwrap());
        let block_size = u32::from_le_bytes(data[12..16].try_into().unwrap());
        let time_float = f64::from_le_bytes(data[16..24].try_into().unwrap());
        let batch = data[24];
        let name = String::from_utf8_lossy(&data[25..]).to_string();

        let dict = PyDict::new_bound(py);
        dict.set_item("name", name)?;
        dict.set_item("size", size)?;
        dict.set_item("blocks", blocks)?;
        dict.set_item("block_size", block_size)?;
        dict.set_item("time", time_float)?;
        dict.set_item("batch", batch)?;

        Ok(dict.into())
    }
}

/// SequencePacket: identifies a block in the transfer stream.
///
/// Wire format: [u8 batch][u32 block_number] = 5 bytes total.
#[pyclass]
#[derive(Debug, Clone)]
pub struct SequencePacket;

pub const SEQUENCE_PACKET_SIZE: usize = 5;

#[pymethods]
impl SequencePacket {
    /// Class constant: struct size in bytes.
    #[classattr]
    const SIZE: usize = SEQUENCE_PACKET_SIZE;

    /// Pack batch (u8) + block (u32) into 5 bytes.
    #[staticmethod]
    pub fn pack(batch: u8, block: u32) -> Vec<u8> {
        let mut buf = Vec::with_capacity(SEQUENCE_PACKET_SIZE);
        buf.push(batch);
        buf.extend_from_slice(&block.to_le_bytes());
        buf
    }

    /// Unpack 5 bytes into a dict: {batch, block}
    #[staticmethod]
    pub fn unpack(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
        if data.len() < SEQUENCE_PACKET_SIZE {
            return Err(PyValueError::new_err(format!(
                "SequencePacket requires {} bytes, got {}",
                SEQUENCE_PACKET_SIZE,
                data.len()
            )));
        }

        let batch = data[0];
        let block = u32::from_le_bytes(data[1..5].try_into().unwrap());

        let dict = PyDict::new_bound(py);
        dict.set_item("batch", batch)?;
        dict.set_item("block", block)?;

        Ok(dict.into())
    }
}

/// ResumeVerifyPacket: carries crash-recovery verification data.
///
/// Wire format: [u32 base_block][u32 count] + count × [u32 CRC32]
#[pyclass]
#[derive(Debug, Clone)]
pub struct ResumeVerifyPacket;

pub const RESUME_VERIFY_HEADER_SIZE: usize = 8;

#[pymethods]
impl ResumeVerifyPacket {
    /// Class constant: header size in bytes.
    #[classattr]
    const HEADER_SIZE: usize = RESUME_VERIFY_HEADER_SIZE;

    /// Pack base_block (u32) + count (u32) into 8 bytes.
    #[staticmethod]
    pub fn pack_header(base_block: u32, count: u32) -> Vec<u8> {
        let mut buf = Vec::with_capacity(RESUME_VERIFY_HEADER_SIZE);
        buf.extend_from_slice(&base_block.to_le_bytes());
        buf.extend_from_slice(&count.to_le_bytes());
        buf
    }

    /// Unpack 8 bytes into a dict: {base_block, count}
    #[staticmethod]
    pub fn unpack_header(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
        if data.len() < RESUME_VERIFY_HEADER_SIZE {
            return Err(PyValueError::new_err(format!(
                "ResumeVerifyPacket header requires {} bytes, got {}",
                RESUME_VERIFY_HEADER_SIZE,
                data.len()
            )));
        }

        let base_block = u32::from_le_bytes(data[0..4].try_into().unwrap());
        let count = u32::from_le_bytes(data[4..8].try_into().unwrap());

        let dict = PyDict::new_bound(py);
        dict.set_item("base_block", base_block)?;
        dict.set_item("count", count)?;

        Ok(dict.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_roundtrip() {
        let framer = WSLinkFramerImpl;
        let payload = b"hello world";
        let frame = framer.build_frame(PACK_DATA_BLOCK, payload);
        let parsed = framer.parse_frame(&frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_DATA_BLOCK);
        assert_eq!(parsed.payload, payload);
        assert_eq!(parsed.bytes_consumed, frame.len());
    }

    #[test]
    fn test_crc_mismatch() {
        let framer = WSLinkFramerImpl;
        let mut frame = framer.build_frame(PACK_DATA_BLOCK, b"test");
        // Corrupt last byte (CRC)
        let last = frame.len() - 1;
        frame[last] ^= 0xFF;
        assert!(matches!(
            framer.parse_frame(&frame),
            Err(FrameError::CrcMismatch { .. })
        ));
    }

    #[test]
    fn test_short_buffer() {
        let framer = WSLinkFramerImpl;
        let frame = framer.build_frame(PACK_DATA_BLOCK, b"test");
        assert!(matches!(
            framer.parse_frame(&frame[..3]),
            Err(FrameError::Incomplete { .. })
        ));
    }

    #[test]
    fn test_sequence_roundtrip() {
        let packed = SequencePacket::pack(2, 99);
        assert_eq!(packed.len(), SEQUENCE_PACKET_SIZE);
        assert_eq!(packed[0], 2); // batch
        assert_eq!(u32::from_le_bytes(packed[1..5].try_into().unwrap()), 99); // block
    }

    #[test]
    fn test_file_header_roundtrip() {
        let packed = FileHeaderPacket::pack("test.bin", 12345, 4, 4096, 1718000000.0, 0);
        assert!(packed.len() >= FILE_HEADER_FIXED_SIZE);
        // Verify name is at the end
        let name = String::from_utf8_lossy(&packed[FILE_HEADER_FIXED_SIZE..]);
        assert_eq!(name, "test.bin");
    }

    #[test]
    fn test_all_packet_types() {
        let framer = WSLinkFramerImpl;
        for pkt_type in [b'A', b'C', b'D', b'H', b'K', b'N', b'O', b'Q', b'R', b'S', b'V', b'Z'] {
            let frame = framer.build_frame(pkt_type, b"x");
            let parsed = framer.parse_frame(&frame).unwrap();
            assert_eq!(parsed.pkt_type, pkt_type);
        }
    }

    #[test]
    fn test_frame_too_large() {
        let framer = WSLinkFramerImpl;
        // Craft a buffer with a huge length field
        let mut buf = vec![0u8; 8];
        buf[0..4].copy_from_slice(&(MAX_FRAME_SIZE as u32 + 1).to_le_bytes());
        assert!(matches!(
            framer.parse_frame(&buf),
            Err(FrameError::TooLarge { .. })
        ));
    }
}
