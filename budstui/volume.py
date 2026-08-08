"""Output volume for the buds' audio sink, via PipeWire/PulseAudio's pactl.

The earbuds themselves have no volume register we can poke: on Linux the
playback level is the Bluetooth sink's volume, which AVRCP absolute-volume
mirrors onto the device. So "overall volume" means the bluez_output sink.
"""

from __future__ import annotations

import re
import shutil
import subprocess

_VOLUME_RE = re.compile(r"(\d+)%")
_TIMEOUT = 3.0


class VolumeError(RuntimeError):
    pass


def _pactl(*args: str) -> str:
    if shutil.which("pactl") is None:
        raise VolumeError("pactl not found (install pipewire-pulse or pulseaudio-utils)")
    try:
        result = subprocess.run(
            ["pactl", *args], capture_output=True, text=True, timeout=_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise VolumeError("pactl timed out") from exc
    if result.returncode != 0:
        raise VolumeError(result.stderr.strip() or f"pactl {args[0]} failed")
    return result.stdout


def sink_name(address: str) -> str | None:
    """Find the PipeWire sink for a Bluetooth device address, if it has one."""
    expected = "bluez_output." + address.upper().replace(":", "_")
    try:
        listing = _pactl("list", "short", "sinks")
    except VolumeError:
        return None
    for line in listing.splitlines():
        fields = line.split("\t")
        if len(fields) > 1 and fields[1].startswith(expected):
            return fields[1]
    return None


def get_volume(sink: str) -> int | None:
    """Current sink volume in percent, or ``None`` if the sink is gone."""
    try:
        output = _pactl("get-sink-volume", sink)
    except VolumeError:
        return None
    match = _VOLUME_RE.search(output)
    return int(match.group(1)) if match else None


def set_volume(sink: str, percent: int) -> None:
    _pactl("set-sink-volume", sink, f"{max(0, min(100, int(percent)))}%")


def get_mute(sink: str) -> bool | None:
    try:
        output = _pactl("get-sink-mute", sink)
    except VolumeError:
        return None
    return "yes" in output.lower()


def set_mute(sink: str, muted: bool) -> None:
    _pactl("set-sink-mute", sink, "1" if muted else "0")
