"""The settings exposed in the settings group.

Each entry knows the message that writes it and, where the earbuds report it,
the offset in the extended-status payload that reads it back. Offsets marked
UNVERIFIED come from the reference implementation rather than from a sweep
against real hardware; the reference explicitly does not implement Buds4 Pro,
so those are hypotheses until confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TOGGLE = "toggle"
CHOICE = "choice"
SLIDER = "slider"

#: Stereo balance is centred at this value.
BALANCE_CENTER = 16
BALANCE_MAX = 32


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    msg: int
    kind: str = TOGGLE
    #: Payload offset the earbuds report this at, or None when write-only.
    offset: int | None = None
    #: True when the wire value is the logical inverse of what we display.
    invert: bool = False
    #: ``(label, wire value)`` pairs for CHOICE settings.
    choices: tuple[tuple[str, int], ...] = ()
    minimum: int = 0
    maximum: int = 1
    #: Only shown when this other setting is on.
    depends_on: str | None = None
    verified: bool = False

    # -- values --------------------------------------------------------------

    def read(self, payload: bytes) -> int | None:
        """The current value, decoded from an extended-status payload."""
        if self.offset is None or self.offset >= len(payload):
            return None
        return self.decode(payload[self.offset])

    def decode(self, raw: int) -> int:
        if self.kind == TOGGLE:
            return int(bool(raw) != self.invert)
        return raw

    def encode(self, value: int) -> int:
        if self.kind == TOGGLE:
            return int(bool(value) != self.invert)
        return value

    def clamp(self, value: int) -> int:
        if self.kind == CHOICE:
            values = [v for _, v in self.choices]
            return value if value in values else values[0]
        return max(self.minimum, min(self.maximum, value))

    def adjust(self, value: int, delta: int) -> int:
        """The value *delta* steps away, for h/l and enter."""
        if self.kind == TOGGLE:
            return 1 - value
        if self.kind == CHOICE:
            values = [v for _, v in self.choices]
            index = values.index(value) if value in values else 0
            return values[(index + delta) % len(values)]
        return self.clamp(value + delta)

    # -- display -------------------------------------------------------------

    def display(self, value: int | None) -> str:
        if value is None:
            return "--"
        if self.kind == TOGGLE:
            return "on" if value else "off"
        if self.kind == CHOICE:
            return next((label for label, v in self.choices if v == value), str(value))
        if self.key == "stereo_balance":
            if value == BALANCE_CENTER:
                return "center"
            side = "L" if value < BALANCE_CENTER else "R"
            amount = abs(value - BALANCE_CENTER) * 100 // BALANCE_CENTER
            return f"{side} {amount}%"
        return str(value)


SETTINGS: list[Setting] = [
    Setting(
        "conversation_detect", "conversation detect", msg=122,
        kind=TOGGLE, offset=26,
    ),
    Setting(
        "conversation_timeout", "conversation timeout", msg=123, kind=CHOICE, offset=27,
        choices=(("5 sec", 0), ("10 sec", 1), ("15 sec", 2)),
        depends_on="conversation_detect",
    ),
    Setting(
        "one_earbud_nc", "noise control with one earbud", msg=111,
        kind=TOGGLE, offset=28,
    ),
    Setting(
        "outside_double_tap", "double-tap edge for volume", msg=149,
        kind=TOGGLE, offset=32,
    ),
    Setting(
        "sidetone", "sidetone on calls", msg=139, kind=TOGGLE, offset=33,
    ),
    Setting(
        "seamless_connection", "seamless connection", msg=175,
        kind=TOGGLE, offset=19, invert=True,
    ),
    Setting(
        "gaming_mode", "gaming mode", msg=135, kind=TOGGLE, offset=None,
    ),
    Setting(
        "call_path", "route calls to phone", msg=110,
        kind=TOGGLE, offset=34, invert=True,
    ),
    Setting(
        "adaptive_volume", "adaptive volume", msg=197, kind=TOGGLE, offset=49,
    ),
    Setting(
        "auto_pause", "pause when a bud is removed", msg=108,
        kind=TOGGLE, offset=45,
    ),
    Setting(
        "stereo_balance", "stereo balance", msg=143, kind=SLIDER, offset=25,
        minimum=0, maximum=BALANCE_MAX,
    ),
]

BY_KEY = {s.key: s for s in SETTINGS}
BY_MSG = {s.msg: s for s in SETTINGS}


def visible(values: dict[str, int]) -> list[Setting]:
    """The settings to show, dropping ones whose parent toggle is off."""
    return [
        s for s in SETTINGS
        if s.depends_on is None or values.get(s.depends_on, 0)
    ]


def read_all(payload: bytes) -> dict[str, int]:
    """Decode every readable setting from an extended-status payload."""
    values = {}
    for setting in SETTINGS:
        value = setting.read(payload)
        if value is not None:
            values[setting.key] = value
    return values
