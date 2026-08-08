"""Panel layout logic."""

from budstui import ui
from budstui.device import BudsState, profile_for
from budstui.protocol import NoiseControlMode


def state(mode: NoiseControlMode, **kwargs) -> BudsState:
    return BudsState(
        name="Buds4 Pro", profile=profile_for("Buds4 Pro"), connected=True,
        noise_mode=mode, **kwargs
    )


def test_level_row_only_appears_for_adjustable_modes():
    kinds = lambda s: [r.kind for r in ui.build_rows(s)]
    assert ui.ROW_LEVEL not in kinds(state(NoiseControlMode.OFF))
    assert ui.ROW_LEVEL not in kinds(state(NoiseControlMode.ADAPTIVE))
    assert ui.ROW_LEVEL in kinds(state(NoiseControlMode.AMBIENT))
    assert ui.ROW_LEVEL in kinds(state(NoiseControlMode.ANC))


def test_volume_row_is_always_last():
    for mode in NoiseControlMode:
        assert ui.build_rows(state(mode))[-1].kind == ui.ROW_VOLUME


def test_level_title_and_value_track_the_mode():
    ambient = state(NoiseControlMode.AMBIENT, ambient_level=2, anc_level=4)
    assert ui.level_title(ambient) == "AMBIENT SOUND LEVEL"
    assert ui.level_value(ambient) == 2
    assert ui.level_levels(ambient) == 5

    anc = state(NoiseControlMode.ANC, ambient_level=2, anc_level=4)
    assert ui.level_title(anc) == "NOISE CANCELING LEVEL"
    assert ui.level_value(anc) == 4


def test_track_knob_position():
    assert ui._track(0, 100).plain.index(ui.KNOB) == 0
    assert ui._track(100, 100).plain.index(ui.KNOB) == ui.TRACK_WIDTH - 1
    assert len(ui._track(50, 100).plain) == ui.TRACK_WIDTH


def test_bar_handles_unknown_and_clamps():
    assert ui._bar(None).plain == ui.EMPTY * ui.BAR_WIDTH
    assert ui._bar(150).plain == ui.FILLED * ui.BAR_WIDTH
    assert ui._bar(0).plain == ui.EMPTY * ui.BAR_WIDTH


def test_panel_renders_every_mode_without_error():
    for mode in NoiseControlMode:
        s = state(mode, battery_left=70, battery_right=72, battery_case=43)
        rows = ui.build_rows(s)
        text = ui.render_panel(s, volume=30, muted=False, rows=rows, cursor=0).plain
        assert "BATTERY" in text and "SOUND MODE" in text and "VOLUME" in text
        for m in s.profile.modes:
            assert m.label in text


def test_panel_reports_a_missing_sink():
    s = state(NoiseControlMode.OFF)
    text = ui.render_panel(s, volume=None, muted=False, rows=ui.build_rows(s), cursor=0).plain
    assert "no audio sink" in text
