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
use std::collections::VecDeque;
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
pub const PACK_PING: u8 = b'P';       // Heartbeat ping (keepalive)
pub const PACK_READY_RECV: u8 = b'Q';
pub const PACK_READY: u8 = b'R';
pub const PACK_SEEK_BLOCK: u8 = b'S';
pub const PACK_VERIFY_BLOCK: u8 = b'V';
pub const PACK_PONG: u8 = b'W';       // Heartbeat pong (response to ping)
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
    m.add("PACK_PING", PACK_PING)?;
    m.add("PACK_READY_RECV", PACK_READY_RECV)?;
    m.add("PACK_READY", PACK_READY)?;
    m.add("PACK_SEEK_BLOCK", PACK_SEEK_BLOCK)?;
    m.add("PACK_VERIFY_BLOCK", PACK_VERIFY_BLOCK)?;
    m.add("PACK_PONG", PACK_PONG)?;
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

// ─── Link Statistics Tracker ─────────────────────────────────────────

/// Real-time link statistics tracker for WSLink sessions.
///
/// Tracks throughput, error rates, congestion state, and transfer progress.
/// Thread-safe for use from async contexts. Exposed to Python via PyO3.
///
/// Usage (Python):
///   tracker = LinkStatsTracker(block_size=4096, max_window=256)
///   tracker.record_block_sent(4096)
///   tracker.record_ack(rtt_ms=1.2)
///   stats = tracker.snapshot()  # returns dict
#[pyclass]
#[derive(Debug, Clone)]
pub struct LinkStatsTracker {
    // Config
    block_size: u32,
    max_window: u32,
    window_size: u32,
    arq_timeout_s: f64,
    heartbeat_interval_s: f64,

    // Timing
    session_start: f64,
    transfer_start: f64,

    // RTT ring buffer
    rtt_history: VecDeque<f64>,
    rtt_capacity: usize,

    // Counters
    bytes_sent: u64,
    bytes_received: u64,
    blocks_acked: u64,
    blocks_naked: u64,
    blocks_retransmitted: u64,
    arq_timeouts: u64,
    crc_failures: u64,
    files_completed_send: u32,
    files_completed_recv: u32,
    files_skipped: u32,
    window_grows: u64,
    window_shrinks: u64,
    pings_sent: u64,
    pongs_received: u64,

    // Current transfer state
    in_flight_blocks: u32,
    total_blocks: u32,
    current_block: u32,
    current_file_size: u64,
    current_file_name: String,
    files_queued: u32,
}

#[pymethods]
impl LinkStatsTracker {
    #[new]
    #[pyo3(signature = (block_size=4096, max_window=256, arq_timeout_s=2.0, heartbeat_interval_s=20.0, rtt_capacity=20))]
    pub fn new(
        block_size: u32,
        max_window: u32,
        arq_timeout_s: f64,
        heartbeat_interval_s: f64,
        rtt_capacity: usize,
    ) -> Self {
        Self {
            block_size,
            max_window,
            window_size: 16,
            arq_timeout_s,
            heartbeat_interval_s,
            session_start: 0.0,
            transfer_start: 0.0,
            rtt_history: VecDeque::with_capacity(rtt_capacity),
            rtt_capacity,
            bytes_sent: 0,
            bytes_received: 0,
            blocks_acked: 0,
            blocks_naked: 0,
            blocks_retransmitted: 0,
            arq_timeouts: 0,
            crc_failures: 0,
            files_completed_send: 0,
            files_completed_recv: 0,
            files_skipped: 0,
            window_grows: 0,
            window_shrinks: 0,
            pings_sent: 0,
            pongs_received: 0,
            in_flight_blocks: 0,
            total_blocks: 0,
            current_block: 0,
            current_file_size: 0,
            current_file_name: String::new(),
            files_queued: 0,
        }
    }

    /// Call once when session starts. Records the start timestamp.
    pub fn start_session(&mut self, timestamp: f64) {
        self.session_start = timestamp;
    }

    /// Call when a new file transfer begins.
    pub fn start_transfer(&mut self, timestamp: f64, file_name: &str, file_size: u64, total_blocks: u32) {
        self.transfer_start = timestamp;
        self.current_file_name = file_name.to_string();
        self.current_file_size = file_size;
        self.total_blocks = total_blocks;
        self.current_block = 0;
    }

    /// Record bytes sent (data block payload, excluding framing).
    pub fn record_block_sent(&mut self, payload_bytes: u32) {
        self.bytes_sent += payload_bytes as u64;
        self.current_block += 1;
    }

    /// Record bytes received (data block payload).
    pub fn record_block_received(&mut self, payload_bytes: u32) {
        self.bytes_received += payload_bytes as u64;
    }

