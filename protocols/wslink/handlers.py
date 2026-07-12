"""Target handlers for WSLink socket proxy channels.

Each handler manages one type of target (SSH agent, TCP, Unix socket, etc.)
and provides the bidirectional data pump between the channel and the target.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Tuple, Type

log = logging.getLogger(__name__)


class TargetType(Enum):
    """Supported target types."""
    SSH_AGENT = "ssh-agent"
    TCP = "tcp"
    UNIX = "unix"
    SERIAL = "serial"
    # Future:
    # NAMED_PIPE = "pipe"  # Windows
    # UDP = "udp"


@dataclass
class TargetConfig:
    """Configuration for a target connection."""
    target_type: TargetType
    address: str  # e.g., "localhost:22", "/tmp/ssh-agent.sock"
    allowed: bool = True  # Policy: is this target allowed?
    max_bandwidth_bps: int = 0  # 0 = unlimited
    audit: bool = False  # Log all traffic


@dataclass
class HandlerStats:
    """Statistics for a target handler."""
    bytes_to_target: int = 0
    bytes_from_target: int = 0
    connect_time_ms: float = 0
    errors: int = 0
    last_error: str = ""


class TargetHandler(ABC):
    """Base class for all target handlers.
    
    Subclasses implement connect() and provide read/write abstractions.
    The base class provides the data pump logic.
    """
    
    def __init__(self, channel_id: int, target: str, flags: int):
        self.channel_id = channel_id
        self.target = target
        self.flags = flags
        self.stats = HandlerStats()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._closing = False
    
    @property
    def connected(self) -> bool:
        return self._connected
    
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the target.
        
        Raises:
            ConnectionError: If connection fails
            PermissionError: If access denied
            TimeoutError: If connection times out
        """
        pass
    
    @abstractmethod
    async def read(self, max_bytes: int = 65536) -> bytes:
        """Read data from the target.
        
        Returns empty bytes on EOF.
        """
        pass
    
    @abstractmethod
    async def write(self, data: bytes) -> int:
        """Write data to the target.
        
        Returns number of bytes written.
        """
        pass
    
    async def close(self) -> None:
        """Close the connection to the target."""
        self._closing = True
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
    
    @classmethod
    def parse_target(cls, target: str) -> Tuple[TargetType, str]:
        """Parse a target string into type and address.
        
        Examples:
            "ssh-agent" -> (SSH_AGENT, <platform-specific path>)
            "tcp:localhost:22" -> (TCP, "localhost:22")
            "unix:/tmp/sock" -> (UNIX, "/tmp/sock")
        """
        if target == "ssh-agent":
            return TargetType.SSH_AGENT, cls._get_ssh_agent_path()
        
        if ":" in target:
            prefix, rest = target.split(":", 1)
            prefix = prefix.lower()
            
            if prefix == "tcp":
                return TargetType.TCP, rest
            elif prefix == "unix":
                return TargetType.UNIX, rest
            elif prefix == "serial":
                return TargetType.SERIAL, rest
            # tcp:host:port — "tcp" is prefix, "host:port" is rest
            # but what if no prefix? e.g., "localhost:22"
            # Assume TCP if it looks like host:port
            try:
                host_port = target.rsplit(":", 1)
                int(host_port[1])  # Port is numeric
                return TargetType.TCP, target
            except (ValueError, IndexError):
                pass
        
        raise ValueError(f"Unknown target format: {target}")
    
    @staticmethod
    def _get_ssh_agent_path() -> str:
        """Get the SSH agent socket path for the current platform."""
        system = platform.system()
        
        if system == "Linux" or system == "Darwin":
            # Check SSH_AUTH_SOCK environment variable
            sock = os.environ.get("SSH_AUTH_SOCK")
            if sock:
                return sock
            # Common fallbacks
            if system == "Darwin":
                # macOS Keychain agent
                return "/private/tmp/com.apple.launchd.*/Listeners"
            raise ValueError("SSH_AUTH_SOCK not set")
        
        elif system == "Windows":
            # OpenSSH for Windows uses a named pipe
            return r"\\.\pipe\openssh-ssh-agent"
        
        raise ValueError(f"Unsupported platform for SSH agent: {system}")


class UnixSocketHandler(TargetHandler):
    """Handler for Unix domain socket targets."""
    
    async def connect(self) -> None:
        import time
        start = time.monotonic()
        
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self.target
            )
            self._connected = True
            self.stats.connect_time_ms = (time.monotonic() - start) * 1000
            log.debug(f"Channel {self.channel_id}: connected to Unix socket {self.target}")
        except FileNotFoundError:
            raise ConnectionError(f"Unix socket not found: {self.target}")
        except PermissionError:
            raise PermissionError(f"Permission denied: {self.target}")
        except Exception as e:
            self.stats.errors += 1
            self.stats.last_error = str(e)
            raise ConnectionError(f"Failed to connect to {self.target}: {e}")
    
    async def read(self, max_bytes: int = 65536) -> bytes:
        if not self._reader:
            return b""
        try:
            data = await self._reader.read(max_bytes)
            self.stats.bytes_from_target += len(data)
            return data
        except Exception as e:
            if not self._closing:
                self.stats.errors += 1
                self.stats.last_error = str(e)
            return b""
    
    async def write(self, data: bytes) -> int:
        if not self._writer:
            return 0
        try:
            self._writer.write(data)
            await self._writer.drain()
            self.stats.bytes_to_target += len(data)
            return len(data)
        except Exception as e:
            self.stats.errors += 1
            self.stats.last_error = str(e)
            return 0


