//! pyprotocols-core — High-performance file transfer protocol primitives.
//!
//! This crate provides Rust implementations of the hot-path operations
//! for three file transfer protocols (WSLink, HSLink, ZMODEM), exposed
//! to Python via PyO3.
//!
//! # Modules
//!
//! - `crc` — SIMD-accelerated CRC-16, CRC-24, CRC-32
//! - `framer` — Frame parsing and building (trait + implementations)
//! - `transport` — Transport abstraction traits
//! - `file_safety` — Path traversal prevention and filename validation
//! - `protocols::wslink` — WSLink packet structs and framer
//! - `protocols::hslink` — HSLink DLE-escaped framer
//! - `protocols::zmodem` — ZMODEM ZDLE codec

pub mod crc;
pub mod file_safety;
pub mod framer;
pub mod transport;
pub mod protocols;

// PyO3 module definition
use pyo3::prelude::*;

/// Python module: `import pyprotocols_core`
#[pymodule]
fn pyprotocols_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // CRC functions
    m.add_function(wrap_pyfunction!(crc::py_crc32, m)?)?;
    m.add_function(wrap_pyfunction!(crc::py_crc16, m)?)?;
    m.add_function(wrap_pyfunction!(crc::py_crc24, m)?)?;

    // File safety
    m.add_function(wrap_pyfunction!(file_safety::py_validate_receive_path, m)?)?;

    // WSLink framer
    m.add_class::<protocols::wslink::WSLinkFramer>()?;
    m.add_class::<protocols::wslink::FileHeaderPacket>()?;
    m.add_class::<protocols::wslink::SequencePacket>()?;
    m.add_class::<protocols::wslink::ResumeVerifyPacket>()?;

    // WSLink constants
    protocols::wslink::register_constants(m)?;

    // HSLink framer
    m.add_class::<protocols::hslink::HSLinkFramer>()?;

    // HSLink constants
    protocols::hslink::register_constants(m)?;

    // ZMODEM codec
    protocols::zmodem::register(m)?;

    Ok(())
}
