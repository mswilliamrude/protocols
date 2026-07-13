"""Per-channel compression and encryption transforms for WSLink socket proxy.

Implements:
- LZ4 compression (fast, ~400MB/s compress, ~2GB/s decompress)
- ChaCha20-Poly1305 AEAD encryption (fast on CPUs without AES-NI)
- AES-256-GCM AEAD encryption (CNSA 1.0/2.0 compliant, fast with AES-NI)

Cipher Suite Support:
- CHACHA20_POLY1305: Default, excellent performance on all CPUs
- AES_256_GCM: NSA CNSA Suite approved, uses hardware AES-NI when available
- AES_256_GCM_SIV: Nonce-misuse resistant variant (future)

Each channel can independently enable compression and/or encryption via flags.
Transforms are applied in order: compress → encrypt (on send), decrypt → decompress (on recv).
"""

from __future__ import annotations

import os
import struct
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Tuple

# Optional dependencies - graceful fallback
try:
    import lz4.frame
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305, AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


class CipherSuite(Enum):
    """Supported cipher suites for channel encryption."""
    
    NONE = auto()               # Raw/unencrypted - explicit rawdog mode
    CHACHA20_POLY1305 = auto()  # Default: fast on all CPUs
    AES_256_GCM = auto()        # CNSA 1.0/2.0: fast with AES-NI hardware
    # Future: AES_256_GCM_SIV for nonce-misuse resistance


# Alias for clarity
RAW = CipherSuite.NONE


class TransformError(Exception):
    """Error during compression/encryption transform."""
    pass


class Transform(ABC):
    """Abstract base for data transforms."""
    
    @abstractmethod
    def encode(self, data: bytes) -> bytes:
        """Transform data for sending."""
        pass
    
    @abstractmethod
    def decode(self, data: bytes) -> bytes:
        """Transform data after receiving."""
        pass


class IdentityTransform(Transform):
    """No-op transform (passthrough)."""
    
    def encode(self, data: bytes) -> bytes:
        return data
    
    def decode(self, data: bytes) -> bytes:
        return data


class LZ4Transform(Transform):
    """LZ4 frame compression.
    
    Uses LZ4 frame format which includes:
    - Magic number
    - Frame descriptor (content size, checksums)
    - Data blocks with optional block checksums
    - End marker and content checksum
    
    This is slightly larger than raw LZ4 but self-describing and safer.
    """
    
    def __init__(self, compression_level: int = 0):
        """
        Args:
            compression_level: 0 (fast) to 16 (high compression). Default 0.
        """
        if not HAS_LZ4:
            raise TransformError("lz4 package not installed: pip install lz4")
        self.compression_level = compression_level
    
    def encode(self, data: bytes) -> bytes:
        """Compress data with LZ4."""
        if len(data) < 64:
            # Don't compress tiny data - overhead not worth it
            # Prefix with 0x00 to indicate uncompressed
            return b'\x00' + data
        
        compressed = lz4.frame.compress(
            data,
            compression_level=self.compression_level,
            store_size=True,
        )
        
        # Only use compressed if it's actually smaller
        if len(compressed) < len(data):
            # Prefix with 0x01 to indicate compressed
            return b'\x01' + compressed
        else:
            return b'\x00' + data
    
    def decode(self, data: bytes) -> bytes:
        """Decompress LZ4 data.
        
        Security: Limits decompressed size to prevent decompression bombs.
        """
        if not data:
            return data
        
        flag = data[0]
        payload = data[1:]
        
        if flag == 0x00:
            # Uncompressed
            return payload
        elif flag == 0x01:
            # LZ4 compressed - with decompression bomb protection
            try:
                # Try with max_output_size (newer lz4 versions)
                return lz4.frame.decompress(payload, max_output_size=MAX_DECOMPRESSED_SIZE)
            except TypeError:
                # Fallback for older lz4 versions without max_output_size
                result = lz4.frame.decompress(payload)
                if len(result) > MAX_DECOMPRESSED_SIZE:
                    raise TransformError(
                        f"Decompressed size {len(result)} exceeds limit {MAX_DECOMPRESSED_SIZE}"
                    )
                return result
        else:
            raise TransformError(f"Unknown compression flag: {flag}")


# Security constants
MAX_NONCE_COUNTER = 2**32  # Conservative limit per NIST SP 800-38D for GCM
MIN_MASTER_KEY_LENGTH = 16  # Minimum 128 bits
RECOMMENDED_MASTER_KEY_LENGTH = 32  # 256 bits recommended
MAX_MESSAGE_SIZE = 64 * 1024 * 1024  # 64MB max message
MAX_DECOMPRESSED_SIZE = 256 * 1024 * 1024  # 256MB decompression limit


