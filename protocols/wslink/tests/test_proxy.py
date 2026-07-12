"""Tests for WSLink socket proxy."""

import asyncio
import os
import socket
import struct
import tempfile
import zlib
import pytest

from protocols.wslink.proxy import SocketProxy, ProxyStats, create_proxy
from protocols.wslink.handlers import TargetPolicy
from protocols.wslink.const import (
    PACK_SOCKET_OPEN,
    PACK_SOCKET_DATA,
    PACK_SOCKET_CLOSE,
    PACK_SOCKET_ERROR,
    PACK_SOCKET_WINDOW,
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_CLOSE_TARGET_REFUSED,
    CHANNEL_INITIAL_CREDIT,
)


class TestProxyCreation:
    """Test proxy creation and configuration."""
    
    def test_create_client_proxy(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        assert not proxy._is_server
        assert proxy.active_channels == 0
        assert isinstance(proxy.stats, ProxyStats)
    
    def test_create_server_proxy(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=True)
        
        assert proxy._is_server
        assert proxy.active_channels == 0
    
    def test_create_with_custom_policy(self):
        packets = []
        policy = TargetPolicy(
            allowed_targets=["tcp:localhost:80"],
            denied_targets=[]
        )
        proxy = create_proxy(packets.append, is_server=True, policy=policy)
        
        assert proxy._policy is policy


class TestProxyStats:
    """Test proxy statistics."""
    
    def test_initial_stats(self):
        stats = ProxyStats()
        
        assert stats.channels_opened == 0
        assert stats.channels_closed == 0
        assert stats.channels_errored == 0
        assert stats.bytes_to_targets == 0
        assert stats.bytes_from_targets == 0
        assert stats.policy_denials == 0
        assert stats.connect_failures == 0


class TestClientProxy:
    """Test client-side proxy operations."""
    
    @pytest.mark.asyncio
    async def test_open_channel(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        
        assert channel_id == 1  # Client starts at 1 (odd)
        assert len(packets) == 1
        assert proxy.stats.channels_opened == 1
        
        # Verify packet structure
        packet = packets[0]
        length = struct.unpack("<I", packet[:4])[0]
        ptype = packet[4:5]  # Single byte as bytes
        assert ptype == PACK_SOCKET_OPEN
    
    @pytest.mark.asyncio
    async def test_open_channel_denied_by_policy(self):
        packets = []
        policy = TargetPolicy(allowed_targets=["tcp:localhost:"], denied_targets=[])
        proxy = create_proxy(packets.append, is_server=False, policy=policy)
        
        with pytest.raises(PermissionError, match="denied by policy"):
            await proxy.open_channel("tcp:evil.com:22")
        
        assert proxy.stats.policy_denials == 1
        assert len(packets) == 0
    
    @pytest.mark.asyncio
    async def test_server_cannot_initiate(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=True)
        
        with pytest.raises(RuntimeError, match="Server cannot initiate"):
            await proxy.open_channel("tcp:localhost:22")
    
    @pytest.mark.asyncio
    async def test_send_data(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        packets.clear()
        
        # Simulate window update to allow sending
        window_payload = struct.pack("<HI", channel_id, 65536)
        await proxy.handle_packet(PACK_SOCKET_WINDOW, window_payload)
        
        result = await proxy.send_data(channel_id, b"hello")
        
        assert result is True
        assert len(packets) == 1
        
        # Verify data packet
        packet = packets[0]
        ptype = packet[4:5]  # Single byte as bytes
        assert ptype == PACK_SOCKET_DATA
    
    @pytest.mark.asyncio
    async def test_close_channel(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        packets.clear()
        
        await proxy.close_channel(channel_id)
        
        assert len(packets) == 1
        assert proxy.stats.channels_closed == 1
        
        # Verify close packet
        packet = packets[0]
        ptype = packet[4:5]  # Single byte as bytes
        assert ptype == PACK_SOCKET_CLOSE


class TestServerProxy:
    """Test server-side proxy operations."""
    
    @pytest.fixture
    def tcp_server(self):
        """Create a temporary TCP server."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.setblocking(False)
        port = server.getsockname()[1]
        yield port, server
        server.close()
    
    @pytest.mark.asyncio
    async def test_handle_open_success(self, tcp_server):
        port, server = tcp_server
        
        packets = []
        proxy = create_proxy(packets.append, is_server=True)
        
        # Simulate OPEN from client
        target = f"tcp:localhost:{port}"
        open_payload = struct.pack("<HH", 1, 0) + target.encode() + b"\x00"
        
        # Handle open in background (it will connect)
        handle_task = asyncio.create_task(
            proxy.handle_packet(PACK_SOCKET_OPEN, open_payload)
        )
        
        # Accept the connection
        await asyncio.sleep(0.05)
        loop = asyncio.get_event_loop()
        try:
            client, _ = await asyncio.wait_for(
                loop.sock_accept(server), timeout=1.0
            )
            client.close()
        except asyncio.TimeoutError:
            pass
        
        await handle_task
        
        # Should have sent WINDOW confirmation
        assert len(packets) >= 1
        window_packet = packets[0]
        ptype = window_packet[4:5]  # Single byte as bytes
        assert ptype == PACK_SOCKET_WINDOW
        
        assert proxy.stats.channels_opened == 1
        assert proxy.active_channels == 1
        
        await proxy.close()
    
    @pytest.mark.asyncio
    async def test_handle_open_policy_denied(self):
        packets = []
        policy = TargetPolicy(allowed_targets=[], denied_targets=[])
        proxy = create_proxy(packets.append, is_server=True, policy=policy)
        
        # Simulate OPEN for denied target
        open_payload = struct.pack("<HH", 1, 0) + b"tcp:evil.com:22\x00"
        await proxy.handle_packet(PACK_SOCKET_OPEN, open_payload)
        
        assert proxy.stats.policy_denials == 1
        
        # Should have sent ERROR
        assert len(packets) == 1
        error_packet = packets[0]
        ptype = error_packet[4:5]  # Single byte as bytes
        assert ptype == PACK_SOCKET_ERROR
    
    @pytest.mark.asyncio
    async def test_handle_open_connect_failed(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=True)
        
        # Simulate OPEN to port that's not listening
        open_payload = struct.pack("<HH", 1, 0) + b"tcp:127.0.0.1:1\x00"
        await proxy.handle_packet(PACK_SOCKET_OPEN, open_payload)
        
        assert proxy.stats.connect_failures == 1
        
        # Should have sent ERROR
        assert len(packets) >= 1
        # Find error packet
        error_found = False
        for packet in packets:
            if packet[4:5] == PACK_SOCKET_ERROR:
                error_found = True
                break
        assert error_found


class TestDataPump:
    """Test bidirectional data pumping."""
    
    @pytest.fixture
    def tcp_echo_server(self):
        """Create a TCP echo server."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        
        async def echo_handler():
            loop = asyncio.get_event_loop()
            server.setblocking(False)
            try:
                client, _ = await asyncio.wait_for(
                    loop.sock_accept(server), timeout=2.0
                )
                client.setblocking(False)
                while True:
                    try:
                        data = await asyncio.wait_for(
                            loop.sock_recv(client, 4096), timeout=1.0
                        )
                        if not data:
                            break
                        await loop.sock_sendall(client, data)
                    except asyncio.TimeoutError:
                        break
                client.close()
            except asyncio.TimeoutError:
                pass
        
        yield port, server, echo_handler
        server.close()
    
    @pytest.mark.asyncio
    async def test_data_round_trip(self, tcp_echo_server):
        port, server, echo_handler = tcp_echo_server
        
        packets = []
        proxy = create_proxy(packets.append, is_server=True)
        
        # Start echo server
        echo_task = asyncio.create_task(echo_handler())
        
        # Simulate OPEN from client
        target = f"tcp:localhost:{port}"
        open_payload = struct.pack("<HH", 1, 0) + target.encode() + b"\x00"
        await proxy.handle_packet(PACK_SOCKET_OPEN, open_payload)
        
        await asyncio.sleep(0.1)
        
        # Simulate DATA from client
        test_data = b"echo test"
        data_payload = struct.pack("<H", 1) + test_data
        await proxy.handle_packet(PACK_SOCKET_DATA, data_payload)
        
        # Wait for echo response
        await asyncio.sleep(0.2)
        
        # Check that data was sent to target
        assert proxy.stats.bytes_to_targets >= len(test_data)
        
        # Clean up
        await proxy.close()
        echo_task.cancel()
        try:
            await echo_task
        except asyncio.CancelledError:
            pass


class TestPacketHandling:
    """Test packet parsing and handling."""
    
    @pytest.mark.asyncio
    async def test_handle_close_packet(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        # Open a channel first
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        packets.clear()
        
        # Simulate CLOSE from server
        close_payload = struct.pack("<HH", channel_id, CHANNEL_CLOSE_NORMAL)
        await proxy.handle_packet(PACK_SOCKET_CLOSE, close_payload)
        
        assert proxy.stats.channels_closed == 1
    
    @pytest.mark.asyncio
    async def test_handle_error_packet(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        # Open a channel first
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        packets.clear()
        
        # Simulate ERROR from server
        error_msg = "Connection refused"
        error_payload = struct.pack("<HH", channel_id, CHANNEL_CLOSE_TARGET_REFUSED)
        error_payload += error_msg.encode()
        await proxy.handle_packet(PACK_SOCKET_ERROR, error_payload)
        
        assert proxy.stats.channels_errored == 1
    
    @pytest.mark.asyncio
    async def test_handle_window_packet(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        # Open a channel first - starts in OPENING state
        channel_id = await proxy.open_channel("tcp:localhost:8080")
        
        # Simulate WINDOW grant - this transitions to OPEN state
        window_payload = struct.pack("<HI", channel_id, 65536)
        await proxy.handle_packet(PACK_SOCKET_WINDOW, window_payload)
        
        # Now channel is OPEN with credit - should be able to send
        packets.clear()
        result = await proxy.send_data(channel_id, b"test")
        assert result is True


class TestProxyLifecycle:
    """Test proxy lifecycle management."""
    
    @pytest.mark.asyncio
    async def test_close_proxy(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        # Open some channels
        await proxy.open_channel("tcp:localhost:8080")
        await proxy.open_channel("tcp:localhost:8081")
        
        await proxy.close()
        
        assert proxy._closed is True
    
    @pytest.mark.asyncio
    async def test_ignore_packets_after_close(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        await proxy.close()
        
        # These should be silently ignored
        await proxy.handle_packet(PACK_SOCKET_DATA, b"\x01\x00test")
        await proxy.handle_packet(PACK_SOCKET_WINDOW, b"\x01\x00\x00\x01\x00\x00")


class TestPacketFraming:
    """Test packet framing format."""
    
    def test_frame_format(self):
        packets = []
        proxy = create_proxy(packets.append, is_server=False)
        
        # Use internal framing method
        payload = b"test payload"
        framed = proxy._frame_packet(PACK_SOCKET_DATA, payload)
        
        # Parse frame
        length = struct.unpack("<I", framed[:4])[0]
        ptype = framed[4:5]  # Single byte as bytes
        data = framed[5:-4]
        crc = struct.unpack("<I", framed[-4:])[0]
        
        assert length == 1 + len(payload)  # type + payload
        assert ptype == PACK_SOCKET_DATA
        assert data == payload
        
        # Verify CRC
        expected_crc = zlib.crc32(framed[:-4]) & 0xFFFFFFFF
        assert crc == expected_crc
