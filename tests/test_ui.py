"""Panel layout logic, including how it adapts to width."""

from budstui import ui
from budstui.device import BudsState, profile_for
from budstui.protocol import NoiseControlMode, Placement


def state(mode=NoiseControlMode.OFF, *, connected=True, **kwargs) -> BudsState:
    return BudsState(
        name="Danilo's Buds4 Pro", profile=profile_for("Buds4 Pro"),
        connected=connected, noise_mode=mode, **kwargs
    )


# -- rows -------------------------------------------------------------------


def test_connection_row_is_always_first():
    for connected in (True, False):
        rows = ui.build_rows(state(connected=connected), has_sink=True)
        assert rows[0].kind == ui.ROW_CONNECTION


def test_only_the_connection_row_is_navigable_while_disconnected():
    rows = ui.build_rows(state(connected=False), has_sink=False)
    assert [r.kind for r in rows] == [ui.ROW_CONNECTION]


def test_level_row_only_appears_for_adjustable_modes():
    kinds = lambda m: [r.kind for r in ui.build_rows(state(m), has_sink=True)]
    assert ui.ROW_LEVEL not in kinds(NoiseControlMode.OFF)
    assert ui.ROW_LEVEL not in kinds(NoiseControlMode.ADAPTIVE)
    assert ui.ROW_LEVEL in kinds(NoiseControlMode.AMBIENT)
    assert ui.ROW_LEVEL in kinds(NoiseControlMode.ANC)


def test_volume_row_needs_a_sink():
    assert ui.ROW_VOLUME not in [r.kind for r in ui.build_rows(state(), has_sink=False)]
    assert ui.build_rows(state(), has_sink=True)[-1].kind == ui.ROW_VOLUME


def test_rows_carry_their_group():
    rows = ui.build_rows(state(NoiseControlMode.ANC), has_sink=True)
    groups = [r.group for r in rows]
    assert groups[0] == ui.GROUP_CONNECTION
    assert ui.GROUP_MODE in groups and ui.GROUP_LEVEL in groups and ui.GROUP_VOLUME in groups


# -- levels -----------------------------------------------------------------


def test_level_title_and_value_track_the_mode():
    ambient = state(NoiseControlMode.AMBIENT, ambient_level=2, anc_level=4)
    assert ui.level_title(ambient) == "ambient sound level"
    assert ui.level_value(ambient) == 2
    assert ui.level_levels(ambient) == 5

    anc = state(NoiseControlMode.ANC, ambient_level=2, anc_level=4)
    assert ui.level_title(anc) == "noise canceling level"
    assert ui.level_value(anc) == 4


# -- primitives -------------------------------------------------------------


def test_track_knob_position():
    assert ui._track(0, 100, 20).plain.index(ui.KNOB) == 0
    assert ui._track(100, 100, 20).plain.index(ui.KNOB) == 19
    assert len(ui._track(50, 100, 20).plain) == 20


def test_bar_handles_unknown_and_clamps():
    assert ui._bar(None, 10).plain == ui.EMPTY * 10
    assert ui._bar(150, 10).plain == ui.FILLED * 10
    assert ui._bar(0, 10).plain == ui.EMPTY * 10


def test_truncate():
    assert ui._truncate("abcdef", 10) == "abcdef"
    assert ui._truncate("abcdef", 4) == "abc…"
    assert ui._truncate("abcdef", 0) == ""


# -- responsiveness ---------------------------------------------------------

WIDTHS = [80, 60, 46, 34, 26, 20]


def test_nothing_overflows_at_any_width():
    s = state(NoiseControlMode.AMBIENT, battery_left=69, battery_right=69, battery_case=28,
              ambient_level=3, placement_left=Placement.WEARING)
    rows = ui.build_rows(s, has_sink=True)
    for width in WIDTHS:
        lines = (
            ui.header_lines(s, active=True, connecting=False, width=width)
            + ui.battery_lines(s, width)
            + ui.mode_lines(s, rows, 1, width)
            + ui.level_lines(s, active=False, width=width)
            + ui.volume_lines(30, False, active=False, width=width)
            + [ui.hint_line(width)]
        )
        for line in lines:
            assert line.cell_len <= width, f"{line.plain!r} overflows width {width}"


def test_sliders_share_a_track_width_so_they_line_up():
    s = state(NoiseControlMode.AMBIENT, ambient_level=3)
    for width in WIDTHS:
        level = ui.level_lines(s, active=False, width=width)[0].plain
        vol = ui.volume_lines(30, False, active=False, width=width)[0].plain
        # Either both keep their track, or both drop it -- never one of each.
        assert ("] +" in level) == ("] +" in vol)
        if "] +" in level:
            assert level.index("] +") == vol.index("] +")


def test_sliders_drop_the_track_rather_than_overflow():
    s = state(NoiseControlMode.AMBIENT, ambient_level=3)
    line = ui.level_lines(s, active=False, width=20)[0]
    assert "[" not in line.plain and "lvl 4/5" in line.plain
    assert line.cell_len <= 20


def test_bars_grow_with_width():
    s = state(battery_left=50, battery_right=50, battery_case=50)
    narrow = ui.battery_lines(s, 30)[0].cell_len
    wide = ui.battery_lines(s, 60)[0].cell_len
    assert narrow < wide


def test_long_mode_labels_shorten_on_narrow_terminals():
    s = state(NoiseControlMode.ANC)
    rows = ui.build_rows(s, has_sink=True)
    assert "active noise canceling" in "".join(l.plain for l in ui.mode_lines(s, rows, 0, 60))
    assert "anc" in "".join(l.plain for l in ui.mode_lines(s, rows, 0, 26))


def test_header_shows_connection_state_and_action():
    connected = ui.header_lines(state(), active=True, connecting=False, width=60)[0].plain
    assert "connected" in connected and "enter: disconnect" in connected

    offline = ui.header_lines(state(connected=False), active=True, connecting=False, width=60)[0].plain
    assert "disconnected" in offline and "enter: connect" in offline

    busy = ui.header_lines(state(), active=False, connecting=True, width=60)[0].plain
    assert "working" in busy


def test_header_shows_wear_state_when_not_focused():
    s = state(placement_left=Placement.WEARING, placement_right=Placement.IN_CASE)
    line = ui.header_lines(s, active=False, connecting=False, width=60)[0].plain
    assert "L:in" in line and "R:case" in line


def test_hint_shrinks_to_fit():
    assert len(ui.hint_line(120).plain) > len(ui.hint_line(30).plain)
    for width in WIDTHS:
        assert ui.hint_line(width).cell_len <= width or width < len(ui._HINTS[-1])
