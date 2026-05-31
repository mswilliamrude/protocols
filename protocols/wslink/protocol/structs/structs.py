import struct

class FileHeaderPacket:
    """
    WS/Link Modernized File Header
    Q = 64-bit unsigned size
    I = 32-bit unsigned blocks
    I = 32-bit unsigned block_size
    d = 64-bit float timestamp
    B = 8-bit batch index
    """
    HEADER_FORMAT = '<QIIdB'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    
    @classmethod
    def pack(cls, name: str, size: int, blocks: int, block_size: int, time_float: float, batch: int):
        name_bytes = name.encode('utf-8')
        header = struct.pack(cls.HEADER_FORMAT, size, blocks, block_size, time_float, batch)
        return header + name_bytes
        
    @classmethod
    def unpack(cls, data: bytes):
        header = struct.unpack(cls.HEADER_FORMAT, data[:cls.HEADER_SIZE])
        name = data[cls.HEADER_SIZE:].decode('utf-8')
        return {
            'size': header[0],
            'blocks': header[1],
            'block_size': header[2],
            'time': header[3],
            'batch': header[4],
            'name': name
        }

class SequencePacket:
    """
    B = 8-bit batch index
    I = 32-bit unsigned block number
    """
    FORMAT = '<BI'
    SIZE = struct.calcsize(FORMAT)
    
    @classmethod
    def pack(cls, batch: int, block: int):
        return struct.pack(cls.FORMAT, batch, block)
        
    @classmethod
    def unpack(cls, data: bytes):
        unpacked = struct.unpack(cls.FORMAT, data[:cls.SIZE])
        return {'batch': unpacked[0], 'block': unpacked[1]}

class ResumeVerifyPacket:
    """
    I = 32-bit base_block
    I = 32-bit count
    """
    HEADER_FORMAT = '<II'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    
    @classmethod
    def pack_header(cls, base_block: int, count: int):
        return struct.pack(cls.HEADER_FORMAT, base_block, count)
        
    @classmethod
    def unpack_header(cls, data: bytes):
        unpacked = struct.unpack(cls.HEADER_FORMAT, data[:cls.HEADER_SIZE])
        return {'base_block': unpacked[0], 'count': unpacked[1]}
