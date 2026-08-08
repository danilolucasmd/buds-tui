"""The settings registry: decoding, inversion, and value stepping."""

import pytest

from budstui.settings import (
    BALANCE_CENTER,
    BY_KEY,
    CHOICE,
    SETTINGS,
    SLIDER,
    TOGGLE,
    read_all,
    visible,
)

# The extended-status payload captured from a Buds4 Pro.
PAYLOAD = bytes.fromhex(
    "04 0d 42 44 01 00 11 00 00 00 ff 22 00 00 67 01 67 01 07 00 04 dd 00 04"
    " 04 10 00 00 00 00 11 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00"
    " 01 01 00 00 00 ff 01 01 00 00 00 00 00 01"
)


def test_every_setting_has_a_unique_key_and_message():
    assert len({s.key for s in SETTINGS}) == len(SETTINGS)
    assert len({s.msg for s in SETTINGS}) == len(SETTINGS)


def test_reads_real_payload():
    values = read_all(PAYLOAD)
    assert values["conversation_detect"] == 0
    assert values["sidetone"] == 0
    assert values["stereo_balance"] == BALANCE_CENTER
    # Reported as 0 on the wire, which means enabled.
    assert values["seamless_connection"] == 1


def test_write_only_settings_are_absent_until_acknowledged():
    assert "gaming_mode" not in read_all(PAYLOAD)
    assert BY_KEY["gaming_mode"].read(PAYLOAD) is None


def test_inverted_settings_round_trip():
    setting = BY_KEY["seamless_connection"]
    assert setting.invert
    for value in (0, 1):
        assert setting.decode(setting.encode(value)) == value
    assert setting.encode(1) == 0  # enabled is 0 on the wire


def test_plain_settings_round_trip():
    for setting in SETTINGS:
        if setting.kind is not TOGGLE or setting.invert:
            continue
        for value in (0, 1):
            assert setting.decode(setting.encode(value)) == value


def test_toggle_adjust_flips_either_direction():
    setting = BY_KEY["sidetone"]
    assert setting.adjust(0, 1) == 1
    assert setting.adjust(1, 1) == 0
    assert setting.adjust(0, -1) == 1


def test_choice_cycles_and_wraps():
    setting = BY_KEY["conversation_timeout"]
    assert setting.kind is CHOICE
    assert setting.adjust(0, 1) == 1
    assert setting.adjust(2, 1) == 0
    assert setting.adjust(0, -1) == 2


def test_slider_clamps_at_both_ends():
    setting = BY_KEY["stereo_balance"]
    assert setting.kind is SLIDER
    assert setting.adjust(setting.maximum, 1) == setting.maximum
    assert setting.adjust(setting.minimum, -1) == setting.minimum


def test_display_labels():
    assert BY_KEY["sidetone"].display(1) == "on"
    assert BY_KEY["sidetone"].display(0) == "off"
    assert BY_KEY["sidetone"].display(None) == "--"
    assert BY_KEY["conversation_timeout"].display(1) == "10 sec"
    balance = BY_KEY["stereo_balance"]
    assert balance.display(BALANCE_CENTER) == "center"
    assert balance.display(0) == "L 100%"
    assert balance.display(balance.maximum) == "R 100%"


def test_dependent_setting_is_hidden_when_its_parent_is_off():
    keys = lambda values: [s.key for s in visible(values)]
    assert "conversation_timeout" not in keys({"conversation_detect": 0})
    assert "conversation_timeout" in keys({"conversation_detect": 1})


def test_unreadable_offsets_do_not_crash_on_short_payloads():
    for setting in SETTINGS:
        assert setting.read(b"\x00\x01") is None or setting.offset in (0, 1)
