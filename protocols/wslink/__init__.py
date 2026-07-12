"""WSLink protocol — bidirectional file transfer + socket proxy."""

from .channel import ChannelMux, ChannelState, Channel, get_channel_mux
from .handlers import (
    TargetHandler,
    TargetType,
    TargetConfig,
    TargetPolicy,
    HandlerStats,
    UnixSocketHandler,
    TCPHandler,
    SSHAgentHandler,
    get_handler,
    register_handler,
    DEFAULT_POLICY,
)
from .proxy import SocketProxy, ProxyStats, create_proxy
from .const import (
    # Packet types
    PACK_SOCKET_OPEN,
    PACK_SOCKET_DATA,
    PACK_SOCKET_CLOSE,
    PACK_SOCKET_ERROR,
    PACK_SOCKET_WINDOW,
    # Channel flags
    CHANNEL_FLAG_BIDIR,
    CHANNEL_FLAG_READ_ONLY,
    CHANNEL_FLAG_WRITE_ONLY,
    CHANNEL_FLAG_COMPRESSED,
    CHANNEL_FLAG_ENCRYPTED,
    CHANNEL_FLAG_AUDIT,
    # Close codes
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_CLOSE_TARGET_REFUSED,
    CHANNEL_CLOSE_TARGET_UNREACHABLE,
    CHANNEL_CLOSE_AUTH_FAILED,
    CHANNEL_CLOSE_TIMEOUT,
    CHANNEL_CLOSE_ADMIN,
    CHANNEL_CLOSE_PROTOCOL_ERROR,
    # Limits
    CHANNEL_MAX_CONCURRENT,
    CHANNEL_INITIAL_CREDIT,
    CHANNEL_ID_CLIENT_START,
    CHANNEL_ID_SERVER_START,
    CHANNEL_ID_MAX,
)

__all__ = [
    # Channel classes
    "ChannelMux",
    "ChannelState", 
    "Channel",
    "get_channel_mux",
    # Target handlers
    "TargetHandler",
    "TargetType",
    "TargetConfig",
    "TargetPolicy",
    "HandlerStats",
    "UnixSocketHandler",
    "TCPHandler",
    "SSHAgentHandler",
    "get_handler",
    "register_handler",
    "DEFAULT_POLICY",
    # Socket proxy
    "SocketProxy",
    "ProxyStats",
    "create_proxy",
    # Packet types
    "PACK_SOCKET_OPEN",
    "PACK_SOCKET_DATA",
    "PACK_SOCKET_CLOSE",
    "PACK_SOCKET_ERROR",
    "PACK_SOCKET_WINDOW",
    # Flags
    "CHANNEL_FLAG_BIDIR",
    "CHANNEL_FLAG_READ_ONLY",
    "CHANNEL_FLAG_WRITE_ONLY",
    "CHANNEL_FLAG_COMPRESSED",
    "CHANNEL_FLAG_ENCRYPTED",
    "CHANNEL_FLAG_AUDIT",
    # Close codes
    "CHANNEL_CLOSE_NORMAL",
    "CHANNEL_CLOSE_TARGET_REFUSED",
    "CHANNEL_CLOSE_TARGET_UNREACHABLE",
    "CHANNEL_CLOSE_AUTH_FAILED",
    "CHANNEL_CLOSE_TIMEOUT",
    "CHANNEL_CLOSE_ADMIN",
    "CHANNEL_CLOSE_PROTOCOL_ERROR",
    # Limits
    "CHANNEL_MAX_CONCURRENT",
    "CHANNEL_INITIAL_CREDIT",
    "CHANNEL_ID_CLIENT_START",
    "CHANNEL_ID_SERVER_START",
    "CHANNEL_ID_MAX",
]
