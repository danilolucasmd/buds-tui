"""SDP record parsing, using a real response from a Buds4 Pro."""

from budstui.sdp import _attributes, _parse_element, _rfcomm_channel

# ServiceSearchAttributeResponse body for UUID 2e73a4ad-... (service "GEARMANAGER").
REAL_RECORD = bytes.fromhex(
    "36 00 47 36 00 44 09 00 00 0a 00 01 00 08 09 00 04 35 0c 35 03 19 01 00"
    " 35 05 19 00 03 08 1b 09 00 09 35 16 35 14 1c 2e 73 a4 ad 33 2d 41 fc 90"
    " e2 16 be f0 65 23 f2 09 01 02 09 01 00 25 0b 47 45 41 52 4d 41 4e 41 47"
    " 45 52"
)


def test_parses_rfcomm_channel():
    records, _ = _parse_element(REAL_RECORD)
    channels = [
        _rfcomm_channel(_attributes(record).get(0x0004)) for record in records
    ]
    assert channels == [27]


def test_service_name_attribute():
    records, _ = _parse_element(REAL_RECORD)
    assert _attributes(records[0])[0x0100] == b"GEARMANAGER"


def test_missing_protocol_list_returns_none():
    assert _rfcomm_channel(None) is None
    assert _rfcomm_channel([]) is None
