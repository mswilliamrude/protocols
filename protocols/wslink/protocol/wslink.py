import asyncio
import collections
import dataclasses
import os
import struct
import time
import logging
import traceback
import zlib
from ..const import *
from .structs.structs import FileHeaderPacket, SequencePacket, ResumeVerifyPacket
from .framer import WSLinkFramer
from .events import EventKind, ProtocolEvent, SessionObserver, CallbackObserver, Subscription
from .capabilities import TransportCapabilities, VERSION as CAPS_VERSION

log = logging.getLogger(__name__)


def _mono() -> float:
    """Monotonic clock for all interval/RTT math.

    Never use wall clock (`time.time()`) for durations: an NTP step can move it
    backward and produce garbage/negative RTT samples. Wall clock is reserved for
    file mtimes only.
    """
    return time.monotonic()


# Builtin packet types owned by the session FSM. Consumer handlers may not shadow
# these unless override=True is passed to register_packet_handler.
_BUILTIN_PACKET_TYPES = frozenset({
    PACK_ACK_BLOCK, PACK_CLOSE_FILE, PACK_DATA_BLOCK, PACK_CHAT_BLOCK,
    PACK_SKIP_FILE, PACK_NAK_BLOCK, PACK_OPEN_FILE, PACK_PING,
    PACK_READY_RECV, PACK_READY, PACK_SEEK_BLOCK, PACK_VERIFY_BLOCK,
    PACK_PONG, PACK_TRANSMIT_DONE,
})

