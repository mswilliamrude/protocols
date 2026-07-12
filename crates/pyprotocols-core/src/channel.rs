//! Channel multiplexer for WSLink socket proxy extension.
//!
//! Manages multiple concurrent bidirectional channels over a single WSLink
//! session. Each channel has independent flow control (SSH RFC 4254 style).
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │                      ChannelMux                              │
//! │  ┌─────────┐ ┌─────────┐ ┌─────────┐       ┌─────────┐     │
//! │  │ Chan 1  │ │ Chan 3  │ │ Chan 5  │  ...  │ Chan N  │     │
//! │  │ credit  │ │ credit  │ │ credit  │       │ credit  │     │
//! │  │ state   │ │ state   │ │ state   │       │ state   │     │
//! │  └────┬────┘ └────┬────┘ └────┬────┘       └────┬────┘     │
//! │       │           │           │                 │           │
//! │       └───────────┴───────────┴─────────────────┘           │
//! │                           │                                  │
//! │                    ┌──────┴──────┐                          │
//! │                    │ Frame Mux   │                          │
//! │                    └──────┬──────┘                          │
//! └───────────────────────────┼──────────────────────────────────┘
//!                             │
//!                      WSLink Session
//! ```

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::types::PyDict;
use std::collections::HashMap;

use crate::protocols::wslink::{
    CHANNEL_MAX_CONCURRENT, CHANNEL_INITIAL_CREDIT,
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_FLAG_BIDIR, CHANNEL_FLAG_READ_ONLY, CHANNEL_FLAG_WRITE_ONLY,
    CHANNEL_FLAG_COMPRESSED, CHANNEL_FLAG_ENCRYPTED, CHANNEL_FLAG_AUDIT,
    SocketOpenPacket, SocketDataPacket, SocketClosePacket,
    SocketErrorPacket, SocketWindowPacket,
};

// ─── Channel State ───────────────────────────────────────────────────

/// State machine for a single channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelState {
    /// Channel open request sent, waiting for peer's WINDOW (implicit ACK)
    Opening,
    /// Channel is open and active
    Open,
    /// Close sent, waiting for peer's close
    Closing,
    /// Channel is fully closed
    Closed,
    /// Channel encountered an error
    Error,
}

impl ChannelState {
    pub fn as_str(&self) -> &'static str {
        match self {
            ChannelState::Opening => "opening",
            ChannelState::Open => "open",
            ChannelState::Closing => "closing",
            ChannelState::Closed => "closed",
            ChannelState::Error => "error",
        }
    }
}

// ─── Channel ─────────────────────────────────────────────────────────

/// A single channel within a multiplexed session.
#[derive(Debug, Clone)]
pub struct Channel {
    /// Unique channel ID (odd = client, even = server)
    pub id: u16,
    /// Target string (e.g., "ssh-agent", "tcp:localhost:5432")
    pub target: String,
    /// Channel flags (BIDIR, COMPRESSED, ENCRYPTED, etc.)
    pub flags: u8,
    /// Current state
    pub state: ChannelState,
    /// Send credit: bytes we're allowed to send to peer
    pub send_credit: u32,
    /// Recv credit: bytes peer is allowed to send to us
    pub recv_credit: u32,
    /// Total bytes sent on this channel
    pub bytes_sent: u64,
    /// Total bytes received on this channel
    pub bytes_recv: u64,
    /// Whether we initiated this channel
    pub is_initiator: bool,
    /// Last error code (if state == Error)
    pub last_error: u16,
    /// Last error message
    pub last_error_msg: String,
}

impl Channel {
    pub fn new(id: u16, target: String, flags: u8, is_initiator: bool) -> Self {
        Self {
            id,
            target,
            flags,
            state: if is_initiator { ChannelState::Opening } else { ChannelState::Open },
            send_credit: 0, // Filled when peer sends WINDOW
            recv_credit: CHANNEL_INITIAL_CREDIT,
            bytes_sent: 0,
            bytes_recv: 0,
            is_initiator,
            last_error: 0,
            last_error_msg: String::new(),
        }
    }

