import asyncio
import os
import time
import logging
import zlib
from ..const import *
from .structs.structs import FileHeaderPacket, SequencePacket, ResumeVerifyPacket
from .framer import WSLinkFramer

log = logging.getLogger(__name__)

class WSLinkSession:
    def __init__(self, transport, **kwargs):
        self.transport = transport
        self.framer = WSLinkFramer(transport)
        
        # State
        self.state = "INIT"
        self.recv_dir = kwargs.get('recv_dir', '.')
        
        # Sender state
        self.files_to_send = []
        self.current_file = None
        self.current_fd = None
        self.total_blocks = 0
        self.next_block_num = 0
        self.batch_index = 0
        self.unacked_blocks = {}
        
        # Receiver state
        self.recv_file = None
        self.recv_fd = None
        self.recv_batch_index = 0
        self.recv_expected_block = 0
        self.recv_file_time = 0.0
        
        # Bandwidth & Congestion Control
        self.block_size = kwargs.get('block_size', 4096)
        self.window_size = kwargs.get('initial_window', 16)
        self.max_window_size = kwargs.get('max_window', 256)
        self.arq_timeout = kwargs.get('arq_timeout', 2.0)
        self.verify_limit = kwargs.get('verify_limit', 100)
        self.rtt_history_size = kwargs.get('rtt_history_size', 20)
        self.block_send_times = {}
        self.rtt_history = []
        
        self.on_chat_received = None
        self._sent_z = False
        
    def add_files(self, file_paths):
        self.files_to_send.extend(file_paths)
        
    async def send_chat(self, message: bytes):
        await self.framer.send_packet(PACK_CHAT_BLOCK, message)
        
    async def loop(self):
        # Initial Handshake
        log.debug("Sending Handshake (R and Q)...")
        await self.framer.send_packet(PACK_READY, b"")
        await self.framer.send_packet(PACK_READY_RECV, b"")
        
        recv_task = asyncio.create_task(self._recv_loop())
        send_task = asyncio.create_task(self._send_loop())
        
        await asyncio.gather(recv_task, send_task)
        
    async def _recv_loop(self):
        while self.state != "DONE":
            packet = await self.framer.read_packet()
            if not packet:
                # EOF or dropped connection
                log.info("Connection closed by peer or read timeout.")
                self.state = "DONE"
                break
                
            pkt_type, payload = packet
            await self._handle_packet(pkt_type, payload)
            
    async def _send_loop(self):
        while self.state != "DONE":
            if self.state == "TRANSFERRING":
                await self._pump_sender()
            await asyncio.sleep(0.01)

    def _open_next_file(self):
        if not self.files_to_send:
            return
            
        filepath = self.files_to_send.pop(0)
        st = os.stat(filepath)
        size = st.st_size
        blocks = (size + self.block_size - 1) // self.block_size
        
        log.info(f"Opening file: {filepath} ({size} bytes)")
        
        filename = os.path.basename(filepath)
        header = FileHeaderPacket.pack(
            name=filename,
            size=size,
            blocks=blocks,
            block_size=self.block_size,
            time_float=st.st_mtime,
            batch=self.batch_index
        )
        
        # send packet asynchronously? We are inside a synchronous context here,
        # but _open_next_file is called from _pump_sender which is async.
        # Let's change _open_next_file to async.
        pass

    async def _open_next_file_async(self):
        if not self.files_to_send:
            return
            
        filepath = self.files_to_send.pop(0)
        st = os.stat(filepath)
        size = st.st_size
        blocks = (size + self.block_size - 1) // self.block_size
        
        log.info(f"Opening file: {filepath} ({size} bytes)")
        
        filename = os.path.basename(filepath)
        header = FileHeaderPacket.pack(
            name=filename,
            size=size,
            blocks=blocks,
            block_size=self.block_size,
            time_float=st.st_mtime,
            batch=self.batch_index
        )
        
        await self.framer.send_packet(PACK_OPEN_FILE, header)
        self.current_file = filepath
        self.current_fd = open(filepath, 'rb')
        self.total_blocks = blocks
        self.next_block_num = 0
        self.unacked_blocks.clear()
        self.block_send_times.clear()

    async def _pump_sender(self):
        if getattr(self, '_sent_z', False):
            return
            
        if not self.current_file:
            if not self.files_to_send:
                if self.batch_index > 0:
                    log.info("All files transmitted.")
                    await self.framer.send_packet(PACK_TRANSMIT_DONE, b"")
                    self._sent_z = True
                return
            await self._open_next_file_async()

        # ARQ Timeout Logic
        if self.unacked_blocks:
            current_time = time.time()
            oldest_block = min(self.unacked_blocks.keys())
            send_time = self.block_send_times.get(oldest_block, current_time)
            if current_time - send_time > self.arq_timeout:
                log.warning(f"ARQ Timeout! Resending block {oldest_block}. Throttling window.")
                self.window_size = max(1, self.window_size // 2) # Halve window on timeout
                
                stored_payload = self.unacked_blocks[oldest_block]
                await self.framer.send_packet(PACK_DATA_BLOCK, stored_payload)
                self.block_send_times[oldest_block] = current_time # Reset timer
                
        # Fill Window
        while len(self.unacked_blocks) < self.window_size and self.next_block_num < self.total_blocks:
            chunk = self.current_fd.read(self.block_size)
            if not chunk:
                break
                
            seq_bytes = SequencePacket.pack(self.batch_index, self.next_block_num)
            payload = seq_bytes + chunk
            
            self.unacked_blocks[self.next_block_num] = payload
            self.block_send_times[self.next_block_num] = time.time()
            
            await self.framer.send_packet(PACK_DATA_BLOCK, payload)
            self.next_block_num += 1

        # EOF Handle
        if self.next_block_num >= self.total_blocks and not self.unacked_blocks:
            log.info(f"File {self.current_file} successfully transferred.")
            await self.framer.send_packet(PACK_CLOSE_FILE, b"")
            self.current_fd.close()
            self.current_file = None
            self.batch_index += 1

    def _update_rtt(self, rtt: float):
        self.rtt_history.append(rtt)
        if len(self.rtt_history) > self.rtt_history_size:
            self.rtt_history.pop(0)
            
        avg_rtt = sum(self.rtt_history) / len(self.rtt_history)
        
        # BBR-style naive scale: if link is fast and window is full, increase window.
        if avg_rtt < 0.1 and len(self.unacked_blocks) >= self.window_size * 0.8:
            self.window_size = min(self.max_window_size, self.window_size + 1)
        elif avg_rtt > 0.5:
            # Bufferbloat detected, scale back gently
            self.window_size = max(1, int(self.window_size * 0.9))

    async def _handle_packet(self, pkt_type: bytes, payload: bytes):
        if pkt_type == PACK_CHAT_BLOCK:
            if self.on_chat_received:
                self.on_chat_received(payload)
            else:
                log.info(f"Chat received: {payload.decode('utf-8', 'ignore')}")
                
        elif pkt_type in (PACK_READY, PACK_READY_RECV):
            if self.state == "INIT":
                log.info("Handshake sync complete. Connection established.")
                self.state = "TRANSFERRING"
                
        elif pkt_type == PACK_ACK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.batch_index:
                ack_block = seq['block']
                
                # RTT measurement
                if ack_block in self.block_send_times:
                    rtt = time.time() - self.block_send_times[ack_block]
                    self._update_rtt(rtt)
                
                cleared = [k for k in self.unacked_blocks.keys() if k <= ack_block]
                for k in cleared:
                    del self.unacked_blocks[k]
                    if k in self.block_send_times:
                        del self.block_send_times[k]

        elif pkt_type == PACK_NAK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.batch_index:
                nak_block = seq['block']
                if nak_block in self.unacked_blocks:
                    log.warning(f"Received NAK for block {nak_block}. Resending.")
                    self.window_size = max(1, self.window_size // 2) # Halve on drop
                    stored_payload = self.unacked_blocks[nak_block]
                    await self.framer.send_packet(PACK_DATA_BLOCK, stored_payload)
                    self.block_send_times[nak_block] = time.time()
                
        elif pkt_type == PACK_OPEN_FILE:
            header = FileHeaderPacket.unpack(payload)
            filename = header['name']
            log.info(f"Peer requested to open file: {filename} ({header['size']} bytes)")
            
            filepath = os.path.join(self.recv_dir, filename)
            self.recv_file = filepath
            self.recv_batch_index = header['batch']
            self.recv_expected_block = 0
            self.recv_file_time = header['time']
            
            # Crash Recovery & Skip Logic
            if os.path.exists(filepath):
                st = os.stat(filepath)
                if st.st_size == header['size']:
                    log.info(f"File {filename} exists and matches size. Sending SKIP (K).")
                    await self.framer.send_packet(PACK_SKIP_FILE, b"")
                    return
                elif st.st_size < header['size']:
                    log.info(f"File {filename} partially exists. Hashing blocks to send VERIFY (V).")
                    with open(filepath, 'rb') as f:
                        count = 0
                        crcs = bytearray()
                        while count < self.verify_limit: # Configurable verification chunking
                            chunk = f.read(self.block_size)
                            if len(chunk) < self.block_size:
                                break
                            crc_val = zlib.crc32(chunk) & 0xFFFFFFFF
                            crcs.extend(struct.pack('<I', crc_val))
                            count += 1
                        
                        if count > 0:
                            v_payload = ResumeVerifyPacket.pack_header(0, count) + crcs
                            await self.framer.send_packet(PACK_VERIFY_BLOCK, v_payload)
                            self.recv_expected_block = count
                            self.recv_fd = open(filepath, 'ab')
                            return
                            
            self.recv_fd = open(filepath, 'wb')
            
        elif pkt_type == PACK_DATA_BLOCK:
            seq_size = SequencePacket.SIZE
            seq = SequencePacket.unpack(payload[:seq_size])
            chunk = payload[seq_size:]
            
            if seq['batch'] == self.recv_batch_index:
                if seq['block'] == self.recv_expected_block:
                    if self.recv_fd:
                        self.recv_fd.write(chunk)
                    self.recv_expected_block += 1
                    
                    ack_payload = SequencePacket.pack(seq['batch'], seq['block'])
                    await self.framer.send_packet(PACK_ACK_BLOCK, ack_payload)
                elif seq['block'] > self.recv_expected_block:
                    log.warning(f"Out of order block {seq['block']} received, expecting {self.recv_expected_block}. Sending NAK.")
                    nak_payload = SequencePacket.pack(seq['batch'], self.recv_expected_block)
                    await self.framer.send_packet(PACK_NAK_BLOCK, nak_payload)
                else:
                    # Duplicate
                    ack_payload = SequencePacket.pack(seq['batch'], seq['block'])
                    await self.framer.send_packet(PACK_ACK_BLOCK, ack_payload)
                    
        elif pkt_type == PACK_SKIP_FILE:
            log.info(f"Peer requested SKIP for file: {self.current_file}")
            self.unacked_blocks.clear()
            self.block_send_times.clear()
            self.next_block_num = self.total_blocks
            
        elif pkt_type == PACK_VERIFY_BLOCK:
            v_header = ResumeVerifyPacket.unpack_header(payload)
            base_block = v_header['base_block']
            count = v_header['count']
            log.info(f"Peer requested VERIFY for {count} blocks starting at {base_block}.")
            
            self.current_fd.seek(base_block * self.block_size)
            verified = 0
            offset = ResumeVerifyPacket.HEADER_SIZE
            for _ in range(count):
                chunk = self.current_fd.read(self.block_size)
                if not chunk: break
                expected_crc = struct.unpack('<I', payload[offset:offset+4])[0]
                if (zlib.crc32(chunk) & 0xFFFFFFFF) == expected_crc:
                    verified += 1
                    offset += 4
                else:
                    break
                    
            log.info(f"Verified {verified} blocks. Seeking sender to block {base_block + verified}.")
            self.next_block_num = base_block + verified
            self.current_fd.seek(self.next_block_num * self.block_size)
            self.unacked_blocks.clear()
            self.block_send_times.clear()
            
            seq_payload = SequencePacket.pack(self.batch_index, self.next_block_num)
            await self.framer.send_packet(PACK_SEEK_BLOCK, seq_payload)
            
        elif pkt_type == PACK_SEEK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.recv_batch_index:
                log.info(f"Sender seeking to block {seq['block']}")
                self.recv_expected_block = seq['block']
                
        elif pkt_type == PACK_CLOSE_FILE:
            if self.recv_fd:
                self.recv_fd.close()
                self.recv_fd = None
                log.info(f"File {self.recv_file} successfully received and closed.")
                if self.recv_file_time:
                    try:
                        os.utime(self.recv_file, (time.time(), self.recv_file_time))
                    except Exception as e:
                        log.warning(f"Could not apply timestamp to {self.recv_file}: {e}")
                self.recv_file = None
                
        elif pkt_type == PACK_TRANSMIT_DONE:
            log.info("Peer signaled all files transmitted (Z).")
            if not self.recv_fd and len(self.unacked_blocks) == 0:
                log.info("Receive queue empty, transitioning to DONE.")
                self.state = "DONE"
