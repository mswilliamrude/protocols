//! File safety — path traversal prevention and filename validation.
//!
//! Centralized security boundary for all protocol receivers.
//! Rejects directory traversal, dotfiles, null bytes, and paths
//! that escape the receive directory.

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use std::path::{Path, PathBuf};

/// Maximum filename length (POSIX NAME_MAX).
pub const MAX_FILENAME_LENGTH: usize = 255;

/// Validate and sanitize a received filename, returning a safe absolute path.
///
/// Performs:
/// 1. Normalize Windows backslash separators
/// 2. Extract basename (strip directory components)
/// 3. Reject empty, dotfile, null-byte, and ".." filenames
/// 4. Enforce length limit (255 bytes)
/// 5. Canonicalize and verify containment within recv_dir
///
/// # Errors
/// Returns `Err` if the filename fails any validation check.
pub fn validate_receive_path(recv_dir: &Path, raw_filename: &str) -> Result<PathBuf, String> {
    // Normalize Windows-style paths
    let normalized = raw_filename.replace('\\', "/");

    // Extract basename
    let filename = Path::new(&normalized)
        .file_name()
        .and_then(|f| f.to_str())
        .unwrap_or("");

    // Reject empty
    if filename.is_empty() {
        return Err(format!("Empty filename after sanitization: {:?}", raw_filename));
    }

    // Reject null bytes
    if filename.contains('\0') {
        return Err(format!("Null byte in filename: {:?}", raw_filename));
    }

    // Reject dotfiles
    if filename.starts_with('.') {
        return Err(format!("Dotfile rejected: {:?}", filename));
    }

    // Reject path traversal sequences
    if filename.contains("..") {
        return Err(format!("Path traversal in filename: {:?}", filename));
    }

    // Enforce length limit
    if filename.len() > MAX_FILENAME_LENGTH {
        return Err(format!(
            "Filename exceeds {} bytes: {:?}...",
            MAX_FILENAME_LENGTH,
            &filename[..50]
        ));
    }

    // Build and canonicalize the path
    let filepath = recv_dir.join(filename);
    let filepath_canon = filepath.canonicalize().unwrap_or_else(|_| {
        // File doesn't exist yet — canonicalize the parent and append filename
        let parent = recv_dir.canonicalize().unwrap_or_else(|_| recv_dir.to_path_buf());
        parent.join(filename)
    });

    let recv_dir_canon = recv_dir.canonicalize().unwrap_or_else(|_| recv_dir.to_path_buf());

    // Containment check
    if !filepath_canon.starts_with(&recv_dir_canon) {
        return Err(format!(
            "Path escapes receive directory: {:?} → {:?}",
            raw_filename, filepath_canon
        ));
    }

    Ok(filepath_canon)
}

/// Python-facing wrapper for validate_receive_path.
#[pyfunction]
#[pyo3(name = "validate_receive_path")]
pub fn py_validate_receive_path(recv_dir: &str, raw_filename: &str) -> PyResult<String> {
    let path = validate_receive_path(Path::new(recv_dir), raw_filename)
        .map_err(|e| PyValueError::new_err(e))?;
    Ok(path.to_string_lossy().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_basic_filename() {
        let dir = std::env::temp_dir().join("test_recv");
        fs::create_dir_all(&dir).unwrap();
        let result = validate_receive_path(&dir, "test.bin");
        assert!(result.is_ok());
        assert!(result.unwrap().starts_with(&dir));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn test_path_traversal_blocked() {
        // basename("../../../etc/passwd") = "passwd" which is safe (contained in dir)
        // The real traversal protection is basename stripping.
        // Test a case where basename produces ".." (directory named "..")
        let dir = std::env::temp_dir().join("test_recv2");
        fs::create_dir_all(&dir).unwrap();
        let result = validate_receive_path(&dir, "..");
        assert!(result.is_err(), "'..' should be rejected as dotfile/traversal");
        // Also test that a bare traversal attempt produces a safe path
        let result2 = validate_receive_path(&dir, "../../../etc/passwd");
        // basename extracts "passwd" which is safe — this is CORRECT behavior
        assert!(result2.is_ok(), "basename('/../etc/passwd') = 'passwd' is safe");
        let path = result2.unwrap();
        assert!(path.starts_with(&dir), "result must be inside recv_dir");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn test_dotfile_rejected() {
        let dir = std::env::temp_dir();
        let result = validate_receive_path(&dir, ".bashrc");
        assert!(result.is_err());
    }

    #[test]
    fn test_null_byte_rejected() {
        let dir = std::env::temp_dir();
        let result = validate_receive_path(&dir, "file\x00.txt");
        assert!(result.is_err());
    }

    #[test]
    fn test_windows_path_normalized() {
        let dir = std::env::temp_dir().join("test_recv3");
        fs::create_dir_all(&dir).unwrap();
        let result = validate_receive_path(&dir, "C:\\Users\\attacker\\evil.exe");
        assert!(result.is_ok());
        // Should extract just "evil.exe"
        let path = result.unwrap();
        assert!(path.to_string_lossy().ends_with("evil.exe"));
        fs::remove_dir_all(&dir).unwrap();
    }
}
