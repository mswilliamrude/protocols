import os
import sys
import selectors
import logging

log = logging.getLogger(__name__)

class Transport:
    """
    Abstract interface for the cooperative async layer.
    """
    def read(self, max_bytes=4096) -> bytes:
        raise NotImplementedError

    def write(self, data: bytes):
        raise NotImplementedError

    def idle(self, timeout=0.01) -> bool:
        raise NotImplementedError


class StdioTransport(Transport):
    """
    Non-blocking transport over Standard Input / Output.
    Perfect for CLI apps invoked by a BBS, terminal emulator, or Telnet server.
    """
    def __init__(self):
        self.fd_in = sys.stdin.fileno()
        self.fd_out = sys.stdout.fileno()
        
        # Set pipes to non-blocking mode
        os.set_blocking(self.fd_in, False)
        os.set_blocking(self.fd_out, False)
        
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.fd_in, selectors.EVENT_READ)
        
        self.in_buffer = bytearray()
        self.out_buffer = bytearray()
        self.carrier_lost = False

    def read(self, max_bytes=4096) -> bytes:
        """Pulls bytes out of our internal inbound buffer."""
        if not self.in_buffer:
            return b""
        data = bytes(self.in_buffer[:max_bytes])
        self.in_buffer = self.in_buffer[max_bytes:]
        return data

    def write(self, data: bytes):
        """Queues bytes into our internal outbound buffer."""
        self.out_buffer.extend(data)

    def idle(self, timeout=0.01) -> bool:
        """
        The equivalent of the original ComIdle() cooperative pump.
        Returns False if the socket (carrier) is lost or EOF is reached, True otherwise.
        """
        if self.carrier_lost:
            return False

        # 1. Drain the outbound buffer to the OS pipe
        if self.out_buffer:
            try:
                written = os.write(self.fd_out, self.out_buffer)
                self.out_buffer = self.out_buffer[written:]
            except BlockingIOError:
                pass # Pipe is full, OS pushes backpressure; we'll try again next tick
            except OSError as e:
                log.error(f"Write error: {e}")
                self.carrier_lost = True
                return False

        # 2. Fill the inbound buffer from the OS pipe
        events = self.selector.select(timeout)
        for key, mask in events:
            if mask & selectors.EVENT_READ:
                try:
                    data = os.read(self.fd_in, 8192)
                    if data:
                        self.in_buffer.extend(data)
                    else:
                        # 0 bytes read on a ready socket means EOF (Carrier Lost)
                        self.carrier_lost = True
                except BlockingIOError:
                    pass
                except OSError as e:
                    log.error(f"Read error: {e}")
                    self.carrier_lost = True
                    return False

        return True