    /// Record a successful ACK with RTT measurement (in seconds).
    pub fn record_ack(&mut self, rtt_s: f64) {
        self.blocks_acked += 1;
        if self.in_flight_blocks > 0 {
            self.in_flight_blocks -= 1;
        }

        // Push RTT sample
        if self.rtt_history.len() >= self.rtt_capacity {
            self.rtt_history.pop_front();
        }
        self.rtt_history.push_back(rtt_s);
    }

    /// Record a NAK (negative acknowledgement).
    pub fn record_nak(&mut self) {
        self.blocks_naked += 1;
        self.blocks_retransmitted += 1;
    }

    /// Record an ARQ timeout retransmission.
    pub fn record_arq_timeout(&mut self) {
        self.arq_timeouts += 1;
        self.blocks_retransmitted += 1;
    }

    /// Record a CRC integrity failure.
    pub fn record_crc_failure(&mut self) {
        self.crc_failures += 1;
    }

    /// Record a ping sent.
    pub fn record_ping(&mut self) {
        self.pings_sent += 1;
    }

    /// Record a pong received.
    pub fn record_pong(&mut self) {
        self.pongs_received += 1;
    }

    /// Record a window size change.
    pub fn set_window(&mut self, new_size: u32) {
        if new_size > self.window_size {
            self.window_grows += 1;
        } else if new_size < self.window_size {
            self.window_shrinks += 1;
        }
        self.window_size = new_size;
    }

    /// Record in-flight block count update.
    pub fn set_in_flight(&mut self, count: u32) {
        self.in_flight_blocks = count;
    }

    /// Record file transfer completed (send side).
    pub fn record_file_sent(&mut self) {
        self.files_completed_send += 1;
        self.transfer_start = 0.0;
        self.current_file_size = 0;
        self.current_file_name.clear();
    }

    /// Record file transfer completed (receive side).
    pub fn record_file_received(&mut self) {
        self.files_completed_recv += 1;
    }

    /// Record a file skipped (already exists).
    pub fn record_file_skipped(&mut self) {
        self.files_skipped += 1;
    }

    /// Set the number of files queued for sending.
    pub fn set_files_queued(&mut self, count: u32) {
        self.files_queued = count;
    }

