"""Rendering for the buds panel.

Every builder takes the width it has to fill and returns lines, so the layout
adapts to the terminal. Kept free of Textual and of I/O so the exact output can
be checked by rendering to a plain Rich console.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from .device import BudsState
from .protocol import NoiseControlMode
from .settings import BY_KEY, SLIDER, Setting, visible

# Groups, in the order they appear.
GROUP_CONNECTION = "connection"
GROUP_BATTERY = "battery"
GROUP_MODE = "sound mode"
GROUP_LEVEL = "level"
GROUP_VOLUME = "volume"
GROUP_SETTINGS = "settings"

# Row kinds.
ROW_CONNECTION = "connection"
ROW_MODE = "mode"
ROW_LEVEL = "level"
ROW_VOLUME = "volume"
ROW_SETTING = "setting"

MIN_BAR = 6
MAX_BAR = 64
#: Readouts are padded to a fixed column so every slider track lines up.
READOUT = 9

FILLED = "█"
EMPTY = "░"
TRACK = "─"
KNOB = "●"

STYLES = {
    "title": "bold #4ade80",
    "label": "#7dd3a0",
    "dim": "#2f6b47",
    "value": "bold #86efac",
    "bar": "#4ade80",
    "bar_low": "bold #f87171",
    "bar_empty": "#1f4a32",
    "cursor": "bold #bbf7d0",
    "active": "bold #4ade80",
    "ok": "bold #4ade80",
    "busy": "bold #fbbf24",
    "off": "#f87171",
    "warn": "bold #fbbf24",
}


@dataclass(frozen=True)
class Row:
    """One navigable line."""

    kind: str
    group: str
    mode: NoiseControlMode | None = None
    #: For ROW_SETTING, the ``Setting.key`` this row edits.
    setting: str | None = None


def build_rows(state: BudsState, *, has_sink: bool) -> list[Row]:
    """The navigable rows for the current state, top to bottom."""
    rows = [Row(ROW_CONNECTION, GROUP_CONNECTION)]
    if state.connected:
        rows += [Row(ROW_MODE, GROUP_MODE, mode) for mode in state.profile.modes]
        if level_levels(state) > 1:
            rows.append(Row(ROW_LEVEL, GROUP_LEVEL))
    if has_sink:
        rows.append(Row(ROW_VOLUME, GROUP_VOLUME))
    if state.connected:
        rows += [
            Row(ROW_SETTING, GROUP_SETTINGS, setting=item.key)
            for item in visible(state.settings)
        ]
    return rows


def level_levels(state: BudsState) -> int:
    """How many level steps the current noise mode offers (0 or 1 means none)."""
    if state.noise_mode == NoiseControlMode.AMBIENT:
        return state.profile.ambient_levels
    if state.noise_mode == NoiseControlMode.ANC:
        return state.profile.anc_levels
    return 0


def level_value(state: BudsState) -> int:
    if state.noise_mode == NoiseControlMode.AMBIENT:
        return state.ambient_level
    if state.noise_mode == NoiseControlMode.ANC:
        return state.anc_level
    return 0


def level_title(state: BudsState) -> str:
    if state.noise_mode == NoiseControlMode.AMBIENT:
        return "ambient sound level"
    if state.noise_mode == NoiseControlMode.ANC:
        return "noise canceling level"
    return "level"


# -- primitives -------------------------------------------------------------


def _fit(width: int, reserved: int) -> int:
    """How wide a bar can be once *reserved* columns are spoken for."""
    return max(MIN_BAR, min(MAX_BAR, width - reserved))


def _split(width: int, left: Text, right: Text) -> Text:
    """Left-aligned and right-aligned halves of one line."""
    if not right.plain:
        return left
    gap = max(1, width - left.cell_len - right.cell_len)
    line = Text()
    line.append_text(left)
    line.append(" " * gap)
    line.append_text(right)
    return line


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"


def _gutter(active: bool) -> Text:
    return Text("▸ " if active else "  ", style=STYLES["cursor"])


def _bar(percent: int | None, width: int) -> Text:
    if percent is None:
        return Text(EMPTY * width, style=STYLES["bar_empty"])
    percent = max(0, min(100, percent))
    filled = round(width * percent / 100)
    bar = Text()
    bar.append(FILLED * filled, style=STYLES["bar_low"] if percent <= 20 else STYLES["bar"])
    bar.append(EMPTY * (width - filled), style=STYLES["bar_empty"])
    return bar


def _track(value: int, maximum: int, width: int) -> Text:
    position = 0 if maximum <= 0 else round((width - 1) * value / maximum)
    position = max(0, min(width - 1, position))
    track = Text()
    track.append(TRACK * position, style=STYLES["bar"])
    track.append(KNOB, style=STYLES["value"])
    track.append(TRACK * (width - 1 - position), style=STYLES["bar_empty"])
    return track


def _slider_line(width: int, *, active: bool, value: int, maximum: int, readout: str) -> Text:
    track_width = width - (2 + 3 + 3 + 2 + READOUT)
    line = _gutter(active)
    if track_width < MIN_BAR:
        # Too narrow for a track: keep the controls and the readout.
        line.append("- ", style=STYLES["dim"])
        line.append(readout, style=STYLES["value"])
        line.append(" +", style=STYLES["dim"])
        return line
    track_width = min(track_width, MAX_BAR)
    line.append("- [", style=STYLES["dim"])
    line.append_text(_track(value, maximum, track_width))
    line.append("] +", style=STYLES["dim"])
    line.append("  " + readout.rjust(READOUT), style=STYLES["value"])
    return line


# -- groups -----------------------------------------------------------------


def header_lines(
    state: BudsState, *, active: bool, connecting: bool, width: int
) -> list[Text]:
    """The device/connection line, which doubles as the connect toggle."""
    if connecting:
        status, status_style = "● working", STYLES["busy"]
    elif state.connected:
        status, status_style = "● connected", STYLES["ok"]
    else:
        status, status_style = "○ disconnected", STYLES["off"]

    left = _gutter(active)
    name = state.name or "no earbuds found"
    left.append(
        _truncate(name, width - len(status) - 4),
        style=STYLES["cursor" if active else "label"],
    )
    left.append("  ")
    left.append(status, style=status_style)

    right = Text()
    if active:
        right.append(
            "enter: disconnect" if state.connected else "enter: connect", style=STYLES["dim"]
        )
    elif state.connected:
        right.append(f"L:{_placement(state.placement_left)}  ", style=STYLES["dim"])
        right.append(f"R:{_placement(state.placement_right)}", style=STYLES["dim"])

    if left.cell_len + right.cell_len + 1 > width:
        right = Text()
    return [_split(width, left, right)]


def _placement(placement) -> str:
    from .protocol import Placement

    return {
        Placement.WEARING: "in",
        Placement.IDLE: "out",
        Placement.IN_CASE: "case",
        Placement.CHARGING: "chg",
    }.get(placement, "--")


def battery_lines(state: BudsState, width: int) -> list[Text]:
    lines = []
    for label, percent, charging in (
        ("left", state.battery_left, state.charging_left),
        ("right", state.battery_right, state.charging_right),
        ("case", state.battery_case, state.charging_case),
    ):
        bar_width = width - (2 + 6 + 2 + 5)
        readout = Text(f"{percent:>3}%" if percent is not None else " --", style=STYLES["value"])
        line = Text("  ")
        line.append(f"{label:<6}", style=STYLES["label"])
        line.append("↯ " if charging else "  ", style=STYLES["value"])
        if bar_width < MIN_BAR:
            # No room for a bar: keep the reading against the right edge.
            lines.append(_split(width, line, readout))
            continue
        line.append_text(_bar(percent, min(bar_width, MAX_BAR)))
        line.append(" ")
        line.append_text(readout)
        lines.append(line)
    return lines


MODE_MARKER = "(active)"


def mode_lines(state: BudsState, rows: list[Row], cursor: int, width: int) -> list[Text]:
    entries = [(i, r) for i, r in enumerate(rows) if r.kind == ROW_MODE and r.mode is not None]
    # Shorten every label together, so the list never mixes long and short forms.
    budget = width - len(MODE_MARKER) - 3
    use_short = any(len(r.mode.label) > budget for _, r in entries)

    lines = []
    for index, row in entries:
        active = index == cursor
        chosen = row.mode == state.noise_mode
        left = _gutter(active)
        style = STYLES["active"] if chosen else (STYLES["cursor"] if active else STYLES["label"])
        label = row.mode.short_label if use_short else row.mode.label
        left.append(_truncate(label, budget), style=style)
        right = Text(MODE_MARKER, style=STYLES["active"]) if chosen else Text()
        lines.append(_split(width, left, right))
    return lines


def level_lines(state: BudsState, *, active: bool, width: int) -> list[Text]:
    levels = level_levels(state)
    value = level_value(state)
    return [
        _slider_line(
            width, active=active, value=value, maximum=levels - 1,
            readout=f"lvl {value + 1}/{levels}",
        )
    ]


def volume_lines(volume: int | None, muted: bool, *, active: bool, width: int) -> list[Text]:
    if volume is None:
        return [Text("  no audio sink for this device", style=STYLES["dim"])]
    readout = "muted" if muted else f"{volume}%"
    line = _slider_line(width, active=active, value=volume, maximum=100, readout=readout)
    if muted:
        line.stylize(STYLES["warn"], line.plain.rindex("muted"))
    return [line]


#: Width of the miniature track drawn next to slider-style settings.
SETTING_TRACK = 8


def _setting_value(setting: Setting, value: int | None, width: int) -> Text:
    readout = setting.display(value)
    if setting.kind != SLIDER or value is None or width < 34:
        return Text(readout, style=STYLES["value"])
    right = Text()
    right.append("[", style=STYLES["dim"])
    right.append_text(_track(value - setting.minimum, setting.maximum - setting.minimum, SETTING_TRACK))
    right.append("] ", style=STYLES["dim"])
    right.append(readout, style=STYLES["value"])
    return right


def settings_lines(state: BudsState, rows: list[Row], cursor: int, width: int) -> list[Text]:
    lines = []
    for index, row in enumerate(rows):
        if row.kind != ROW_SETTING or row.setting is None:
            continue
        setting = BY_KEY.get(row.setting)
        if setting is None:
            continue
        active = index == cursor
        value = state.settings.get(setting.key)
        right = _setting_value(setting, value, width)
        left = _gutter(active)
        style = STYLES["cursor"] if active else STYLES["label"]
        left.append(_truncate(setting.label, width - right.cell_len - 3), style=style)
        lines.append(_split(width, left, right))
    return lines


_HINTS = [
    "tab section   j/k navigate   enter select   h/l adjust   1-4 mode   m mute   r reconnect   q quit",
    "tab section   j/k navigate   enter select   h/l adjust   m mute   q quit",
    "j/k navigate   enter select   h/l adjust   q quit",
    "j/k  enter  h/l  q",
]


def hint_line(width: int) -> Text:
    """The widest key hint that fits."""
    for hint in _HINTS:
        if len(hint) <= width:
            return Text(hint, style=STYLES["dim"])
    return Text(_HINTS[-1], style=STYLES["dim"])
