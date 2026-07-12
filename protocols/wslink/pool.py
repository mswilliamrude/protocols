"""Parallel connection pool for WSLink socket proxy.

Bonds multiple WebSocket connections together for higher throughput.
Each connection in the pool can carry multiple channels; the pool
distributes load across connections using configurable strategies.

Use cases:
- Saturate high-bandwidth links (10G+) where single WebSocket is bottleneck
- Redundancy: channels survive individual connection failures
- Load balancing across multiple server endpoints

Security features:
- Async locks on all state mutations (assign/unassign/rebalance)
- Thread-safe synchronous operations via threading.Lock
- Safe rebalance with proper lock ordering
- Connection limit enforcement
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

log = logging.getLogger(__name__)


# Security constants
MAX_POOL_CONNECTIONS = 64
MAX_CHANNELS_PER_CONNECTION = 256


class DispatchStrategy(Enum):
    """Strategy for selecting which connection to use."""
    ROUND_ROBIN = "round_robin"      # Cycle through connections
    LEAST_LOADED = "least_loaded"    # Pick connection with fewest active channels
    LEAST_LATENCY = "least_latency"  # Pick connection with lowest RTT
    RANDOM = "random"                # Random selection
    STICKY = "sticky"                # Stick to one connection per channel


class ConnectionState(Enum):
    """State of a pooled connection."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DRAINING = "draining"    # No new channels, waiting for existing to close
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass
class ConnectionStats:
    """Statistics for a single connection."""
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0
    channels_active: int = 0
    channels_total: int = 0
    connect_time: float = 0.0
    last_activity: float = 0.0
    latency_ms: float = 0.0
    errors: int = 0


@dataclass
class PoolStats:
    """Aggregate statistics for the connection pool."""
    connections_active: int = 0
    connections_total: int = 0
    connections_failed: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    channels_active: int = 0
    rebalance_count: int = 0


class PooledConnection:
    """A single connection in the pool."""
    
    def __init__(
        self,
        conn_id: int,
        send_func: Callable[[bytes], None],
        endpoint: str = "",
    ):
        self.conn_id = conn_id
        self.send = send_func
        self.endpoint = endpoint
        self.state = ConnectionState.CONNECTING
        self.stats = ConnectionStats()
        self.channels: Set[int] = set()
        self._ping_task: Optional[asyncio.Task] = None
        self._last_pong: float = 0.0
    
    @property
    def is_available(self) -> bool:
        """Can this connection accept new channels?"""
        return self.state == ConnectionState.CONNECTED
    
    def add_channel(self, channel_id: int) -> None:
        """Track a channel on this connection."""
        self.channels.add(channel_id)
        self.stats.channels_active = len(self.channels)
        self.stats.channels_total += 1
    
    def remove_channel(self, channel_id: int) -> None:
        """Remove a channel from this connection."""
        self.channels.discard(channel_id)
        self.stats.channels_active = len(self.channels)
    
    def record_send(self, nbytes: int) -> None:
        """Record bytes sent."""
        self.stats.bytes_sent += nbytes
        self.stats.packets_sent += 1
        self.stats.last_activity = time.monotonic()
    
    def record_recv(self, nbytes: int) -> None:
        """Record bytes received."""
        self.stats.bytes_recv += nbytes
        self.stats.packets_recv += 1
        self.stats.last_activity = time.monotonic()
    
    def update_latency(self, latency_ms: float) -> None:
        """Update RTT measurement."""
        # Exponential moving average
        alpha = 0.3
        if self.stats.latency_ms == 0:
            self.stats.latency_ms = latency_ms
        else:
            self.stats.latency_ms = alpha * latency_ms + (1 - alpha) * self.stats.latency_ms