@dataclass
class EncryptionKey:
    """Encryption key material for a channel.
    
    Security features:
    - Session-unique prefix prevents nonce reuse across sessions
    - Thread-safe nonce generation via lock
    - Counter overflow detection with rekey requirement
    """
    key: bytes  # 32 bytes for ChaCha20/AES-256
    nonce_counter: int = 0  # 64-bit counter for nonce generation
    session_id: bytes = field(default_factory=lambda: os.urandom(4))  # Session-unique prefix
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    
    def next_nonce(self) -> bytes:
        """Generate the next unique nonce (12 bytes), thread-safe.
        
        Format: [4-byte session_id][8-byte counter]
        - session_id: Random per EncryptionKey instance, prevents cross-session reuse
        - counter: Monotonic, prevents intra-session reuse
        
        Raises:
            TransformError: If nonce space is exhausted (requires rekeying)
        """
        with self._lock:
            if self.nonce_counter >= MAX_NONCE_COUNTER:
                raise TransformError(
                    f"Nonce counter exhausted ({self.nonce_counter} >= {MAX_NONCE_COUNTER}) - rekey required"
                )
            nonce = self.session_id + struct.pack("<Q", self.nonce_counter)
            self.nonce_counter += 1
            return nonce


class ChaCha20Transform(Transform):
    """ChaCha20-Poly1305 AEAD encryption.
    
    Provides:
    - Confidentiality (ChaCha20 stream cipher)
    - Integrity (Poly1305 MAC)
    - Authentication of associated data (channel ID)
    
    Wire format: [12-byte nonce][ciphertext][16-byte tag]
    
    Security features:
    - Session-unique nonces prevent cross-session reuse
    - Thread-safe nonce generation
    - Message size limits prevent memory exhaustion
    - Sanitized error messages prevent information leakage
    """
    
    TAG_SIZE = 16
    NONCE_SIZE = 12
    KEY_SIZE = 32
    
    def __init__(self, key: bytes, channel_id: int):
        """
        Args:
            key: 32-byte encryption key
            channel_id: Channel ID for associated data
        """
        if not HAS_CRYPTO:
            raise TransformError(
                "cryptography package not installed: pip install cryptography"
            )
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"Key must be {self.KEY_SIZE} bytes, got {len(key)}")
        
        self._cipher = ChaCha20Poly1305(key)
        self._key_state = EncryptionKey(key=key)
        self._channel_id = channel_id
        # Associated data: channel ID as 2-byte LE
        self._aad = struct.pack("<H", channel_id)
    
    def encode(self, data: bytes) -> bytes:
        """Encrypt data with ChaCha20-Poly1305."""
        if len(data) > MAX_MESSAGE_SIZE:
            raise TransformError(f"Message too large: {len(data)} > {MAX_MESSAGE_SIZE}")
        nonce = self._key_state.next_nonce()
        ciphertext = self._cipher.encrypt(nonce, data, self._aad)
        # Wire format: nonce + ciphertext (includes tag)
        return nonce + ciphertext
    
    def decode(self, data: bytes) -> bytes:
        """Decrypt ChaCha20-Poly1305 data."""
        if len(data) < self.NONCE_SIZE + self.TAG_SIZE:
            raise TransformError("Encrypted data too short")
        if len(data) > MAX_MESSAGE_SIZE + self.NONCE_SIZE + self.TAG_SIZE:
            raise TransformError("Encrypted data too large")
        
        nonce = data[:self.NONCE_SIZE]
        ciphertext = data[self.NONCE_SIZE:]
        
        try:
            return self._cipher.decrypt(nonce, ciphertext, self._aad)
        except Exception:
            # Sanitize error - don't leak crypto internals
            raise TransformError("Decryption failed") from None


