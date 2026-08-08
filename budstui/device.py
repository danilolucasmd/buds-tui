"""Discovery, connection and state tracking for Galaxy Buds earbuds."""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field, replace

from .protocol import (
    EOM,
    SOM,
    UUID_SPP_LEGACY,
    UUID_SPP_NEW,
    Message,
    MsgId,
    NoiseControlMode,
    Placement,
    decode_stream,
)
from .sdp import find_rfcomm_channel

#: Acknowledgement frame; payload is ``[echoed_msg_id, value]``.
MSG_ACK = 66

#: Some Python builds (notably the standalone ones) omit Bluetooth socket support.
HAS_BLUETOOTH = hasattr(socket, "AF_BLUETOOTH")

_DEVICE_RE = re.compile(r"^Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.*)$", re.I)


@dataclass(frozen=True)
class ModelProfile:
    """Per-model capabilities and level ranges."""

    name: str
    #: Number of selectable ambient-sound levels.
    ambient_levels: int = 5
    #: Number of selectable ANC levels (1 means the level is not adjustable).
    anc_levels: int = 2
    #: Whether the model offers the adaptive noise-control mode.
    has_adaptive: bool = True

    @property
    def modes(self) -> list[NoiseControlMode]:
        modes = [NoiseControlMode.OFF, NoiseControlMode.AMBIENT]
        if self.has_adaptive:
            modes.append(NoiseControlMode.ADAPTIVE)
        modes.append(NoiseControlMode.ANC)
        return modes


#: Known models, matched against the advertised Bluetooth name.
MODEL_PROFILES: list[tuple[re.Pattern[str], ModelProfile]] = [
    (re.compile(r"buds\s*4\s*pro", re.I), ModelProfile("Buds4 Pro", ambient_levels=5, anc_levels=5)),
    (re.compile(r"buds\s*4", re.I), ModelProfile("Buds4", ambient_levels=5, anc_levels=5)),
    (re.compile(r"buds\s*3\s*pro", re.I), ModelProfile("Buds3 Pro", ambient_levels=5, anc_levels=2)),
    (re.compile(r"buds\s*3", re.I), ModelProfile("Buds3", ambient_levels=5, anc_levels=2)),
    (re.compile(r"buds\s*2\s*pro", re.I), ModelProfile("Buds2 Pro", ambient_levels=4, anc_levels=2, has_adaptive=False)),
    (re.compile(r"buds\s*2", re.I), ModelProfile("Buds2", ambient_levels=4, anc_levels=1, has_adaptive=False)),
    (re.compile(r"buds\s*pro", re.I), ModelProfile("Buds Pro", ambient_levels=4, anc_levels=2, has_adaptive=False)),
    (re.compile(r"buds\s*live", re.I), ModelProfile("Buds Live", ambient_levels=0, anc_levels=1, has_adaptive=False)),
    (re.compile(r"buds", re.I), ModelProfile("Galaxy Buds", ambient_levels=4, anc_levels=1, has_adaptive=False)),
]


def profile_for(name: str) -> ModelProfile:
    for pattern, profile in MODEL_PROFILES:
        if pattern.search(name):
            return profile
    return ModelProfile("Galaxy Buds")


@dataclass
class BudsState:
    """Everything the UI needs to render, refreshed from device messages."""

    address: str = ""
    name: str = ""
    profile: ModelProfile = field(default_factory=lambda: ModelProfile("Galaxy Buds"))
    connected: bool = False
    battery_left: int | None = None
    battery_right: int | None = None
    battery_case: int | None = None
    charging_left: bool = False
    charging_right: bool = False
    charging_case: bool = False
    placement_left: Placement = Placement.DISCONNECTED
    placement_right: Placement = Placement.DISCONNECTED
    noise_mode: NoiseControlMode = NoiseControlMode.OFF
    ambient_level: int = 0
    anc_level: int = 0

    @property
    def battery_earbuds(self) -> int | None:
        """Combined earbud battery: the lower of the two, as Samsung reports it."""
        values = [v for v in (self.battery_left, self.battery_right) if v]
        return min(values) if values else None

    @property
    def earbuds_charging(self) -> bool:
        return self.charging_left or self.charging_right

    @property
    def worn(self) -> bool:
        return Placement.WEARING in (self.placement_left, self.placement_right)