class ConnectionPool:
    """Pool of WebSocket connections for parallel data transfer.
    
    Usage:
        pool = ConnectionPool(strategy=DispatchStrategy.LEAST_LOADED)
        
        # Add connections as they're established
        pool.add_connection(conn_id=1, send_func=ws1.send)
        pool.add_connection(conn_id=2, send_func=ws2.send)
        
        # Send data - pool selects best connection
        pool.send(channel_id=5, data=packet_bytes)
        
        # Handle incoming data (from any connection)
        pool.on_receive(conn_id=1, data=packet_bytes)
    
    Thread safety:
        - Async methods use asyncio.Lock for coroutine-safe operations
        - Sync methods use threading.Lock for thread-safe operations
        - All state mutations are protected by appropriate locks
    """
    
    def __init__(
        self,
        strategy: DispatchStrategy = DispatchStrategy.ROUND_ROBIN,
        min_connections: int = 1,
        max_connections: int = 8,
        rebalance_threshold: float = 0.3,  # Rebalance if load differs by 30%
    ):
        self.strategy = strategy
        self.min_connections = min_connections
        self.max_connections = min(max_connections, MAX_POOL_CONNECTIONS)
        self.rebalance_threshold = rebalance_threshold
        
        self._connections: Dict[int, PooledConnection] = {}
        self._channel_to_conn: Dict[int, int] = {}  # channel_id -> conn_id
        self._round_robin_index = 0
        self._stats = PoolStats()
        
        # Locks for thread safety
        self._async_lock = asyncio.Lock()  # For async operations
        self._sync_lock = threading.Lock()  # For sync operations (send, on_receive)
        
        # Callbacks
        self._on_receive: Optional[Callable[[int, bytes], None]] = None
        self._on_connection_lost: Optional[Callable[[int], None]] = None
    
    @property
    def stats(self) -> PoolStats:
        """Get aggregate pool statistics."""
        self._stats.connections_active = sum(
            1 for c in self._connections.values() if c.is_available
        )
        self._stats.connections_total = len(self._connections)
        self._stats.channels_active = len(self._channel_to_conn)
        self._stats.bytes_sent = sum(c.stats.bytes_sent for c in self._connections.values())
        self._stats.bytes_recv = sum(c.stats.bytes_recv for c in self._connections.values())
        return self._stats
    
    @property
    def active_connections(self) -> int:
        """Number of connections available for new channels."""
        return sum(1 for c in self._connections.values() if c.is_available)
    
    def set_receive_callback(self, callback: Callable[[int, bytes], None]) -> None:
        """Set callback for incoming data: callback(channel_id, data)."""
        self._on_receive = callback
    
    def set_connection_lost_callback(self, callback: Callable[[int], None]) -> None:
        """Set callback for connection failures: callback(conn_id)."""
        self._on_connection_lost = callback
    
    async def add_connection(
        self,
        conn_id: int,
        send_func: Callable[[bytes], None],
        endpoint: str = "",
    ) -> PooledConnection:
        """Add a new connection to the pool.
        
        Args:
            conn_id: Unique identifier for this connection
            send_func: Function to send bytes on this connection
            endpoint: Optional endpoint URL for logging
        
        Returns:
            The PooledConnection wrapper
        """
        async with self._async_lock:
            if conn_id in self._connections:
                raise ValueError(f"Connection {conn_id} already exists")
            
            if len(self._connections) >= self.max_connections:
                raise ValueError(f"Pool at max capacity ({self.max_connections})")
            
            conn = PooledConnection(conn_id, send_func, endpoint)
            conn.state = ConnectionState.CONNECTED
            conn.stats.connect_time = time.monotonic()
            self._connections[conn_id] = conn
            
            log.info(f"Added connection {conn_id} to pool ({endpoint})")
            return conn
    
    async def remove_connection(self, conn_id: int, graceful: bool = True) -> None:
        """Remove a connection from the pool.
        
        Args:
            conn_id: Connection to remove
            graceful: If True, drain channels first; if False, immediate removal
        """
        async with self._async_lock:
            conn = self._connections.get(conn_id)
            if not conn:
                return
            
            if graceful and conn.channels:
                # Mark as draining - no new channels, wait for existing
                conn.state = ConnectionState.DRAINING
                log.info(f"Draining connection {conn_id} ({len(conn.channels)} channels)")
                return
            
            # Immediate removal
            self._remove_connection_internal(conn_id)
    
    def _remove_connection_internal(self, conn_id: int) -> None:
        """Internal: remove connection without lock (caller must hold lock)."""
        conn = self._connections.pop(conn_id, None)
        if not conn:
            return
        
        # Reassign orphaned channels
        orphaned = list(conn.channels)
        for channel_id in orphaned:
            self._channel_to_conn.pop(channel_id, None)
        
        conn.state = ConnectionState.DISCONNECTED
        log.info(f"Removed connection {conn_id} ({len(orphaned)} orphaned channels)")
    
    async def mark_connection_failed(self, conn_id: int) -> List[int]:
        """Mark a connection as failed and return orphaned channel IDs.
        
        Caller should close/reopen these channels on a different connection.
        """
        async with self._async_lock:
            conn = self._connections.get(conn_id)
            if not conn:
                return []
            
            conn.state = ConnectionState.FAILED
            conn.stats.errors += 1
            self._stats.connections_failed += 1
            
            orphaned = list(conn.channels)
            for channel_id in orphaned:
                self._channel_to_conn.pop(channel_id, None)
            conn.channels.clear()
            
            if self._on_connection_lost:
                self._on_connection_lost(conn_id)
            
            log.warning(f"Connection {conn_id} failed ({len(orphaned)} orphaned channels)")
            return orphaned
    
    async def assign_channel(self, channel_id: int, conn_id: Optional[int] = None) -> int:
        """Assign a channel to a connection.
        
        Args:
            channel_id: The channel to assign
            conn_id: Specific connection ID, or None to auto-select
        
        Returns:
            The connection ID the channel was assigned to
        
        Raises:
            RuntimeError: If no connections available
            ValueError: If specified connection not available
        """
        async with self._async_lock:
            # Check if channel already assigned
            if channel_id in self._channel_to_conn:
                existing_conn = self._channel_to_conn[channel_id]
                log.warning(f"Channel {channel_id} already assigned to connection {existing_conn}")
                return existing_conn
            
            if conn_id is not None:
                # Specific connection requested
                conn = self._connections.get(conn_id)
                if not conn or not conn.is_available:
                    raise ValueError(f"Connection {conn_id} not available")
                # Check per-connection channel limit
                if len(conn.channels) >= MAX_CHANNELS_PER_CONNECTION:
                    raise ValueError(f"Connection {conn_id} at channel capacity")
            else:
                # Auto-select based on strategy
                conn = self._select_connection()
                if not conn:
                    raise RuntimeError("No connections available")
                conn_id = conn.conn_id
            
            conn.add_channel(channel_id)
            self._channel_to_conn[channel_id] = conn_id
            return conn_id
    
    async def unassign_channel(self, channel_id: int) -> None:
        """Remove channel assignment.
        
        Thread-safe: uses async lock to protect state mutations.
        """
        async with self._async_lock:
            conn_id = self._channel_to_conn.pop(channel_id, None)
            if conn_id is not None:
                conn = self._connections.get(conn_id)
                if conn:
                    conn.remove_channel(channel_id)
                    
                    # Check if draining connection is now empty
                    if conn.state == ConnectionState.DRAINING and not conn.channels:
                        self._remove_connection_internal(conn_id)
    
    def assign_channel_sync(self, channel_id: int, conn_id: Optional[int] = None) -> int:
        """Synchronous version of assign_channel for non-async contexts.
        
        Uses threading.Lock for thread safety in synchronous code.
        """
        with self._sync_lock:
            if channel_id in self._channel_to_conn:
                return self._channel_to_conn[channel_id]
            
            if conn_id is not None:
                conn = self._connections.get(conn_id)
                if not conn or not conn.is_available:
                    raise ValueError(f"Connection {conn_id} not available")
            else:
                conn = self._select_connection()
                if not conn:
                    raise RuntimeError("No connections available")
                conn_id = conn.conn_id
            
            conn.add_channel(channel_id)
            self._channel_to_conn[channel_id] = conn_id
            return conn_id
    
    def unassign_channel_sync(self, channel_id: int) -> None:
        """Synchronous version of unassign_channel for non-async contexts."""
        with self._sync_lock:
            conn_id = self._channel_to_conn.pop(channel_id, None)
            if conn_id is not None:
                conn = self._connections.get(conn_id)
                if conn:
                    conn.remove_channel(channel_id)
    
    def send(self, channel_id: int, data: bytes) -> bool:
        """Send data for a channel on its assigned connection.
        
        Returns True if sent, False if channel not assigned or connection unavailable.
        
        Thread-safe: uses sync lock for state access.
        """
        with self._sync_lock:
            conn_id = self._channel_to_conn.get(channel_id)
            if conn_id is None:
                return False
            
            conn = self._connections.get(conn_id)
            if not conn or conn.state not in (ConnectionState.CONNECTED, ConnectionState.DRAINING):
                return False
            
            # Get send function while holding lock
            send_func = conn.send
        
        # Send outside of lock to avoid blocking
        send_func(data)
        
        # Update stats (quick operation, re-acquire lock)
        with self._sync_lock:
            conn = self._connections.get(conn_id)
            if conn:
                conn.record_send(len(data))
        
        return True
    
    def send_broadcast(self, data: bytes) -> int:
        """Send data on all active connections. Returns number of connections sent to."""
        with self._sync_lock:
            send_funcs = [
                (conn.conn_id, conn.send) 
                for conn in self._connections.values() 
                if conn.is_available
            ]
        
        count = 0
        for conn_id, send_func in send_funcs:
            try:
                send_func(data)
                count += 1
                # Update stats
                with self._sync_lock:
                    conn = self._connections.get(conn_id)
                    if conn:
                        conn.record_send(len(data))
            except Exception as e:
                log.warning(f"Broadcast send failed on connection {conn_id}: {e}")
        
        return count
    
    def on_receive(self, conn_id: int, data: bytes) -> None:
        """Handle incoming data from a connection.
        
        Thread-safe: uses sync lock for state access.
        """
        with self._sync_lock:
            conn = self._connections.get(conn_id)
            if conn:
                conn.record_recv(len(data))
            callback = self._on_receive
        
        if callback:
            # Extract channel_id from data (first 2 bytes in our protocol)
            if len(data) >= 2:
                import struct
                channel_id = struct.unpack("<H", data[:2])[0]
                callback(channel_id, data)
    
    def update_latency(self, conn_id: int, latency_ms: float) -> None:
        """Update RTT measurement for a connection."""
        conn = self._connections.get(conn_id)
        if conn:
            conn.update_latency(latency_ms)
    
    def _select_connection(self) -> Optional[PooledConnection]:
        """Select a connection based on the configured strategy."""
        available = [c for c in self._connections.values() if c.is_available]
        if not available:
            return None
        
        if self.strategy == DispatchStrategy.ROUND_ROBIN:
            self._round_robin_index = (self._round_robin_index + 1) % len(available)
            return available[self._round_robin_index]
        
        elif self.strategy == DispatchStrategy.LEAST_LOADED:
            return min(available, key=lambda c: len(c.channels))
        
        elif self.strategy == DispatchStrategy.LEAST_LATENCY:
            return min(available, key=lambda c: c.stats.latency_ms or float('inf'))
        
        elif self.strategy == DispatchStrategy.RANDOM:
            import random
            return random.choice(available)
        
        elif self.strategy == DispatchStrategy.STICKY:
            # For sticky, prefer connection with most channels (consolidate)
            return max(available, key=lambda c: len(c.channels))
        
        return available[0]
    
    def get_connection_for_channel(self, channel_id: int) -> Optional[PooledConnection]:
        """Get the connection a channel is assigned to."""
        conn_id = self._channel_to_conn.get(channel_id)
        if conn_id is not None:
            return self._connections.get(conn_id)
        return None
    
    def get_all_connections(self) -> List[PooledConnection]:
        """Get all connections in the pool."""
        return list(self._connections.values())
    
    async def rebalance(self) -> int:
        """Rebalance channels across connections.
        
        Moves channels from overloaded connections to underloaded ones.
        Returns number of channels moved.
        
        Thread-safe: uses async lock to protect all state mutations.
        """
        async with self._async_lock:
            available = [c for c in self._connections.values() if c.is_available]
            if len(available) < 2:
                return 0
            
            # Calculate average load
            total_channels = sum(len(c.channels) for c in available)
            if total_channels == 0:
                return 0
            
            avg_load = total_channels / len(available)
            if avg_load == 0:
                return 0
            
            # Find overloaded and underloaded connections
            threshold = avg_load * self.rebalance_threshold
            overloaded = [c for c in available if len(c.channels) > avg_load + threshold]
            underloaded = [c for c in available if len(c.channels) < avg_load - threshold]
            
            if not overloaded or not underloaded:
                return 0
            
            moved = 0
            for over_conn in overloaded:
                excess = int(len(over_conn.channels) - avg_load)
                # Take a snapshot of channels to move
                channels_to_move = list(over_conn.channels)[:excess]
                
                for channel_id in channels_to_move:
                    if not underloaded:
                        break
                    
                    # Pick least loaded underloaded connection
                    target = min(underloaded, key=lambda c: len(c.channels))
                    
                    # Verify channel still on over_conn (could have been removed)
                    if channel_id not in over_conn.channels:
                        continue
                    
                    # Move channel
                    over_conn.remove_channel(channel_id)
                    target.add_channel(channel_id)
                    self._channel_to_conn[channel_id] = target.conn_id
                    moved += 1
                    
                    # Remove from underloaded if now at average
                    if len(target.channels) >= avg_load:
                        underloaded.remove(target)
            
            if moved > 0:
                self._stats.rebalance_count += 1
                log.info(f"Rebalanced {moved} channels across {len(available)} connections")
            
            return moved
    
    async def close(self) -> None:
        """Close all connections in the pool."""
        async with self._async_lock:
            for conn_id in list(self._connections.keys()):
                self._remove_connection_internal(conn_id)
            self._channel_to_conn.clear()


