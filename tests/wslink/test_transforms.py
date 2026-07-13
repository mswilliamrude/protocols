"""Tests for WSLink channel transforms (compression/encryption)."""

import os
import struct
import pytest

from protocols.wslink.transforms import (
    Transform,
    IdentityTransform,
    LZ4Transform,
    ChaCha20Transform,
    AES256GCMTransform,
    CompositeTransform,
    TransformError,
    EncryptionKey,
    CipherSuite,
    RAW,
    derive_channel_key,
    create_channel_transform,
    create_encryption_transform,
    compression_available,
    encryption_available,
    generate_key,
    cipher_suite_info,
    has_aes_ni,
    recommended_cipher_suite,
    cnsa_cipher_suite,
    HAS_LZ4,
    HAS_CRYPTO,
)
from protocols.wslink.const import CHANNEL_FLAG_COMPRESSED, CHANNEL_FLAG_ENCRYPTED


class TestIdentityTransform:
    """Test the no-op identity transform."""
    
    def test_encode_passthrough(self):
        transform = IdentityTransform()
        data = b"hello world"
        assert transform.encode(data) == data
    
    def test_decode_passthrough(self):
        transform = IdentityTransform()
        data = b"hello world"
        assert transform.decode(data) == data
    
    def test_empty_data(self):
        transform = IdentityTransform()
        assert transform.encode(b"") == b""
        assert transform.decode(b"") == b""


