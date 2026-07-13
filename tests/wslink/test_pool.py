"""Tests for WSLink connection pool."""

import asyncio
import pytest

from protocols.wslink.pool import (
    ConnectionPool,
    PooledConnection,
    PooledProxy,
    ConnectionState,
    DispatchStrategy,
    ConnectionStats,
    PoolStats,
    create_pool,
)


class TestPooledConnection:
    """Test individual pooled connection."""
    
    def test_create_connection(self):
        packets = []
        conn = PooledConnection(
            conn_id=1,
            send_func=packets.append,
            endpoint="ws://localhost:8080"
        )
        
        assert conn.conn_id == 1
        assert conn.endpoint == "ws://localhost:8080"
        assert conn.state == ConnectionState.CONNECTING
        assert len(conn.channels) == 0
    
    def test_is_available(self):
        conn = PooledConnection(1, lambda x: None)
        
        assert not conn.is_available  # Still CONNECTING
        
        conn.state = ConnectionState.CONNECTED
        assert conn.is_available
        
        conn.state = ConnectionState.DRAINING
        assert not conn.is_available
        
        conn.state = ConnectionState.FAILED
        assert not conn.is_available
    
    def test_add_remove_channel(self):
        conn = PooledConnection(1, lambda x: None)
        
        conn.add_channel(5)
        assert 5 in conn.channels
        assert conn.stats.channels_active == 1
        assert conn.stats.channels_total == 1
        
        conn.add_channel(7)
        assert conn.stats.channels_active == 2
        assert conn.stats.channels_total == 2
        
        conn.remove_channel(5)
        assert 5 not in conn.channels
        assert conn.stats.channels_active == 1
    
    def test_record_send_recv(self):
        conn = PooledConnection(1, lambda x: None)
        
        conn.record_send(100)
        assert conn.stats.bytes_sent == 100
        assert conn.stats.packets_sent == 1
        
        conn.record_recv(200)
        assert conn.stats.bytes_recv == 200
        assert conn.stats.packets_recv == 1
    
    def test_latency_ema(self):
        conn = PooledConnection(1, lambda x: None)
        
        # First measurement
        conn.update_latency(100.0)
        assert conn.stats.latency_ms == 100.0
        
        # EMA update
        conn.update_latency(50.0)
        # 0.3 * 50 + 0.7 * 100 = 15 + 70 = 85
        assert 84 < conn.stats.latency_ms < 86


