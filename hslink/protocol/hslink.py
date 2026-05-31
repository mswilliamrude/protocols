from ..const import *
from ..tools import crc16, crc24, crc32
from ..base import HSLinkBase
from ..error import HSLinkError
import time
import logging

log = logging.getLogger(__name__)

class HSLink(HSLinkBase):
    def __init__(self, getc, putc):
        super().__init__(getc, putc)
        self.alternate_dle = False
        self.crc_size = DEF_CRC_SIZE

    def _escape_byte(self, byte):
        if byte in (START_PACKET_CHR, END_PACKET_CHR, DLE_CHR, XON_CHR, XOFF_CHR, CAN_CHR):
            # Escape the byte
            esc = byte ^ (0x40 if self.alternate_dle else 0x80)
            return bytes([DLE_CHR, esc])
        return bytes([byte])

    def _unescape_byte(self, escaped_byte):
        return escaped_byte ^ (0x40 if self.alternate_dle else 0x80)

    def _send_packet(self, pkt_type, payload, timeout=1):
        """
        Builds and sends an HS/Link frame:
        [START][TYPE][PAYLOAD][CRC][END]
        """
        # Calculate CRC over payload
        if self.crc_size == 2:
            crc_val = crc16(payload)
            crc_bytes = crc_val.to_bytes(2, 'little')
        elif self.crc_size == 4:
            crc_val = crc32(payload)
            crc_bytes = crc_val.to_bytes(4, 'little')
        else:
            # Default 24-bit CRC
            crc_val = crc24(payload)
            crc_bytes = crc_val.to_bytes(3, 'little')

        # Frame structure
        raw_frame = pkt_type + payload + crc_bytes
        
        # Escape the frame
        escaped_frame = bytearray()
        for b in raw_frame:
            escaped_frame.extend(self._escape_byte(b))

        # Build final packet
        packet = bytes([START_PACKET_CHR]) + escaped_frame + bytes([END_PACKET_CHR])
        self._send(packet, timeout)
        
    def send(self, files, overwrite=False):
        """
        Main entry point to send files over HS/Link.
        """
        log.warning("HS/Link send state machine is not yet fully implemented.")
        raise NotImplementedError("HS/Link C structs must be mapped to Python struct.pack first.")

    def recv(self, dest_dir):
        """
        Main entry point to receive files over HS/Link.
        """
        log.warning("HS/Link recv state machine is not yet fully implemented.")
        raise NotImplementedError("HS/Link C structs must be mapped to Python struct.pack first.")
