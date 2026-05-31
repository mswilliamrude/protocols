import struct
import zlib
import logging

log = logging.getLogger(__name__)

class WSLinkFramer:
    """
    Modern Length-Prefixed Clean Pipe Framer.
    Format: [4-byte length L] [1-byte type] [payload] [4-byte CRC32]
    where L = length of (type + payload + CRC32).
    """
    def __init__(self, transport):
        self.transport = transport
        
    async def read_packet(self):
        len_bytes = await self.transport.read_exactly(4)
        if not len_bytes:
            return None
            
        length = struct.unpack('<I', len_bytes)[0]
        if length < 5: # min: 1 byte type + 4 byte CRC
            log.warning(f"Invalid frame length: {length}")
            return b'?', b''
            
        packet_data = await self.transport.read_exactly(length)
        if len(packet_data) < length:
            return None # Actual EOF if transport closed halfway
            
        pkt_type = packet_data[0:1]
        payload = packet_data[1:-4]
        expected_crc = struct.unpack('<I', packet_data[-4:])[0]
        
        actual_crc = zlib.crc32(packet_data[:-4]) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            log.warning(f"CRC Mismatch! Expected {expected_crc:08x}, got {actual_crc:08x}")
            # Return a junk tuple so the loop continues instead of interpreting as EOF.
            return b'?', b''
            
        return pkt_type, payload
        
    async def send_packet(self, pkt_type: bytes, payload: bytes):
        data = pkt_type + payload
        crc = zlib.crc32(data) & 0xFFFFFFFF
        # Length includes type (1) + payload + CRC (4)
        length = len(data) + 4
        frame = struct.pack('<I', length) + data + struct.pack('<I', crc)
        await self.transport.write(frame)