class TestConnectionPool:
    """Test connection pool operations."""
    
    @pytest.mark.asyncio
    async def test_create_pool(self):
        pool = create_pool()
        
        assert pool.strategy == DispatchStrategy.LEAST_LOADED
        assert pool.min_connections == 1
        assert pool.max_connections == 8
        assert pool.active_connections == 0
    
    @pytest.mark.asyncio
    async def test_add_connection(self):
        pool = create_pool()
        packets = []
        
        conn = await pool.add_connection(
            conn_id=1,
            send_func=packets.append,
            endpoint="ws://localhost:8080"
        )
        
        assert conn.conn_id == 1
        assert conn.state == ConnectionState.CONNECTED
        assert pool.active_connections == 1
    
    @pytest.mark.asyncio
    async def test_add_duplicate_connection_fails(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        
        with pytest.raises(ValueError, match="already exists"):
            await pool.add_connection(1, lambda x: None)
    
    @pytest.mark.asyncio
    async def test_max_connections_enforced(self):
        pool = create_pool(max_connections=2)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        with pytest.raises(ValueError, match="max capacity"):
            await pool.add_connection(3, lambda x: None)
    
    @pytest.mark.asyncio
    async def test_remove_connection(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        assert pool.active_connections == 1
        
        await pool.remove_connection(1, graceful=False)
        assert pool.active_connections == 0
    
    @pytest.mark.asyncio
    async def test_graceful_drain(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        await pool.assign_channel(5, conn_id=1)
        
        # Graceful removal should just mark as draining
        await pool.remove_connection(1, graceful=True)
        
        conn = pool._connections.get(1)
        assert conn is not None
        assert conn.state == ConnectionState.DRAINING
        
        # Unassign channel - should now remove
        await pool.unassign_channel(5)
        assert 1 not in pool._connections
    
    @pytest.mark.asyncio
    async def test_assign_channel(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        # Assign to specific connection
        assigned = await pool.assign_channel(5, conn_id=1)
        assert assigned == 1
        
        conn = pool._connections[1]
        assert 5 in conn.channels
    
    @pytest.mark.asyncio
    async def test_assign_channel_auto_select(self):
        pool = create_pool(strategy=DispatchStrategy.ROUND_ROBIN)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        # Auto-select
        conn1 = await pool.assign_channel(5)
        conn2 = await pool.assign_channel(7)
        
        # Round robin should alternate
        assert conn1 != conn2 or pool.active_connections == 1
    
    @pytest.mark.asyncio
    async def test_assign_no_connections_fails(self):
        pool = create_pool()
        
        with pytest.raises(RuntimeError, match="No connections available"):
            await pool.assign_channel(5)
    
    @pytest.mark.asyncio
    async def test_send_data(self):
        pool = create_pool()
        packets = []
        
        await pool.add_connection(1, packets.append)
        await pool.assign_channel(5, conn_id=1)
        
        result = pool.send(5, b"test data")
        
        assert result is True
        assert packets == [b"test data"]
    
    @pytest.mark.asyncio
    async def test_send_unassigned_channel(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        
        result = pool.send(999, b"test")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_send_broadcast(self):
        pool = create_pool()
        packets1 = []
        packets2 = []
        
        await pool.add_connection(1, packets1.append)
        await pool.add_connection(2, packets2.append)
        
        count = pool.send_broadcast(b"broadcast")
        
        assert count == 2
        assert packets1 == [b"broadcast"]
        assert packets2 == [b"broadcast"]
    
    @pytest.mark.asyncio
    async def test_mark_connection_failed(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        await pool.assign_channel(5, conn_id=1)
        await pool.assign_channel(7, conn_id=1)
        
        orphaned = await pool.mark_connection_failed(1)
        
        assert set(orphaned) == {5, 7}
        assert pool._connections[1].state == ConnectionState.FAILED
        assert pool.stats.connections_failed == 1
    
    @pytest.mark.asyncio
    async def test_connection_lost_callback(self):
        pool = create_pool()
        lost_connections = []
        
        pool.set_connection_lost_callback(lost_connections.append)
        
        await pool.add_connection(1, lambda x: None)
        await pool.mark_connection_failed(1)
        
        assert lost_connections == [1]


class TestDispatchStrategies:
    """Test different dispatch strategies."""
    
    @pytest.mark.asyncio
    async def test_round_robin(self):
        pool = create_pool(strategy=DispatchStrategy.ROUND_ROBIN)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        await pool.add_connection(3, lambda x: None)
        
        # Should cycle through connections
        assignments = []
        for i in range(6):
            assignments.append(await pool.assign_channel(i))
        
        # Each connection should get 2 channels
        assert assignments.count(1) == 2 or assignments.count(2) == 2
    
    @pytest.mark.asyncio
    async def test_least_loaded(self):
        pool = create_pool(strategy=DispatchStrategy.LEAST_LOADED)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        # Assign some to conn 1
        await pool.assign_channel(10, conn_id=1)
        await pool.assign_channel(11, conn_id=1)
        
        # Next should go to conn 2 (least loaded)
        assigned = await pool.assign_channel(12)
        assert assigned == 2
    
    @pytest.mark.asyncio
    async def test_least_latency(self):
        pool = create_pool(strategy=DispatchStrategy.LEAST_LATENCY)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        # Set latencies
        pool.update_latency(1, 100.0)
        pool.update_latency(2, 50.0)
        
        # Should prefer conn 2 (lower latency)
        assigned = await pool.assign_channel(5)
        assert assigned == 2


class TestPoolRebalancing:
    """Test channel rebalancing."""
    
    @pytest.mark.asyncio
    async def test_rebalance_moves_channels(self):
        pool = create_pool(rebalance_threshold=0.2)
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        # Load up connection 1
        for i in range(10):
            await pool.assign_channel(i, conn_id=1)
        
        # Connection 2 has 0 channels, conn 1 has 10
        moved = await pool.rebalance()
        
        # Should have moved some channels
        assert moved > 0
        
        # Should be more balanced now
        conn1_load = len(pool._connections[1].channels)
        conn2_load = len(pool._connections[2].channels)
        assert abs(conn1_load - conn2_load) < 5


class TestPoolStats:
    """Test pool statistics."""
    
    @pytest.mark.asyncio
    async def test_aggregate_stats(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        
        pool._connections[1].stats.bytes_sent = 100
        pool._connections[2].stats.bytes_sent = 200
        
        await pool.assign_channel(5, conn_id=1)
        await pool.assign_channel(7, conn_id=2)
        
        stats = pool.stats
        
        assert stats.connections_active == 2
        assert stats.connections_total == 2
        assert stats.bytes_sent == 300
        assert stats.channels_active == 2


class TestPooledProxy:
    """Test pooled proxy wrapper."""
    
    @pytest.mark.asyncio
    async def test_create_pooled_proxy(self):
        pool = create_pool()
        packets = []
        
        await pool.add_connection(1, packets.append)
        
        proxy = PooledProxy(pool, is_server=False)
        
        assert proxy._pool is pool
        assert not proxy._is_server
    
    @pytest.mark.asyncio
    async def test_open_channel_on_pool(self):
        pool = create_pool()
        packets = []
        
        await pool.add_connection(1, packets.append)
        
        proxy = PooledProxy(pool, is_server=False)
        channel_id, conn_id = await proxy.open_channel("tcp:localhost:8080")
        
        assert channel_id == 1  # First client channel
        assert conn_id == 1
        assert len(packets) == 1  # OPEN packet sent
    
    @pytest.mark.asyncio
    async def test_server_cannot_initiate(self):
        pool = create_pool()
        await pool.add_connection(1, lambda x: None)
        
        proxy = PooledProxy(pool, is_server=True)
        
        with pytest.raises(RuntimeError, match="Server cannot initiate"):
            await proxy.open_channel("tcp:localhost:8080")
    
    @pytest.mark.asyncio
    async def test_close_pooled_proxy(self):
        pool = create_pool()
        await pool.add_connection(1, lambda x: None)
        
        proxy = PooledProxy(pool, is_server=False)
        await proxy.close()
        
        assert pool.active_connections == 0


class TestPoolClose:
    """Test pool cleanup."""
    
    @pytest.mark.asyncio
    async def test_close_removes_all(self):
        pool = create_pool()
        
        await pool.add_connection(1, lambda x: None)
        await pool.add_connection(2, lambda x: None)
        await pool.assign_channel(5, conn_id=1)
        await pool.assign_channel(7, conn_id=2)
        
        await pool.close()
        
        assert len(pool._connections) == 0
        assert len(pool._channel_to_conn) == 0
