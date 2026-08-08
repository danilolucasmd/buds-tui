"""Minimal SDP client, used to resolve the RFCOMM channel of a service.

BlueZ does not expose remote SDP records over D-Bus, so we query the device
directly over L2CAP (PSM 1) and parse the ProtocolDescriptorList ourselves.
"""

from __future__ import annotations

import socket
import struct
import uuid as uuidlib

PSM_SDP = 1

_PDU_SERVICE_SEARCH_ATTR_REQ = 0x06
_PDU_SERVICE_SEARCH_ATTR_RSP = 0x07
_PDU_ERROR_RSP = 0x01

_ATTR_PROTOCOL_DESCRIPTOR_LIST = 0x0004

_UUID_L2CAP = 0x0100
_UUID_RFCOMM = 0x0003

# Data element type codes (the high 5 bits of a descriptor byte).
_TYPE_NIL, _TYPE_UINT, _TYPE_INT, _TYPE_UUID = 0, 1, 2, 3
_TYPE_STR, _TYPE_BOOL, _TYPE_DES, _TYPE_DEA, _TYPE_URL = 4, 5, 6, 7, 8

_BASE_UUID_SUFFIX = uuidlib.UUID("00000000-0000-1000-8000-00805f9b34fb").bytes[4:]


class SdpError(Exception):
    pass


def _element_size(data: bytes, offset: int) -> tuple[int, int, int]:
    """Return ``(type, value_offset, value_length)`` for the element at *offset*."""
    descriptor = data[offset]
    type_ = descriptor >> 3
    size_index = descriptor & 0x07
    offset += 1

    if type_ == _TYPE_NIL:
        return type_, offset, 0
    if size_index < 5:
        # Sizes 1, 2, 4, 8, 16 bytes -- except uint/int, where index 0 means 1 byte.
        return type_, offset, 1 << size_index
    width = 1 << (size_index - 5)  # 5 -> uint8, 6 -> uint16, 7 -> uint32
    length = int.from_bytes(data[offset : offset + width], "big")
    return type_, offset + width, length


def _parse_element(data: bytes, offset: int = 0):
    """Parse one data element, returning ``(value, next_offset)``."""
    type_, value_at, length = _element_size(data, offset)
    end = value_at + length
    raw = data[value_at:end]

    if type_ == _TYPE_NIL:
        return None, end
    if type_ in (_TYPE_UINT, _TYPE_BOOL):
        return int.from_bytes(raw, "big"), end
    if type_ == _TYPE_INT:
        return int.from_bytes(raw, "big", signed=True), end
    if type_ == _TYPE_UUID:
        if length == 2:
            return int.from_bytes(raw, "big"), end
        if length == 4:
            return int.from_bytes(raw, "big"), end
        return uuidlib.UUID(bytes=raw), end
    if type_ in (_TYPE_STR, _TYPE_URL):
        return raw, end
    if type_ in (_TYPE_DES, _TYPE_DEA):
        items = []
        inner = value_at
        while inner < end:
            item, inner = _parse_element(data, inner)
            items.append(item)
        return items, end
    raise SdpError(f"unsupported data element type {type_}")


def _encode_uuid(service_uuid: str) -> bytes:
    parsed = uuidlib.UUID(service_uuid)
    if parsed.bytes[4:] == _BASE_UUID_SUFFIX:
        # A short UUID from the Bluetooth base range: send it as 32 bits.
        return b"\x1a" + parsed.bytes[:4]
    return b"\x1c" + parsed.bytes


def _uuid_matches(candidate, short_uuid: int) -> bool:
    """Compare a parsed SDP UUID against a 16-bit Bluetooth short UUID."""
    if isinstance(candidate, int):
        # 16- and 32-bit UUIDs are parsed as plain integers.
        return candidate == short_uuid
    if isinstance(candidate, uuidlib.UUID):
        return (
            candidate.bytes[4:] == _BASE_UUID_SUFFIX
            and int.from_bytes(candidate.bytes[:4], "big") == short_uuid
        )
    return False


def _attributes(record) -> dict[int, object]:
    """Turn a flat ``[id, value, id, value, ...]`` record into a dict."""
    if not isinstance(record, list):
        return {}
    return {
        record[i]: record[i + 1]
        for i in range(0, len(record) - 1, 2)
        if isinstance(record[i], int)
    }


def _rfcomm_channel(protocol_descriptor_list) -> int | None:
    """Pull the RFCOMM channel out of a ProtocolDescriptorList."""
    if not isinstance(protocol_descriptor_list, list):
        return None
    for descriptor in protocol_descriptor_list:
        if not isinstance(descriptor, list) or not descriptor:
            continue
        if _uuid_matches(descriptor[0], _UUID_RFCOMM):
            for param in descriptor[1:]:
                if isinstance(param, int):
                    return param
    return None


def find_rfcomm_channel(address: str, service_uuid: str, timeout: float = 8.0) -> int | None:
    """Resolve the RFCOMM channel *service_uuid* is served on, or ``None``."""
    search_pattern = _encode_uuid(service_uuid)
    request = (
        b"\x35" + bytes([len(search_pattern)]) + search_pattern  # ServiceSearchPattern
        + struct.pack(">H", 0xFFFF)                             # MaximumAttributeByteCount
        + b"\x35\x05\x0a"                                       # AttributeIDList: one range
        + struct.pack(">HH", _ATTR_PROTOCOL_DESCRIPTOR_LIST, _ATTR_PROTOCOL_DESCRIPTOR_LIST)
    )

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    sock.settimeout(timeout)
    try:
        sock.connect((address, PSM_SDP))
        attribute_bytes = b""
        continuation = b"\x00"
        transaction = 1
        while True:
            params = request + continuation
            sock.send(
                struct.pack(">BHH", _PDU_SERVICE_SEARCH_ATTR_REQ, transaction, len(params)) + params
            )
            response = sock.recv(4096)
            if len(response) < 5:
                raise SdpError("short SDP response")
            pdu_id, _tid, param_len = struct.unpack(">BHH", response[:5])
            if pdu_id == _PDU_ERROR_RSP:
                raise SdpError(f"SDP error {response[5:7].hex()}")
            if pdu_id != _PDU_SERVICE_SEARCH_ATTR_RSP:
                raise SdpError(f"unexpected SDP PDU {pdu_id:#04x}")

            body = response[5 : 5 + param_len]
            byte_count = struct.unpack(">H", body[:2])[0]
            attribute_bytes += body[2 : 2 + byte_count]

            continuation_field = body[2 + byte_count :]
            if not continuation_field or continuation_field[0] == 0:
                break
            continuation = continuation_field[: 1 + continuation_field[0]]
            transaction += 1

        if not attribute_bytes:
            return None
        records, _ = _parse_element(attribute_bytes)
    finally:
        sock.close()

    # records is a DES of per-service attribute lists: [attr_id, attr_value, ...].
    if not isinstance(records, list):
        return None
    for record in records:
        channel = _rfcomm_channel(_attributes(record).get(_ATTR_PROTOCOL_DESCRIPTOR_LIST))
        if channel is not None:
            return channel
    return None
