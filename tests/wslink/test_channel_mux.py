"""Integration tests for ChannelMux — both Python and Rust implementations."""

import pytest
import struct
from typing import Tuple

# Test both implementations
from protocols.wslink.channel import ChannelMux as PyChannelMux, ChannelState
from protocols.wslink.const import (
    CHANNEL_FLAG_BIDIR,
    CHANNEL_FLAG_COMPRESSED,
    CHANNEL_FLAG_ENCRYPTED,
    CHANNEL_FLAG_READ_ONLY,
    CHANNEL_INITIAL_CREDIT,
    CHANNEL_CLOSE_NORMAL,
    CHANNEL_CLOSE_TIMEOUT,
)

# Try to import Rust implementation
try:
    from pyprotocols_core import ChannelMux as RustChannelMux
    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    RustChannelMux = None


def get_implementations():
    """Return list of (name, class) for parametrized tests."""
    impls = [("python", PyChannelMux)]
    if HAS_RUST:
        impls.append(("rust", RustChannelMux))
    return impls


@pytest.fixture(params=get_implementations(), ids=lambda x: x[0])
def mux_class(request):
    """Fixture providing both Python and Rust ChannelMux classes."""
    return request.param[1]


class TestChannelMuxBasic:
    """Basic ChannelMux operations."""

    def test_create_client_mux(self, mux_class):
        mux = mux_class(is_client=True)
        stats = mux.stats()
        assert stats["is_client"] is True
        assert stats["active_channels"] == 0
        assert stats["next_client_id"] == 1
        assert stats["next_server_id"] == 2

    def test_create_server_mux(self, mux_class):
        mux = mux_class(is_client=False)
        stats = mux.stats()
        assert stats["is_client"] is False

    def test_open_channel_client(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, packet = mux.open_channel("ssh-agent", CHANNEL_FLAG_BIDIR)
        
        assert channel_id == 1  # First odd ID
        assert len(packet) > 0
        assert mux.stats()["active_channels"] == 1

    def test_open_channel_server(self, mux_class):
        mux = mux_class(is_client=False)
        channel_id, packet = mux.open_channel("tcp:localhost:22", 0)
        
        assert channel_id == 2  # First even ID

    def test_open_multiple_channels(self, mux_class):
        mux = mux_class(is_client=True)
        
        id1, _ = mux.open_channel("a", 0)
        id2, _ = mux.open_channel("b", 0)
        id3, _ = mux.open_channel("c", 0)
        
        assert id1 == 1
        assert id2 == 3
        assert id3 == 5
        assert mux.stats()["active_channels"] == 3

    def test_max_channels_enforced(self, mux_class):
        mux = mux_class(is_client=True, max_channels=3)
        
        mux.open_channel("a", 0)
        mux.open_channel("b", 0)
        mux.open_channel("c", 0)
        
        with pytest.raises(ValueError, match="max channels"):
            mux.open_channel("d", 0)


class TestChannelMuxFlowControl:
    """Flow control (credit-based) tests."""

    def test_channel_starts_in_opening_state(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        
        info = mux.get_channel(channel_id)
        assert info["state"] == "opening"

    def test_window_transitions_to_open(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        
        mux.handle_window(channel_id, CHANNEL_INITIAL_CREDIT)
        
        info = mux.get_channel(channel_id)
        assert info["state"] == "open"
        assert info["send_credit"] == CHANNEL_INITIAL_CREDIT

    def test_send_blocked_without_credit(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 100)  # Small credit
        
        # Consume all credit
        result = mux.send_data(channel_id, b"x" * 100)
        assert result is not None
        
        # Now send should return None (no credit)
        result = mux.send_data(channel_id, b"more")
        assert result is None

    def test_send_consumes_credit(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        mux.send_data(channel_id, b"hello")  # 5 bytes
        
        info = mux.get_channel(channel_id)
        assert info["send_credit"] == 995
        assert info["bytes_sent"] == 5

    def test_window_adds_credit(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 100)
        mux.handle_window(channel_id, 200)
        
        assert mux.get_send_credit(channel_id) == 300

    def test_grant_window(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)  # Open the channel
        
        packet = mux.grant_window(channel_id, 5000)
        
        assert len(packet) == 6  # 2 bytes channel + 4 bytes credit
        info = mux.get_channel(channel_id)
        assert info["recv_credit"] == CHANNEL_INITIAL_CREDIT + 5000


class TestChannelMuxLifecycle:
    """Channel lifecycle tests."""

    def test_close_channel(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        close_packet = mux.close_channel(channel_id, CHANNEL_CLOSE_NORMAL)
        
        assert len(close_packet) == 4  # 2 bytes channel + 2 bytes code
        info = mux.get_channel(channel_id)
        assert info["state"] == "closing"

    def test_handle_peer_close(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        # Peer sends close
        send_back, packet = mux.handle_close(channel_id, CHANNEL_CLOSE_NORMAL)
        
        assert send_back is True  # We need to send close back
        assert packet is not None
        info = mux.get_channel(channel_id)
        assert info["state"] == "closed"

    def test_handle_close_when_already_closing(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        # We initiate close
        mux.close_channel(channel_id, CHANNEL_CLOSE_NORMAL)
        
        # Peer responds with close
        send_back, packet = mux.handle_close(channel_id, CHANNEL_CLOSE_NORMAL)
        
        assert send_back is False  # Don't send back, we already sent
        assert packet is None

    def test_remove_channel(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        
        assert mux.stats()["active_channels"] == 1
        
        removed = mux.remove_channel(channel_id)
        
        assert removed is True
        assert mux.stats()["active_channels"] == 0

    def test_remove_nonexistent_channel(self, mux_class):
        mux = mux_class(is_client=True)
        removed = mux.remove_channel(999)
        assert removed is False


class TestChannelMuxPeerHandling:
    """Handling peer-initiated channels."""

    def test_handle_peer_open_client_side(self, mux_class):
        mux = mux_class(is_client=True)
        
        # Server opens channel 2 (even)
        channel_id, window_packet = mux.handle_open(2, CHANNEL_FLAG_BIDIR, "reverse-shell")
        
        assert channel_id == 2
        assert len(window_packet) == 6  # Initial window grant
        assert mux.is_channel_open(2)

    def test_handle_peer_open_server_side(self, mux_class):
        mux = mux_class(is_client=False)
        
        # Client opens channel 1 (odd)
        channel_id, window_packet = mux.handle_open(1, 0, "ssh-agent")
        
        assert channel_id == 1
        assert mux.is_channel_open(1)

    def test_reject_wrong_parity_client(self, mux_class):
        mux = mux_class(is_client=True)
        
        # Server should not open odd channels
        with pytest.raises(ValueError, match="wrong parity"):
            mux.handle_open(1, 0, "bad")

    def test_reject_wrong_parity_server(self, mux_class):
        mux = mux_class(is_client=False)
        
        # Client should not open even channels
        with pytest.raises(ValueError, match="wrong parity"):
            mux.handle_open(2, 0, "bad")

    def test_reject_duplicate_channel(self, mux_class):
        mux = mux_class(is_client=True)
        
        mux.handle_open(2, 0, "first")
        
        with pytest.raises(ValueError, match="already exists"):
            mux.handle_open(2, 0, "second")


class TestChannelMuxErrors:
    """Error handling tests."""

    def test_handle_error(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        mux.handle_error(channel_id, CHANNEL_CLOSE_TIMEOUT, "connection timed out")
        
        info = mux.get_channel(channel_id)
        # Error is informational, doesn't close channel
        assert info["state"] == "open"

    def test_send_error(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        
        error_packet = mux.send_error(channel_id, CHANNEL_CLOSE_TIMEOUT, "timeout")
        
        assert len(error_packet) > 6  # channel + code + len + message

    def test_operations_on_unknown_channel(self, mux_class):
        mux = mux_class(is_client=True)
        
        with pytest.raises(ValueError, match="unknown channel"):
            mux.send_data(999, b"data")
        
        with pytest.raises(ValueError, match="unknown channel"):
            mux.handle_window(999, 1000)
        
        with pytest.raises(ValueError, match="unknown channel"):
            mux.close_channel(999, 0)


class TestChannelMuxFlags:
    """Channel flag handling."""

    def test_bidirectional_flag(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", CHANNEL_FLAG_BIDIR)
        
        info = mux.get_channel(channel_id)
        assert info["is_bidirectional"] is True

    def test_compressed_flag(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", CHANNEL_FLAG_COMPRESSED)
        
        info = mux.get_channel(channel_id)
        assert info["is_compressed"] is True

    def test_encrypted_flag(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", CHANNEL_FLAG_ENCRYPTED)
        
        info = mux.get_channel(channel_id)
        assert info["is_encrypted"] is True

    def test_combined_flags(self, mux_class):
        mux = mux_class(is_client=True)
        flags = CHANNEL_FLAG_BIDIR | CHANNEL_FLAG_COMPRESSED | CHANNEL_FLAG_ENCRYPTED
        channel_id, _ = mux.open_channel("test", flags)
        
        info = mux.get_channel(channel_id)
        assert info["is_bidirectional"] is True
        assert info["is_compressed"] is True
        assert info["is_encrypted"] is True


class TestChannelMuxStats:
    """Statistics tracking."""

    def test_stats_aggregate_bytes(self, mux_class):
        mux = mux_class(is_client=True)
        
        ch1, _ = mux.open_channel("a", 0)
        ch2, _ = mux.open_channel("b", 0)
        
        mux.handle_window(ch1, 10000)
        mux.handle_window(ch2, 10000)
        
        mux.send_data(ch1, b"hello")  # 5 bytes
        mux.send_data(ch2, b"world!")  # 6 bytes
        
        stats = mux.stats()
        assert stats["total_bytes_sent"] == 11

    def test_stats_channels_by_state(self, mux_class):
        mux = mux_class(is_client=True)
        
        ch1, _ = mux.open_channel("a", 0)  # opening
        ch2, _ = mux.open_channel("b", 0)
        mux.handle_window(ch2, 1000)  # open
        ch3, _ = mux.open_channel("c", 0)
        mux.handle_window(ch3, 1000)
        mux.close_channel(ch3, 0)  # closing
        
        stats = mux.stats()
        by_state = stats["channels_by_state"]
        
        assert by_state.get("opening", 0) == 1
        assert by_state.get("open", 0) == 1
        assert by_state.get("closing", 0) == 1

    def test_reset(self, mux_class):
        mux = mux_class(is_client=True)
        mux.open_channel("a", 0)
        mux.open_channel("b", 0)
        
        mux.reset()
        
        stats = mux.stats()
        assert stats["active_channels"] == 0
        assert stats["total_opened"] == 0
        assert stats["next_client_id"] == 1


class TestChannelMuxDataHandling:
    """Data send/receive tests."""

    def test_handle_incoming_data(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        data, needs_window = mux.handle_data(channel_id, b"incoming data")
        
        assert data == b"incoming data"
        info = mux.get_channel(channel_id)
        assert info["bytes_recv"] == 13

    def test_needs_window_update_when_low(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        # Consume most of recv credit
        large_data = b"x" * (CHANNEL_INITIAL_CREDIT - 1000)
        _, needs_window = mux.handle_data(channel_id, large_data)
        
        # Should need window update (below 25% threshold)
        assert needs_window is True


class TestPacketFormats:
    """Verify packet binary formats match between implementations."""

    def test_open_packet_format(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, packet = mux.open_channel("ssh-agent", CHANNEL_FLAG_BIDIR)
        
        # Format: channel_id (2) + flags (1) + target (N) [+ optional null terminator]
        parsed_id = struct.unpack("<H", packet[:2])[0]
        parsed_flags = packet[2]
        parsed_target = packet[3:].rstrip(b"\x00").decode("utf-8")  # Handle optional null
        
        assert parsed_id == channel_id
        assert parsed_flags == CHANNEL_FLAG_BIDIR
        assert parsed_target == "ssh-agent"

    def test_close_packet_format(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        packet = mux.close_channel(channel_id, CHANNEL_CLOSE_TIMEOUT)
        
        # Format: channel_id (2) + code (2)
        parsed_id, parsed_code = struct.unpack("<HH", packet)
        
        assert parsed_id == channel_id
        assert parsed_code == CHANNEL_CLOSE_TIMEOUT

    def test_data_packet_format(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        packet = mux.send_data(channel_id, b"hello")
        
        # Format: channel_id (2) + data (N)
        parsed_id = struct.unpack("<H", packet[:2])[0]
        parsed_data = packet[2:]
        
        assert parsed_id == channel_id
        assert parsed_data == b"hello"

    def test_window_packet_format(self, mux_class):
        mux = mux_class(is_client=True)
        channel_id, _ = mux.open_channel("test", 0)
        mux.handle_window(channel_id, 1000)
        
        packet = mux.grant_window(channel_id, 50000)
        
        # Format: channel_id (2) + credit (4)
        parsed_id, parsed_credit = struct.unpack("<HI", packet)
        
        assert parsed_id == channel_id
        assert parsed_credit == 50000


# Run with: pytest protocols/wslink/tests/test_channel_mux.py -v
