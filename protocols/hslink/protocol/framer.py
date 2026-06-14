from ..const import *
from ..tools import crc16, crc24, crc32
import logging

log = logging.getLogger(__name__)

class HSLinkFramer:
    """
    Handles exactly Step 2: The Codec / Framing layer.
    Consumes raw bytes from a Transport, unescapes DLE chars, verifies CRC, 
    and yields clean (Packet Type, Payload) tuples.
    """
    MAX_BUFFER_SIZE = 1 * 1024 * 1024  # 1MB — FIX E

    def __init__(self, transport):
        self.transport = transport
        self.crc_size = DEF_CRC_SIZE # Default to 3 (24-bit)
        self.alternate_dle = False
        
        self._buffer = bytearray()
        self._in_packet = False
        self._escaped = False
        self._current_packet = bytearray()

    def _unescape(self, byte: int) -> int:
        return byte ^ (0x40 if self.alternate_dle else 0x80)

    def _escape(self, byte: int) -> bytes:
        if byte in (START_PACKET_CHR, END_PACKET_CHR, DLE_CHR, XON_CHR, XOFF_CHR, CAN_CHR):
            return bytes([DLE_CHR, byte ^ (0x40 if self.alternate_dle else 0x80)])
        return bytes([byte])

    def send_packet(self, pkt_type: bytes, payload: bytes):
        """
        Calculates CRC, appends it, escapes special characters, frames with START/END,
        and pushes it to the transport layer.
        """
        # The CRC spans the Type byte + Payload
        raw_data = pkt_type + payload
        
        if self.crc_size == 2:
            crc_val = crc16(raw_data)
            crc_bytes = crc_val.to_bytes(2, 'little')
        elif self.crc_size == 4:
            crc_val = crc32(raw_data)
            crc_bytes = crc_val.to_bytes(4, 'little')
        else:
            crc_val = crc24(raw_data)
            crc_bytes = crc_val.to_bytes(3, 'little')

        frame = bytearray([START_PACKET_CHR])
        for b in raw_data + crc_bytes:
            frame.extend(self._escape(b))
        frame.append(END_PACKET_CHR)

        self.transport.write(frame)

    def read_packets(self):
        """
        Pulls data from transport, unescapes, and yields clean packets.
        """
        # Grab any newly arrived bytes from the transport layer
        new_data = self.transport.read()
        if new_data:
            self._buffer.extend(new_data)

        # FIX E — BUFFER SIZE CAP: prevent unbounded memory consumption
        if len(self._buffer) > self.MAX_BUFFER_SIZE:
            log.error("Buffer overflow — peer sending unbounded data. Resetting.")
            self._buffer.clear()
            self._in_packet = False
            self._escaped = False
            self._current_packet = bytearray()
            return []

        packets = []
        i = 0
        while i < len(self._buffer):
            b = self._buffer[i]
            i += 1
            
            if b == START_PACKET_CHR:
                # Discard anything before START, begin a new packet
                self._in_packet = True
                self._current_packet = bytearray()
                self._escaped = False
                continue
                
            if self._in_packet:
                if b == END_PACKET_CHR:
                    # Packet Complete!
                    self._in_packet = False
                    pkt = self._finalize_packet()
                    if pkt:
                        packets.append(pkt)
                elif b == DLE_CHR:
                    self._escaped = True
                elif b == CAN_CHR:
                    # Connection Cancel sequence requested
                    self._in_packet = False
                    self._current_packet = bytearray()
                    log.warning("Received CAN (Cancel) sequence in stream.")
                else:
                    if self._escaped:
                        self._current_packet.append(self._unescape(b))
                        self._escaped = False
                    else:
                        self._current_packet.append(b)
                        
        # Slide the buffer forward
        self._buffer = self._buffer[i:]
        return packets

    def _finalize_packet(self):
        """Validates CRC and splits into Type and Payload."""
        if len(self._current_packet) < 1 + self.crc_size:
            log.warning("Packet too short to contain CRC")
            return None
            
        raw_data = self._current_packet[:-self.crc_size]
        received_crc_bytes = self._current_packet[-self.crc_size:]
        received_crc = int.from_bytes(received_crc_bytes, 'little')
        
        if self.crc_size == 2:
            expected_crc = crc16(raw_data)
        elif self.crc_size == 4:
            expected_crc = crc32(raw_data)
        else:
            expected_crc = crc24(raw_data)
            
        if received_crc != expected_crc:
            log.warning(f"CRC Mismatch! Expected 0x{expected_crc:x}, got 0x{received_crc:x}")
            return None
            
        pkt_type = bytes([raw_data[0]])
        payload = bytes(raw_data[1:])
        return (pkt_type, payload)