class TCPHandler(TargetHandler):
    """Handler for TCP socket targets."""
    
    async def connect(self) -> None:
        import time
        start = time.monotonic()
        
        # Parse host:port
        try:
            if self.target.startswith("["):
                # IPv6: [host]:port
                bracket_end = self.target.index("]")
                host = self.target[1:bracket_end]
                port = int(self.target[bracket_end + 2:])
            else:
                host, port_str = self.target.rsplit(":", 1)
                port = int(port_str)
        except (ValueError, IndexError):
            raise ValueError(f"Invalid TCP target format: {self.target}")
        
        try:
            self._reader, self._writer = await asyncio.open_connection(
                host, port
            )
            self._connected = True
            self.stats.connect_time_ms = (time.monotonic() - start) * 1000
            log.debug(f"Channel {self.channel_id}: connected to TCP {host}:{port}")
        except socket.gaierror as e:
            raise ConnectionError(f"DNS resolution failed for {host}: {e}")
        except ConnectionRefusedError:
            raise ConnectionError(f"Connection refused: {host}:{port}")
        except TimeoutError:
            raise TimeoutError(f"Connection timed out: {host}:{port}")
        except Exception as e:
            self.stats.errors += 1
            self.stats.last_error = str(e)
            raise ConnectionError(f"Failed to connect to {host}:{port}: {e}")
    
    async def read(self, max_bytes: int = 65536) -> bytes:
        if not self._reader:
            return b""
        try:
            data = await self._reader.read(max_bytes)
            self.stats.bytes_from_target += len(data)
            return data
        except Exception as e:
            if not self._closing:
                self.stats.errors += 1
                self.stats.last_error = str(e)
            return b""
    
    async def write(self, data: bytes) -> int:
        if not self._writer:
            return 0
        try:
            self._writer.write(data)
            await self._writer.drain()
            self.stats.bytes_to_target += len(data)
            return len(data)
        except Exception as e:
            self.stats.errors += 1
            self.stats.last_error = str(e)
            return 0


class SSHAgentHandler(UnixSocketHandler):
    """Handler for SSH agent socket.
    
    On Unix: connects to $SSH_AUTH_SOCK
    On Windows: connects to \\.\pipe\openssh-ssh-agent
    """
    
    def __init__(self, channel_id: int, target: str, flags: int):
        # Resolve ssh-agent to actual path
        _, path = TargetHandler.parse_target("ssh-agent")
        super().__init__(channel_id, path, flags)
        self._original_target = target
    
    async def connect(self) -> None:
        system = platform.system()
        
        if system == "Windows":
            # Windows named pipe — different connection method
            await self._connect_windows_pipe()
        else:
            # Unix socket
            await super().connect()
    
    async def _connect_windows_pipe(self) -> None:
        """Connect to Windows named pipe for SSH agent."""
        import time
        start = time.monotonic()
        
        # Windows named pipe requires different handling
        # For now, use the socket module with AF_UNIX emulation or
        # fall back to synchronous pipe access
        try:
            import ctypes
            from ctypes import wintypes
            
            # This is a simplified version — production would use
            # asyncio-compatible Windows named pipe handling
            raise NotImplementedError(
                "Windows named pipe support requires additional implementation. "
                "Consider using ssh-pageant or OpenSSH's Unix socket bridge."
            )
        except ImportError:
            raise NotImplementedError("Windows named pipe support not available")


# Handler registry
_HANDLERS: Dict[TargetType, Type[TargetHandler]] = {
    TargetType.SSH_AGENT: SSHAgentHandler,
    TargetType.UNIX: UnixSocketHandler,
    TargetType.TCP: TCPHandler,
}


def get_handler(channel_id: int, target: str, flags: int) -> TargetHandler:
    """Create appropriate handler for a target string.
    
    Args:
        channel_id: The channel this handler is for
        target: Target string (e.g., "ssh-agent", "tcp:localhost:22")
        flags: Channel flags
    
    Returns:
        Appropriate TargetHandler subclass instance
    
    Raises:
        ValueError: If target format is unknown
        KeyError: If no handler registered for target type
    """
    target_type, address = TargetHandler.parse_target(target)
    
    handler_cls = _HANDLERS.get(target_type)
    if not handler_cls:
        raise KeyError(f"No handler registered for target type: {target_type}")
    
    return handler_cls(channel_id, address, flags)


def register_handler(target_type: TargetType, handler_cls: Type[TargetHandler]) -> None:
    """Register a custom handler for a target type."""
    _HANDLERS[target_type] = handler_cls


# Target allowlist for security
@dataclass
class TargetPolicy:
    """Security policy for target connections."""
    
    # Allowed target patterns (prefix match)
    allowed_targets: list = field(default_factory=lambda: [
        "ssh-agent",
        "tcp:localhost:",
        "tcp:127.0.0.1:",
        "tcp:::1:",
        "unix:/tmp/",
        "unix:/var/run/",
    ])
    
    # Denied patterns (checked first)
    denied_targets: list = field(default_factory=list)
    
    # Max concurrent channels per target type
    max_channels_per_type: Dict[TargetType, int] = field(default_factory=lambda: {
        TargetType.SSH_AGENT: 4,
        TargetType.TCP: 64,
        TargetType.UNIX: 32,
    })
    
    def is_allowed(self, target: str) -> bool:
        """Check if a target is allowed by policy."""
        # Check denied first
        for pattern in self.denied_targets:
            if target.startswith(pattern) or target == pattern:
                return False
        
        # Check allowed
        for pattern in self.allowed_targets:
            if target.startswith(pattern) or target == pattern:
                return True
        
        return False


# Default policy instance
DEFAULT_POLICY = TargetPolicy()