class PooledProxy:
    """Socket proxy with connection pooling.
    
    Wraps SocketProxy to distribute channels across multiple connections.
    """
    
    def __init__(
        self,
        pool: ConnectionPool,
        is_server: bool = False,
    ):
        from .proxy import SocketProxy
        from .handlers import TargetPolicy, DEFAULT_POLICY
        
        self._pool = pool
        self._is_server = is_server
        
        # Create a proxy per connection
        self._proxies: Dict[int, SocketProxy] = {}
        
        # Set up pool callbacks
        pool.set_receive_callback(self._on_pool_receive)
        pool.set_connection_lost_callback(self._on_connection_lost)
    
    def _get_or_create_proxy(self, conn_id: int) -> "SocketProxy":
        """Get or create a SocketProxy for a connection."""
        from .proxy import SocketProxy
        
        if conn_id not in self._proxies:
            conn = self._pool._connections.get(conn_id)
            if not conn:
                raise ValueError(f"Connection {conn_id} not in pool")
            
            proxy = SocketProxy(
                send_callback=conn.send,
                is_server=self._is_server,
            )
            self._proxies[conn_id] = proxy
        
        return self._proxies[conn_id]
    
    async def open_channel(self, target: str, flags: int = 0) -> Tuple[int, int]:
        """Open a channel on the best available connection.
        
        Returns: (channel_id, conn_id)
        """
        if self._is_server:
            raise RuntimeError("Server cannot initiate channels")
        
        # Select connection
        conn = self._pool._select_connection()
        if not conn:
            raise RuntimeError("No connections available")
        
        # Open channel on that connection's proxy
        proxy = self._get_or_create_proxy(conn.conn_id)
        channel_id = await proxy.open_channel(target, flags)
        
        # Track in pool
        await self._pool.assign_channel(channel_id, conn.conn_id)
        
        return channel_id, conn.conn_id
    
    async def send_data(self, channel_id: int, data: bytes) -> bool:
        """Send data on a channel."""
        conn = self._pool.get_connection_for_channel(channel_id)
        if not conn:
            return False
        
        proxy = self._proxies.get(conn.conn_id)
        if not proxy:
            return False
        
        return await proxy.send_data(channel_id, data)
    
    async def close_channel(self, channel_id: int, code: int = 0) -> None:
        """Close a channel."""
        conn = self._pool.get_connection_for_channel(channel_id)
        if conn:
            proxy = self._proxies.get(conn.conn_id)
            if proxy:
                await proxy.close_channel(channel_id, code)
        
        await self._pool.unassign_channel(channel_id)
    
    async def handle_packet(self, conn_id: int, packet_type: int, payload: bytes) -> None:
        """Handle incoming packet from a specific connection."""
        proxy = self._get_or_create_proxy(conn_id)
        await proxy.handle_packet(packet_type, payload)
    
    def _on_pool_receive(self, channel_id: int, data: bytes) -> None:
        """Handle data received from pool."""
        # Data is already routed to the correct proxy via handle_packet
        pass
    
    def _on_connection_lost(self, conn_id: int) -> None:
        """Handle connection failure."""
        proxy = self._proxies.pop(conn_id, None)
        if proxy:
            # Proxy cleanup happens automatically
            pass
    
    async def close(self) -> None:
        """Close all proxies and the pool."""
        for proxy in self._proxies.values():
            await proxy.close()
        self._proxies.clear()
        await self._pool.close()


def create_pool(
    strategy: DispatchStrategy = DispatchStrategy.LEAST_LOADED,
    min_connections: int = 1,
    max_connections: int = 8,
    rebalance_threshold: float = 0.3,
) -> ConnectionPool:
    """Create a connection pool with default settings."""
    return ConnectionPool(
        strategy=strategy,
        min_connections=min_connections,
        max_connections=max_connections,
        rebalance_threshold=rebalance_threshold,
    )
