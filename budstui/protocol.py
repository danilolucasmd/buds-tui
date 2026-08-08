"""Galaxy Buds SPP wire protocol.

Frame layout (non-legacy models, i.e. Buds Pro and newer)::

    FD | size_lo size_hi | msg_id | payload... | crc_lo crc_hi | DD
    ^^ SOM               ^^ int16 LE                            ^^ EOM

``size`` counts the message id, the payload and the CRC (``len(payload) + 3``).
The top bits of the header carry flags: ``0x2000`` marks a fragment and
``0x1000`` marks a response. The CRC is CRC-16/CCITT (XMODEM: poly 0x1021,
init 0) over ``msg_id + payload``, transmitted little endian.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

SOM = 0xFD
EOM = 0xDD
# The "SMEP" alternative framing some firmwares expose on a second channel.
SMEP_SOM = 0xFC
SMEP_EOM = 0xCC

FLAG_FRAGMENT = 0x2000
FLAG_RESPONSE = 0x1000
SIZE_MASK = 0x3FF

#: Vendor SPP service advertised by Buds Pro and newer.
UUID_SPP_NEW = "2e73a4ad-332d-41fc-90e2-16bef06523f2"
#: Plain Serial Port service used by the original Buds and Buds+.
UUID_SPP_LEGACY = "00001101-0000-1000-8000-00805f9b34fb"


class MsgId(enum.IntEnum):
    """Message ids used by this client (a subset of the full protocol)."""

    STATUS_UPDATED = 96
    EXTENDED_STATUS_UPDATED = 97
    NOISE_CONTROLS_UPDATE = 119
    NOISE_CONTROLS = 120
    SET_AMBIENT_MODE = 128
    CUSTOMIZE_AMBIENT_SOUND = 130
    NOISE_REDUCTION_LEVEL = 131
    AMBIENT_VOLUME = 132
    MANAGER_INFO = 136
    EXTRA_HIGH_AMBIENT = 150
    SET_NOISE_REDUCTION = 152
    NOISE_REDUCTION_MODE_UPDATE = 155


class NoiseControlMode(enum.IntEnum):
    OFF = 0
    ANC = 1
    AMBIENT = 2
    ADAPTIVE = 3

    @property
    def label(self) -> str:
        return {
            NoiseControlMode.OFF: "off",
            NoiseControlMode.ANC: "active noise canceling",
            NoiseControlMode.AMBIENT: "ambient sound",
            NoiseControlMode.ADAPTIVE: "adaptive",
        }[self]


class Placement(enum.IntEnum):
    """Per-earbud placement, as reported in the status payload."""

    DISCONNECTED = 0
    WEARING = 1
    IDLE = 2
    IN_CASE = 3
    CHARGING = 4

    @classmethod
    def parse(cls, value: int) -> "Placement":
        try:
            return cls(value)
        except ValueError:
            return cls.DISCONNECTED


class InvalidPacket(Exception):
    """Raised when a frame cannot be decoded."""


def _build_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def crc16_ccitt(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (_CRC_TABLE[((crc >> 8) ^ byte) & 0xFF] ^ (crc << 8)) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Message:
    id: int
    payload: bytes = b""
    is_response: bool = False
    is_fragment: bool = False

    @property
    def name(self) -> str:
        try:
            return MsgId(self.id).name
        except ValueError:
            return f"UNKNOWN_{self.id}"

    def encode(self, som: int = SOM, eom: int = EOM) -> bytes:
        size = len(self.payload) + 3
        if size > SIZE_MASK:
            raise ValueError(f"payload too large: {len(self.payload)} bytes")
        header = size
        if self.is_fragment:
            header |= FLAG_FRAGMENT
        if self.is_response:
            header |= FLAG_RESPONSE
        body = bytes([self.id]) + self.payload
        crc = crc16_ccitt(body)
        return bytes([som]) + header.to_bytes(2, "little") + body + crc.to_bytes(2, "little") + bytes([eom])

    def __str__(self) -> str:
        return f"{self.name}({self.payload.hex(' ') or '-'})"


def decode(buffer: bytes, som: int = SOM, eom: int = EOM) -> tuple[Message | None, int]:
    """Decode one frame from the front of *buffer*.

    Returns ``(message, consumed)``. ``message`` is ``None`` when the buffer
    does not yet hold a complete frame, in which case ``consumed`` is the number
    of leading bytes to discard (garbage before the next SOM).
    """
    start = buffer.find(som)
    if start == -1:
        return None, len(buffer)
    if start > 0:
        return None, start
    if len(buffer) < 6:
        return None, 0

    header = int.from_bytes(buffer[1:3], "little")
    size = header & SIZE_MASK
    if size < 3:
        raise InvalidPacket(f"size {size} is too small")

    total = size + 4  # SOM + header(2) + size + EOM
    if len(buffer) < total:
        return None, 0

    msg_id = buffer[3]
    payload = buffer[4 : 1 + size]
    crc_wire = int.from_bytes(buffer[1 + size : 3 + size], "little")
    if buffer[total - 1] != eom:
        raise InvalidPacket("missing end-of-message byte")

    crc_calc = crc16_ccitt(bytes([msg_id]) + payload)
    if crc_calc != crc_wire:
        raise InvalidPacket(f"checksum mismatch: {crc_calc:#06x} != {crc_wire:#06x}")

    message = Message(
        id=msg_id,
        payload=payload,
        # Note: the flag is set on requests, so an absent flag means response.
        is_response=not header & FLAG_RESPONSE,
        is_fragment=bool(header & FLAG_FRAGMENT),
    )
    return message, total


def decode_stream(buffer: bytearray, som: int = SOM, eom: int = EOM) -> list[Message]:
    """Drain every complete frame from *buffer*, mutating it in place.

    Undecodable frames are skipped by resynchronising on the next SOM byte.
    """
    messages: list[Message] = []
    while buffer:
        try:
            message, consumed = decode(bytes(buffer), som, eom)
        except InvalidPacket:
            # Resynchronise: drop this SOM and look for the next one.
            nxt = buffer.find(som, 1)
            del buffer[: len(buffer) if nxt == -1 else nxt]
            continue
        if consumed == 0:
            break
        del buffer[:consumed]
        if message is not None:
            messages.append(message)
    return messages
