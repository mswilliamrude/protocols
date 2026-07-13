"""WSLink protocol observability — event types and observer interface.

Object-oriented and zero-cost when unused: the session only constructs and
dispatches :class:`ProtocolEvent` instances when at least one
:class:`SessionObserver` is subscribed, and frame-level events honour a
per-subscription sample rate so per-frame observation never becomes the
bottleneck at wire speed.

See ``docs/design/WSLINK_EXTENSION_API.md`` §4 for the design rationale.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

log = logging.getLogger(__name__)


class EventKind:
    """String constants for protocol event kinds.

    Kept as plain strings (not an ``enum``) for cheap cross-language parity with
    the Rust core, which exposes the same identifiers.
    """

    STATE_CHANGE = "state_change"
    CONGESTION = "congestion"
    RTT_SAMPLE = "rtt_sample"
    FRAME = "frame"
    INTEGRITY = "integrity"
    BUFFER = "buffer"
    CHANNEL = "channel"
    TRANSFER = "transfer"

    ALL = frozenset({
        STATE_CHANGE, CONGESTION, RTT_SAMPLE, FRAME,
        INTEGRITY, BUFFER, CHANNEL, TRANSFER,
    })


@dataclass(frozen=True)
class ProtocolEvent:
    """An immutable observation of protocol-internal behaviour.

    Attributes:
        kind: one of :class:`EventKind`.
        ts_monotonic: ``time.monotonic()`` timestamp (interval-safe; never wall clock).
        session_id: identifier of the emitting session.
        payload: kind-specific fields (see WSLINK_EXTENSION_API.md §4.3).
    """

    kind: str
    ts_monotonic: float
    session_id: str
    payload: Dict[str, Any] = field(default_factory=dict)


class SessionObserver(ABC):
    """Base class for objects that observe protocol events.

    Observers are **synchronous** and **best-effort**: :meth:`on_event` must not
    block or perform slow work on the hot path — hand off to a queue if async work
    is required. Any exception raised is caught and logged by the session and is
    never propagated into the protocol loop.
    """

    @abstractmethod
    def on_event(self, event: ProtocolEvent) -> None:
        """Handle a single protocol event. Must be fast and non-blocking."""
        raise NotImplementedError


class CallbackObserver(SessionObserver):
    """Adapts a plain callable to the :class:`SessionObserver` interface.

    Convenience for consumers that do not want to subclass. The callable receives
    the :class:`ProtocolEvent` and must obey the same non-blocking contract.
    """

    def __init__(self, callback: Callable[[ProtocolEvent], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback

    def on_event(self, event: ProtocolEvent) -> None:
        self._callback(event)


@dataclass
class Subscription:
    """A registered observer plus its delivery policy.

    Attributes:
        observer: the :class:`SessionObserver` to notify.
        sample_rate: for high-frequency (frame-level) events, deliver only every
            Nth event. ``1`` delivers every event. Non-frame events are always
            delivered regardless of ``sample_rate``.
        level: advisory verbosity filter ("debug"/"info"/"warning"); the session
            may use it to suppress low-value events.
    """

    observer: SessionObserver
    sample_rate: int = 1
    level: str = "info"
    _frame_counter: int = 0

    def wants(self, kind: str) -> bool:
        """Return True if an event of ``kind`` should be delivered to this
        subscription, advancing the frame sampler when applicable."""
        if kind != EventKind.FRAME:
            return True
        # Frame events are sampled to stay cheap at wire speed.
        self._frame_counter += 1
        if self.sample_rate <= 1:
            return True
        return (self._frame_counter % self.sample_rate) == 0