    /// Return a complete stats snapshot as a Python dict.
    ///
    /// This is the primary query interface. All values are computed at
    /// call time from the accumulated counters.
    pub fn snapshot(&self, py: Python<'_>, now: f64) -> PyResult<PyObject> {
        let session_elapsed = if self.session_start > 0.0 { now - self.session_start } else { 0.0 };

        // RTT stats
        let (rtt_avg, rtt_min, rtt_max) = if self.rtt_history.is_empty() {
            (0.0, 0.0, 0.0)
        } else {
            let sum: f64 = self.rtt_history.iter().sum();
            let avg = sum / self.rtt_history.len() as f64;
            let min = self.rtt_history.iter().cloned().fold(f64::INFINITY, f64::min);
            let max = self.rtt_history.iter().cloned().fold(0.0_f64, f64::max);
            (avg, min, max)
        };

        // Average throughput
        let total_bytes = self.bytes_sent + self.bytes_received;
        let (throughput_kbps, throughput_mbps) = if session_elapsed > 0.0 {
            let bps = (total_bytes as f64 * 8.0) / session_elapsed;
            (bps / 1000.0, bps / 1_000_000.0)
        } else {
            (0.0, 0.0)
        };

        // Instantaneous throughput (window capacity / RTT)
        let (instant_kbps, instant_mbps) = if rtt_avg > 0.0 && self.window_size > 0 {
            let bps = (self.window_size as f64 * self.block_size as f64 * 8.0) / rtt_avg;
            (bps / 1000.0, bps / 1_000_000.0)
        } else {
            (0.0, 0.0)
        };

        // Error rate
        let total_attempted = self.blocks_acked + self.blocks_naked + self.blocks_retransmitted;
        let error_rate = if total_attempted > 0 {
            (self.blocks_naked + self.blocks_retransmitted) as f64 / total_attempted as f64
        } else {
            0.0
        };

        // Window utilization
        let window_util = if self.window_size > 0 {
            self.in_flight_blocks as f64 / self.window_size as f64
        } else {
            0.0
        };

        // Transfer progress
        let (progress, bytes_remaining) = if self.total_blocks > 0 {
            let p = self.current_block as f64 / self.total_blocks as f64;
            let r = (self.total_blocks - self.current_block) as u64 * self.block_size as u64;
            (p, r)
        } else {
            (0.0, 0)
        };

        // ETA
        let eta_s = if self.transfer_start > 0.0 && self.current_block > 0 {
            let elapsed_file = now - self.transfer_start;
            let time_per_block = elapsed_file / self.current_block as f64;
            let blocks_left = self.total_blocks.saturating_sub(self.current_block);
            blocks_left as f64 * time_per_block
        } else {
            0.0
        };

        let dict = PyDict::new_bound(py);

        // Connection state
        dict.set_item("session_elapsed_s", (session_elapsed * 100.0).round() / 100.0)?;

        // RTT (milliseconds)
        dict.set_item("rtt_avg_ms", (rtt_avg * 100_000.0).round() / 100.0)?;
        dict.set_item("rtt_min_ms", (rtt_min * 100_000.0).round() / 100.0)?;
        dict.set_item("rtt_max_ms", (rtt_max * 100_000.0).round() / 100.0)?;
        dict.set_item("rtt_samples", self.rtt_history.len())?;

        // Congestion window
        dict.set_item("window_size", self.window_size)?;
        dict.set_item("window_max", self.max_window)?;
        dict.set_item("window_utilization", (window_util * 1000.0).round() / 1000.0)?;
        dict.set_item("window_grows", self.window_grows)?;
        dict.set_item("window_shrinks", self.window_shrinks)?;
        dict.set_item("in_flight_blocks", self.in_flight_blocks)?;

        // Throughput
        dict.set_item("throughput_avg_kbps", (throughput_kbps * 10.0).round() / 10.0)?;
        dict.set_item("throughput_avg_mbps", (throughput_mbps * 1000.0).round() / 1000.0)?;
        dict.set_item("throughput_instant_kbps", (instant_kbps * 10.0).round() / 10.0)?;
        dict.set_item("throughput_instant_mbps", (instant_mbps * 1000.0).round() / 1000.0)?;

        // Data volume
        dict.set_item("bytes_sent", self.bytes_sent)?;
        dict.set_item("bytes_received", self.bytes_received)?;
        dict.set_item("bytes_total", total_bytes)?;
        dict.set_item("bytes_remaining", bytes_remaining)?;

        // Block-level counters
        dict.set_item("blocks_acked", self.blocks_acked)?;
        dict.set_item("blocks_naked", self.blocks_naked)?;
        dict.set_item("blocks_retransmitted", self.blocks_retransmitted)?;
        dict.set_item("arq_timeouts", self.arq_timeouts)?;

        // Integrity
        dict.set_item("crc_failures", self.crc_failures)?;
        dict.set_item("error_rate", (error_rate * 100_000.0).round() / 100_000.0)?;

        // File transfer progress
        dict.set_item("files_completed_send", self.files_completed_send)?;
        dict.set_item("files_completed_recv", self.files_completed_recv)?;
        dict.set_item("files_skipped", self.files_skipped)?;
        dict.set_item("files_queued", self.files_queued)?;
        dict.set_item("current_file", if self.current_file_name.is_empty() { None } else { Some(&self.current_file_name) })?;
        dict.set_item("current_file_size", self.current_file_size)?;
        dict.set_item("transfer_progress", (progress * 10000.0).round() / 10000.0)?;
        dict.set_item("transfer_eta_s", (eta_s * 10.0).round() / 10.0)?;

        // Heartbeat
        dict.set_item("pings_sent", self.pings_sent)?;
        dict.set_item("pongs_received", self.pongs_received)?;

        // Config
        dict.set_item("block_size", self.block_size)?;
        dict.set_item("arq_timeout_s", self.arq_timeout_s)?;
        dict.set_item("heartbeat_interval_s", self.heartbeat_interval_s)?;

        Ok(dict.into())
    }