    /// Check if we can send `len` bytes (have sufficient credit).
    pub fn can_send(&self, len: usize) -> bool {
        self.state == ChannelState::Open && self.send_credit >= len as u32
    }

    /// Consume send credit after sending data.
    pub fn consume_send_credit(&mut self, len: usize) {
        self.send_credit = self.send_credit.saturating_sub(len as u32);
        self.bytes_sent += len as u64;
    }

    /// Add to send credit (when peer sends WINDOW).
    pub fn add_send_credit(&mut self, credit: u32) {
        self.send_credit = self.send_credit.saturating_add(credit);
        // Transition from Opening to Open on first credit grant
        if self.state == ChannelState::Opening {
            self.state = ChannelState::Open;
        }
    }

    /// Consume recv credit when receiving data.
    pub fn consume_recv_credit(&mut self, len: usize) {
        self.recv_credit = self.recv_credit.saturating_sub(len as u32);
        self.bytes_recv += len as u64;
    }

    /// Add to recv credit (when we send WINDOW).
    pub fn add_recv_credit(&mut self, credit: u32) {
        self.recv_credit = self.recv_credit.saturating_add(credit);
    }

    /// Check if we should send a WINDOW update (low recv credit).
    pub fn needs_window_update(&self) -> bool {
        // Send update when credit drops below 25% of initial
        self.state == ChannelState::Open && 
        self.recv_credit < CHANNEL_INITIAL_CREDIT / 4
    }

    /// Check various flag combinations
    pub fn is_bidirectional(&self) -> bool {
        self.flags & CHANNEL_FLAG_BIDIR != 0 ||
        (self.flags & (CHANNEL_FLAG_READ_ONLY | CHANNEL_FLAG_WRITE_ONLY) == 0)
    }

    pub fn is_read_only(&self) -> bool {
        self.flags & CHANNEL_FLAG_READ_ONLY != 0
    }

    pub fn is_write_only(&self) -> bool {
        self.flags & CHANNEL_FLAG_WRITE_ONLY != 0
    }

    pub fn is_compressed(&self) -> bool {
        self.flags & CHANNEL_FLAG_COMPRESSED != 0
    }

    pub fn is_encrypted(&self) -> bool {
        self.flags & CHANNEL_FLAG_ENCRYPTED != 0
    }

    pub fn is_audited(&self) -> bool {
        self.flags & CHANNEL_FLAG_AUDIT != 0
    }
}

// ─── Channel Mux ─────────────────────────────────────────────────────

/// Result of processing an incoming frame.
#[derive(Debug)]
pub enum MuxEvent {
    /// New channel opened by peer
    ChannelOpened { channel_id: u16, target: String, flags: u8 },
    /// Data received on a channel
    DataReceived { channel_id: u16, data: Vec<u8> },
    /// Channel closed
    ChannelClosed { channel_id: u16, code: u16 },
    /// Error on channel (channel still open)
    ChannelError { channel_id: u16, code: u16, message: String },
    /// Window update (for send credit tracking)
    WindowUpdate { channel_id: u16, credit: u32 },
    /// Protocol error (invalid frame, unknown channel, etc.)
    ProtocolError { message: String },
}

/// Multiplexer managing multiple channels over a WSLink session.
#[pyclass]
#[derive(Debug)]
pub struct ChannelMux {
    /// Active channels by ID
    channels: HashMap<u16, Channel>,
    /// Next client-initiated channel ID (odd: 1, 3, 5, ...)
    next_client_id: u16,
    /// Next server-initiated channel ID (even: 2, 4, 6, ...)
    next_server_id: u16,
    /// Whether this mux is client-side (initiates odd IDs)
    is_client: bool,
    /// Maximum concurrent channels
    max_channels: usize,
    /// Total channels opened (lifetime)
    total_opened: u64,
    /// Total channels closed (lifetime)
    total_closed: u64,
}

