import asyncio

class AsyncTransport:
    async def read_exactly(self, n: int) -> bytes:
        raise NotImplementedError
        
    async def write(self, data: bytes):
        raise NotImplementedError
        
    async def close(self):
        raise NotImplementedError

class AsyncStreamTransport(AsyncTransport):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        
    async def read_exactly(self, n: int) -> bytes:
        try:
            return await self.reader.readexactly(n)
        except asyncio.IncompleteReadError:
            return b""
        except ConnectionResetError:
            return b""
            
    async def write(self, data: bytes):
        self.writer.write(data)
        await self.writer.drain()
        
    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()