class WSLinkSession:
    def __init__(self, transport, **kwargs):
        self.transport = transport
        self.framer = WSLinkFramer(transport)
        
        # State
        self._state = "INIT"
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

        # ─── Link Statistics ──────────────────────────────────────────
        # Real-time telemetry counters. Queried via get_link_stats() at any
        # time from any coroutine. A parallel accelerated implementation is
        # available as pyprotocols_core.LinkStatsTracker (Rust/PyO3); this
        # inline version keeps the pure-Python session self-contained with no
        # compiled-extension dependency.
        self._stats_session_start = _mono()
        self._stats_bytes_sent = 0
        self._stats_bytes_received = 0
        self._stats_blocks_acked = 0
        self._stats_blocks_naked = 0
        self._stats_blocks_retransmitted = 0
        self._stats_crc_failures = 0
        self._stats_files_completed_send = 0
        self._stats_files_completed_recv = 0
        self._stats_files_skipped = 0
        self._stats_window_shrinks = 0
        self._stats_window_grows = 0
        self._stats_pings_sent = 0
        self._stats_pongs_received = 0
        self._stats_arq_timeouts = 0
        self._stats_transfer_start = 0.0  # time of first data block in current file
        self._stats_current_file_size = 0

        # ─── Extension & Observability (v2) ───────────────────────────
        # Consumer-registered packet handlers for new (non-builtin) types, e.g.
        # the socket-proxy's lowercase channel types. This is the supported
        # replacement for monkeypatching `_handle_packet`.
        self._packet_handlers = {}                      # bytes(1) -> async handler(pkt_type, payload)
        # Protocol-event observers. The session builds/dispatches ProtocolEvent
        # objects ONLY when this list is non-empty (zero-cost when unused).
        self._subscriptions = []                        # list[Subscription]
        # Capability negotiation. Advertising is opt-in so the wire stays
        # byte-identical to legacy peers unless explicitly enabled. Even when
        # advertised, the local default equals legacy behaviour (CRC on, ARQ on).
        self._advertise_capabilities = bool(kwargs.get('advertise_capabilities', False))
        self._local_capabilities = kwargs.get('local_capabilities') or TransportCapabilities.legacy()
        # A v2 session that advertises inherently speaks the current caps VERSION;
        # stamp it so negotiation yields the correct min-of-both-versions.
        if self._advertise_capabilities and self._local_capabilities.version == 0:
            self._local_capabilities = dataclasses.replace(self._local_capabilities, version=CAPS_VERSION)
        self._negotiated_capabilities = TransportCapabilities.legacy()
        # Stable id used in emitted events.
        self.session_id = kwargs.get('session_id') or f"wslink-{id(self):x}"

    # ── State machine (observable) ────────────────────────────────────
    @property
    def state(self) -> str:
        """Current FSM state (read-only; write via :meth:`_set_state`)."""
        return self._state

    def _set_state(self, new_state: str, reason: str = "") -> None:
        """Transition the FSM and emit a ``state_change`` event.

        Centralising state writes is what makes transitions observable — e.g. the
        historical TRANSMIT_DONE/Z chat-kill race would surface here as
        ``state_change{old=TRANSFERRING, new=DONE, reason=...}``.
        """
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        self._emit(EventKind.STATE_CHANGE, old=old, new=new_state, reason=reason)

    # ── Extension API ─────────────────────────────────────────────────
    def register_packet_handler(self, pkt_type, handler, *, override: bool = False) -> None:
        """Register an async handler for a packet type.

        Args:
            pkt_type: single-byte packet type (``bytes`` of length 1).
            handler: ``async def handler(pkt_type: bytes, payload: bytes)``.
            override: allow shadowing a builtin type (default: refuse).

        Raises:
            ValueError: on malformed type or a refused builtin collision.
        """
        if not isinstance(pkt_type, (bytes, bytearray)) or len(pkt_type) != 1:
            raise ValueError("pkt_type must be a single byte")
        pkt_type = bytes(pkt_type)
        if not callable(handler):
            raise ValueError("handler must be callable")
        if not override and pkt_type in _BUILTIN_PACKET_TYPES:
            raise ValueError(f"refusing to shadow builtin packet type {pkt_type!r} (pass override=True)")
        self._packet_handlers[pkt_type] = handler

    def unregister_packet_handler(self, pkt_type) -> None:
        """Remove a previously registered handler (no-op if absent)."""
        self._packet_handlers.pop(bytes(pkt_type), None)

    def subscribe(self, observer, *, sample_rate: int = 1, level: str = "info") -> None:
        """Subscribe an observer to protocol events.

        Args:
            observer: a :class:`SessionObserver`, or a plain callable (wrapped in
                :class:`CallbackObserver`).
            sample_rate: deliver only every Nth ``frame`` event (other kinds are
                always delivered). Keeps per-frame observation cheap at wire speed.
            level: advisory verbosity filter.
        """
        if not isinstance(observer, SessionObserver):
            if callable(observer):
                observer = CallbackObserver(observer)
            else:
                raise TypeError("observer must be a SessionObserver or callable")
        self._subscriptions.append(Subscription(observer=observer,
                                                sample_rate=max(1, int(sample_rate)),
                                                level=level))

    def unsubscribe(self, observer) -> None:
        """Remove an observer (matches the wrapped callable or the observer itself)."""
        self._subscriptions = [
            s for s in self._subscriptions
            if s.observer is not observer
            and not (isinstance(s.observer, CallbackObserver) and s.observer._callback is observer)
        ]

    async def send(self, pkt_type, payload: bytes = b"") -> None:
        """Public egress: frame and send a packet immediately.

        Supported alternative to reaching into ``session.framer`` — used by
        consumers (e.g. the socket proxy) to emit their own registered types.
        """
        await self.framer.send_packet_immediate(pkt_type, payload)

    @property
    def negotiated_capabilities(self) -> TransportCapabilities:
        """The effective capabilities after handshake negotiation."""
        return self._negotiated_capabilities

    def _emit(self, kind: str, **payload) -> None:
        """Build and dispatch a :class:`ProtocolEvent` to all subscribers.

        Zero-cost when there are no subscribers: returns before constructing any
        object. Observer exceptions are caught and logged, never propagated.
        """
        subs = self._subscriptions
        if not subs:
            return
        event = None
        for sub in subs:
            if not sub.wants(kind):
                continue
            if event is None:
                event = ProtocolEvent(kind=kind, ts_monotonic=_mono(),
                                      session_id=self.session_id, payload=payload)
            try:
                sub.observer.on_event(event)
            except Exception as e:  # never let observation break the protocol
                log.debug("observer raised on %s event: %s", kind, e)

    def add_files(self, file_paths):
        self.files_to_send.extend(file_paths)
        # Wake the send loop. Without this, files queued AFTER the session is
        # already running (dynamic add — e.g. a server queueing a freshly
        # rendered screenshot mid-session) are never transmitted: the send
        # loop sits blocked on `await self._send_event.wait()` (see _send_loop)
        # with no signal to re-pump. Queueing != sending; the producer MUST
        # wake the consumer. Safe to call before loop() starts too (the event
        # simply stays set until the first clear() at the top of _send_loop).
        self._send_event.set()
        
    def get_link_stats(self) -> dict:
        """Query connection statistics: throughput, error rates, congestion state.

        Returns a dict suitable for JSON serialization with all link metrics.
        Can be called at any time — safe from any coroutine or thread.
        """
        now = _mono()
        session_elapsed = now - self._stats_session_start

        # RTT statistics
        rtt_samples = list(self.rtt_history)
        if rtt_samples:
            rtt_avg = sum(rtt_samples) / len(rtt_samples)
            rtt_min = min(rtt_samples)
            rtt_max = max(rtt_samples)
        else:
            rtt_avg = rtt_min = rtt_max = 0.0

        # Throughput calculation (based on bytes sent over session lifetime)
        total_bytes = self._stats_bytes_sent + self._stats_bytes_received
        if session_elapsed > 0:
            throughput_bps = (total_bytes * 8) / session_elapsed
            throughput_kbps = throughput_bps / 1000
            throughput_mbps = throughput_bps / 1_000_000
        else:
            throughput_kbps = throughput_mbps = 0.0

        # Instantaneous throughput (last RTT window worth of data)
        if rtt_avg > 0 and self.window_size > 0:
            instant_bps = (self.window_size * self.block_size * 8) / rtt_avg
            instant_kbps = instant_bps / 1000
            instant_mbps = instant_bps / 1_000_000
        else:
            instant_kbps = instant_mbps = 0.0

        # Error rate
        total_blocks_attempted = self._stats_blocks_acked + self._stats_blocks_naked + self._stats_blocks_retransmitted
        if total_blocks_attempted > 0:
            error_rate = (self._stats_blocks_naked + self._stats_blocks_retransmitted) / total_blocks_attempted
        else:
            error_rate = 0.0

        # Window utilization
        if self.window_size > 0:
            window_utilization = len(self.unacked_blocks) / self.window_size
        else:
            window_utilization = 0.0

        # Transfer progress (current file)
        if self.total_blocks > 0:
            transfer_progress = self.next_block_num / self.total_blocks
            bytes_remaining = (self.total_blocks - self.next_block_num) * self.block_size
        else:
            transfer_progress = 0.0
            bytes_remaining = 0

        # ETA for current file
        if self._stats_transfer_start > 0 and self.next_block_num > 0:
            elapsed_file = now - self._stats_transfer_start
            blocks_done = self.next_block_num
            blocks_left = self.total_blocks - blocks_done
            if blocks_done > 0:
                time_per_block = elapsed_file / blocks_done
                eta_seconds = blocks_left * time_per_block
            else:
                eta_seconds = 0.0
        else:
            eta_seconds = 0.0

        return {
            # Connection state
            "state": self.state,
            "session_elapsed_s": round(session_elapsed, 2),

            # RTT (milliseconds)
            "rtt_avg_ms": round(rtt_avg * 1000, 2),
            "rtt_min_ms": round(rtt_min * 1000, 2),
            "rtt_max_ms": round(rtt_max * 1000, 2),
            "rtt_samples": len(rtt_samples),

            # Congestion window
            "window_size": self.window_size,
            "window_max": self.max_window_size,
            "window_utilization": round(window_utilization, 3),
            "window_grows": self._stats_window_grows,
            "window_shrinks": self._stats_window_shrinks,
            "in_flight_blocks": len(self.unacked_blocks),

            # Throughput
            "throughput_avg_kbps": round(throughput_kbps, 1),
            "throughput_avg_mbps": round(throughput_mbps, 3),
            "throughput_instant_kbps": round(instant_kbps, 1),
            "throughput_instant_mbps": round(instant_mbps, 3),

            # Data volume
            "bytes_sent": self._stats_bytes_sent,
            "bytes_received": self._stats_bytes_received,
            "bytes_total": total_bytes,
            "bytes_remaining": bytes_remaining,

            # Block-level counters
            "blocks_acked": self._stats_blocks_acked,
            "blocks_naked": self._stats_blocks_naked,
            "blocks_retransmitted": self._stats_blocks_retransmitted,
            "arq_timeouts": self._stats_arq_timeouts,

            # Integrity
            "crc_failures": self._stats_crc_failures,
            "error_rate": round(error_rate, 5),

            # File transfer progress
            "files_completed_send": self._stats_files_completed_send,
            "files_completed_recv": self._stats_files_completed_recv,
            "files_skipped": self._stats_files_skipped,
            "files_queued": len(self.files_to_send),
            "current_file": os.path.basename(self.current_file) if self.current_file else None,
            "current_file_size": self._stats_current_file_size,
            "transfer_progress": round(transfer_progress, 4),
            "transfer_eta_s": round(eta_seconds, 1),

            # Heartbeat
            "pings_sent": self._stats_pings_sent,
            "pongs_received": self._stats_pongs_received,

            # Config
            "block_size": self.block_size,
            "arq_timeout_s": self.arq_timeout,
            "heartbeat_interval_s": self.heartbeat_interval,

            # v2 — capabilities & observability
            "caps_version": self._negotiated_capabilities.version,
            "caps_wire_crc": self._negotiated_capabilities.wire_crc,
            "caps_reliable": self._negotiated_capabilities.is_reliable,
            "caps_max_block_size": self._negotiated_capabilities.max_block_size,
            "observers": len(self._subscriptions),
            "registered_handlers": len(self._packet_handlers),
        }

    async def send_chat(self, message: bytes):
        await self.framer.send_packet_immediate(PACK_CHAT_BLOCK, message)
        
    async def loop(self):
        # Initial Handshake. The READY payload carries the local capability
        # advertisement when enabled; legacy peers ignore it (backward-compatible).
        # Advertising is opt-in so the wire stays byte-identical by default.
        log.debug("Sending Handshake (R and Q)...")
        ready_payload = self._local_capabilities.encode() if self._advertise_capabilities else b""
        await self.framer.send_packet_immediate(PACK_READY, ready_payload)
        await self.framer.send_packet_immediate(PACK_READY_RECV, b"")
        
        recv_task = asyncio.create_task(self._recv_loop())
        send_task = asyncio.create_task(self._send_loop())
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            await asyncio.gather(recv_task, send_task, heartbeat_task)
        except Exception as e:
            log.error(f"WSLinkSession loop error: {e}\n{traceback.format_exc()}")
            self._set_state("DONE", reason="loop_error")

    async def _heartbeat_loop(self):
        """Send periodic PING to keep the connection alive and detect dead peers."""
        while self.state != "DONE":
            await asyncio.sleep(self.heartbeat_interval)
            if self.state == "DONE":
                break
            try:
                # Send PING with a MONOTONIC timestamp for RTT measurement. The
                # value is echoed back and compared by THIS peer only, so a
                # monotonic origin is both correct and immune to NTP steps.
                payload = struct.pack('<d', _mono())
                await self.framer.send_packet_immediate(PACK_PING, payload)
                self._stats_pings_sent += 1
                log.debug("Heartbeat PING sent")
            except Exception as e:
                log.warning(f"Heartbeat send failed: {e}")
                self._set_state("DONE", reason="heartbeat_send_failed")
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
                self._set_state("DONE", reason="idle_timeout")
                break
                
            if not packet:
                # EOF or dropped connection
                log.info("Connection closed by peer or read timeout.")
                self._set_state("DONE", reason="peer_closed")
                break
                
            pkt_type, payload = packet

            # Integrity guard: the framer returns (b'?', b'') on a CRC mismatch
            # or an invalid frame length (see WSLinkFramer.read_packet). Discard
            # the corrupt frame explicitly rather than letting b'?' fall through
            # to _handle_packet, where it would trip the state-machine guard and
            # log a misleading "rejected packet type" warning. Counting these
            # separately gives real integrity-error visibility in link stats.
            if pkt_type == b'?':
                self._stats_crc_failures += 1
                log.warning("CRC integrity failure detected, frame discarded.")
                self._emit(EventKind.INTEGRITY, reason="crc_or_length",
                           crc_failures=self._stats_crc_failures)
                continue

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
                    self._set_state("DONE", reason="sender_error")
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
        self._stats_current_file_size = size
        self._stats_transfer_start = _mono()
        self._emit(EventKind.TRANSFER, event="open", file=os.path.basename(filepath),
                   size=size, blocks=blocks)

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
            current_time = _mono()
            oldest_block = min(self.unacked_blocks.keys())
            send_time = self.block_send_times.get(oldest_block, current_time)
            if current_time - send_time > self.arq_timeout:
                log.warning(f"ARQ Timeout! Resending block {oldest_block}. Throttling window.")
                _win_before = self.window_size
                self.window_size = max(1, self.window_size // 2) # Halve window on timeout
                self._stats_window_shrinks += 1
                self._stats_arq_timeouts += 1
                self._stats_blocks_retransmitted += 1
                
                stored_payload = self.unacked_blocks[oldest_block]
                await self.framer.send_packet_immediate(PACK_DATA_BLOCK, stored_payload)
                self._stats_bytes_sent += len(stored_payload)
                self.block_send_times[oldest_block] = current_time # Reset timer
                self._emit(EventKind.CONGESTION, event="arq_timeout", reason="arq_timeout",
                           window_before=_win_before, window_after=self.window_size,
                           block=oldest_block)
                
        # Fill Window — buffer all data frames, flush once at the end
        sent_any = False
        fill_time = _mono()  # Single timestamp for entire burst
        while len(self.unacked_blocks) < self.window_size and self.next_block_num < self.total_blocks:
            chunk = self.current_fd.read(self.block_size)
            if not chunk:
                break
                
            seq_bytes = SequencePacket.pack(self.batch_index, self.next_block_num)
            payload = seq_bytes + chunk
            
            self.unacked_blocks[self.next_block_num] = payload
            self.block_send_times[self.next_block_num] = fill_time
            
            await self.framer.send_packet(PACK_DATA_BLOCK, payload)
            self._stats_bytes_sent += len(chunk)
            self.next_block_num += 1
            sent_any = True

        # Single flush for entire window fill
        if sent_any:
            await self.framer.flush()

        # EOF Handle
        if self.next_block_num >= self.total_blocks and not self.unacked_blocks:
            log.info(f"File {self.current_file} successfully transferred.")
            _done_file = os.path.basename(self.current_file) if self.current_file else None
            await self.framer.send_packet_immediate(PACK_CLOSE_FILE, b"")
            self.current_fd.close()
            self.current_file = None
            self.batch_index += 1
            self._stats_files_completed_send += 1
            self._stats_transfer_start = 0.0
            self._stats_current_file_size = 0
            self._emit(EventKind.TRANSFER, event="complete_send", file=_done_file,
                       files_completed=self._stats_files_completed_send)

    def _update_rtt(self, rtt: float):
        self.rtt_history.append(rtt)  # deque(maxlen=N) auto-evicts oldest
            
        avg_rtt = sum(self.rtt_history) / len(self.rtt_history)
        _win_before = self.window_size

        # BBR-style naive scale: if link is fast and window is full, increase window.
        if avg_rtt < 0.1 and len(self.unacked_blocks) >= self.window_size * 0.8:
            self.window_size = min(self.max_window_size, self.window_size + 1)
            self._stats_window_grows += 1
        elif avg_rtt > 0.5:
            # Bufferbloat detected, scale back gently
            self.window_size = max(1, int(self.window_size * 0.9))
            self._stats_window_shrinks += 1

        # Observability: RTT sample always; congestion event only on a window change.
        if self._subscriptions:
            self._emit(EventKind.RTT_SAMPLE, rtt_ms=round(rtt * 1000, 3),
                       avg_rtt_ms=round(avg_rtt * 1000, 3),
                       window=self.window_size, inflight=len(self.unacked_blocks))
            if self.window_size != _win_before:
                self._emit(EventKind.CONGESTION,
                           event="window_grow" if self.window_size > _win_before else "window_shrink",
                           reason="rtt_scale", window_before=_win_before,
                           window_after=self.window_size, avg_rtt_ms=round(avg_rtt * 1000, 3))

    async def _handle_packet(self, pkt_type: bytes, payload: bytes):
        # Consumer-registered handlers take precedence for their types. This is
        # the supported replacement for monkeypatching _handle_packet: e.g. the
        # socket proxy registers its lowercase channel types here instead of
        # wrapping this method. Builtins cannot be shadowed unless override=True.
        handler = self._packet_handlers.get(pkt_type)
        if handler is not None:
            await handler(pkt_type, payload)
            return

        # Heartbeat: PING/PONG allowed in any state (keepalive)
        if pkt_type == PACK_PING:
            # Respond with PONG (echo payload back for RTT measurement)
            await self.framer.send_packet_immediate(PACK_PONG, payload)
            log.debug("Heartbeat PONG sent (reply to PING)")
            return

        if pkt_type == PACK_PONG:
            # Measure RTT from ping timestamp (monotonic origin — see _heartbeat_loop).
            # NOTE: heartbeat RTT is surfaced for observability ONLY; it deliberately
            # does NOT feed the congestion window (that stays driven by data-block
            # ACKs, preserving pre-v2 behaviour). This is the "own probe" the timing
            # telemetry needs once ARQ/ACK RTT sampling is reduced.
            if len(payload) >= 8:
                ping_time = struct.unpack('<d', payload[:8])[0]
                rtt = _mono() - ping_time
                self._stats_pongs_received += 1
                if rtt >= 0 and self._subscriptions:
                    self._emit(EventKind.RTT_SAMPLE, source="heartbeat",
                               rtt_ms=round(rtt * 1000, 3), window=self.window_size,
                               inflight=len(self.unacked_blocks))
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
            # Parse a capability advertisement if the peer included one (READY only).
            # Legacy peers send an empty payload -> decode() returns None -> we keep
            # the conservative legacy negotiation (CRC on, ARQ on).
            if pkt_type == PACK_READY and payload:
                remote_caps = TransportCapabilities.decode(payload)
                if remote_caps is not None:
                    self._negotiated_capabilities = TransportCapabilities.negotiate(
                        self._local_capabilities, remote_caps)
                    log.info(f"WSLink capabilities negotiated: {self._negotiated_capabilities}")
            if self.state == "INIT":
                log.info("Handshake sync complete. Connection established.")
                self._set_state("TRANSFERRING", reason="handshake")
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
                    rtt = _mono() - self.block_send_times[ack_block]
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
                
                self._stats_blocks_acked += 1
                self._send_event.set()  # Wake sender — window space freed

        elif pkt_type == PACK_NAK_BLOCK:
            seq = SequencePacket.unpack(payload)
            if seq['batch'] == self.batch_index:
                nak_block = seq['block']
                if nak_block in self.unacked_blocks:
                    log.warning(f"Received NAK for block {nak_block}. Resending.")
                    _win_before = self.window_size
                    self.window_size = max(1, self.window_size // 2) # Halve on drop
                    self._stats_window_shrinks += 1
                    self._stats_blocks_naked += 1
                    self._stats_blocks_retransmitted += 1
                    stored_payload = self.unacked_blocks[nak_block]
                    await self.framer.send_packet_immediate(PACK_DATA_BLOCK, stored_payload)
                    self._stats_bytes_sent += len(stored_payload)
                    self.block_send_times[nak_block] = _mono()
                    self._emit(EventKind.CONGESTION, event="nak", reason="nak",
                               window_before=_win_before, window_after=self.window_size,
                               block=nak_block)
                
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
                self._stats_bytes_received += len(chunk)
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
            self._stats_files_skipped += 1
            self._stats_transfer_start = 0.0
            self._stats_current_file_size = 0
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
                self._stats_files_completed_recv += 1
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
