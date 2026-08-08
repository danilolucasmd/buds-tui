"""Protocol tests, built around frames actually captured from a Buds4 Pro."""

import pytest

from budstui.protocol import (
    EOM,
    SOM,
    InvalidPacket,
    Message,
    MsgId,
    NoiseControlMode,
    crc16_ccitt,
    decode,
    decode_stream,
)

# A real EXTENDED_STATUS_UPDATED frame pushed by the earbuds on connect.
REAL_FRAME = bytes.fromhex(
    "fd 41 00 61 04 0d 46 48 01 00 11 00 00 00 ff 22 00 00 67 01 67 01 07 00"
    " 04 dd 00 04 04 10 00 00 00 00 11 02 00 00 00 00 00 00 00 00 00 00 00 00"
    " 00 00 01 00 01 01 00 00 00 ff 01 01 00 00 00 00 00 01 89 14 dd"
)


def test_crc_matches_captured_frame():
    payload = REAL_FRAME[4:-3]
    assert crc16_ccitt(bytes([MsgId.EXTENDED_STATUS_UPDATED]) + payload) == 0x1489


def test_decode_captured_frame():
    message, consumed = decode(REAL_FRAME)
    assert consumed == len(REAL_FRAME) == 69
    assert message is not None
    assert message.id == MsgId.EXTENDED_STATUS_UPDATED
    assert len(message.payload) == 62
    assert message.payload[2] == 0x46  # left battery
    assert message.payload[12] == 0    # noise control mode: off


def test_encode_round_trip():
    original = Message(MsgId.NOISE_CONTROLS, bytes([NoiseControlMode.ANC]))
    encoded = original.encode()
    assert encoded[0] == SOM and encoded[-1] == EOM
    assert len(encoded) == 8  # SOM + header(2) + id + payload(1) + crc(2) + EOM
    decoded, consumed = decode(encoded)
    assert consumed == len(encoded)
    assert decoded.id == MsgId.NOISE_CONTROLS
    assert decoded.payload == bytes([1])


def test_empty_payload_round_trip():
    encoded = Message(MsgId.EXTENDED_STATUS_UPDATED).encode()
    decoded, _ = decode(encoded)
    assert decoded.payload == b""


def test_partial_frame_is_not_consumed():
    message, consumed = decode(REAL_FRAME[:20])
    assert message is None and consumed == 0


def test_leading_garbage_is_skipped():
    message, consumed = decode(b"\x00\x11" + REAL_FRAME)
    assert message is None and consumed == 2


def test_bad_checksum_raises():
    corrupt = bytearray(REAL_FRAME)
    corrupt[10] ^= 0xFF
    with pytest.raises(InvalidPacket):
        decode(bytes(corrupt))


def test_decode_stream_handles_two_frames_and_a_remainder():
    buffer = bytearray(REAL_FRAME + REAL_FRAME + REAL_FRAME[:10])
    messages = decode_stream(buffer)
    assert len(messages) == 2
    assert len(buffer) == 10  # the partial frame is kept for the next read


def test_decode_stream_resynchronises_after_corruption():
    corrupt = bytearray(REAL_FRAME)
    corrupt[10] ^= 0xFF
    buffer = bytearray(bytes(corrupt) + REAL_FRAME)
    messages = decode_stream(buffer)
    assert [m.id for m in messages] == [MsgId.EXTENDED_STATUS_UPDATED]


def test_oversized_payload_rejected():
    with pytest.raises(ValueError):
        Message(MsgId.NOISE_CONTROLS, b"\x00" * 2000).encode()