#[pymethods]
impl ChannelMux {
    /// Create a new channel multiplexer.
    ///
    /// Args:
    ///   is_client: True if this is the client side (initiates odd channel IDs)
    ///   max_channels: Maximum concurrent channels (default 256)
    #[new]
    #[pyo3(signature = (is_client=true, max_channels=256))]
    pub fn new(is_client: bool, max_channels: usize) -> Self {
        Self {
            channels: HashMap::new(),
            next_client_id: 1,
            next_server_id: 2,
            is_client,
            max_channels: max_channels.min(CHANNEL_MAX_CONCURRENT),
            total_opened: 0,
            total_closed: 0,
        }
    }

    /// Open a new channel to a target.
    ///
    /// Returns: (channel_id, open_packet_bytes)
    /// Raises: ValueError if max channels reached
    pub fn open_channel(&mut self, target: &str, flags: u8) -> PyResult<(u16, Vec<u8>)> {
        if self.channels.len() >= self.max_channels {
            return Err(PyValueError::new_err(format!(
                "max channels ({}) reached",
                self.max_channels
            )));
        }

        // Allocate next ID for our side
        let id = if self.is_client {
            let id = self.next_client_id;
            self.next_client_id = self.next_client_id.wrapping_add(2);
            if self.next_client_id == 1 { self.next_client_id = 1; } // Wrap to valid odd
            id
        } else {
            let id = self.next_server_id;
            self.next_server_id = self.next_server_id.wrapping_add(2);
            if self.next_server_id == 0 { self.next_server_id = 2; } // Wrap to valid even
            id
        };

        // Create channel in Opening state
        let channel = Channel::new(id, target.to_string(), flags, true);
        self.channels.insert(id, channel);
        self.total_opened += 1;

        // Build OPEN packet
        let packet = SocketOpenPacket::pack(id, flags, target);
        Ok((id, packet))
    }

    /// Handle an incoming SOCKET_OPEN from peer.
    ///
    /// Returns: (channel_id, window_packet_bytes) to send back
    pub fn handle_open(&mut self, channel_id: u16, flags: u8, target: &str) -> PyResult<(u16, Vec<u8>)> {
        // Validate channel ID parity (should be from peer's side)
        let is_peer_initiated = if self.is_client {
            channel_id % 2 == 0 // Server uses even
        } else {
            channel_id % 2 == 1 // Client uses odd
        };

        if !is_peer_initiated {
            return Err(PyValueError::new_err(format!(
                "invalid channel ID {} for peer (wrong parity)",
                channel_id
            )));
        }

        if self.channels.len() >= self.max_channels {
            return Err(PyValueError::new_err("max channels reached"));
        }

        if self.channels.contains_key(&channel_id) {
            return Err(PyValueError::new_err(format!(
                "channel {} already exists",
                channel_id
            )));
        }

        // Create channel (already Open since peer initiated)
        let channel = Channel::new(channel_id, target.to_string(), flags, false);
        self.channels.insert(channel_id, channel);
        self.total_opened += 1;

        // Send initial WINDOW to grant credit
        let window_packet = SocketWindowPacket::pack(channel_id, CHANNEL_INITIAL_CREDIT);
        Ok((channel_id, window_packet))
    }

    /// Prepare to send data on a channel.
    ///
    /// Returns: data_packet_bytes or None if no credit available
    pub fn send_data(&mut self, channel_id: u16, data: &[u8]) -> PyResult<Option<Vec<u8>>> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        if channel.state != ChannelState::Open {
            return Err(PyValueError::new_err(format!(
                "channel {} not open (state: {})",
                channel_id,
                channel.state.as_str()
            )));
        }

        if !channel.can_send(data.len()) {
            return Ok(None); // No credit, caller should wait for WINDOW
        }