@pytest.mark.skipif(not HAS_LZ4, reason="lz4 not installed")
class TestLZ4Transform:
    """Test LZ4 compression transform."""
    
    def test_compress_decompress_roundtrip(self):
        transform = LZ4Transform()
        # Use compressible data (repeated pattern)
        data = b"hello world! " * 100
        
        compressed = transform.encode(data)
        decompressed = transform.decode(compressed)
        
        assert decompressed == data
    
    def test_small_data_not_compressed(self):
        transform = LZ4Transform()
        # Small data should not be compressed
        data = b"tiny"
        
        encoded = transform.encode(data)
        
        # Should have 0x00 prefix (uncompressed)
        assert encoded[0] == 0x00
        assert encoded[1:] == data
    
    def test_incompressible_data(self):
        transform = LZ4Transform()
        # Random data is not compressible
        data = os.urandom(1000)
        
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
        # Should be uncompressed (0x00 prefix) since compression wouldn't help
        assert encoded[0] == 0x00
    
    def test_compressible_data_actually_compressed(self):
        transform = LZ4Transform()
        # Highly compressible data
        data = b"A" * 10000
        
        encoded = transform.encode(data)
        
        # Should have 0x01 prefix (compressed)
        assert encoded[0] == 0x01
        # Should be much smaller
        assert len(encoded) < len(data) // 10
    
    def test_compression_level(self):
        transform_fast = LZ4Transform(compression_level=0)
        transform_high = LZ4Transform(compression_level=9)
        
        data = b"test data " * 1000
        
        fast = transform_fast.encode(data)
        high = transform_high.encode(data)
        
        # Both should roundtrip correctly
        assert transform_fast.decode(fast) == data
        assert transform_high.decode(high) == data
        
        # High compression might be smaller (or same)
        assert len(high) <= len(fast) + 100  # Allow some variance
    
    def test_invalid_compression_flag(self):
        transform = LZ4Transform()
        
        with pytest.raises(TransformError, match="Unknown compression flag"):
            transform.decode(b"\xFF" + b"invalid data")


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestChaCha20Transform:
    """Test ChaCha20-Poly1305 encryption transform."""
    
    def test_encrypt_decrypt_roundtrip(self):
        key = generate_key()
        transform = ChaCha20Transform(key, channel_id=1)
        
        data = b"secret message"
        
        encrypted = transform.encode(data)
        decrypted = transform.decode(encrypted)
        
        assert decrypted == data
    
    def test_encryption_changes_data(self):
        key = generate_key()
        transform = ChaCha20Transform(key, channel_id=1)
        
        data = b"secret message"
        encrypted = transform.encode(data)
        
        # Encrypted should be different from plaintext
        assert encrypted != data
        # Should be larger (nonce + tag overhead)
        assert len(encrypted) == len(data) + 12 + 16
    
    def test_unique_nonces(self):
        key = generate_key()
        transform = ChaCha20Transform(key, channel_id=1)
        
        data = b"same message"
        enc1 = transform.encode(data)
        enc2 = transform.encode(data)
        
        # Same plaintext should produce different ciphertext (unique nonce)
        assert enc1 != enc2
        
        # But both should decrypt correctly
        assert transform.decode(enc1) == data
        assert transform.decode(enc2) == data
    
    def test_different_channels_different_ciphertext(self):
        key = generate_key()
        transform1 = ChaCha20Transform(key, channel_id=1)
        transform2 = ChaCha20Transform(key, channel_id=2)
        
        data = b"same message"
        # Reset nonce counter to same value for comparison
        transform1._key_state.nonce_counter = 0
        transform2._key_state.nonce_counter = 0
        
        enc1 = transform1.encode(data)
        enc2 = transform2.encode(data)
        
        # Different AAD (channel_id) means different ciphertext
        assert enc1 != enc2
    
    def test_tampered_data_fails(self):
        key = generate_key()
        transform = ChaCha20Transform(key, channel_id=1)
        
        data = b"important data"
        encrypted = transform.encode(data)
        
        # Tamper with ciphertext
        tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 0xFF])
        
        with pytest.raises(TransformError, match="Decryption failed"):
            transform.decode(tampered)
    
    def test_wrong_key_fails(self):
        key1 = generate_key()
        key2 = generate_key()
        
        transform1 = ChaCha20Transform(key1, channel_id=1)
        transform2 = ChaCha20Transform(key2, channel_id=1)
        
        data = b"secret"
        encrypted = transform1.encode(data)
        
        with pytest.raises(TransformError, match="Decryption failed"):
            transform2.decode(encrypted)
    
    def test_invalid_key_length(self):
        with pytest.raises(ValueError, match="Key must be 32 bytes"):
            ChaCha20Transform(b"short", channel_id=1)
    
    def test_data_too_short(self):
        key = generate_key()
        transform = ChaCha20Transform(key, channel_id=1)
        
        with pytest.raises(TransformError, match="too short"):
            transform.decode(b"tiny")


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestAES256GCMTransform:
    """Test AES-256-GCM encryption transform (CNSA compliant)."""
    
    def test_encrypt_decrypt_roundtrip(self):
        key = generate_key()
        transform = AES256GCMTransform(key, channel_id=1)
        
        data = b"Hello, NSA-approved world!"
        encrypted = transform.encode(data)
        decrypted = transform.decode(encrypted)
        
        assert decrypted == data
    
    def test_encryption_changes_data(self):
        key = generate_key()
        transform = AES256GCMTransform(key, channel_id=1)
        
        data = b"plaintext"
        encrypted = transform.encode(data)
        
        assert encrypted != data
        assert len(encrypted) == len(data) + 12 + 16  # nonce + tag
    
    def test_unique_nonces(self):
        key = generate_key()
        transform = AES256GCMTransform(key, channel_id=1)
        
        data = b"same data"
        enc1 = transform.encode(data)
        enc2 = transform.encode(data)
        
        # Same plaintext should produce different ciphertext (unique nonce)
        assert enc1 != enc2
        
        # But both should decrypt correctly
        assert transform.decode(enc1) == data
        assert transform.decode(enc2) == data
    
    def test_different_channels_different_ciphertext(self):
        key = generate_key()
        transform1 = AES256GCMTransform(key, channel_id=1)
        transform2 = AES256GCMTransform(key, channel_id=2)
        
        data = b"same message"
        # Reset nonce counter to same value for comparison
        transform1._key_state.nonce_counter = 0
        transform2._key_state.nonce_counter = 0
        
        enc1 = transform1.encode(data)
        enc2 = transform2.encode(data)
        
        # Different AAD (channel_id) means different ciphertext
        assert enc1 != enc2
    
    def test_tampered_data_fails(self):
        key = generate_key()
        transform = AES256GCMTransform(key, channel_id=1)
        
        data = b"classified data"
        encrypted = transform.encode(data)
        
        # Tamper with ciphertext
        tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 0xFF])
        
        with pytest.raises(TransformError, match="Decryption failed"):
            transform.decode(tampered)
    
    def test_wrong_key_fails(self):
        key1 = generate_key()
        key2 = generate_key()
        
        transform1 = AES256GCMTransform(key1, channel_id=1)
        transform2 = AES256GCMTransform(key2, channel_id=1)
        
        data = b"top secret"
        encrypted = transform1.encode(data)
        
        with pytest.raises(TransformError, match="Decryption failed"):
            transform2.decode(encrypted)
    
    def test_invalid_key_length(self):
        with pytest.raises(ValueError, match="Key must be 32 bytes"):
            AES256GCMTransform(b"short", channel_id=1)
    
    def test_data_too_short(self):
        key = generate_key()
        transform = AES256GCMTransform(key, channel_id=1)
        
        with pytest.raises(TransformError, match="too short"):
            transform.decode(b"tiny")
    
    def test_interop_same_key_different_transforms_fail(self):
        """AES-GCM and ChaCha20 are NOT interoperable even with same key."""
        key = generate_key()
        
        aes_transform = AES256GCMTransform(key, channel_id=1)
        chacha_transform = ChaCha20Transform(key, channel_id=1)
        
        data = b"test data"
        aes_encrypted = aes_transform.encode(data)
        
        # ChaCha20 should fail to decrypt AES-GCM ciphertext
        with pytest.raises(TransformError):
            chacha_transform.decode(aes_encrypted)


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestEncryptionKey:
    """Test encryption key nonce generation."""
    
    def test_nonce_uniqueness(self):
        key_state = EncryptionKey(key=generate_key())
        
        nonces = set()
        for _ in range(1000):
            nonce = key_state.next_nonce()
            assert len(nonce) == 12
            nonces.add(nonce)
        
        # All nonces should be unique
        assert len(nonces) == 1000
    
    def test_nonce_counter_increments(self):
        key_state = EncryptionKey(key=generate_key())
        
        assert key_state.nonce_counter == 0
        key_state.next_nonce()
        assert key_state.nonce_counter == 1
        key_state.next_nonce()
        assert key_state.nonce_counter == 2


