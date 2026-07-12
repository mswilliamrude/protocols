"""Socket proxy — bidirectional data pump between ChannelMux and targets.

This module provides the SocketProxy class that:
1. Receives SOCKET_OPEN packets and creates target handlers
2. Pumps data bidirectionally between channels and targets
3. Handles flow control (credit/window updates)
4. Manages channel lifecycle (open, close, error)
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set, Tuple

from .channel import ChannelMux, ChannelState, get_channel_mux
from .handlers import (
    TargetHandler,
    TargetPolicy,
    get_handler,
    DEFAULT_POLICY,
)
from .const import (
    PACK_SOCKET_OPEN,
    PACK_SOCKET_DATA,
    PACK_SOCKET_CLOSE,
    PACK_SOCKET_ERROR,
    PACK_SOCKET_WINDOW,
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_CLOSE_TARGET_REFUSED,
    CHANNEL_CLOSE_TARGET_UNREACHABLE,
    CHANNEL_CLOSE_PROTOCOL_ERROR,
    CHANNEL_INITIAL_CREDIT,
)

log = logging.getLogger(__name__)


@dataclass
class ProxyStats:
    """Aggregate statistics for the socket proxy."""
    channels_opened: int = 0
    channels_closed: int = 0
    channels_errored: int = 0
    bytes_to_targets: int = 0
    bytes_from_targets: int = 0
    policy_denials: int = 0
    connect_failures: int = 0


class SocketProxy:
    """Bidirectional socket proxy over WSLink channels.
    
    Usage:
        proxy = SocketProxy(send_callback, is_server=True)
        
        # When WebSocket message received:
        await proxy.handle_packet(packet_type, payload)
        
        # To initiate a channel (client side):
        channel_id = await proxy.open_channel("tcp:localhost:22")
    """
    
    def __init__(
        self,
        send_callback: Callable[[bytes], None],
        is_server: bool = False,
        policy: Optional[TargetPolicy] = None,
    ):
        """Initialize the socket proxy.
        
        Args:
            send_callback: Function to send packets over WebSocket.
                          Called with complete framed packet bytes.
            is_server: True if this is the server side (accepts channels).
            policy: Target connection policy. Defaults to DEFAULT_POLICY.
        """
        self._send = send_callback
        self._is_server = is_server
        self._policy = policy or DEFAULT_POLICY
        self._mux = get_channel_mux(is_client=not is_server)
        self._handlers: Dict[int, TargetHandler] = {}
        self._pump_tasks: Dict[int, asyncio.Task] = {}
        self._stats = ProxyStats()
        self._closed = False
    
    @property
    def stats(self) -> ProxyStats:
        return self._stats
    
    @property
    def active_channels(self) -> int:
        return len(self._handlers)
    
    async def handle_packet(self, packet_type: int, payload: bytes) -> None:
        """Handle an incoming socket proxy packet.
        
        Args:
            packet_type: One of PACK_SOCKET_* constants
            payload: Raw packet payload (after type byte, before CRC)
        """
        if self._closed:
            return
        
        if packet_type == PACK_SOCKET_OPEN:
            await self._handle_open(payload)
        elif packet_type == PACK_SOCKET_DATA:
            await self._handle_data(payload)
        elif packet_type == PACK_SOCKET_CLOSE:
            await self._handle_close(payload)
        elif packet_type == PACK_SOCKET_ERROR:
            await self._handle_error(payload)
        elif packet_type == PACK_SOCKET_WINDOW:
            await self._handle_window(payload)
        else:
            log.warning(f"Unknown packet type: {packet_type}")
    
    async def open_channel(self, target: str, flags: int = 0) -> int:
        """Open a new channel to a target (client-side).
        
        Args:
            target: Target string (e.g., "ssh-agent", "tcp:localhost:22")
            flags: Channel flags
        
        Returns:
            Channel ID
        
        Raises:
            RuntimeError: If max channels reached
            PermissionError: If target denied by policy
        """
        if self._is_server:
            raise RuntimeError("Server cannot initiate channels")
        
        if not self._policy.is_allowed(target):
            self._stats.policy_denials += 1
            raise PermissionError(f"Target denied by policy: {target}")
        
        # Create channel in mux
        packet = self._mux.open_channel(target, flags)
        if packet is None:
            raise RuntimeError("Max channels reached")
        
        # Extract channel ID from packet (bytes 0:2)
        channel_id = struct.unpack("<H", packet[:2])[0]
        
        # Send OPEN packet
        self._send(self._frame_packet(PACK_SOCKET_OPEN, packet))
        
        self._stats.channels_opened += 1
        log.debug(f"Opened channel {channel_id} to {target}")
        
        return channel_id
    
    async def send_data(self, channel_id: int, data: bytes) -> bool:
        """Send data on a channel.
        
        Args:
            channel_id: Channel to send on
            data: Data to send
        
        Returns:
            True if sent, False if blocked by flow control
        """
        packet = self._mux.send_data(channel_id, data)
        if packet is None:
            return False  # Blocked by flow control
        
        self._send(self._frame_packet(PACK_SOCKET_DATA, packet))
        return True
    
    async def close_channel(self, channel_id: int, code: int = CHANNEL_CLOSE_NORMAL) -> None:
        """Close a channel.
        
        Args:
            channel_id: Channel to close
            code: Close code
        """
        packet = self._mux.close_channel(channel_id, code)
        if packet:
            self._send(self._frame_packet(PACK_SOCKET_CLOSE, packet))
        
        await self._cleanup_channel(channel_id)
        self._stats.channels_closed += 1
    
    async def close(self) -> None:
        """Close all channels and stop the proxy."""
        self._closed = True
        
        # Cancel all pump tasks
        for task in self._pump_tasks.values():
            task.cancel()
        
        # Close all handlers
        for handler in list(self._handlers.values()):
            await handler.close()
        
        self._handlers.clear()
        self._pump_tasks.clear()
    
    # --- Internal packet handlers ---
    
    async def _handle_open(self, payload: bytes) -> None:
        """Handle SOCKET_OPEN packet (server-side)."""
        if not self._is_server:
            # Client receiving OPEN — this is a WINDOW response
            # The mux handles state transition
            return
        
        # Parse: channel_id (2) + flags (2) + target (null-terminated)
        channel_id, flags = struct.unpack("<HH", payload[:4])
        target_bytes = payload[4:]
        
        # Strip null terminator if present
        if target_bytes.endswith(b"\x00"):
            target_bytes = target_bytes[:-1]
        target = target_bytes.decode("utf-8")
        
        log.debug(f"Received OPEN for channel {channel_id} -> {target}")
        
        # Policy check
        if not self._policy.is_allowed(target):
            self._stats.policy_denials += 1
            log.warning(f"Policy denied target: {target}")
            await self._send_error(channel_id, CHANNEL_CLOSE_TARGET_REFUSED, "Denied by policy")
            return
        
        # Accept the channel in mux
        result = self._mux.handle_open(channel_id, target, flags)
        if result is None:
            log.warning(f"Mux rejected channel {channel_id}")
            await self._send_error(channel_id, CHANNEL_CLOSE_PROTOCOL_ERROR, "Channel rejected")
            return
        
        # Create handler and connect to target
        try:
            handler = get_handler(channel_id, target, flags)
            await handler.connect()
            self._handlers[channel_id] = handler
        except Exception as e:
            self._stats.connect_failures += 1
            log.error(f"Failed to connect to {target}: {e}")
            await self._send_error(channel_id, CHANNEL_CLOSE_TARGET_UNREACHABLE, str(e))
            self._mux.close_channel(channel_id, CHANNEL_CLOSE_TARGET_UNREACHABLE)
            return
        
        # Send WINDOW to confirm channel is open
        window_packet = self._mux.send_window_update(channel_id, CHANNEL_INITIAL_CREDIT)
        if window_packet:
            self._send(self._frame_packet(PACK_SOCKET_WINDOW, window_packet))
        
        # Start data pump task
        self._pump_tasks[channel_id] = asyncio.create_task(
            self._pump_from_target(channel_id, handler)
        )
        
        self._stats.channels_opened += 1
        log.info(f"Channel {channel_id} connected to {target}")
    
    async def _handle_data(self, payload: bytes) -> None:
        """Handle SOCKET_DATA packet."""
        # Parse: channel_id (2) + data
        channel_id = struct.unpack("<H", payload[:2])[0]
        data = payload[2:]
        
        # Update mux flow control
        result = self._mux.handle_data(channel_id, data)
        if result is None:
            log.warning(f"Data rejected for channel {channel_id}")
            return
        
        # Check if we need to send window update
        window_packet = self._mux.send_window_update(channel_id, 0)  # 0 = auto-calculate
        if window_packet:
            self._send(self._frame_packet(PACK_SOCKET_WINDOW, window_packet))
        
        # Forward to target
        handler = self._handlers.get(channel_id)
        if handler and handler.connected:
            try:
                await handler.write(data)
                self._stats.bytes_to_targets += len(data)
            except Exception as e:
                log.error(f"Write to target failed on channel {channel_id}: {e}")
                await self.close_channel(channel_id, CHANNEL_CLOSE_TARGET_UNREACHABLE)
    
    async def _handle_close(self, payload: bytes) -> None:
        """Handle SOCKET_CLOSE packet."""
        channel_id, code = struct.unpack("<HH", payload[:4])
        
        log.debug(f"Received CLOSE for channel {channel_id} (code={code})")
        
        self._mux.handle_peer_close(channel_id, code)
        await self._cleanup_channel(channel_id)
        self._stats.channels_closed += 1
    
    async def _handle_error(self, payload: bytes) -> None:
        """Handle SOCKET_ERROR packet."""
        channel_id, code = struct.unpack("<HH", payload[:4])
        message = payload[4:].decode("utf-8", errors="replace")
        
        log.warning(f"Error on channel {channel_id}: {code} - {message}")
        
        self._mux.handle_error(channel_id, code, message)
        await self._cleanup_channel(channel_id)
        self._stats.channels_errored += 1
    
    async def _handle_window(self, payload: bytes) -> None:
        """Handle SOCKET_WINDOW packet."""
        channel_id, credit = struct.unpack("<HI", payload[:6])
        
        log.debug(f"Window update for channel {channel_id}: +{credit}")
        
        self._mux.handle_window(channel_id, credit)
        
        # If this is the first WINDOW (channel confirmation), we can start using it
        # The mux handles state transition internally
    
    # --- Internal helpers ---
    
    async def _pump_from_target(self, channel_id: int, handler: TargetHandler) -> None:
        """Pump data from target to channel."""
        try:
            while handler.connected and not self._closed:
                data = await handler.read(32768)  # 32KB chunks
                if not data:
                    # EOF from target
                    log.debug(f"EOF from target on channel {channel_id}")
                    await self.close_channel(channel_id, CHANNEL_CLOSE_NORMAL)
                    return
                
                self._stats.bytes_from_targets += len(data)
                
                # Send data, respecting flow control
                while data and not self._closed:
                    packet = self._mux.send_data(channel_id, data)
                    if packet is None:
                        # Blocked by flow control — wait a bit
                        await asyncio.sleep(0.01)
                        continue
                    
                    self._send(self._frame_packet(PACK_SOCKET_DATA, packet))
                    
                    # Check how much was sent (packet = channel_id + data sent)
                    sent_len = len(packet) - 2
                    data = data[sent_len:]
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Pump error on channel {channel_id}: {e}")
            await self.close_channel(channel_id, CHANNEL_CLOSE_TARGET_UNREACHABLE)
    
    async def _send_error(self, channel_id: int, code: int, message: str) -> None:
        """Send an error packet."""
        packet = self._mux.send_error(channel_id, code, message)
        if packet:
            self._send(self._frame_packet(PACK_SOCKET_ERROR, packet))
    
    async def _cleanup_channel(self, channel_id: int) -> None:
        """Clean up channel resources."""
        # Cancel pump task
        task = self._pump_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Close handler
        handler = self._handlers.pop(channel_id, None)
        if handler:
            await handler.close()
    
    def _frame_packet(self, packet_type: int, payload: bytes) -> bytes:
        """Frame a packet with length, type, and CRC.
        
        Format: [4-byte LE length][1-byte type][payload][4-byte LE CRC32]
        """
        import zlib
        
        # Length includes type + payload (not length itself or CRC)
        length = 1 + len(payload)
        
        # Build packet
        packet = struct.pack("<I", length) + bytes([packet_type]) + payload
        
        # Add CRC32
        crc = zlib.crc32(packet) & 0xFFFFFFFF
        packet += struct.pack("<I", crc)
        
        return packet


# Convenience function for quick proxy setup
def create_proxy(
    send_callback: Callable[[bytes], None],
    is_server: bool = False,
    policy: Optional[TargetPolicy] = None,
) -> SocketProxy:
    """Create a socket proxy with default settings.
    
    Args:
        send_callback: Function to send packets over WebSocket
        is_server: True if this is the server side
        policy: Target connection policy
    
    Returns:
        Configured SocketProxy instance
    """
    return SocketProxy(send_callback, is_server, policy)