    /// Reset all counters (but preserve config). Use when session reconnects.
    pub fn reset(&mut self) {
        self.session_start = 0.0;
        self.transfer_start = 0.0;
        self.rtt_history.clear();
        self.bytes_sent = 0;
        self.bytes_received = 0;
        self.blocks_acked = 0;
        self.blocks_naked = 0;
        self.blocks_retransmitted = 0;
        self.arq_timeouts = 0;
        self.crc_failures = 0;
        self.files_completed_send = 0;
        self.files_completed_recv = 0;
        self.files_skipped = 0;
        self.window_grows = 0;
        self.window_shrinks = 0;
        self.pings_sent = 0;
        self.pongs_received = 0;
        self.in_flight_blocks = 0;
        self.total_blocks = 0;
        self.current_block = 0;
        self.current_file_size = 0;
        self.current_file_name.clear();
        self.files_queued = 0;
        self.window_size = 16;
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
        for pkt_type in [b'A', b'C', b'D', b'H', b'K', b'N', b'O', b'P', b'Q', b'R', b'S', b'V', b'W', b'Z'] {
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

    #[test]
    fn test_heartbeat_ping_pong_roundtrip() {
        let framer = WSLinkFramerImpl;
        // PING carries an f64 timestamp as payload
        let timestamp: f64 = 1718000000.123456;
        let payload = timestamp.to_le_bytes();
        
        // Build and parse PING frame
        let ping_frame = framer.build_frame(PACK_PING, &payload);
        let parsed = framer.parse_frame(&ping_frame).unwrap();
        assert_eq!(parsed.pkt_type, PACK_PING);
        assert_eq!(parsed.payload.len(), 8);
        let parsed_ts = f64::from_le_bytes(parsed.payload[..8].try_into().unwrap());
        assert_eq!(parsed_ts, timestamp);
        
        // PONG echoes the same payload back
        let pong_frame = framer.build_frame(PACK_PONG, &parsed.payload);
        let pong_parsed = framer.parse_frame(&pong_frame).unwrap();
        assert_eq!(pong_parsed.pkt_type, PACK_PONG);
        assert_eq!(pong_parsed.payload, payload);
    }

    #[test]
    fn test_link_stats_tracker_basic() {
        let mut tracker = LinkStatsTracker::new(4096, 256, 2.0, 20.0, 20);
        
        // Start session
        tracker.start_session(1000.0);
        
        // Start a file transfer
        tracker.start_transfer(1000.0, "test.bin", 40960, 10);
        
        // Send 5 blocks
        for _ in 0..5 {
            tracker.record_block_sent(4096);
        }
        assert_eq!(tracker.bytes_sent, 20480);
        assert_eq!(tracker.current_block, 5);
        
        // ACK 4 blocks with varying RTT
        tracker.set_in_flight(5);
        tracker.record_ack(0.001);  // 1ms
        tracker.record_ack(0.002);  // 2ms
        tracker.record_ack(0.0015); // 1.5ms
        tracker.record_ack(0.001);  // 1ms
        assert_eq!(tracker.blocks_acked, 4);
        assert_eq!(tracker.in_flight_blocks, 1);
        assert_eq!(tracker.rtt_history.len(), 4);
        
        // NAK + retransmit
        tracker.record_nak();
        assert_eq!(tracker.blocks_naked, 1);
        assert_eq!(tracker.blocks_retransmitted, 1);
        
        // Window changes
        tracker.set_window(32);
        assert_eq!(tracker.window_grows, 1);
        tracker.set_window(16);
        assert_eq!(tracker.window_shrinks, 1);
        
        // CRC failure
        tracker.record_crc_failure();
        assert_eq!(tracker.crc_failures, 1);
        
        // Heartbeat
        tracker.record_ping();
        tracker.record_pong();
        assert_eq!(tracker.pings_sent, 1);
        assert_eq!(tracker.pongs_received, 1);
        
        // File complete
        tracker.record_file_sent();
        assert_eq!(tracker.files_completed_send, 1);
        assert!(tracker.current_file_name.is_empty());
    }

    #[test]
    fn test_link_stats_tracker_reset() {
        let mut tracker = LinkStatsTracker::new(4096, 256, 2.0, 20.0, 20);
        tracker.start_session(1000.0);
        tracker.record_block_sent(4096);
        tracker.record_ack(0.001);
        tracker.record_crc_failure();
        
        assert_eq!(tracker.bytes_sent, 4096);
        assert_eq!(tracker.blocks_acked, 1);
        assert_eq!(tracker.crc_failures, 1);
        
        tracker.reset();
        
        assert_eq!(tracker.bytes_sent, 0);
        assert_eq!(tracker.blocks_acked, 0);
        assert_eq!(tracker.crc_failures, 0);
        assert_eq!(tracker.window_size, 16);
        assert!(tracker.rtt_history.is_empty());
    }

    #[test]
    fn test_link_stats_tracker_error_rate() {
        let mut tracker = LinkStatsTracker::new(4096, 256, 2.0, 20.0, 20);
        
        // 100 ACKs, 5 NAKs, 2 ARQ timeouts = 7/107 error rate
        for _ in 0..100 {
            tracker.record_ack(0.001);
        }
        for _ in 0..5 {
            tracker.record_nak();
        }
        for _ in 0..2 {
            tracker.record_arq_timeout();
        }
        
        let total = tracker.blocks_acked + tracker.blocks_naked + tracker.blocks_retransmitted;
        // 100 acked + 5 naked + 7 retransmitted (5 from NAK + 2 from ARQ) = 112
        assert_eq!(total, 112);
        let error_rate = (tracker.blocks_naked + tracker.blocks_retransmitted) as f64 / total as f64;
        assert!(error_rate > 0.1 && error_rate < 0.12);
    }
}
