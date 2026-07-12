"""Tests for WSLink target handlers."""

import asyncio
import os
import socket
import struct
import tempfile
import pytest

from protocols.wslink.handlers import (
    TargetHandler,
    TargetType,
    TargetPolicy,
    TargetConfig,
    HandlerStats,
    UnixSocketHandler,
    TCPHandler,
    SSHAgentHandler,
    get_handler,
    register_handler,
    DEFAULT_POLICY,
)


class TestTargetParsing:
    """Test target string parsing."""
    
    def test_parse_tcp_with_prefix(self):
        target_type, address = TargetHandler.parse_target("tcp:localhost:22")
        assert target_type == TargetType.TCP
        assert address == "localhost:22"
    
    def test_parse_tcp_without_prefix(self):
        target_type, address = TargetHandler.parse_target("localhost:8080")
        assert target_type == TargetType.TCP
        assert address == "localhost:8080"
    
    def test_parse_unix(self):
        target_type, address = TargetHandler.parse_target("unix:/tmp/test.sock")
        assert target_type == TargetType.UNIX
        assert address == "/tmp/test.sock"
    
    def test_parse_ssh_agent(self):
        # Set SSH_AUTH_SOCK for test
        old_val = os.environ.get("SSH_AUTH_SOCK")
        os.environ["SSH_AUTH_SOCK"] = "/tmp/ssh-test/agent.123"
        try:
            target_type, address = TargetHandler.parse_target("ssh-agent")
            assert target_type == TargetType.SSH_AGENT
            assert address == "/tmp/ssh-test/agent.123"
        finally:
            if old_val:
                os.environ["SSH_AUTH_SOCK"] = old_val
            else:
                os.environ.pop("SSH_AUTH_SOCK", None)
    
    def test_parse_ipv6_tcp(self):
        target_type, address = TargetHandler.parse_target("tcp:[::1]:8080")
        assert target_type == TargetType.TCP
        assert address == "[::1]:8080"
    
    def test_parse_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown target format"):
            TargetHandler.parse_target("invalid-target-no-port")


class TestTargetPolicy:
    """Test target policy enforcement."""
    
    def test_default_policy_allows_localhost(self):
        assert DEFAULT_POLICY.is_allowed("tcp:localhost:22")
        assert DEFAULT_POLICY.is_allowed("tcp:127.0.0.1:8080")
        assert DEFAULT_POLICY.is_allowed("tcp:::1:443")
    
    def test_default_policy_allows_ssh_agent(self):
        assert DEFAULT_POLICY.is_allowed("ssh-agent")
    
    def test_default_policy_allows_tmp_unix(self):
        assert DEFAULT_POLICY.is_allowed("unix:/tmp/test.sock")
        assert DEFAULT_POLICY.is_allowed("unix:/var/run/docker.sock")
    
    def test_default_policy_denies_remote(self):
        assert not DEFAULT_POLICY.is_allowed("tcp:evil.com:22")
        assert not DEFAULT_POLICY.is_allowed("tcp:192.168.1.1:80")
    
    def test_custom_policy_deny_takes_precedence(self):
        policy = TargetPolicy(
            allowed_targets=["tcp:localhost:"],
            denied_targets=["tcp:localhost:22"]
        )
        assert policy.is_allowed("tcp:localhost:80")
        assert not policy.is_allowed("tcp:localhost:22")
    
    def test_empty_policy_denies_all(self):
        policy = TargetPolicy(allowed_targets=[], denied_targets=[])
        assert not policy.is_allowed("tcp:localhost:22")
        assert not policy.is_allowed("ssh-agent")


class TestHandlerFactory:
    """Test get_handler factory function."""
    
    def test_get_tcp_handler(self):
        handler = get_handler(1, "tcp:localhost:22", 0)
        assert isinstance(handler, TCPHandler)
        assert handler.channel_id == 1
        assert handler.target == "localhost:22"
    
    def test_get_unix_handler(self):
        handler = get_handler(2, "unix:/tmp/test.sock", 0)
        assert isinstance(handler, UnixSocketHandler)
        assert handler.channel_id == 2
        assert handler.target == "/tmp/test.sock"
    
    def test_get_ssh_agent_handler(self):
        old_val = os.environ.get("SSH_AUTH_SOCK")
        os.environ["SSH_AUTH_SOCK"] = "/tmp/ssh-agent.sock"
        try:
            handler = get_handler(3, "ssh-agent", 0)
            assert isinstance(handler, SSHAgentHandler)
        finally:
            if old_val:
                os.environ["SSH_AUTH_SOCK"] = old_val
            else:
                os.environ.pop("SSH_AUTH_SOCK", None)


class TestHandlerStats:
    """Test handler statistics tracking."""
    
    def test_stats_initial_values(self):
        stats = HandlerStats()
        assert stats.bytes_to_target == 0
        assert stats.bytes_from_target == 0
        assert stats.connect_time_ms == 0
        assert stats.errors == 0
        assert stats.last_error == ""
    
    def test_stats_can_be_updated(self):
        stats = HandlerStats()
        stats.bytes_to_target += 100
        stats.bytes_from_target += 200
        stats.errors += 1
        stats.last_error = "Connection reset"
        
        assert stats.bytes_to_target == 100
        assert stats.bytes_from_target == 200
        assert stats.errors == 1
        assert stats.last_error == "Connection reset"