def _run_timed(*args: str, timeout: float) -> str:
    if shutil.which(args[0]) is None:
        return ""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timed out"
    except OSError as exc:
        return f"error: {exc}"
    return result.stdout + result.stderr


def _run(*args: str) -> str:
    if shutil.which(args[0]) is None:
        return ""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout


@dataclass(frozen=True)
class KnownDevice:
    address: str
    name: str
    connected: bool


def _looks_like_buds(name: str, info: str) -> bool:
    return UUID_SPP_NEW in info or (UUID_SPP_LEGACY in info and "buds" in name.lower())


def list_buds() -> list[KnownDevice]:
    """Every paired device that speaks the buds protocol, connected ones first."""
    found: list[KnownDevice] = []
    for line in _run("bluetoothctl", "devices", "Paired").splitlines():
        match = _DEVICE_RE.match(line.strip())
        if not match:
            continue
        address, name = match.group(1).upper(), match.group(2).strip()
        info = _run("bluetoothctl", "info", address)
        if not _looks_like_buds(name, info.lower()):
            continue
        found.append(KnownDevice(address, name, "connected: yes" in info.lower()))
    found.sort(key=lambda d: not d.connected)
    return found


def list_connected_buds() -> list[tuple[str, str]]:
    """``(address, name)`` for every *connected* pair of buds."""
    return [(d.address, d.name) for d in list_buds() if d.connected]


def is_connected(address: str) -> bool:
    return "connected: yes" in _run("bluetoothctl", "info", address).lower()


def bluetooth_connect(address: str, timeout: float = 25.0) -> tuple[bool, str]:
    """Bring up the Bluetooth link, as ``bluetoothctl connect`` would."""
    output = _run_timed("bluetoothctl", "connect", address, timeout=timeout)
    if "successful" in output.lower() or is_connected(address):
        return True, ""
    return False, _first_error(output) or "connection failed"


