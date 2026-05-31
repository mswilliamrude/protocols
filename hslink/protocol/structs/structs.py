"""
HS/Link Protocol Structures

This module handles the serialization and deserialization of the exact, byte-for-byte
C structures used by the 1994 16-bit DOS HS/Link implementation.

In the original protocol:
- char -> 8-bit
- int -> 16-bit
- long -> 32-bit
- All structures must be packed explicitly to avoid platform-specific padding.
"""
import struct

# The struct format strings use '<' to enforce little-endian byte order (x86 standard)

class ControlMapping:
    """
    Used to remap control characters for transparent links.
    Original C Struct:
        uchar xon_map;
        uchar xoff_map;
        uchar dle_map;
        uchar start_map;
        uchar end_map;
    """
    FORMAT = '<BBBBB'
    SIZE = struct.calcsize(FORMAT)

    @classmethod
    def pack(cls, xon, xoff, dle, start, end):
        return struct.pack(cls.FORMAT, xon, xoff, dle, start, end)

    @classmethod
    def unpack(cls, data):
        return struct.unpack(cls.FORMAT, data)


class FileHeaderPacket:
    """
    PACK_OPEN_FILE (Type 'O')
    Original C Struct:
        char  name[13];      // 8.3 filename + null
        long  size;          // bytes (32-bit)
        block_number blocks; // size in blocks (16-bit)
        int   BlockSize;     // data block size for this transfer (16-bit)
        ftime time;          // modification timestamp (DOS ftime layout) (16-bit)
        uchar batch;         // file_number batch index
        char  spare[20];
    """
    # 13s = 13 char bytes, l = 32-bit int, h = 16-bit short, h = 16-bit short, I = 32-bit unsigned int, B = unsigned char, 20s = 20 char bytes
    FORMAT = '<13slhhIB20s'
    SIZE = struct.calcsize(FORMAT)

    @classmethod
    def pack(cls, name: bytes, size: int, blocks: int, block_size: int, time_dos: int, batch: int, spare: bytes = b'\x00'*20):
        # Ensure name is null-padded to 13 bytes
        name_padded = name.ljust(13, b'\x00')[:13]
        return struct.pack(cls.FORMAT, name_padded, size, blocks, block_size, time_dos, batch, spare)

    @classmethod
    def unpack(cls, data):
        unpacked = struct.unpack(cls.FORMAT, data)
        # Handle null-terminated 8.3 filename parsing correctly
        raw_name = unpacked[0]
        if b'\x00' in raw_name:
            raw_name = raw_name.split(b'\x00')[0]
            
        return {
            'name': raw_name,
            'size': unpacked[1],
            'blocks': unpacked[2],
            'BlockSize': unpacked[3],
            'time': unpacked[4],
            'batch': unpacked[5],
            'spare': unpacked[6]
        }


class SequencePacket:
    """
    Used by ACK/NAK and Data Packets.
    Original C Struct:
        file_number batch;   // uchar (8-bit)
        block_number block;  // 16-bit
    """
    FORMAT = '<Bh'
    SIZE = struct.calcsize(FORMAT)

    @classmethod
    def pack(cls, batch: int, block: int):
        return struct.pack(cls.FORMAT, batch, block)

    @classmethod
    def unpack(cls, data):
        unpacked = struct.unpack(cls.FORMAT, data)
        return {'batch': unpacked[0], 'block': unpacked[1]}


class ExtNakPacket:
    """
    PACK_EXTNAK_BLOCK (Type 'M')
    Original C Struct:
        sequence_packet seq; // 3 bytes
        uchar nak_reason;    // 1 byte
        uchar errlsr;        // 1 byte
        long  errcsip;       // 4 bytes
        CRC_type check[8];   // 8 * 4 bytes = 32 bytes (Assume 32-bit array for struct sizing, protocol logic determines width)
    """
    # We will treat the CRC array dynamically in protocol layer, but pack the header here.
    # B = batch, h = block, B = nak_reason, B = errlsr, l = errcsip
    HEADER_FORMAT = '<BhBBl'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    @classmethod
    def pack_header(cls, batch: int, block: int, nak_reason: int, errlsr: int = 0, errcsip: int = 0):
        return struct.pack(cls.HEADER_FORMAT, batch, block, nak_reason, errlsr, errcsip)


class ResumeVerifyPacket:
    """
    PACK_VERIFY_BLOCK (Type 'V')
    Original C Struct:
        block_number base_block; // 16-bit
        int count;               // 16-bit
        CRC_type check[100];     // Dynamic width in protocol logic
    """
    HEADER_FORMAT = '<hh'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    @classmethod
    def pack_header(cls, base_block: int, count: int):
        return struct.pack(cls.HEADER_FORMAT, base_block, count)