class TestUnixSocketHandler:
    """Test Unix socket handler with real sockets."""
    
    @pytest.fixture
    def unix_server(self):
        """Create a temporary Unix socket server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = os.path.join(tmpdir, "test.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(sock_path)
            server.listen(1)
            server.setblocking(False)
            yield sock_path, server
            server.close()
    
    @pytest.mark.asyncio
    async def test_connect_success(self, unix_server):
        sock_path, server = unix_server
        
        handler = UnixSocketHandler(1, sock_path, 0)
        
        # Connect in background
        connect_task = asyncio.create_task(handler.connect())
        
        # Accept the connection
        await asyncio.sleep(0.01)
        loop = asyncio.get_event_loop()
        client, _ = await loop.sock_accept(server)
        client.close()
        
        await connect_task
        assert handler.connected
        assert handler.stats.connect_time_ms > 0
        
        await handler.close()
    
    @pytest.mark.asyncio
    async def test_connect_not_found(self):
        handler = UnixSocketHandler(1, "/nonexistent/path.sock", 0)
        
        with pytest.raises(ConnectionError, match="not found"):
            await handler.connect()
    
    @pytest.mark.asyncio
    async def test_read_write(self, unix_server):
        sock_path, server = unix_server
        
        handler = UnixSocketHandler(1, sock_path, 0)
        
        # Connect
        connect_task = asyncio.create_task(handler.connect())
        await asyncio.sleep(0.01)
        loop = asyncio.get_event_loop()
        client, _ = await loop.sock_accept(server)
        await connect_task
        
        # Write from handler, read from server
        await handler.write(b"hello")
        await asyncio.sleep(0.01)
        data = client.recv(1024)
        assert data == b"hello"
        assert handler.stats.bytes_to_target == 5
        
        # Write from server, read from handler
        client.send(b"world")
        await asyncio.sleep(0.01)
        data = await handler.read(1024)
        assert data == b"world"
        assert handler.stats.bytes_from_target == 5
        
        client.close()
        await handler.close()


class TestTCPHandler:
    """Test TCP handler with real sockets."""
    
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
    async def test_connect_success(self, tcp_server):
        port, server = tcp_server
        
        handler = TCPHandler(1, f"127.0.0.1:{port}", 0)
        
        # Connect in background
        connect_task = asyncio.create_task(handler.connect())
        
        # Accept the connection
        await asyncio.sleep(0.01)
        loop = asyncio.get_event_loop()
        client, _ = await loop.sock_accept(server)
        client.close()
        
        await connect_task
        assert handler.connected
        assert handler.stats.connect_time_ms > 0
        
        await handler.close()
    
    @pytest.mark.asyncio
    async def test_connect_refused(self):
        # Use a port that's definitely not listening
        handler = TCPHandler(1, "127.0.0.1:1", 0)
        
        with pytest.raises(ConnectionError):
            await handler.connect()
    
    def test_parse_ipv6_address(self):
        handler = TCPHandler(1, "[::1]:8080", 0)
        # Just verify it parses without error
        assert handler.target == "[::1]:8080"
    
    @pytest.mark.asyncio
    async def test_read_write(self, tcp_server):
        port, server = tcp_server
        
        handler = TCPHandler(1, f"127.0.0.1:{port}", 0)
        
        # Connect
        connect_task = asyncio.create_task(handler.connect())
        await asyncio.sleep(0.01)
        loop = asyncio.get_event_loop()
        client, _ = await loop.sock_accept(server)
        await connect_task
        
        # Write from handler
        await handler.write(b"request")
        await asyncio.sleep(0.01)
        data = client.recv(1024)
        assert data == b"request"
        
        # Write from server
        client.send(b"response")
        await asyncio.sleep(0.01)
        data = await handler.read(1024)
        assert data == b"response"
        
        client.close()
        await handler.close()


class TestCustomHandlerRegistration:
    """Test registering custom handlers."""
    
    def test_register_custom_handler(self):
        class CustomHandler(TargetHandler):
            async def connect(self):
                self._connected = True
            
            async def read(self, max_bytes=65536):
                return b""
            
            async def write(self, data):
                return len(data)
        
        # Register for a new type
        register_handler(TargetType.SERIAL, CustomHandler)
        
        handler = get_handler(1, "serial:/dev/ttyUSB0", 0)
        assert isinstance(handler, CustomHandler)


class TestTargetConfig:
    """Test target configuration dataclass."""
    
    def test_default_values(self):
        config = TargetConfig(
            target_type=TargetType.TCP,
            address="localhost:22"
        )
        assert config.allowed is True
        assert config.max_bandwidth_bps == 0
        assert config.audit is False
    
    def test_custom_values(self):
        config = TargetConfig(
            target_type=TargetType.SSH_AGENT,
            address="/tmp/agent.sock",
            allowed=True,
            max_bandwidth_bps=1_000_000,
            audit=True
        )
        assert config.max_bandwidth_bps == 1_000_000
        assert config.audit is True
