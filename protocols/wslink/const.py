# wslink packet types — file transfer (uppercase)
PACK_ACK_BLOCK = b'A'
PACK_CLOSE_FILE = b'C'
PACK_DATA_BLOCK = b'D'  # Single data block type
PACK_CHAT_BLOCK = b'H'
PACK_SKIP_FILE = b'K'
PACK_NAK_BLOCK = b'N'   # Replaces M as well
PACK_OPEN_FILE = b'O'
PACK_PING = b'P'        # Heartbeat ping (keepalive)
PACK_READY_RECV = b'Q'
PACK_READY = b'R'
PACK_SEEK_BLOCK = b'S'
PACK_VERIFY_BLOCK = b'V'
PACK_PONG = b'W'        # Heartbeat pong (response to ping)
PACK_TRANSMIT_DONE = b'Z'

# wslink packet types — socket proxy channels (lowercase, no collision)
# See: docs/SOCKET_PROXY_SPEC.md
PACK_SOCKET_OPEN = b's'    # Open a proxy channel
PACK_SOCKET_DATA = b'd'    # Forward bytes on channel
PACK_SOCKET_CLOSE = b'c'   # Graceful channel close
PACK_SOCKET_ERROR = b'e'   # Channel error notification
PACK_SOCKET_WINDOW = b'w'  # Flow control credit grant

# Channel ID allocation:
#   Odd  (1, 3, 5, ...) = client-initiated
#   Even (2, 4, 6, ...) = server-initiated
# This gives 32,768 IDs per side. If asymmetric usage is expected,
# consider explicit ID negotiation in SOCKET_OPEN.
CHANNEL_ID_CLIENT_START = 1
CHANNEL_ID_SERVER_START = 2
CHANNEL_ID_MAX = 65535

# Channel flags (bitmask in SOCKET_OPEN)
CHANNEL_FLAG_BIDIR = 0x01       # Bidirectional (default if no direction flags)
CHANNEL_FLAG_READ_ONLY = 0x02   # Client can only read
CHANNEL_FLAG_WRITE_ONLY = 0x04  # Client can only write
CHANNEL_FLAG_COMPRESSED = 0x08  # LZ4 compress channel data
CHANNEL_FLAG_ENCRYPTED = 0x10   # ChaCha20-Poly1305 encryption
CHANNEL_FLAG_AUDIT = 0x20       # Log all traffic to audit trail

# Channel close codes
CHANNEL_CLOSE_NORMAL = 0x0000           # Normal close
CHANNEL_CLOSE_TARGET_REFUSED = 0x0001   # Target refused connection
CHANNEL_CLOSE_TARGET_UNREACHABLE = 0x0002  # Target not reachable
CHANNEL_CLOSE_AUTH_FAILED = 0x0003      # Authentication failed
CHANNEL_CLOSE_TIMEOUT = 0x0004          # Connection timed out
CHANNEL_CLOSE_ADMIN = 0x0005            # Closed by administrator
CHANNEL_CLOSE_PROTOCOL_ERROR = 0x0006   # Protocol violation

# Flow control (SSH RFC 4254 §5.2 style)
CHANNEL_INITIAL_CREDIT = 65536  # 64KB initial window per direction
CHANNEL_MAX_CONCURRENT = 256    # Max simultaneous channels per session

MAX_BLOCK_SIZE = 4096