class TestCompositeTransform:
    """Test chaining multiple transforms."""
    
    def test_chain_identity_transforms(self):
        transform = CompositeTransform([
            IdentityTransform(),
            IdentityTransform(),
        ])
        
        data = b"test data"
        assert transform.encode(data) == data
        assert transform.decode(data) == data
    
    @pytest.mark.skipif(not HAS_LZ4, reason="lz4 not installed")
    def test_chain_compression_only(self):
        transform = CompositeTransform([LZ4Transform()])
        
        data = b"compressible " * 100
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
    
    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
    def test_chain_encryption_only(self):
        key = generate_key()
        transform = CompositeTransform([ChaCha20Transform(key, 1)])
        
        data = b"secret"
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
    
    @pytest.mark.skipif(not (HAS_LZ4 and HAS_CRYPTO), reason="lz4 or cryptography not installed")
    def test_chain_compress_then_encrypt(self):
        key = generate_key()
        transform = CompositeTransform([
            LZ4Transform(),
            ChaCha20Transform(key, channel_id=1),
        ])
        
        # Large compressible data
        data = b"The quick brown fox " * 500
        
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
        # Should be smaller than original due to compression
        # (even with encryption overhead)
        assert len(encoded) < len(data)


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestKeyDerivation:
    """Test HKDF key derivation."""
    
    def test_derive_produces_32_bytes(self):
        master = generate_key()
        derived = derive_channel_key(master, channel_id=1, is_client=True)
        
        assert len(derived) == 32
    
    def test_different_channels_different_keys(self):
        master = generate_key()
        key1 = derive_channel_key(master, channel_id=1, is_client=True)
        key2 = derive_channel_key(master, channel_id=2, is_client=True)
        
        assert key1 != key2
    
    def test_different_directions_different_keys(self):
        master = generate_key()
        key_c2s = derive_channel_key(master, channel_id=1, is_client=True)
        key_s2c = derive_channel_key(master, channel_id=1, is_client=False)
        
        assert key_c2s != key_s2c
    
    def test_same_inputs_same_key(self):
        master = generate_key()
        key1 = derive_channel_key(master, channel_id=1, is_client=True)
        key2 = derive_channel_key(master, channel_id=1, is_client=True)
        
        assert key1 == key2


class TestCreateChannelTransform:
    """Test the transform factory function."""
    
    def test_no_flags_returns_identity(self):
        transform = create_channel_transform(flags=0)
        assert isinstance(transform, IdentityTransform)
    
    @pytest.mark.skipif(not HAS_LZ4, reason="lz4 not installed")
    def test_compressed_flag(self):
        transform = create_channel_transform(flags=CHANNEL_FLAG_COMPRESSED)
        
        data = b"test " * 100
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
    
    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
    def test_encrypted_flag(self):
        key = generate_key()
        transform = create_channel_transform(
            flags=CHANNEL_FLAG_ENCRYPTED,
            encryption_key=key,
            channel_id=1,
        )
        
        data = b"secret"
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data
    
    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
    def test_encrypted_without_key_fails(self):
        with pytest.raises(TransformError, match="no key provided"):
            create_channel_transform(flags=CHANNEL_FLAG_ENCRYPTED)
    
    @pytest.mark.skipif(not (HAS_LZ4 and HAS_CRYPTO), reason="lz4 or cryptography not installed")
    def test_both_flags(self):
        key = generate_key()
        transform = create_channel_transform(
            flags=CHANNEL_FLAG_COMPRESSED | CHANNEL_FLAG_ENCRYPTED,
            encryption_key=key,
            channel_id=1,
        )
        
        data = b"compress and encrypt this " * 100
        encoded = transform.encode(data)
        decoded = transform.decode(encoded)
        
        assert decoded == data


