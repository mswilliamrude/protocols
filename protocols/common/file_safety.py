"""
Shared file safety utilities for all protocol implementations.

Provides centralized path traversal prevention, filename validation,
and receive directory containment checking. Used by HS/Link, WS/Link,
and ZMODEM receivers.
"""

import os
import logging

log = logging.getLogger(__name__)

# Maximum filename length (POSIX NAME_MAX)
MAX_FILENAME_LENGTH = 255


class FilenameSafetyError(ValueError):
    """Raised when a received filename fails safety checks."""
    pass


def validate_receive_path(recv_dir: str, raw_filename: str) -> str:
    """Validate and sanitize a received filename, returning a safe absolute path.
    
    Performs:
    1. Strip directory components (os.path.basename)
    2. Handle Windows-style backslash paths
    3. Reject empty, dotfile, and null-byte filenames
    4. Enforce filename length limit (255 bytes)
    5. Resolve realpath and verify containment within recv_dir
    
    Args:
        recv_dir: The directory where received files should be placed.
        raw_filename: The untrusted filename from the peer.
        
    Returns:
        Safe absolute path within recv_dir.
        
    Raises:
        FilenameSafetyError: If the filename fails any validation check.
    """
    # Handle Windows-style paths (backslash as separator)
    normalized = raw_filename.replace('\\', '/')
    
    # Strip directory components
    filename = os.path.basename(normalized)
    
    # Reject empty filenames
    if not filename:
        raise FilenameSafetyError(f"Empty filename after sanitization: {raw_filename!r}")
    
    # Reject null bytes (defense-in-depth, CPython 3.x already rejects these)
    if '\x00' in filename:
        raise FilenameSafetyError(f"Null byte in filename: {raw_filename!r}")
    
    # Reject dotfiles (hidden files, .bashrc, .ssh, etc.)
    if filename.startswith('.'):
        raise FilenameSafetyError(f"Dotfile rejected: {filename!r}")
    
    # Reject filenames containing path traversal sequences
    if '..' in filename:
        raise FilenameSafetyError(f"Path traversal in filename: {filename!r}")
    
    # Enforce length limit
    if len(filename.encode('utf-8')) > MAX_FILENAME_LENGTH:
        raise FilenameSafetyError(
            f"Filename exceeds {MAX_FILENAME_LENGTH} bytes: {filename[:50]!r}..."
        )
    
    # Resolve the final path
    filepath = os.path.realpath(os.path.join(recv_dir, filename))
    recv_dir_real = os.path.realpath(recv_dir)
    
    # Containment check — filepath must be inside recv_dir
    if not filepath.startswith(recv_dir_real + os.sep) and filepath != recv_dir_real:
        raise FilenameSafetyError(
            f"Path escapes receive directory: {raw_filename!r} → {filepath}"
        )
    
    return filepath


def validate_file_size(size: int, max_size: int = 10 * 1024 * 1024 * 1024) -> None:
    """Validate a peer-reported file size.
    
    Args:
        size: The file size reported by the peer.
        max_size: Maximum allowed size (default 10GB).
        
    Raises:
        ValueError: If size is negative or exceeds maximum.
    """
    if size < 0:
        raise ValueError(f"Negative file size: {size}")
    if size > max_size:
        raise ValueError(f"File size {size} exceeds maximum {max_size}")


def validate_mtime(mtime: float) -> float:
    """Validate and clamp a peer-reported modification time.
    
    Returns a sane mtime or the current time if invalid.
    """
    import time
    now = time.time()
    
    # Reject obviously invalid timestamps
    if mtime <= 0 or mtime != mtime:  # mtime != mtime catches NaN
        return now
    
    # Reject far-future timestamps (more than 1 day ahead)
    if mtime > now + 86400:
        return now
    
    return mtime
