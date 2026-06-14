import asyncio
import collections
import os
import struct
import time
import logging
import traceback
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
        self.idle_timeout = kwargs.get('idle_timeout', 60.0)   # 3 missed heartbeats = dead
        self.heartbeat_interval = kwargs.get('heartbeat_interval', 20.0)  # Send ping every 20s
        self.verify_limit = kwargs.get('verify_limit', 100)
        self.rtt_history_size = kwargs.get('rtt_history_size', 20)
        self.block_send_times = {}
        self.rtt_history = collections.deque(maxlen=self.rtt_history_size)
        
        self.on_chat_received = None
        self._sent_z = False
        self._send_event = asyncio.Event()
        
    def add_files(self, file_paths):
        self.files_to_send.extend(file_paths)
        
    async def send_chat(self, message: bytes):
        await self.framer.send_packet_immediate(PACK_CHAT_BLOCK, message)
        
    async def loop(self):
        # Initial Handshake
        log.debug("Sending Handshake (R and Q)...")
        await self.framer.send_packet_immediate(PACK_READY, b"")
        await self.framer.send_packet_immediate(PACK_READY_RECV, b"")
        
        recv_task = asyncio.create_task(self._recv_loop())
        send_task = asyncio.create_task(self._send_loop())
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            await asyncio.gather(recv_task, send_task, heartbeat_task)
        except Exception as e:
            log.error(f"WSLinkSession loop error: {e}\n{traceback.format_exc()}")
            self.state = "DONE"

    async def _heartbeat_loop(self):
        """Send periodic PING to keep the connection alive and detect dead peers."""
        while self.state != "DONE":
            await asyncio.sleep(self.heartbeat_interval)
            if self.state == "DONE":
                break
            try:
                # Send PING with timestamp for RTT measurement
                payload = struct.pack('<d', time.time())
                await self.framer.send_packet_immediate(PACK_PING, payload)
                log.debug("Heartbeat PING sent")
            except Exception as e:
                log.warning(f"Heartbeat send failed: {e}")
                self.state = "DONE"
                break
        
    async def _recv_loop(self):
        while self.state != "DONE":
            try:
                if self.idle_timeout and self.idle_timeout > 0:
                    packet = await asyncio.wait_for(
                        self.framer.read_packet(), timeout=self.idle_timeout
                    )
                else:
                    # No timeout — wait indefinitely (MCP chat sessions rely on heartbeat)
                    packet = await self.framer.read_packet()
            except asyncio.TimeoutError:
                log.warning(f"Idle timeout — no data received in {self.idle_timeout}s. Closing session.")
                self.state = "DONE"
                break
                
            if not packet:
                # EOF or dropped connection
                log.info("Connection closed by peer or read timeout.")
                self.state = "DONE"
                break
                
            pkt_type, payload = packet

            try:
                await self._handle_packet(pkt_type, payload)
            except Exception as e:
                log.error(f"Error handling packet type {pkt_type}: {e}\n{traceback.format_exc()}")
            
    async def _send_loop(self):
        while self.state != "DONE":
            # Clear BEFORE pump so any events set DURING pump are preserved
            self._send_event.clear()

            if self.state == "TRANSFERRING":
                try:
                    await self._pump_sender()
                except Exception as e:
                    log.error(f"Sender error: {e}\n{traceback.format_exc()}")
                    self.state = "DONE"
                    break

            # If an event arrived during pump, loop immediately
            if self._send_event.is_set():
                continue

            # Wait for signal or timeout for ARQ retransmit check
            try:
                await asyncio.wait_for(self._send_event.wait(), timeout=self.arq_timeout)
            except asyncio.TimeoutError:
                pass  # Timeout — check for retransmits

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
        
        await self.framer.send_packet_immediate(PACK_OPEN_FILE, header)
        self.current_file = filepath
        self.current_fd = open(filepath, 'rb')
        self.total_blocks = blocks
        self.next_block_num = 0
        self.unacked_blocks.clear()
        self.block_send_times.clear()

    async def _pump_sender(self):
        if self._sent_z:
            return
            
        if not self.current_file:
            if not self.files_to_send:
                if not self._sent_z:
                    # Only send TRANSMIT_DONE if we actually transferred files.
                    # Sending Z with no files (batch_index=0) causes the peer's
                    # session to exit, killing the chat channel used for MCP traffic.
                    if self.batch_index > 0:
                        log.info("All files transmitted. Signaling TRANSMIT_DONE (Z).")
                        await self.framer.send_packet_immediate(PACK_TRANSMIT_DONE, b"")
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
                await self.framer.send_packet_immediate(PACK_DATA_BLOCK, stored_payload)
                self.block_send_times[oldest_block] = current_time # Reset timer
                
        # Fill Window — buffer all data frames, flush once at the end
        sent_any = False
        fill_time = time.time()  # Single timestamp for entire burst
        while len(self.unacked_blocks) < self.window_size and self.next_block_num < self.total_blocks:
            chunk = self.current_fd.read(self.block_size)
            if not chunk:
                break
                
            seq_bytes = SequencePacket.pack(self.batch_index, self.next_block_num)
            payload = seq_bytes + chunk
            
            self.unacked_blocks[self.next_block_num] = payload
            self.block_send_times[self.next_block_num] = fill_time
            
            await self.framer.send_packet(PACK_DATA_BLOCK, payload)
            self.next_block_num += 1
            sent_any = True

        # Single flush for entire window fill
        if sent_any:
            await self.framer.flush()

        # EOF Handle
        if self.next_block_num >= self.total_blocks and not self.unacked_blocks:
            log.info(f"File {self.current_file} successfully transferred.")
            await self.framer.send_packet_immediate(PACK_CLOSE_FILE, b"")
            self.current_fd.close()
            self.current_file = None
            self.batch_index += 1

    def _update_rtt(self, rtt: float):
        self.rtt_history.append(rtt)  # deque(maxlen=N) auto-evicts oldest
            
        avg_rtt = sum(self.rtt_history) / len(self.rtt_history)
        
        # BBR-style naive scale: if link is fast and window is full, increase window.
        if avg_rtt < 0.1 and len(self.unacked_blocks) >= self.window_size * 0.8:
            self.window_size = min(self.max_window_size, self.window_size + 1)
        elif avg_rtt > 0.5:
            # Bufferbloat detected, scale back gently
            self.window_size = max(1, int(self.window_size * 0.9))

    async def _handle_packet(self, pkt_type: bytes, payload: bytes):
        # Heartbeat: PING/PONG allowed in any state (keepalive)
        if pkt_type == PACK_PING:
            # Respond with PONG (echo payload back for RTT measurement)
            await self.framer.send_packet_immediate(PACK_PONG, payload)
            log.debug("Heartbeat PONG sent (reply to PING)")
            return

        if pkt_type == PACK_PONG:
            # Measure RTT from ping timestamp
            if len(payload) >= 8:
                ping_time = struct.unpack('<d', payload[:8])[0]
                rtt = time.time() - ping_time
                log.debug(f"Heartbeat PONG received (RTT: {rtt*1000:.1f}ms)")
            return

        # Chat is allowed in any state (out-of-band messaging)
        if pkt_type == PACK_CHAT_BLOCK:
            if self.on_chat_received:
                self.on_chat_received(payload)
            else:
                log.info(f"Chat received: {payload.decode('utf-8', 'ignore')}")
            return

        # Handshake packets — only valid in INIT state
        if pkt_type in (PACK_READY, PACK_READY_RECV):
            if self.state == "INIT":
                log.info("Handshake sync complete. Connection established.")
                self.state = "TRANSFERRING"
                self._send_event.set()  # Wake sender to start transfer
            return

        # All other packets require TRANSFERRING state
        if self.state != "TRANSFERRING":
            log.warning(f"Rejected packet type {pkt_type!r} in state {self.state} (requires TRANSFERRING)")
            return

        if pkt_type == PACK_ACK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.batch_index:
                ack_block = seq['block']
                
                # RTT measurement — only accurate for the specific block ACKed
                if ack_block in self.block_send_times:
                    rtt = time.time() - self.block_send_times[ack_block]
                    self._update_rtt(rtt)
                
                # Selective ACK: only clear the specific block acknowledged.
                # The receiver sends per-block ACKs, so each ACK confirms
                # exactly one block. Cumulative clearing is UNSAFE because
                # if block N arrives but block N-1 was lost/reordered, clearing
                # all blocks <= N removes N-1 from retransmit tracking forever.
                if ack_block in self.unacked_blocks:
                    del self.unacked_blocks[ack_block]
                if ack_block in self.block_send_times:
                    del self.block_send_times[ack_block]
                
                self._send_event.set()  # Wake sender — window space freed

        elif pkt_type == PACK_NAK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.batch_index:
                nak_block = seq['block']
                if nak_block in self.unacked_blocks:
                    log.warning(f"Received NAK for block {nak_block}. Resending.")
                    self.window_size = max(1, self.window_size // 2) # Halve on drop
                    stored_payload = self.unacked_blocks[nak_block]
                    await self.framer.send_packet_immediate(PACK_DATA_BLOCK, stored_payload)
                    self.block_send_times[nak_block] = time.time()
                
        elif pkt_type == PACK_OPEN_FILE:
            header = FileHeaderPacket.unpack(payload)
            raw_name = header['name']
            
            # Security: strip directory components to prevent path traversal
            filename = os.path.basename(raw_name)
            if not filename or filename.startswith('.'):
                log.error(f"Rejected unsafe filename: {raw_name!r}")
                await self.framer.send_packet(PACK_SKIP_FILE, b"")
                return
            
            filepath = os.path.realpath(os.path.join(self.recv_dir, filename))
            recv_dir_real = os.path.realpath(self.recv_dir)
            if not filepath.startswith(recv_dir_real + os.sep) and filepath != recv_dir_real:
                log.error(f"Path traversal attempt blocked: {raw_name!r} -> {filepath}")
                await self.framer.send_packet(PACK_SKIP_FILE, b"")
                return
            
            log.info(f"Peer requested to open file: {filename} ({header['size']} bytes)")
            self.recv_file = filepath
            self.recv_batch_index = header['batch']
            self.recv_expected_block = 0
            self.recv_file_time = header['time']
            
            # Crash Recovery & Skip Logic
            if os.path.exists(filepath):
                st = os.stat(filepath)
                if st.st_size == header['size']:
                    log.info(f"File {filename} exists and matches size. Sending SKIP (K).")
                    await self.framer.send_packet_immediate(PACK_SKIP_FILE, b"")
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
                            await self.framer.send_packet_immediate(PACK_VERIFY_BLOCK, v_payload)
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
                    await self.framer.send_packet_immediate(PACK_ACK_BLOCK, ack_payload)
                elif seq['block'] > self.recv_expected_block:
                    log.warning(f"Out of order block {seq['block']} received, expecting {self.recv_expected_block}. Sending NAK.")
                    nak_payload = SequencePacket.pack(seq['batch'], self.recv_expected_block)
                    await self.framer.send_packet_immediate(PACK_NAK_BLOCK, nak_payload)
                else:
                    # Duplicate
                    ack_payload = SequencePacket.pack(seq['batch'], seq['block'])
                    await self.framer.send_packet_immediate(PACK_ACK_BLOCK, ack_payload)
                    
        elif pkt_type == PACK_SKIP_FILE:
            log.info(f"Peer requested SKIP for file: {self.current_file}")
            self.unacked_blocks.clear()
            self.block_send_times.clear()
            self.next_block_num = self.total_blocks
            self._send_event.set()  # Wake sender — file skipped, move to next
            
        elif pkt_type == PACK_VERIFY_BLOCK:
            if self.current_fd is None:
                log.warning("Received VERIFY_BLOCK but no file is currently open for sending. Ignoring.")
                return
            if len(payload) < ResumeVerifyPacket.HEADER_SIZE:
                log.warning("VERIFY_BLOCK payload too short for header. Ignoring.")
                return
            v_header = ResumeVerifyPacket.unpack_header(payload)
            base_block = v_header['base_block']
            count = v_header['count']
            
            # Cap count to prevent unbounded CPU/disk usage from crafted packets
            count = min(count, self.verify_limit)
            
            # Validate payload has enough CRC data
            expected_payload_size = ResumeVerifyPacket.HEADER_SIZE + count * 4
            if len(payload) < expected_payload_size:
                log.warning(f"VERIFY_BLOCK payload too short: need {expected_payload_size}, got {len(payload)}. Ignoring.")
                return
            
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
            await self.framer.send_packet_immediate(PACK_SEEK_BLOCK, seq_payload)
            
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
            # NOTE: Do NOT transition to DONE here. The chat channel (used for
            # JSON-RPC MCP traffic in the Unimind router) must remain active
            # after file transfer completes. Only explicit WebSocket close or
            # idle timeout should end the session.