class TestCapabilityChecks:
    """Test capability detection functions."""
    
    def test_compression_available(self):
        assert compression_available() == HAS_LZ4
    
    def test_encryption_available(self):
        assert encryption_available() == HAS_CRYPTO
    
    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
    def test_generate_key_length(self):
        key = generate_key()
        assert len(key) == 32
    
    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
    def test_generate_key_unique(self):
        keys = [generate_key() for _ in range(100)]
        assert len(set(keys)) == 100  # All unique


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestCipherSuiteSelection:
    """Test cipher suite selection and factory functions."""
    
    def test_cipher_suite_none_returns_identity(self):
        transform = create_encryption_transform(CipherSuite.NONE, b"x" * 32, channel_id=1)
        assert isinstance(transform, IdentityTransform)
    
    def test_cipher_suite_chacha20(self):
        key = generate_key()
        transform = create_encryption_transform(CipherSuite.CHACHA20_POLY1305, key, channel_id=1)
        assert isinstance(transform, ChaCha20Transform)
        
        data = b"test"
        assert transform.decode(transform.encode(data)) == data
    
    def test_cipher_suite_aes256gcm(self):
        key = generate_key()
        transform = create_encryption_transform(CipherSuite.AES_256_GCM, key, channel_id=1)
        assert isinstance(transform, AES256GCMTransform)
        
        data = b"test"
        assert transform.decode(transform.encode(data)) == data
    
    def test_raw_alias(self):
        """RAW should be an alias for CipherSuite.NONE."""
        assert RAW == CipherSuite.NONE
    
    def test_create_channel_transform_with_cipher_suite(self):
        key = generate_key()
        
        # AES-256-GCM via cipher_suite parameter
        transform = create_channel_transform(
            flags=CHANNEL_FLAG_ENCRYPTED,
            encryption_key=key,
            channel_id=1,
            cipher_suite=CipherSuite.AES_256_GCM,
        )
        
        data = b"CNSA compliant data"
        assert transform.decode(transform.encode(data)) == data
    
    def test_rawdog_mode_ignores_encryption_flag(self):
        """CipherSuite.NONE should disable encryption even if flag is set."""
        key = generate_key()
        
        transform = create_channel_transform(
            flags=CHANNEL_FLAG_ENCRYPTED,
            encryption_key=key,
            channel_id=1,
            cipher_suite=CipherSuite.NONE,  # Explicit rawdog
        )
        
        # Should be identity (no encryption)
        data = b"raw data"
        encoded = transform.encode(data)
        assert encoded == data  # No transformation


class TestCipherSuiteInfo:
    """Test cipher suite metadata functions."""
    
    def test_cipher_suite_info_none(self):
        info = cipher_suite_info(CipherSuite.NONE)
        assert info["name"] == "None (raw)"
        assert info["cnsa_approved"] is False
        assert info["key_size"] == 0
    
    def test_cipher_suite_info_chacha20(self):
        info = cipher_suite_info(CipherSuite.CHACHA20_POLY1305)
        assert info["name"] == "ChaCha20-Poly1305"
        assert info["cnsa_approved"] is False
        assert info["key_size"] == 32
        assert info["nonce_size"] == 12
        assert info["tag_size"] == 16
    
    def test_cipher_suite_info_aes256gcm(self):
        info = cipher_suite_info(CipherSuite.AES_256_GCM)
        assert info["name"] == "AES-256-GCM"
        assert info["cnsa_approved"] is True
        assert info["key_size"] == 32
        assert info["nonce_size"] == 12
        assert info["tag_size"] == 16
    
    def test_cnsa_cipher_suite(self):
        suite = cnsa_cipher_suite()
        assert suite == CipherSuite.AES_256_GCM
        
        info = cipher_suite_info(suite)
        assert info["cnsa_approved"] is True
    
    def test_has_aes_ni_returns_bool(self):
        # Just verify it returns a bool without crashing
        result = has_aes_ni()
        assert isinstance(result, bool)
    
    def test_recommended_cipher_suite_returns_valid(self):
        suite = recommended_cipher_suite()
        assert suite in (CipherSuite.CHACHA20_POLY1305, CipherSuite.AES_256_GCM)


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestKeyDerivationSHA384:
    """Test CNSA-compliant SHA-384 key derivation."""
    
    def test_sha384_produces_32_bytes(self):
        master = generate_key()
        derived = derive_channel_key(master, channel_id=1, is_client=True, use_sha384=True)
        
        assert len(derived) == 32
    
    def test_sha384_different_from_sha256(self):
        master = generate_key()
        key_sha256 = derive_channel_key(master, channel_id=1, is_client=True, use_sha384=False)
        key_sha384 = derive_channel_key(master, channel_id=1, is_client=True, use_sha384=True)
        
        assert key_sha256 != key_sha384
    
    def test_sha384_deterministic(self):
        master = generate_key()
        key1 = derive_channel_key(master, channel_id=1, is_client=True, use_sha384=True)
        key2 = derive_channel_key(master, channel_id=1, is_client=True, use_sha384=True)
        
        assert key1 == key2