        channel.consume_send_credit(data.len());
        let packet = SocketDataPacket::pack(channel_id, data);
        Ok(Some(packet))
    }

    /// Handle incoming data on a channel.
    ///
    /// Returns: (data, should_send_window)
    pub fn handle_data(&mut self, channel_id: u16, data: Vec<u8>) -> PyResult<(Vec<u8>, bool)> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        if channel.state != ChannelState::Open {
            return Err(PyValueError::new_err(format!(
                "received data on non-open channel {} (state: {})",
                channel_id,
                channel.state.as_str()
            )));
        }

        channel.consume_recv_credit(data.len());
        let needs_window = channel.needs_window_update();

        Ok((data, needs_window))
    }

    /// Handle incoming WINDOW update.
    pub fn handle_window(&mut self, channel_id: u16, credit: u32) -> PyResult<()> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        channel.add_send_credit(credit);
        Ok(())
    }

    /// Build a WINDOW packet to grant more recv credit to peer.
    pub fn grant_window(&mut self, channel_id: u16, credit: u32) -> PyResult<Vec<u8>> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        channel.add_recv_credit(credit);
        Ok(SocketWindowPacket::pack(channel_id, credit))
    }

    /// Close a channel gracefully.
    ///
    /// Returns: close_packet_bytes
    pub fn close_channel(&mut self, channel_id: u16, code: u16) -> PyResult<Vec<u8>> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        channel.state = ChannelState::Closing;
        Ok(SocketClosePacket::pack(channel_id, code))
    }

    /// Handle incoming CLOSE from peer.
    ///
    /// Returns: (should_send_close_back, close_packet_bytes_if_any)
    pub fn handle_close(&mut self, channel_id: u16, _code: u16) -> PyResult<(bool, Option<Vec<u8>>)> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        let send_close_back = channel.state != ChannelState::Closing;
        channel.state = ChannelState::Closed;
        self.total_closed += 1;

        if send_close_back {
            Ok((true, Some(SocketClosePacket::pack(channel_id, CHANNEL_CLOSE_NORMAL))))
        } else {
            Ok((false, None))
        }
    }

    /// Remove a closed channel from the mux.
    pub fn remove_channel(&mut self, channel_id: u16) -> PyResult<bool> {
        Ok(self.channels.remove(&channel_id).is_some())
    }

    /// Handle incoming ERROR notification.
    pub fn handle_error(&mut self, channel_id: u16, code: u16, message: &str) -> PyResult<()> {
        let channel = self.channels.get_mut(&channel_id)
            .ok_or_else(|| PyValueError::new_err(format!("unknown channel {}", channel_id)))?;

        channel.last_error = code;
        channel.last_error_msg = message.to_string();
        // Note: ERROR doesn't change state - it's informational
        Ok(())
    }

    /// Send an error notification on a channel.
    pub fn send_error(&mut self, channel_id: u16, code: u16, message: &str) -> PyResult<Vec<u8>> {
        // Verify channel exists
        if !self.channels.contains_key(&channel_id) {
            return Err(PyValueError::new_err(format!("unknown channel {}", channel_id)));
        }
        Ok(SocketErrorPacket::pack(channel_id, code, message))
    }

    /// Get channel info as a dict.
    pub fn get_channel(&self, py: Python<'_>, channel_id: u16) -> PyResult<Option<PyObject>> {
        match self.channels.get(&channel_id) {
            Some(ch) => {
                let dict = PyDict::new_bound(py);
                dict.set_item("id", ch.id)?;
                dict.set_item("target", &ch.target)?;
                dict.set_item("flags", ch.flags)?;
                dict.set_item("state", ch.state.as_str())?;
                dict.set_item("send_credit", ch.send_credit)?;
                dict.set_item("recv_credit", ch.recv_credit)?;
                dict.set_item("bytes_sent", ch.bytes_sent)?;
                dict.set_item("bytes_recv", ch.bytes_recv)?;
                dict.set_item("is_initiator", ch.is_initiator)?;
                dict.set_item("is_bidirectional", ch.is_bidirectional())?;
                dict.set_item("is_compressed", ch.is_compressed())?;
                dict.set_item("is_encrypted", ch.is_encrypted())?;
                dict.set_item("is_audited", ch.is_audited())?;
                Ok(Some(dict.into()))
            }
            None => Ok(None),
        }
    }

    /// List all channel IDs.
    pub fn list_channels(&self) -> Vec<u16> {
        self.channels.keys().copied().collect()
    }

    /// Get mux statistics.
    pub fn stats(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new_bound(py);
        dict.set_item("is_client", self.is_client)?;
        dict.set_item("active_channels", self.channels.len())?;
        dict.set_item("max_channels", self.max_channels)?;
        dict.set_item("total_opened", self.total_opened)?;
        dict.set_item("total_closed", self.total_closed)?;
        dict.set_item("next_client_id", self.next_client_id)?;
        dict.set_item("next_server_id", self.next_server_id)?;

        // Aggregate stats
        let mut total_send_credit: u64 = 0;
        let mut total_recv_credit: u64 = 0;
        let mut total_bytes_sent: u64 = 0;
        let mut total_bytes_recv: u64 = 0;
        let mut channels_by_state: HashMap<&str, u32> = HashMap::new();

        for ch in self.channels.values() {
            total_send_credit += ch.send_credit as u64;
            total_recv_credit += ch.recv_credit as u64;
            total_bytes_sent += ch.bytes_sent;
            total_bytes_recv += ch.bytes_recv;
            *channels_by_state.entry(ch.state.as_str()).or_insert(0) += 1;
        }

        dict.set_item("total_send_credit", total_send_credit)?;
        dict.set_item("total_recv_credit", total_recv_credit)?;
        dict.set_item("total_bytes_sent", total_bytes_sent)?;
        dict.set_item("total_bytes_recv", total_bytes_recv)?;

        let state_dict = PyDict::new_bound(py);
        for (state, count) in channels_by_state {
            state_dict.set_item(state, count)?;
        }
        dict.set_item("channels_by_state", state_dict)?;

        Ok(dict.into())
    }

    /// Check if a channel exists and is open.
    pub fn is_channel_open(&self, channel_id: u16) -> bool {
        self.channels.get(&channel_id)
            .map(|ch| ch.state == ChannelState::Open)
            .unwrap_or(false)
    }

    /// Get available send credit for a channel.
    pub fn get_send_credit(&self, channel_id: u16) -> Option<u32> {
        self.channels.get(&channel_id).map(|ch| ch.send_credit)
    }

    /// Reset the mux (close all channels, reset counters).
    pub fn reset(&mut self) {
        self.channels.clear();
        self.next_client_id = 1;
        self.next_server_id = 2;
        self.total_opened = 0;
        self.total_closed = 0;
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_channel_state_transitions() {
        let mut ch = Channel::new(1, "ssh-agent".into(), CHANNEL_FLAG_BIDIR, true);
        assert_eq!(ch.state, ChannelState::Opening);
        
        // First window grants moves to Open
        ch.add_send_credit(65536);
        assert_eq!(ch.state, ChannelState::Open);
        assert_eq!(ch.send_credit, 65536);
    }

    #[test]
    fn test_channel_flow_control() {
        let mut ch = Channel::new(1, "test".into(), 0, true);
        ch.state = ChannelState::Open;
        ch.send_credit = 1000;

        // Can send within credit
        assert!(ch.can_send(500));
        assert!(ch.can_send(1000));
        assert!(!ch.can_send(1001));

        // Consume credit
        ch.consume_send_credit(500);
        assert_eq!(ch.send_credit, 500);
        assert_eq!(ch.bytes_sent, 500);

        // Can't send more than remaining
        assert!(!ch.can_send(501));
    }

    #[test]
    fn test_channel_recv_credit() {
        let mut ch = Channel::new(1, "test".into(), 0, false);
        assert_eq!(ch.recv_credit, CHANNEL_INITIAL_CREDIT);

        // Consume recv credit
        ch.consume_recv_credit(60000);
        assert_eq!(ch.recv_credit, CHANNEL_INITIAL_CREDIT - 60000);
        assert!(ch.needs_window_update()); // Below 25%
    }

    #[test]
    fn test_mux_open_channel_client() {
        let mut mux = ChannelMux::new(true, 256);
        
        let (id1, _) = mux.open_channel("ssh-agent", CHANNEL_FLAG_BIDIR).unwrap();
        assert_eq!(id1, 1); // First odd ID
        
        let (id2, _) = mux.open_channel("tcp:localhost:22", 0).unwrap();
        assert_eq!(id2, 3); // Next odd ID

        assert_eq!(mux.list_channels().len(), 2);
    }

    #[test]
    fn test_mux_open_channel_server() {
        let mut mux = ChannelMux::new(false, 256);
        
        let (id1, _) = mux.open_channel("ssh-agent", 0).unwrap();
        assert_eq!(id1, 2); // First even ID
        
        let (id2, _) = mux.open_channel("unix:/tmp/sock", 0).unwrap();
        assert_eq!(id2, 4); // Next even ID
    }

    #[test]
    fn test_mux_handle_peer_open() {
        let mut mux = ChannelMux::new(true, 256); // Client
        
        // Server opens channel 2 (even)
        let (id, window_packet) = mux.handle_open(2, CHANNEL_FLAG_BIDIR, "tcp:db:5432").unwrap();
        assert_eq!(id, 2);
        assert!(!window_packet.is_empty());
        
        // Channel should be open
        assert!(mux.is_channel_open(2));
    }

    #[test]
    fn test_mux_reject_wrong_parity() {
        let mut mux = ChannelMux::new(true, 256); // Client
        
        // Server should not open odd channels
        let result = mux.handle_open(1, 0, "test");
        assert!(result.is_err());
    }

    #[test]
    fn test_mux_max_channels() {
        let mut mux = ChannelMux::new(true, 3);
        
        mux.open_channel("a", 0).unwrap();
        mux.open_channel("b", 0).unwrap();
        mux.open_channel("c", 0).unwrap();
        
        // Fourth should fail
        let result = mux.open_channel("d", 0);
        assert!(result.is_err());
    }

    #[test]
    fn test_mux_send_data_with_credit() {
        let mut mux = ChannelMux::new(true, 256);
        let (id, _) = mux.open_channel("test", 0).unwrap();
        
        // No credit yet - should return None
        let result = mux.send_data(id, b"hello").unwrap();
        assert!(result.is_none());
        
        // Grant credit via window
        mux.handle_window(id, 1000).unwrap();
        
        // Now should work
        let result = mux.send_data(id, b"hello").unwrap();
        assert!(result.is_some());
    }

    #[test]
    fn test_mux_close_handshake() {
        let mut mux = ChannelMux::new(true, 256);
        let (id, _) = mux.open_channel("test", 0).unwrap();
        mux.handle_window(id, 1000).unwrap(); // Open it

        // We initiate close
        let close_packet = mux.close_channel(id, CHANNEL_CLOSE_NORMAL).unwrap();
        assert!(!close_packet.is_empty());

        // Peer sends close back
        let (send_back, _) = mux.handle_close(id, CHANNEL_CLOSE_NORMAL).unwrap();
        assert!(!send_back); // We already sent close

        // Channel should be closed
        assert!(!mux.is_channel_open(id));
    }

    #[test]
    fn test_mux_peer_initiates_close() {
        let mut mux = ChannelMux::new(true, 256);
        let (id, _) = mux.open_channel("test", 0).unwrap();
        mux.handle_window(id, 1000).unwrap();

        // Peer initiates close
        let (send_back, close_packet) = mux.handle_close(id, CHANNEL_CLOSE_NORMAL).unwrap();
        assert!(send_back);
        assert!(close_packet.is_some());
    }

    #[test]
    fn test_channel_flags() {
        let ch = Channel::new(1, "test".into(), 
            CHANNEL_FLAG_COMPRESSED | CHANNEL_FLAG_ENCRYPTED | CHANNEL_FLAG_AUDIT, true);
        
        assert!(ch.is_bidirectional()); // No direction flags = bidir
        assert!(ch.is_compressed());
        assert!(ch.is_encrypted());
        assert!(ch.is_audited());
        assert!(!ch.is_read_only());
        assert!(!ch.is_write_only());
    }

    #[test]
    fn test_channel_read_only() {
        let ch = Channel::new(1, "test".into(), CHANNEL_FLAG_READ_ONLY, true);
        assert!(ch.is_read_only());
        assert!(!ch.is_bidirectional());
        assert!(!ch.is_write_only());
    }
}
