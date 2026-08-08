"""Rendering for the buds panel.

Kept free of Textual and of any I/O so the exact output can be tested by
rendering to a plain Rich console.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from .device import BudsState
from .protocol import NoiseControlMode

# Row kinds, in the order they appear in the panel.
ROW_MODE = "mode"
ROW_LEVEL = "level"
ROW_VOLUME = "volume"

BAR_WIDTH = 22
TRACK_WIDTH = 20
PANEL_WIDTH = 46

FILLED = "█"
EMPTY = "░"
TRACK = "─"
KNOB = "●"

STYLES = {
    "header": "bold #4ade80",
    "label": "#7dd3a0",
    "dim": "#2f6b47",
    "value": "bold #86efac",
    "bar": "#4ade80",
    "bar_low": "bold #f87171",
    "bar_empty": "#1f4a32",
    "cursor": "bold #bbf7d0",
    "selected": "bold #4ade80",
    "muted": "#2f6b47",
    "warn": "bold #fbbf24",
}


@dataclass(frozen=True)
class Row:
    """One navigable line in the panel."""

    kind: str
    mode: NoiseControlMode | None = None


def build_rows(state: BudsState) -> list[Row]:
    """The navigable rows for the current state, top to bottom."""
    rows = [Row(ROW_MODE, mode) for mode in state.profile.modes]
    if level_levels(state) > 1:
        rows.append(Row(ROW_LEVEL))
    rows.append(Row(ROW_VOLUME))
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
        return "AMBIENT SOUND LEVEL"
    if state.noise_mode == NoiseControlMode.ANC:
        return "NOISE CANCELING LEVEL"
    return "LEVEL"


def _bar(percent: int | None, width: int = BAR_WIDTH) -> Text:
    if percent is None:
        return Text(EMPTY * width, style=STYLES["bar_empty"])
    percent = max(0, min(100, percent))
    filled = round(width * percent / 100)
    style = STYLES["bar_low"] if percent <= 20 else STYLES["bar"]
    text = Text()
    text.append(FILLED * filled, style=style)
    text.append(EMPTY * (width - filled), style=STYLES["bar_empty"])
    return text


def _track(value: int, maximum: int, width: int = TRACK_WIDTH) -> Text:
    """A slider track with the knob at *value* out of *maximum*."""
    position = 0 if maximum <= 0 else round((width - 1) * value / maximum)
    position = max(0, min(width - 1, position))
    text = Text()
    text.append(TRACK * position, style=STYLES["bar"])
    text.append(KNOB, style=STYLES["value"])
    text.append(TRACK * (width - 1 - position), style=STYLES["bar_empty"])
    return text


def _section(text: Text, title: str) -> None:
    text.append("▌", style=STYLES["header"])
    text.append(f"{title}\n", style=STYLES["header"])


def _rule(text: Text, width: int = PANEL_WIDTH) -> None:
    text.append("\n")
    text.append("─" * width + "\n", style=STYLES["dim"])
    text.append("\n")


def _gutter(text: Text, active: bool) -> None:
    text.append("▸ " if active else "  ", style=STYLES["cursor"])


def render_panel(
    state: BudsState,
    *,
    volume: int | None,
    muted: bool,
    rows: list[Row],
    cursor: int,
    status: str = "",
) -> Text:
    text = Text()

    # -- battery -----------------------------------------------------------
    _section(text, "BATTERY")
    for label, percent, charging in (
        ("left+right", state.battery_earbuds, state.earbuds_charging),
        ("case", state.battery_case, state.charging_case),
    ):
        text.append("  ")
        text.append(f"{label:<11}", style=STYLES["label"])
        text.append("[", style=STYLES["dim"])
        text.append("↯" if charging else " ", style=STYLES["value"] if charging else STYLES["dim"])
        text.append("] ", style=STYLES["dim"])
        text.append_text(_bar(percent))
        text.append(f" {percent:>3}%\n" if percent is not None else "   --\n", style=STYLES["value"])

    _rule(text)

    # -- sound mode --------------------------------------------------------
    _section(text, "SOUND MODE")
    for index, row in enumerate(rows):
        if row.kind != ROW_MODE or row.mode is None:
            continue
        active = index == cursor
        chosen = row.mode == state.noise_mode
        _gutter(text, active)
        text.append("[", style=STYLES["dim"])
        text.append("*" if chosen else " ", style=STYLES["selected"])
        text.append("] ", style=STYLES["dim"])
        style = STYLES["selected"] if chosen else (STYLES["cursor"] if active else STYLES["label"])
        text.append(row.mode.label, style=style)
        if chosen:
            text.append("  ←", style=STYLES["selected"])
        text.append("\n")

    # -- contextual level slider ------------------------------------------
    level_row = next((i for i, r in enumerate(rows) if r.kind == ROW_LEVEL), None)
    if level_row is not None:
        levels = level_levels(state)
        value = level_value(state)
        _rule(text)
        _section(text, level_title(state))
        _gutter(text, level_row == cursor)
        text.append("- [", style=STYLES["dim"])
        text.append_text(_track(value, levels - 1))
        text.append("] +", style=STYLES["dim"])
        text.append(f"  lvl {value + 1}/{levels}\n", style=STYLES["value"])

    # -- volume ------------------------------------------------------------
    volume_row = next((i for i, r in enumerate(rows) if r.kind == ROW_VOLUME), None)
    if volume_row is not None:
        _rule(text)
        _section(text, "VOLUME")
        _gutter(text, volume_row == cursor)
        if volume is None:
            text.append("no audio sink for this device", style=STYLES["dim"])
            text.append("\n")
        else:
            text.append("- [", style=STYLES["dim"])
            text.append_text(_track(volume, 100))
            text.append("] +", style=STYLES["dim"])
            text.append(f"  {volume:>3}%", style=STYLES["muted"] if muted else STYLES["value"])
            text.append("  muted\n" if muted else "\n", style=STYLES["warn"])

    if status:
        _rule(text)
        text.append(status, style=STYLES["warn"])
        text.append("\n")

    return text
