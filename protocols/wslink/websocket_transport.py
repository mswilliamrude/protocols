import asyncio
import logging
from .transport import AsyncTransport

log = logging.getLogger(__name__)


class WebSocketTransport(AsyncTransport):
    """
    Adapts a websockets client or Starlette WebSocket server to the AsyncTransport interface
    used by WSLink. Since WebSockets are message-based but WSLink expects a byte stream, 
    this class buffers incoming messages.
    """
    def __init__(self, ws):
        self.ws = ws
        self.buffer = bytearray()
        self.cond = asyncio.Condition()
        self._closed = False
        
    async def feed_data(self, data: bytes):
        """Called externally when the WebSocket receives binary data."""
        async with self.cond:
            self.buffer.extend(data)
            self.cond.notify_all()

    @property
    def is_closed(self) -> bool:
        """Whether this transport has been marked closed."""
        return self._closed
            
    def mark_closed(self):
        """Called externally when the WebSocket closes. Thread-safe (sync)."""
        self._closed = True
        # Wake any waiters so they can see the closed state
        asyncio.ensure_future(self._notify_closed())

    async def _notify_closed(self):
        """Notify condition waiters that the transport is closed."""
        try:
            async with self.cond:
                self.cond.notify_all()
        except Exception:
            pass
        
    async def read_exactly(self, n: int) -> bytes:
        async with self.cond:
            while len(self.buffer) < n:
                if self._closed:
                    return b""
                await self.cond.wait()
            
            data = bytes(self.buffer[:n])
            self.buffer = self.buffer[n:]
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
            self._closed = True
            
    async def close(self):
        self._closed = True
        try:
            await self.ws.close()
        except Exception:
            pass
