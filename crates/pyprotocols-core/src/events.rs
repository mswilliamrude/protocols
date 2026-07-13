//! Protocol event-kind string constants — parity with the Python `EventKind`
//! (`protocols/wslink/protocol/events.py`).
//!
//! The Rust core has no session state machine (the FSM lives in Python), so this
//! module exposes the shared identifiers only, guaranteeing both languages agree
//! on event-kind strings when a consumer bridges Rust-produced telemetry with the
//! Python observer API.

use pyo3::prelude::*;

pub const STATE_CHANGE: &str = "state_change";
pub const CONGESTION: &str = "congestion";
pub const RTT_SAMPLE: &str = "rtt_sample";
pub const FRAME: &str = "frame";
pub const INTEGRITY: &str = "integrity";
pub const BUFFER: &str = "buffer";
pub const CHANNEL: &str = "channel";
pub const TRANSFER: &str = "transfer";

/// Register event-kind constants in the Python module.
pub fn register_constants(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("EVENT_STATE_CHANGE", STATE_CHANGE)?;
    m.add("EVENT_CONGESTION", CONGESTION)?;
    m.add("EVENT_RTT_SAMPLE", RTT_SAMPLE)?;
    m.add("EVENT_FRAME", FRAME)?;
    m.add("EVENT_INTEGRITY", INTEGRITY)?;
    m.add("EVENT_BUFFER", BUFFER)?;
    m.add("EVENT_CHANNEL", CHANNEL)?;
    m.add("EVENT_TRANSFER", TRANSFER)?;
    Ok(())
}
