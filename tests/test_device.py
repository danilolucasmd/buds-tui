"""State parsing, checked against payloads captured from a Buds4 Pro."""

from budstui.device import BudsConnection, profile_for
from budstui.protocol import NoiseControlMode, Placement

# EXTENDED_STATUS_UPDATED payload: mode off, ambient level 4, ANC level 4.
EXTENDED = bytes.fromhex(
    "04 0d 46 48 01 00 11 00 00 00 ff 22 00 00 67 01 67 01 07 00 04 dd 00 04"
    " 04 10 00 00 00 00 11 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00"
    " 01 01 00 00 00 ff 01 01 00 00 00 00 00 01"
)
# STATUS_UPDATED payload: batteries 69/71, both worn, case at 43%.
STATUS = bytes.fromhex("01 45 47 01 00 11 2b 00")


def connection() -> BudsConnection:
    return BudsConnection("A8:D1:62:8A:A2:1F", "Danilo's Buds4 Pro")


def test_extended_status_parsing():
    conn = connection()
    conn._apply_extended_status(EXTENDED)
    state = conn.state
    assert (state.battery_left, state.battery_right) == (70, 72)
    assert state.placement_left == Placement.WEARING
    assert state.placement_right == Placement.WEARING
    assert state.noise_mode == NoiseControlMode.OFF
    assert state.ambient_level == 4
    assert state.anc_level == 4


def test_short_status_parsing():
    conn = connection()
    conn._apply_status(STATUS)
    state = conn.state
    assert (state.battery_left, state.battery_right) == (69, 71)
    assert state.battery_case == 43
    assert not state.charging_left and not state.charging_case


def test_charging_bits():
    conn = connection()
    conn._apply_status(bytes.fromhex("01 45 47 01 00 11 2b 15"))
    assert conn.state.charging_left      # 0x10
    assert conn.state.charging_right     # 0x04
    assert conn.state.charging_case      # 0x01
    assert conn.state.earbuds_charging


def test_case_battery_survives_an_extended_status_without_one():
    conn = connection()
    conn._apply_status(STATUS)
    conn._apply_extended_status(EXTENDED)  # carries 0 for the case
    assert conn.state.battery_case == 43


def test_acknowledgement_updates_state():
    from budstui.protocol import MsgId

    conn = connection()
    conn._apply_ack(MsgId.NOISE_CONTROLS, int(NoiseControlMode.ADAPTIVE))
    assert conn.state.noise_mode == NoiseControlMode.ADAPTIVE
    conn._apply_ack(MsgId.AMBIENT_VOLUME, 2)
    assert conn.state.ambient_level == 2
    conn._apply_ack(MsgId.NOISE_REDUCTION_LEVEL, 3)
    assert conn.state.anc_level == 3


def test_battery_earbuds_uses_the_lower_reading():
    conn = connection()
    conn._apply_status(STATUS)
    assert conn.state.battery_earbuds == 69


def test_profile_detection():
    assert profile_for("Danilo's Buds4 Pro").name == "Buds4 Pro"
    assert profile_for("Galaxy Buds2 Pro").name == "Buds2 Pro"
    assert profile_for("Galaxy Buds Live").name == "Buds Live"
    assert profile_for("something else").name == "Galaxy Buds"


def test_buds2_pro_has_no_adaptive_mode():
    assert NoiseControlMode.ADAPTIVE not in profile_for("Galaxy Buds2 Pro").modes
    assert NoiseControlMode.ADAPTIVE in profile_for("Buds4 Pro").modes
