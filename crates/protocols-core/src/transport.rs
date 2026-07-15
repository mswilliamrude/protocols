//! Transport traits — abstract byte stream I/O.
//!
//! Protocols operate over transports without knowing the underlying
//! medium (TCP, WebSocket, serial port, PTY, etc.).
//!
//! Two variants:
//! - `Transport` — synchronous (blocking), used by HS/Link and ZMODEM
//! - `AsyncTransport` — async (tokio), used by WS/Link
//!
//! ## Session Modes (Phase 2 — future)
//!
//! WSLink sessions operate in two modes:
//! - **ChatOnly**: No file transfers. TRANSMIT_DONE (Z) is never sent.
//!   Used by MCP router for JSON-RPC traffic over the H (chat) channel.
//! - **FileTransfer**: Files are transferred via sliding-window ARQ.
//!   Z is sent when batch_index > 0 and all files are complete.
//!   Chat channel remains active after Z (session doesn't end).
//!
//! When implementing the full session state machine in Rust, model this
//! as an enum:
//! ```ignore
//! enum SessionMode {
//!     ChatOnly,           // Never sends Z, session lives until close/timeout
//!     FileTransfer,       // Sends Z after files, chat stays active
//! }
//! ```

use std::io;

/// Synchronous transport trait (blocking I/O).
///
/// Used by HS/Link (over stdio/PTY) and ZMODEM (over serial/PTY).
pub trait Transport {
    /// Read exactly `n` bytes. Blocks until all bytes are available or EOF.
    /// Returns empty Vec on EOF.
    fn read_exact(&mut self, n: usize) -> io::Result<Vec<u8>>;

    /// Read up to `n` bytes (non-blocking if possible, blocking otherwise).
    fn read(&mut self, n: usize) -> io::Result<Vec<u8>>;

    /// Write all bytes to the transport.
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;

    /// Flush any buffered output.
    fn flush(&mut self) -> io::Result<()>;

    /// Close the transport.
    fn close(&mut self) -> io::Result<()>;

    /// Check if the transport is still connected/open.
    fn is_open(&self) -> bool;
}

/// Async transport trait (for tokio/asyncio bridge).
///
/// Used by WS/Link over WebSocket. The Python side implements this
/// via the asyncio transport adapter; Rust calls back into Python
/// for actual I/O.
#[cfg(feature = "async")]
pub trait AsyncTransport: Send + Sync {
    /// Read exactly `n` bytes asynchronously.
    fn read_exact(&self, n: usize) -> impl std::future::Future<Output = io::Result<Vec<u8>>> + Send;

    /// Write all bytes asynchronously.
    fn write_all(&self, data: &[u8]) -> impl std::future::Future<Output = io::Result<()>> + Send;

    /// Close the transport.
    fn close(&self) -> impl std::future::Future<Output = io::Result<()>> + Send;
}

/// Transport configuration parameters.
#[derive(Debug, Clone)]
pub struct TransportConfig {
    /// Read timeout in milliseconds (0 = no timeout).
    pub read_timeout_ms: u64,
    /// Write buffer size for coalescing.
    pub write_buffer_size: usize,
    /// Maximum frame size to accept.
    pub max_frame_size: usize,
}

impl Default for TransportConfig {
    fn default() -> Self {
        Self {
            read_timeout_ms: 60_000, // 60s idle timeout
            write_buffer_size: 1024 * 1024, // 1MB write buffer
            max_frame_size: 1024 * 1024, // 1MB max frame
        }
    }
}
