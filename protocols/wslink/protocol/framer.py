import struct
import zlib
import asyncio
import logging

log = logging.getLogger(__name__)

# Maximum frame size: 1MB. Generous for 64KB blocks + headers.
# Prevents OOM from malicious/corrupted length fields (uint32 max = 4GB).
MAX_FRAME_SIZE = 1 * 1024 * 1024

class WSLinkFramer:
    """
    Modern Length-Prefixed Clean Pipe Framer.
    Format: [4-byte length L] [1-byte type] [payload] [4-byte CRC32]
    where L = length of (type + payload + CRC32).
    
    Supports write coalescing: multiple send_packet() calls buffer
    internally and flush as a single write() call via flush().
    """
    def __init__(self, transport):
        self.transport = transport
        self._write_buf = bytearray()
        self._flush_lock = asyncio.Lock()
        
    async def read_packet(self):
        len_bytes = await self.transport.read_exactly(4)
        if not len_bytes:
            return None
            
        length = struct.unpack('<I', len_bytes)[0]
        if length < 5: # min: 1 byte type + 4 byte CRC
            log.warning(f"Invalid frame length: {length}")
            return b'?', b''
        
        if length > MAX_FRAME_SIZE:
            log.error(f"Frame length {length} exceeds MAX_FRAME_SIZE ({MAX_FRAME_SIZE}). Dropping connection.")
            return None
            
        packet_data = await self.transport.read_exactly(length)
        if len(packet_data) < length:
            return None # Actual EOF if transport closed halfway
            
        pkt_type = packet_data[0:1]
        payload = packet_data[1:-4]
        expected_crc = struct.unpack('<I', packet_data[-4:])[0]
        
        actual_crc = zlib.crc32(packet_data[:-4]) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            log.warning(f"CRC Mismatch! Expected {expected_crc:08x}, got {actual_crc:08x}")
            return b'?', b''
            
        return pkt_type, payload
        
    def _build_frame(self, pkt_type: bytes, payload: bytes) -> bytes:
        """Build a complete wire frame (no I/O)."""
        data = pkt_type + payload
        crc = zlib.crc32(data) & 0xFFFFFFFF
        length = len(data) + 4
        return struct.pack('<I', length) + data + struct.pack('<I', crc)

    async def send_packet(self, pkt_type: bytes, payload: bytes):
        """Buffer a frame for sending. Call flush() to write all buffered frames."""
        frame = self._build_frame(pkt_type, payload)
        self._write_buf.extend(frame)
    
    async def send_packet_immediate(self, pkt_type: bytes, payload: bytes):
        """Build and send a frame immediately (bypasses buffer). Use for
        control packets (ACK, NAK, handshake) that need low latency."""
        frame = self._build_frame(pkt_type, payload)
        await self.transport.write(frame)

    async def flush(self):
        """Flush all buffered frames as a single write. Thread-safe."""
        if not self._write_buf:
            return
        async with self._flush_lock:
            if not self._write_buf:
                return
            data = bytes(self._write_buf)
            self._write_buf.clear()
            await self.transport.write(data)
