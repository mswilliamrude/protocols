"""Pure Python ChannelMux — fallback when Rust extension unavailable.

For data plane ≤400 Mbps (SSH agent, serial, DNS, USB 2.0).
Rust ChannelMux preferred for >400 Mbps workloads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .const import (
    CHANNEL_MAX_CONCURRENT,
    CHANNEL_INITIAL_CREDIT,
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_FLAG_BIDIR,
    CHANNEL_FLAG_READ_ONLY,
    CHANNEL_FLAG_WRITE_ONLY,
    CHANNEL_FLAG_COMPRESSED,
    CHANNEL_FLAG_ENCRYPTED,
    CHANNEL_FLAG_AUDIT,
    PACK_SOCKET_OPEN,
    PACK_SOCKET_DATA,
    PACK_SOCKET_CLOSE,
    PACK_SOCKET_ERROR,
    PACK_SOCKET_WINDOW,
)


class ChannelState(Enum):
    """State machine for a single channel."""
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class Channel:
    """A single channel within a multiplexed session."""
    id: int
    target: str
    flags: int
    state: ChannelState
    send_credit: int = 0
    recv_credit: int = CHANNEL_INITIAL_CREDIT
    bytes_sent: int = 0
    bytes_recv: int = 0
    is_initiator: bool = True
    last_error: int = 0
    last_error_msg: str = ""

    def can_send(self, length: int) -> bool:
        """Check if we can send `length` bytes (have sufficient credit)."""
        return self.state == ChannelState.OPEN and self.send_credit >= length

    def consume_send_credit(self, length: int) -> None:
        """Consume send credit after sending data."""
        self.send_credit = max(0, self.send_credit - length)
        self.bytes_sent += length

    def add_send_credit(self, credit: int) -> None:
        """Add to send credit (when peer sends WINDOW)."""
        self.send_credit += credit
        if self.state == ChannelState.OPENING:
            self.state = ChannelState.OPEN

    def consume_recv_credit(self, length: int) -> None:
        """Consume recv credit when receiving data."""
        self.recv_credit = max(0, self.recv_credit - length)
        self.bytes_recv += length

    def add_recv_credit(self, credit: int) -> None:
        """Add to recv credit (when we send WINDOW)."""
        self.recv_credit += credit

    def needs_window_update(self) -> bool:
        """Check if we should send a WINDOW update (low recv credit)."""
        return (
            self.state == ChannelState.OPEN and
            self.recv_credit < CHANNEL_INITIAL_CREDIT // 4
        )

    @property
    def is_bidirectional(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_BIDIR) or not (
            self.flags & (CHANNEL_FLAG_READ_ONLY | CHANNEL_FLAG_WRITE_ONLY)
        )

    @property
    def is_read_only(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_READ_ONLY)

    @property
    def is_write_only(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_WRITE_ONLY)

    @property
    def is_compressed(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_COMPRESSED)

    @property
    def is_encrypted(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_ENCRYPTED)

    @property
    def is_audited(self) -> bool:
        return bool(self.flags & CHANNEL_FLAG_AUDIT)


class ChannelMux:
    """Multiplexer managing multiple channels over a WSLink session.
    
    Pure Python implementation — use Rust ChannelMux for >400 Mbps.
    """

    def __init__(self, is_client: bool = True, max_channels: int = 256):
        self.channels: Dict[int, Channel] = {}
        self._next_client_id = 1
        self._next_server_id = 2
        self.is_client = is_client
        self.max_channels = min(max_channels, CHANNEL_MAX_CONCURRENT)
        self.total_opened = 0
        self.total_closed = 0

    def open_channel(self, target: str, flags: int = 0) -> Tuple[int, bytes]:
        """Open a new channel to a target.
        
        Returns: (channel_id, open_packet_bytes)
        Raises: ValueError if max channels reached
        """
        if len(self.channels) >= self.max_channels:
            raise ValueError(f"max channels ({self.max_channels}) reached")

        # Allocate next ID for our side
        if self.is_client:
            channel_id = self._next_client_id
            self._next_client_id += 2
            if self._next_client_id > 0xFFFF:
                self._next_client_id = 1
        else:
            channel_id = self._next_server_id
            self._next_server_id += 2
            if self._next_server_id > 0xFFFF:
                self._next_server_id = 2

        channel = Channel(
            id=channel_id,
            target=target,
            flags=flags,
            state=ChannelState.OPENING,
            is_initiator=True,
        )
        self.channels[channel_id] = channel
        self.total_opened += 1

        # Pack OPEN: channel_id (2) + flags (1) + target (N)
        packet = struct.pack("<HB", channel_id, flags) + target.encode("utf-8")
        return channel_id, packet

    def handle_open(self, channel_id: int, flags: int, target: str) -> Tuple[int, bytes]:
        """Handle an incoming SOCKET_OPEN from peer.
        
        Returns: (channel_id, window_packet_bytes) to send back
        """
        # Validate channel ID parity
        is_peer_initiated = (
            (channel_id % 2 == 0) if self.is_client else (channel_id % 2 == 1)
        )
        if not is_peer_initiated:
            raise ValueError(f"invalid channel ID {channel_id} for peer (wrong parity)")

        if len(self.channels) >= self.max_channels:
            raise ValueError("max channels reached")

        if channel_id in self.channels:
            raise ValueError(f"channel {channel_id} already exists")

        channel = Channel(
            id=channel_id,
            target=target,
            flags=flags,
            state=ChannelState.OPEN,  # Already open since peer initiated
            is_initiator=False,
        )
        self.channels[channel_id] = channel
        self.total_opened += 1

        # Send initial WINDOW
        window_packet = struct.pack("<HI", channel_id, CHANNEL_INITIAL_CREDIT)
        return channel_id, window_packet

    def send_data(self, channel_id: int, data: bytes) -> Optional[bytes]:
        """Prepare to send data on a channel.
        
        Returns: data_packet_bytes or None if no credit available
        """
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")

        if channel.state != ChannelState.OPEN:
            raise ValueError(f"channel {channel_id} not open (state: {channel.state.value})")

        if not channel.can_send(len(data)):
            return None

        channel.consume_send_credit(len(data))
        # Pack DATA: channel_id (2) + data (N)
        return struct.pack("<H", channel_id) + data

    def handle_data(self, channel_id: int, data: bytes) -> Tuple[bytes, bool]:
        """Handle incoming data on a channel.
        
        Returns: (data, should_send_window)
        """
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")

        if channel.state != ChannelState.OPEN:
            raise ValueError(
                f"received data on non-open channel {channel_id} (state: {channel.state.value})"
            )

        channel.consume_recv_credit(len(data))
        return data, channel.needs_window_update()

    def handle_window(self, channel_id: int, credit: int) -> None:
        """Handle incoming WINDOW update."""
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")
        channel.add_send_credit(credit)

    def grant_window(self, channel_id: int, credit: int) -> bytes:
        """Build a WINDOW packet to grant more recv credit to peer."""
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")
        channel.add_recv_credit(credit)
        return struct.pack("<HI", channel_id, credit)

    def close_channel(self, channel_id: int, code: int = CHANNEL_CLOSE_NORMAL) -> bytes:
        """Close a channel gracefully."""
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")
        channel.state = ChannelState.CLOSING
        return struct.pack("<HH", channel_id, code)

    def handle_close(self, channel_id: int, code: int) -> Tuple[bool, Optional[bytes]]:
        """Handle incoming CLOSE from peer.
        
        Returns: (should_send_close_back, close_packet_bytes_if_any)
        """
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")

        send_back = channel.state != ChannelState.CLOSING
        channel.state = ChannelState.CLOSED
        self.total_closed += 1

        if send_back:
            return True, struct.pack("<HH", channel_id, CHANNEL_CLOSE_NORMAL)
        return False, None

    def remove_channel(self, channel_id: int) -> bool:
        """Remove a closed channel from the mux."""
        return self.channels.pop(channel_id, None) is not None

    def handle_error(self, channel_id: int, code: int, message: str) -> None:
        """Handle incoming ERROR notification."""
        channel = self.channels.get(channel_id)
        if channel is None:
            raise ValueError(f"unknown channel {channel_id}")
        channel.last_error = code
        channel.last_error_msg = message

    def send_error(self, channel_id: int, code: int, message: str) -> bytes:
        """Send an error notification on a channel."""
        if channel_id not in self.channels:
            raise ValueError(f"unknown channel {channel_id}")
        msg_bytes = message.encode("utf-8")
        return struct.pack("<HHH", channel_id, code, len(msg_bytes)) + msg_bytes

    def get_channel(self, channel_id: int) -> Optional[dict]:
        """Get channel info as a dict."""
        channel = self.channels.get(channel_id)
        if channel is None:
            return None
        return {
            "id": channel.id,
            "target": channel.target,
            "flags": channel.flags,
            "state": channel.state.value,
            "send_credit": channel.send_credit,
            "recv_credit": channel.recv_credit,
            "bytes_sent": channel.bytes_sent,
            "bytes_recv": channel.bytes_recv,
            "is_initiator": channel.is_initiator,
            "is_bidirectional": channel.is_bidirectional,
            "is_compressed": channel.is_compressed,
            "is_encrypted": channel.is_encrypted,
            "is_audited": channel.is_audited,
        }

    def list_channels(self) -> List[int]:
        """List all channel IDs."""
        return list(self.channels.keys())

    def stats(self) -> dict:
        """Get mux statistics."""
        channels_by_state: Dict[str, int] = {}
        total_send_credit = 0
        total_recv_credit = 0
        total_bytes_sent = 0
        total_bytes_recv = 0

        for ch in self.channels.values():
            state_name = ch.state.value
            channels_by_state[state_name] = channels_by_state.get(state_name, 0) + 1
            total_send_credit += ch.send_credit
            total_recv_credit += ch.recv_credit
            total_bytes_sent += ch.bytes_sent
            total_bytes_recv += ch.bytes_recv

        return {
            "is_client": self.is_client,
            "active_channels": len(self.channels),
            "max_channels": self.max_channels,
            "total_opened": self.total_opened,
            "total_closed": self.total_closed,
            "next_client_id": self._next_client_id,
            "next_server_id": self._next_server_id,
            "total_send_credit": total_send_credit,
            "total_recv_credit": total_recv_credit,
            "total_bytes_sent": total_bytes_sent,
            "total_bytes_recv": total_bytes_recv,
            "channels_by_state": channels_by_state,
        }

    def is_channel_open(self, channel_id: int) -> bool:
        """Check if a channel exists and is open."""
        channel = self.channels.get(channel_id)
        return channel is not None and channel.state == ChannelState.OPEN

    def get_send_credit(self, channel_id: int) -> Optional[int]:
        """Get available send credit for a channel."""
        channel = self.channels.get(channel_id)
        return channel.send_credit if channel else None

    def reset(self) -> None:
        """Reset the mux (close all channels, reset counters)."""
        self.channels.clear()
        self._next_client_id = 1
        self._next_server_id = 2
        self.total_opened = 0
        self.total_closed = 0


# Auto-select implementation
def get_channel_mux(is_client: bool = True, max_channels: int = 256) -> ChannelMux:
    """Get the best available ChannelMux implementation.
    
    Prefers Rust for performance, falls back to pure Python.
    """
    try:
        from pyprotocols_core import ChannelMux as RustChannelMux
        return RustChannelMux(is_client=is_client, max_channels=max_channels)
    except ImportError:
        return ChannelMux(is_client=is_client, max_channels=max_channels)