class AES256GCMTransform(Transform):
    """AES-256-GCM AEAD encryption (CNSA Suite compliant).
    
    NSA CNSA 1.0 and 2.0 approved cipher. Uses hardware AES-NI
    acceleration when available (most modern x86/ARM CPUs).
    
    Provides:
    - Confidentiality (AES-256 in GCM mode)
    - Integrity (GHASH-based authentication tag)
    - Authentication of associated data (channel ID)
    
    Wire format: [12-byte nonce][ciphertext][16-byte tag]
    
    Security features:
    - Session-unique nonces prevent cross-session reuse
    - Thread-safe nonce generation
    - Message size limits prevent memory exhaustion
    - Sanitized error messages prevent information leakage
    
    Performance notes:
    - With AES-NI: ~5-10 GB/s (faster than ChaCha20)
    - Without AES-NI: ~200-400 MB/s (slower than ChaCha20)
    """
    
    TAG_SIZE = 16
    NONCE_SIZE = 12  # 96-bit nonce per NIST SP 800-38D
    KEY_SIZE = 32    # 256-bit key for CNSA compliance
    
    def __init__(self, key: bytes, channel_id: int):
        """
        Args:
            key: 32-byte encryption key
            channel_id: Channel ID for associated data
        """
        if not HAS_CRYPTO:
            raise TransformError(
                "cryptography package not installed: pip install cryptography"
            )
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"Key must be {self.KEY_SIZE} bytes, got {len(key)}")
        
        self._cipher = AESGCM(key)
        self._key_state = EncryptionKey(key=key)
        self._channel_id = channel_id
        # Associated data: channel ID as 2-byte LE
        self._aad = struct.pack("<H", channel_id)
    
    def encode(self, data: bytes) -> bytes:
        """Encrypt data with AES-256-GCM."""
        if len(data) > MAX_MESSAGE_SIZE:
            raise TransformError(f"Message too large: {len(data)} > {MAX_MESSAGE_SIZE}")
        nonce = self._key_state.next_nonce()
        ciphertext = self._cipher.encrypt(nonce, data, self._aad)
        # Wire format: nonce + ciphertext (includes tag)
        return nonce + ciphertext
    
    def decode(self, data: bytes) -> bytes:
        """Decrypt AES-256-GCM data."""
        if len(data) < self.NONCE_SIZE + self.TAG_SIZE:
            raise TransformError("Encrypted data too short")
        if len(data) > MAX_MESSAGE_SIZE + self.NONCE_SIZE + self.TAG_SIZE:
            raise TransformError("Encrypted data too large")
        
        nonce = data[:self.NONCE_SIZE]
        ciphertext = data[self.NONCE_SIZE:]
        
        try:
            return self._cipher.decrypt(nonce, ciphertext, self._aad)
        except Exception:
            # Sanitize error - don't leak crypto internals
            raise TransformError("Decryption failed") from None


class CompositeTransform(Transform):
    """Chain multiple transforms together.
    
    Encode order: first → last
    Decode order: last → first
    
    Example: CompositeTransform([LZ4Transform(), ChaCha20Transform(key, chan)])
    This compresses then encrypts on send, decrypts then decompresses on recv.
    """
    
    def __init__(self, transforms: list):
        self._transforms = transforms
    
    def encode(self, data: bytes) -> bytes:
        for transform in self._transforms:
            data = transform.encode(data)
        return data
    
    def decode(self, data: bytes) -> bytes:
        for transform in reversed(self._transforms):
            data = transform.decode(data)
        return data


