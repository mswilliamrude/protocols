"""WSLink transport capability negotiation.

Capabilities are advertised in the ``READY`` handshake payload. Legacy peers send
an empty ``READY`` payload and *ignore* any payload they receive
(``wslink.py`` READY handler reads no payload), so advertising is
backward-compatible: a v2 peer that receives an empty / non-magic payload falls
back to :meth:`TransportCapabilities.legacy` — i.e. current behaviour (wire CRC on,
ARQ on).

**Fail-safe negotiation.** A behaviour-relaxing capability (skip ARQ, skip wire
CRC, skip reorder buffer) is enabled *only if both peers advertise it*. The wire
CRC is only disabled when **both** sides explicitly agree; if either side wants it,
it stays on. This guarantees that mixing a v2 peer with a conservative/legacy peer
never weakens integrity or reliability.

Wire format of the ``READY`` payload (little-endian), 11 bytes::

    [4 bytes magic b'WLC1'][1 byte version][2 bytes flags][4 bytes max_block_size]

The Rust core (`crates/pyprotocols-core/src/capabilities.rs`) implements the
byte-identical encoder/decoder so a Rust-backed session negotiates the same way.
See ``docs/design/WSLINK_V2_ARCHITECTURE.md`` §3.1 / §4.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

#: Magic prefix identifying a capability advertisement in a READY payload.
MAGIC = b"WLC1"
#: Wire format version for the capability block.
VERSION = 1

# Fixed 11-byte header: magic(4) + version(1) + flags(2) + max_block_size(4).
_HEADER = struct.Struct("<4sBHI")

# ── Capability flag bits (u16) ───────────────────────────────────────────────
FLAG_RELIABLE = 0x0001            # transport guarantees delivery -> ARQ may be skipped
FLAG_PROVIDES_INTEGRITY = 0x0002  # transport authenticates data -> wire CRC may be skipped
FLAG_ORDERED = 0x0004             # transport guarantees ordering -> no reorder buffer needed
FLAG_WIRE_CRC = 0x0008            # wire CRC currently ENABLED (default on)


@dataclass(frozen=True)
class TransportCapabilities:
    """Negotiated (or advertised) transport capabilities for a WSLink session.

    Defaults are deliberately conservative and equal to pre-v2 behaviour:
    reliability/integrity/ordering are *not* assumed, and the wire CRC stays on.
    """

    is_reliable: bool = False
    provides_integrity: bool = False
    is_ordered: bool = False
    wire_crc: bool = True          # default ON -> preserves current behaviour
    max_block_size: int = 4096
    version: int = 0               # 0 = legacy / no capabilities advertised

    @classmethod
    def legacy(cls) -> "TransportCapabilities":
        """The conservative default matching pre-v2 behaviour (CRC on, ARQ on)."""
        return cls()

    def to_flags(self) -> int:
        """Pack the boolean capabilities into the u16 flag bitmask."""
        flags = 0
        if self.is_reliable:
            flags |= FLAG_RELIABLE
        if self.provides_integrity:
            flags |= FLAG_PROVIDES_INTEGRITY
        if self.is_ordered:
            flags |= FLAG_ORDERED
        if self.wire_crc:
            flags |= FLAG_WIRE_CRC
        return flags

    def encode(self) -> bytes:
        """Serialise to the 11-byte READY-payload wire format."""
        return _HEADER.pack(MAGIC, VERSION, self.to_flags(), int(self.max_block_size) & 0xFFFFFFFF)

    @classmethod
    def decode(cls, payload: bytes):
        """Parse a READY payload.

        Returns a :class:`TransportCapabilities` if ``payload`` is a valid
        capability advertisement, or ``None`` for a legacy/empty/unrecognised
        payload (caller should fall back to :meth:`legacy`).
        """
        if not payload or len(payload) < _HEADER.size:
            return None
        magic, version, flags, max_block = _HEADER.unpack(payload[: _HEADER.size])
        if magic != MAGIC:
            return None
        return cls(
            is_reliable=bool(flags & FLAG_RELIABLE),
            provides_integrity=bool(flags & FLAG_PROVIDES_INTEGRITY),
            is_ordered=bool(flags & FLAG_ORDERED),
            wire_crc=bool(flags & FLAG_WIRE_CRC),
            max_block_size=int(max_block),
            version=int(version),
        )

    @staticmethod
    def negotiate(
        local: "TransportCapabilities",
        remote: "TransportCapabilities",
    ) -> "TransportCapabilities":
        """Combine local and remote advertisements into the effective capabilities.

        A relaxing capability is enabled only if **both** peers advertise it. The
        wire CRC is disabled only if **both** peers set ``wire_crc=False`` (any side
        wanting it keeps it on). ``max_block_size`` is the minimum of both ceilings.
        """
        return TransportCapabilities(
            is_reliable=local.is_reliable and remote.is_reliable,
            provides_integrity=local.provides_integrity and remote.provides_integrity,
            is_ordered=local.is_ordered and remote.is_ordered,
            # Fail-safe: CRC stays on unless BOTH peers opt out.
            wire_crc=local.wire_crc or remote.wire_crc,
            max_block_size=min(int(local.max_block_size), int(remote.max_block_size)),
            version=min(local.version, remote.version) if remote.version else local.version,
        )
