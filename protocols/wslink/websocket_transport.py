import asyncio
import logging
from .transport import AsyncTransport
from .protocol.framer import MAX_FRAME_SIZE

log = logging.getLogger(__name__)

# Default high-water mark for the receive buffer. The framer caps a single frame
# at MAX_FRAME_SIZE (1 MB), so this is comfortably larger than any single read.
DEFAULT_MAX_BUFFER_BYTES = 128 * 1024 * 1024  # 128 MB
# Compact the buffer (drop the consumed prefix) once the read cursor passes this.
DEFAULT_COMPACT_THRESHOLD = 1 * 1024 * 1024   # 1 MB


class WebSocketTransport(AsyncTransport):
    """Adapts a ``websockets`` client or Starlette server WebSocket to the
    :class:`AsyncTransport` byte-stream interface used by WSLink.

    WebSockets are message-framed but WSLink expects a byte stream, so incoming
    messages are buffered. This implementation fixes three issues in the original:

    1. **O(1) amortised reads.** Reads advance a cursor instead of reslicing the
       whole buffer on every call (the old ``self.buffer = self.buffer[n:]`` was
       O(n) per read — O(n^2) under load). The consumed prefix is compacted
       periodically to bound memory.
    2. **Bounded memory / backpressure.** ``feed_data`` blocks once the unconsumed
       buffer reaches the high-water mark, propagating flow control to the WS pump
       (and thus TCP) instead of growing without limit.
    3. **Thread-safe close.** ``mark_closed`` schedules the wakeup via
       ``loop.call_soon_threadsafe`` rather than ``asyncio.ensure_future`` (which
       requires a running loop in the calling thread).
    """

    def __init__(self, ws, *, max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
                 compact_threshold: int = DEFAULT_COMPACT_THRESHOLD):
        self.ws = ws
        self.buffer = bytearray()
        self._read_pos = 0
        self.cond = asyncio.Condition()
        self._closed = False
        # Clamp the high-water mark to at least 2x the max frame so a single
        # read (<= MAX_FRAME_SIZE) can never exceed the buffer cap and deadlock
        # against feed_data backpressure.
        self.max_buffer_bytes = max(int(max_buffer_bytes), 2 * MAX_FRAME_SIZE)
        self._compact_threshold = int(compact_threshold)
        # Capture the running loop for thread-safe close scheduling. Constructed
        # inside the event loop (router connects in an async context), so this
        # normally succeeds; falls back to None (see mark_closed) otherwise.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def _unconsumed(self) -> int:
        """Bytes buffered but not yet read."""
        return len(self.buffer) - self._read_pos

    async def feed_data(self, data: bytes):
        """Feed inbound WebSocket bytes into the read buffer.

        Applies backpressure: if the unconsumed buffer is at the high-water mark,
        wait until the reader drains it. This bounds memory and pushes flow control
        back to the caller (the WS receive pump) instead of buffering unboundedly.
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        async with self.cond:
            while not self._closed and self._unconsumed() >= self.max_buffer_bytes:
                await self.cond.wait()
            if self._closed:
                return
            self.buffer.extend(data)
            self.cond.notify_all()

    @property
    def is_closed(self) -> bool:
        """Whether this transport has been marked closed."""
        return self._closed

    def mark_closed(self):
        """Mark the transport closed. Safe to call from any thread.

        Sets the closed flag (atomic enough for a bool) and schedules the condition
        wakeup on the event loop thread. Blocked ``read_exactly``/``feed_data``
        waiters can only exist once the loop is running, so if no loop was captured
        there is nothing to wake.
        """
        self._closed = True
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._schedule_notify)
            except RuntimeError:
                pass  # loop already closed — nothing waiting

    def _schedule_notify(self):
        """Runs on the loop thread; kick off the async notifier."""
        asyncio.ensure_future(self._notify_closed())

    async def _notify_closed(self):
        """Wake all condition waiters so they observe the closed state."""
        try:
            async with self.cond:
                self.cond.notify_all()
        except Exception:
            pass

    async def read_exactly(self, n: int) -> bytes:
        """Read exactly ``n`` bytes, or ``b""`` on close.

        Advances a read cursor (O(1) amortised) and compacts the consumed prefix
        once it grows past the threshold, rather than reslicing on every read.
        """
        async with self.cond:
            while self._unconsumed() < n:
                if self._closed:
                    return b""
                await self.cond.wait()

            start = self._read_pos
            end = start + n
            data = bytes(self.buffer[start:end])
            self._read_pos = end

            # Compact: drop the consumed prefix when it dominates the buffer. This
            # keeps memory bounded and slicing cheap without copying on every read.
            if self._read_pos >= self._compact_threshold and self._read_pos * 2 >= len(self.buffer):
                del self.buffer[:self._read_pos]
                self._read_pos = 0

            # Space may have freed — wake any feed_data producer blocked on backpressure.
            self.cond.notify_all()
            return data

    async def write(self, data: bytes):
        """Write data to the WebSocket. No-op if transport is closed."""
        if self._closed:
            log.debug("write() called on closed transport — dropping %d bytes", len(data))
            return
        try:
            if hasattr(self.ws, 'send_bytes'):
                # Starlette Server WebSocket
                await self.ws.send_bytes(data)
            else:
                # websockets Client
                await self.ws.send(data)
        except (RuntimeError, ConnectionError, OSError) as e:
            # Connection already closed — mark ourselves and drop silently
            log.debug("write() failed (connection gone): %s", e)
            self.mark_closed()

    async def close(self):
        self.mark_closed()
        try:
            await self.ws.close()
        except Exception:
            pass
