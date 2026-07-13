# WSLink Security Model

## Threat Model

WSLink operates in environments where:

1. Network traffic may be intercepted (mitigated by encryption)
2. Malicious clients may attempt resource exhaustion (mitigated by flow control)
3. Servers may be targets of SSRF attacks (mitigated by target validation)
4. Error messages may leak sensitive information (mitigated by sanitization)

## Security Hardening Summary

This document describes security measures implemented during the Phase 5 security audit, addressing 10 critical and 10 high severity findings.

## Cryptographic Security

### Nonce Safety (Critical)

**Threat**: Nonce reuse in AEAD ciphers catastrophically breaks confidentiality.

**Mitigation**: Session-unique nonce construction:

```python
# 4-byte random session ID + 8-byte counter
nonce = session_id (4 bytes) || counter (8 bytes)
```

- `session_id`: Cryptographically random, generated once per session
- `counter`: Thread-safe monotonic counter with lock
- Maximum 2^32 messages per session (counter overflow detection)

```python
MAX_NONCE_COUNTER = 2**32

def _get_nonce(self) -> bytes:
    with self._nonce_lock:
        if self._nonce_counter >= MAX_NONCE_COUNTER:
            raise NonceExhaustedError("Session nonce space exhausted")
        counter = self._nonce_counter
        self._nonce_counter += 1
    return self._session_id + struct.pack("<Q", counter)
```

### Key Derivation (Critical)

**Threat**: Weak key derivation allows key recovery attacks.

**Mitigation**: HKDF-SHA256 with per-session random salt:

```python
session_salt = os.urandom(32)  # Fresh per session
derived_key = HKDF(
    algorithm=SHA256,
    length=32,
    salt=session_salt,
    info=b"wslink-session-key"
).derive(master_key)
```

### Master Key Validation (High)

**Threat**: Weak or short keys reduce security margin.

**Mitigation**: Key validation at initialization:

```python
MIN_KEY_LENGTH = 16  # 128 bits minimum

if len(master_key) < MIN_KEY_LENGTH:
    raise ValueError(f"Master key too short: {len(master_key)} < {MIN_KEY_LENGTH}")
```

## Flow Control Security

### Credit Bounds (Critical)

**Threat**: Integer overflow in credit tracking leads to infinite credit.

**Mitigation**: Bounded credit with overflow detection:

```python
CHANNEL_MAX_CREDIT = 2**32  # 4GB maximum

def adjust_credit(self, channel_id: int, delta: int) -> int:
    current = self._credits[channel_id]
    new_credit = current + delta
    
    if new_credit < 0:
        raise FlowControlViolation(f"Credit underflow: {current} + {delta}")
    if new_credit > CHANNEL_MAX_CREDIT:
        raise FlowControlViolation(f"Credit overflow: {new_credit} > {CHANNEL_MAX_CREDIT}")
    
    self._credits[channel_id] = new_credit
    return new_credit
```

### Flow Control Violation (High)

**Threat**: Malicious peer sends more data than credit allows.

**Mitigation**: Explicit exception and channel termination:

```python
class FlowControlViolation(ChannelError):
    """Raised when peer exceeds credit allowance."""
    pass

# In data handling:
if len(data) > channel.remote_credit:
    raise FlowControlViolation(
        f"Data {len(data)} exceeds credit {channel.remote_credit}"
    )
```

## Network Security

### SSRF Protection (Critical)

**Threat**: Attacker uses proxy to reach internal services.

**Mitigation**: Multi-layer SSRF protection:

```python
PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

BLOCKED_METADATA_HOSTS = [
    "169.254.169.254",           # AWS/GCP metadata
    "metadata.google.internal",   # GCP metadata
    "metadata.azure.internal",    # Azure metadata
]

async def resolve_and_check_ip(hostname: str, port: int) -> tuple[str, int]:
    """Resolve hostname and validate IP is not private/blocked."""
    # Resolve to IP
    infos = await asyncio.get_event_loop().getaddrinfo(hostname, port)
    ip = infos[0][4][0]
    
    # Check blocked hosts
    if hostname.lower() in BLOCKED_METADATA_HOSTS:
        raise SSRFError(f"Blocked metadata host: {hostname}")
    
    # Check private ranges
    ip_obj = ipaddress.ip_address(ip)
    for network in PRIVATE_IP_RANGES:
        if ip_obj in network:
            raise SSRFError(f"Private IP blocked: {ip}")
    
    return ip, port
```

### DNS Rebinding Protection (High)

**Threat**: Attacker's DNS returns private IP after validation.

**Mitigation**: Resolve once, connect to resolved IP:

```python
# BAD: Resolve twice (vulnerable)
# validate(hostname)  # Resolves to public IP
# connect(hostname)   # May resolve to private IP

# GOOD: Resolve once, use IP
resolved_ip, port = await resolve_and_check_ip(hostname, port)
await connect(resolved_ip, port)  # Use resolved IP directly
```

### Connection Timeouts (High)

**Threat**: Slow connections exhaust resources.

**Mitigation**: Configurable timeouts with defaults:

```python
DEFAULT_CONNECT_TIMEOUT = 30.0  # seconds
DEFAULT_READ_TIMEOUT = 60.0

async def connect(self, target: str, timeout: float = DEFAULT_CONNECT_TIMEOUT):
    async with asyncio.timeout(timeout):
        # Connection logic
```

## Path Security

### Path Traversal Protection (Critical)

**Threat**: Attacker accesses files outside allowed directories.

**Mitigation**: Path validation and canonicalization:

```python
MAX_TARGET_LENGTH = 4096

def validate_unix_path(path: str) -> str:
    """Validate and canonicalize Unix socket path."""
    if len(path) > MAX_TARGET_LENGTH:
        raise PathTraversalError(f"Path too long: {len(path)}")
    
    # Reject obvious traversal attempts
    if ".." in path:
        raise PathTraversalError("Path traversal detected: '..'")
    
    # Canonicalize
    canonical = os.path.realpath(path)
    
    # Must be absolute
    if not canonical.startswith("/"):
        raise PathTraversalError("Path must be absolute")
    
    return canonical
```

## Concurrency Security

### Async Lock Safety (High)

**Threat**: Race conditions in connection pool lead to inconsistent state.

**Mitigation**: Dual-lock pattern for async and sync contexts:

```python
class ConnectionPool:
    def __init__(self):
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
    
    async def assign_channel(self, channel_id: int, conn_id: int = None) -> int:
        async with self._async_lock:
            # Thread-safe channel assignment
            return self._assign_channel_internal(channel_id, conn_id)
    
    def assign_channel_sync(self, channel_id: int, conn_id: int = None) -> int:
        with self._sync_lock:
            return self._assign_channel_internal(channel_id, conn_id)
```

### Channel ID Collision (High)

**Threat**: Channel ID wraparound causes collision with active channel.

**Mitigation**: Collision detection on wraparound:

```python
def _next_channel_id(self) -> int:
    start_id = self._next_id
    while True:
        channel_id = self._next_id
        self._next_id += 2  # Maintain parity
        
        if self._next_id > 65535:
            self._next_id = 1 if self._is_client else 2
        
        if channel_id not in self._channels:
            return channel_id
        
        if self._next_id == start_id:
            raise ChannelError("No available channel IDs")
```

## Error Handling Security

### Error Sanitization (Critical)

**Threat**: Detailed error messages leak sensitive information.

**Mitigation**: Pre-defined error messages only:

```python
SANITIZED_ERRORS = {
    "connection_refused": "Target connection refused",
    "timeout": "Connection timed out",
    "dns_failed": "Name resolution failed",
    "permission_denied": "Permission denied",
    "not_found": "Target not found",
    "internal": "Internal error",
}

def sanitize_error(exc: Exception) -> str:
    """Return safe error message without internal details."""
    exc_type = type(exc).__name__.lower()
    
    if "refused" in str(exc).lower():
        return SANITIZED_ERRORS["connection_refused"]
    if "timeout" in exc_type or "timed out" in str(exc).lower():
        return SANITIZED_ERRORS["timeout"]
    # ... pattern matching
    
    # Default: never return actual exception message
    return SANITIZED_ERRORS["internal"]
```

### Exception Sanitization in Crypto (Critical)

**Threat**: Crypto exceptions reveal key material or plaintext.

**Mitigation**: Catch and re-raise with sanitized message:

```python
def decrypt(self, ciphertext: bytes) -> bytes:
    try:
        return self._cipher.decrypt(nonce, ciphertext, None)
    except Exception:
        # Never leak original exception details
        raise DecryptionError("Decryption failed")
```

## Resource Limits

### Connection Limits (High)

```python
MAX_POOL_CONNECTIONS = 64
MAX_CHANNELS_PER_CONNECTION = 256
MAX_PACKET_SIZE = 64 * 1024 * 1024  # 64MB
MAX_DECOMPRESSED_SIZE = 256 * 1024 * 1024  # 256MB
```

### Payload Validation (High)

**Threat**: Oversized payloads cause memory exhaustion.

**Mitigation**: Size validation before processing:

```python
def validate_payload(self, data: bytes) -> None:
    if len(data) > MAX_PACKET_SIZE:
        raise PayloadTooLargeError(
            f"Payload {len(data)} exceeds limit {MAX_PACKET_SIZE}"
        )
```

## Compression Security

### Decompression Bomb Protection (High)

**Threat**: Small compressed payload expands to exhaust memory.

**Mitigation**: Bounded decompression:

```python
MAX_DECOMPRESSED_SIZE = 256 * 1024 * 1024  # 256MB

def decompress(self, data: bytes) -> bytes:
    # LZ4 with output size limit (version-compatible)
    try:
        return lz4.frame.decompress(data, max_output_size=MAX_DECOMPRESSED_SIZE)
    except TypeError:
        # Older LZ4 without max_output_size parameter
        result = lz4.frame.decompress(data)
        if len(result) > MAX_DECOMPRESSED_SIZE:
            raise DecompressionError("Decompressed size exceeds limit")
        return result
```

## Audit Trail

All security-relevant events are logged:

- Connection attempts (allowed/denied)
- Channel operations (open/close/error)
- Policy violations (SSRF, path traversal)
- Crypto events (key derivation, nonce exhaustion)

## Security Testing

### Test Coverage

| Category | Tests | Description |
|----------|-------|-------------|
| Nonce Safety | 8 | Uniqueness, counter overflow, thread safety |
| Key Derivation | 4 | HKDF correctness, salt uniqueness |
| Flow Control | 6 | Credit bounds, violation detection |
| SSRF | 5 | Private IP blocking, metadata protection |
| Path Traversal | 4 | Traversal detection, canonicalization |
| Error Sanitization | 6 | Message safety, no information leakage |

### Running Security Tests

```bash
# All security-related tests
python -m pytest tests/wslink/ -v -k "security or ssrf or traversal or nonce"

# Crypto tests specifically
python -m pytest tests/wslink/test_transforms.py -v
```