def bluetooth_disconnect(address: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Drop the Bluetooth link entirely, releasing audio as well as control."""
    output = _run_timed("bluetoothctl", "disconnect", address, timeout=timeout)
    if "successful" in output.lower() or not is_connected(address):
        return True, ""
    return False, _first_error(output) or "disconnect failed"


def _first_error(output: str) -> str:
    for line in output.splitlines():
        if "failed" in line.lower() or "error" in line.lower():
            return line.strip()
    return ""


class ConnectionError_(RuntimeError):
    pass


class BudsConnection:
    """An RFCOMM session with a pair of earbuds.

    Reads run in a background task; decoded messages update :attr:`state` and
    fire :attr:`on_update`.
    """

    def __init__(self, address: str, name: str = "") -> None:
        self.address = address
        self._resolve_error = ""
        self.state = BudsState(address=address, name=name, profile=profile_for(name))
        self.on_update: list = []
        self._sock: socket.socket | None = None
        self._reader: asyncio.Task | None = None
        self._buffer = bytearray()
        self._ready = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, timeout: float = 12.0) -> None:
        if not HAS_BLUETOOTH:
            raise ConnectionError_(
                "this Python was built without Bluetooth socket support; "
                "use your distribution's python3"
            )
        channel = await asyncio.to_thread(self._resolve_channel)
        if channel is None:
            detail = f" ({self._resolve_error})" if self._resolve_error else ""
            raise ConnectionError_(f"no Galaxy Buds service found on {self.address}{detail}")
        sock = await asyncio.to_thread(self._open, channel)
        self._sock = sock
        self.state = replace(self.state, connected=True)
        self._reader = asyncio.create_task(self._read_loop())
        # The buds push a full status snapshot right after the socket opens.
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass

    def _resolve_channel(self) -> int | None:
        """Ask the device which RFCOMM channel serves the buds protocol."""
        errors: list[str] = []
        for uuid in (UUID_SPP_NEW, UUID_SPP_LEGACY):
            try:
                channel = find_rfcomm_channel(self.address, uuid)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            if channel is not None:
                return channel
        self._resolve_error = "; ".join(errors)
        return None

    def _open(self, channel: int) -> socket.socket:
        last: OSError | None = None
        for _ in range(15):
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(8)
            try:
                sock.connect((self.address, channel))
                sock.setblocking(False)
                return sock
            except OSError as exc:
                sock.close()
                last = exc
                if exc.errno != 16:  # EBUSY: a previous session is still tearing down
                    break
                import time

                time.sleep(0.4)
        raise ConnectionError_(f"could not open RFCOMM channel {channel}: {last}")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
            self._reader = None
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self.state = replace(self.state, connected=False)

    # -- io ----------------------------------------------------------------

    async def send(self, msg_id: int, payload: bytes = b"") -> None:
        if self._sock is None:
            raise ConnectionError_("not connected")
        data = Message(msg_id, payload).encode(SOM, EOM)
        await asyncio.get_running_loop().sock_sendall(self._sock, data)

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        try:
            while True:
                chunk = await loop.sock_recv(self._sock, 4096)
                if not chunk:
                    break
                self._buffer += chunk
                for message in decode_stream(self._buffer, SOM, EOM):
                    self._handle(message)
        except (asyncio.CancelledError, OSError):
            raise
        finally:
            if self.state.connected:
                self.state = replace(self.state, connected=False)
                self._notify()

    # -- message handling --------------------------------------------------

    def _handle(self, message: Message) -> None:
        changed = True
        if message.id == MsgId.EXTENDED_STATUS_UPDATED:
            self._apply_extended_status(message.payload)
            self._ready.set()
        elif message.id == MsgId.STATUS_UPDATED:
            self._apply_status(message.payload)
        elif message.id == MsgId.NOISE_CONTROLS_UPDATE and message.payload:
            self.state = replace(self.state, noise_mode=_mode(message.payload[0]))
        elif message.id == MSG_ACK and len(message.payload) >= 2:
            self._apply_ack(message.payload[0], message.payload[1])
        else:
            changed = False
        if changed:
            self._notify()

    def _apply_ack(self, echoed_id: int, value: int) -> None:
        if echoed_id == MsgId.NOISE_CONTROLS:
            self.state = replace(self.state, noise_mode=_mode(value))
        elif echoed_id == MsgId.AMBIENT_VOLUME:
            self.state = replace(self.state, ambient_level=value)
        elif echoed_id == MsgId.NOISE_REDUCTION_LEVEL:
            self.state = replace(self.state, anc_level=value)

    def _apply_status(self, payload: bytes) -> None:
        """Short status frame: revision, batteries, placement, charging bits."""
        if len(payload) < 7:
            return
        charging = payload[7] if len(payload) > 7 else 0
        self.state = replace(
            self.state,
            battery_left=payload[1] or None,
            battery_right=payload[2] or None,
            placement_left=Placement.parse((payload[5] & 0xF0) >> 4),
            placement_right=Placement.parse(payload[5] & 0x0F),
            battery_case=payload[6] or None,
            charging_left=bool(charging & 0x10),
            charging_right=bool(charging & 0x04),
            charging_case=bool(charging & 0x01),
        )

    def _apply_extended_status(self, payload: bytes) -> None:
        """Full snapshot pushed when the session opens."""
        if len(payload) < 25:
            return
        self.state = replace(
            self.state,
            battery_left=payload[2] or None,
            battery_right=payload[3] or None,
            placement_left=Placement.parse((payload[6] & 0xF0) >> 4),
            placement_right=Placement.parse(payload[6] & 0x0F),
            battery_case=payload[7] or self.state.battery_case,
            noise_mode=_mode(payload[12]),
            ambient_level=payload[23],
            anc_level=payload[24],
        )

    def update_state(self, **changes) -> None:
        """Apply a local state change, e.g. optimistically before the ack lands."""
        self.state = replace(self.state, **changes)
        self._notify()

    def _notify(self) -> None:
        for callback in self.on_update:
            callback(self.state)

    # -- commands ----------------------------------------------------------

    async def set_noise_mode(self, mode: NoiseControlMode) -> None:
        await self.send(MsgId.NOISE_CONTROLS, bytes([int(mode)]))

    async def set_ambient_level(self, level: int) -> None:
        level = max(0, min(self.state.profile.ambient_levels - 1, level))
        await self.send(MsgId.AMBIENT_VOLUME, bytes([level]))

    async def set_anc_level(self, level: int) -> None:
        level = max(0, min(self.state.profile.anc_levels - 1, level))
        await self.send(MsgId.NOISE_REDUCTION_LEVEL, bytes([level]))


def _mode(value: int) -> NoiseControlMode:
    try:
        return NoiseControlMode(value)
    except ValueError:
        return NoiseControlMode.OFF