def derive_channel_key(
    master_key: bytes,
    channel_id: int,
    is_client: bool,
    use_sha384: bool = False,
    salt: bytes = b"wslink-v1-channel-key-salt",
) -> bytes:
    """Derive a unique encryption key for a channel.
    
    Uses HKDF to derive a 32-byte key from the master key.
    The channel ID and direction are included to ensure each
    channel (and direction) has a unique key.
    
    Security features:
    - Static salt improves extraction when master key has non-uniform entropy
    - Master key length validation prevents weak keys
    - Per-channel/direction isolation via info parameter
    
    Args:
        master_key: Shared master key (minimum 16 bytes, 32 recommended)
        channel_id: Channel ID
        is_client: True for client→server, False for server→client
        use_sha384: Use SHA-384 for CNSA compliance (default: SHA-256)
        salt: HKDF salt (default: static application-specific salt)
    
    Returns:
        32-byte derived key for this channel
        
    Raises:
        ValueError: If master key is too short
        TransformError: If cryptography package not available
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:
        raise TransformError("cryptography package required for key derivation")
    
    # Validate master key length
    if len(master_key) < MIN_MASTER_KEY_LENGTH:
        raise ValueError(
            f"Master key must be at least {MIN_MASTER_KEY_LENGTH} bytes, got {len(master_key)}"
        )
    
    # Build info string: "wslink-channel-{id}-{direction}"
    direction = b"c2s" if is_client else b"s2c"
    info = b"wslink-channel-" + struct.pack("<H", channel_id) + b"-" + direction
    
    # CNSA requires SHA-384 minimum
    algorithm = hashes.SHA384() if use_sha384 else hashes.SHA256()
    
    hkdf = HKDF(
        algorithm=algorithm,
        length=32,
        salt=salt,
        info=info,
    )
    return hkdf.derive(master_key)


def create_encryption_transform(
    cipher_suite: CipherSuite,
    key: bytes,
    channel_id: int,
) -> Transform:
    """Create an encryption transform for the specified cipher suite.
    
    Args:
        cipher_suite: Which cipher to use
        key: 32-byte encryption key
        channel_id: Channel ID (used as AAD)
    
    Returns:
        Configured encryption Transform
    
    Raises:
        TransformError: If cipher suite not supported or deps missing
    """
    if cipher_suite == CipherSuite.NONE:
        return IdentityTransform()
    elif cipher_suite == CipherSuite.CHACHA20_POLY1305:
        return ChaCha20Transform(key, channel_id)
    elif cipher_suite == CipherSuite.AES_256_GCM:
        return AES256GCMTransform(key, channel_id)
    else:
        raise TransformError(f"Unsupported cipher suite: {cipher_suite}")


def create_channel_transform(
    flags: int,
    encryption_key: Optional[bytes] = None,
    channel_id: int = 0,
    compression_level: int = 0,
    cipher_suite: CipherSuite = CipherSuite.CHACHA20_POLY1305,
) -> Transform:
    """Create the appropriate transform for a channel based on its flags.
    
    Args:
        flags: Channel flags (CHANNEL_FLAG_COMPRESSED, CHANNEL_FLAG_ENCRYPTED)
        encryption_key: 32-byte key for encryption (required if encrypted)
        channel_id: Channel ID (used as AAD for encryption)
        compression_level: LZ4 compression level (0-16)
        cipher_suite: Which cipher to use (default: ChaCha20-Poly1305)
    
    Returns:
        Configured Transform instance
    """
    from .const import CHANNEL_FLAG_COMPRESSED, CHANNEL_FLAG_ENCRYPTED
    
    transforms = []
    
    # Compression first (before encryption)
    if flags & CHANNEL_FLAG_COMPRESSED:
        if not HAS_LZ4:
            raise TransformError("Compression requested but lz4 not installed")
        transforms.append(LZ4Transform(compression_level))
    
    # Then encryption
    if flags & CHANNEL_FLAG_ENCRYPTED:
        if cipher_suite == CipherSuite.NONE:
            pass  # Explicit rawdog - no encryption even if flag set
        elif not HAS_CRYPTO:
            raise TransformError("Encryption requested but cryptography not installed")
        elif not encryption_key:
            raise TransformError("Encryption requested but no key provided")
        else:
            transforms.append(create_encryption_transform(cipher_suite, encryption_key, channel_id))
    
    if not transforms:
        return IdentityTransform()
    elif len(transforms) == 1:
        return transforms[0]
    else:
        return CompositeTransform(transforms)


# Capability checks
def compression_available() -> bool:
    """Check if LZ4 compression is available."""
    return HAS_LZ4


def encryption_available() -> bool:
    """Check if ChaCha20 encryption is available."""
    return HAS_CRYPTO


def generate_key() -> bytes:
    """Generate a random 32-byte key for encryption."""
    return os.urandom(32)


def cipher_suite_info(suite: CipherSuite) -> dict:
    """Return metadata about a cipher suite.
    
    Returns:
        Dict with name, cnsa_approved, key_size, nonce_size, tag_size
    """
    info = {
        CipherSuite.NONE: {
            "name": "None (raw)",
            "cnsa_approved": False,
            "key_size": 0,
            "nonce_size": 0,
            "tag_size": 0,
            "description": "No encryption - raw data passthrough",
        },
        CipherSuite.CHACHA20_POLY1305: {
            "name": "ChaCha20-Poly1305",
            "cnsa_approved": False,
            "key_size": 32,
            "nonce_size": 12,
            "tag_size": 16,
            "description": "IETF RFC 8439 - fast on all CPUs",
        },
        CipherSuite.AES_256_GCM: {
            "name": "AES-256-GCM",
            "cnsa_approved": True,
            "key_size": 32,
            "nonce_size": 12,
            "tag_size": 16,
            "description": "NIST SP 800-38D - CNSA 1.0/2.0 approved",
        },
    }
    return info.get(suite, {"name": "Unknown", "cnsa_approved": False})


def has_aes_ni() -> bool:
    """Check if CPU has AES-NI hardware acceleration.
    
    AES-NI provides ~10x speedup for AES-GCM operations.
    Returns True if likely available (not guaranteed).
    """
    try:
        # Check /proc/cpuinfo on Linux
        with open("/proc/cpuinfo", "r") as f:
            return "aes" in f.read().lower()
    except (IOError, OSError):
        pass
    
    # Fallback: assume modern x86_64 has it
    import platform
    return platform.machine() in ("x86_64", "AMD64", "aarch64")


def recommended_cipher_suite() -> CipherSuite:
    """Return the recommended cipher suite for this system.
    
    Uses AES-256-GCM if AES-NI is available (faster),
    otherwise ChaCha20-Poly1305 (consistent performance).
    """
    if has_aes_ni():
        return CipherSuite.AES_256_GCM
    return CipherSuite.CHACHA20_POLY1305


def cnsa_cipher_suite() -> CipherSuite:
    """Return the CNSA-compliant cipher suite (AES-256-GCM)."""
    return CipherSuite.AES_256_GCM
