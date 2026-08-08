"""The buds-tui Textual application."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from . import ui, volume as volume_ctl
from .device import (
    BudsConnection,
    BudsState,
    KnownDevice,
    bluetooth_connect,
    bluetooth_disconnect,
    list_buds,
    profile_for,
)
from .protocol import MsgId, NoiseControlMode

VOLUME_STEP = 5


class BudsApp(App):
    CSS = """
    Screen {
        background: #04120a;
        align: center middle;
    }
    #wrap {
        width: 100%;
        max-width: 74;
        height: auto;
        max-height: 100%;
        padding: 1 2;
        background: #04120a;
    }
    #header { margin-bottom: 1; }
    #status { color: #fbbf24; margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("j,down", "cursor(1)", "down", show=False),
        Binding("k,up", "cursor(-1)", "up", show=False),
        Binding("l,right", "adjust(1)", "increase", show=False),
        Binding("h,left", "adjust(-1)", "decrease", show=False),
        Binding("enter,space", "select", "select", show=False),
        Binding("tab", "group(1)", "next section", show=False, priority=True),
        Binding("shift+tab", "group(-1)", "previous section", show=False, priority=True),
        Binding("1", "pick(0)", "mode 1", show=False),
        Binding("2", "pick(1)", "mode 2", show=False),
        Binding("3", "pick(2)", "mode 3", show=False),
        Binding("4", "pick(3)", "mode 4", show=False),
        Binding("m", "toggle_mute", "mute", show=False),
        Binding("r", "reconnect", "reconnect", show=False),
        Binding("g", "cursor_home", "first", show=False),
        Binding("G", "cursor_end", "last", show=False),
        Binding("q,ctrl+c", "quit", "quit", show=False),
    ]

    def __init__(self, address: str | None = None) -> None:
        super().__init__()
        self._wanted_address = address
        self.connection: BudsConnection | None = None
        self.device: KnownDevice | None = None
        self._offline = BudsState(profile=profile_for(""))
        self.rows: list[ui.Row] = []
        self.cursor = 0
        self.sink: str | None = None
        self.volume: int | None = None
        self.muted = False
        self.busy = False
        self.status = ""

    @property
    def state(self) -> BudsState:
        """The connection owns the state; fall back to an offline one when closed."""
        return self.connection.state if self.connection is not None else self._offline

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wrap"):
            yield ui_panel(self._header_lines, id="header")
            yield Static("", id="status")
            yield group_box("battery", self._battery_lines, id="battery")
            yield group_box("sound mode", self._mode_lines, id="mode")
            yield group_box("level", self._level_lines, id="level")
            yield group_box("volume", self._volume_lines, id="volume")
            yield ui_panel(self._hint_lines, id="hint")

    async def on_mount(self) -> None:
        self.rows = ui.build_rows(self.state, has_sink=False)
        self._render()
        self.set_interval(2.0, self._poll_volume)
        self.run_worker(self._startup(), exclusive=True)

    # -- builders ----------------------------------------------------------

    def _row_index(self, kind: str) -> int | None:
        return next((i for i, r in enumerate(self.rows) if r.kind == kind), None)

    def _is_active(self, kind: str) -> bool:
        return self._row_index(kind) == self.cursor

    def _header_lines(self, width: int) -> list[Text]:
        return ui.header_lines(
            self.state,
            active=self._is_active(ui.ROW_CONNECTION),
            connecting=self.busy,
            width=width,
        )

    def _battery_lines(self, width: int) -> list[Text]:
        return ui.battery_lines(self.state, width)

    def _mode_lines(self, width: int) -> list[Text]:
        return ui.mode_lines(self.state, self.rows, self.cursor, width)

    def _level_lines(self, width: int) -> list[Text]:
        return ui.level_lines(self.state, active=self._is_active(ui.ROW_LEVEL), width=width)

    def _volume_lines(self, width: int) -> list[Text]:
        return ui.volume_lines(
            self.volume, self.muted, active=self._is_active(ui.ROW_VOLUME), width=width
        )

    def _hint_lines(self, width: int) -> list[Text]:
        return [ui.hint_line(width)]

    # -- connection --------------------------------------------------------

    async def _startup(self) -> None:
        await self._discover()
        # Only attach to earbuds that are already connected; bringing the link
        # up is the user's call, on the connection row.
        if self.device is not None and self.device.connected:
            await self._open_session()
        self._sync_rows()

    async def _discover(self) -> None:
        devices = await asyncio.to_thread(list_buds)
        if self._wanted_address:
            wanted = self._wanted_address.upper()
            devices = [d for d in devices if d.address.upper() == wanted]
            if not devices:
                devices = [KnownDevice(wanted, wanted, False)]
        if not devices:
            self.status = "no paired Galaxy Buds found - pair them first, then press r"
            self._render()
            return
        self.device = devices[0]
        self._offline = BudsState(
            address=self.device.address,
            name=self.device.name,
            profile=profile_for(self.device.name),
        )
        self.status = ""

    async def _open_session(self) -> bool:
        """Open the RFCOMM control session to already-connected earbuds."""
        assert self.device is not None
        connection = BudsConnection(self.device.address, self.device.name)
        connection.on_update.append(self._on_device_update)
        try:
            await connection.connect()
        except Exception as exc:
            self.status = f"could not open control session: {exc}"
            self._render()
            return False
        self.connection = connection
        self.sink = await asyncio.to_thread(volume_ctl.sink_name, self.device.address)
        self.status = ""
        await self._poll_volume()
        return True

    async def _close_session(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def _toggle_connection(self) -> None:
        if self.busy or self.device is None:
            return
        self.busy = True
        self.status = ""
        self._render()
        try:
            if self.state.connected:
                await self._close_session()
                ok, error = await asyncio.to_thread(bluetooth_disconnect, self.device.address)
                self.status = "" if ok else f"disconnect failed: {error}"
                self.sink = None
                self.volume = None
                self.device = KnownDevice(self.device.address, self.device.name, False)
            else:
                ok, error = await asyncio.to_thread(bluetooth_connect, self.device.address)
                if not ok:
                    self.status = f"connect failed: {error}"
                else:
                    self.device = KnownDevice(self.device.address, self.device.name, True)
                    # Give BlueZ a moment to publish the service records.
                    await asyncio.sleep(1.0)
                    await self._open_session()
        finally:
            self.busy = False
            self._sync_rows()

    def _on_device_update(self, state: BudsState) -> None:
        if not state.connected and self.connection is not None:
            self.status = "earbuds disconnected"
            self._offline = BudsState(
                address=state.address, name=state.name, profile=state.profile
            )
            self.connection = None
        self._sync_rows()

    async def action_reconnect(self) -> None:
        await self._close_session()
        self.status = ""
        self.run_worker(self._startup(), exclusive=True)

    # -- volume ------------------------------------------------------------

    async def _poll_volume(self) -> None:
        if not self.sink:
            return
        sink = self.sink
        volume, muted = await asyncio.to_thread(
            lambda: (volume_ctl.get_volume(sink), volume_ctl.get_mute(sink))
        )
        if volume is None:
            self.sink = await asyncio.to_thread(volume_ctl.sink_name, self.state.address)
            return
        if (volume, muted) != (self.volume, self.muted):
            self.volume, self.muted = volume, bool(muted)
            self._render()

    async def _set_volume(self, percent: int) -> None:
        if not self.sink:
            return
        self.volume = max(0, min(100, percent))
        self._render()
        await asyncio.to_thread(volume_ctl.set_volume, self.sink, self.volume)

    async def action_toggle_mute(self) -> None:
        if not self.sink:
            return
        self.muted = not self.muted
        self._render()
        await asyncio.to_thread(volume_ctl.set_mute, self.sink, self.muted)

    # -- navigation --------------------------------------------------------

    def _sync_rows(self) -> None:
        """Rebuild rows after a state change, keeping the cursor on its row."""
        previous = self._current()
        self.rows = ui.build_rows(self.state, has_sink=bool(self.sink))
        if previous is not None and previous in self.rows:
            self.cursor = self.rows.index(previous)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self._render()

    def _current(self) -> ui.Row | None:
        if 0 <= self.cursor < len(self.rows):
            return self.rows[self.cursor]
        return None

    def action_cursor(self, delta: int) -> None:
        if self.rows:
            self.cursor = (self.cursor + delta) % len(self.rows)
            self._render()

    def action_cursor_home(self) -> None:
        self.cursor = 0
        self._render()

    def action_cursor_end(self) -> None:
        self.cursor = max(0, len(self.rows) - 1)
        self._render()

    def action_group(self, delta: int) -> None:
        """Jump to the first row of the next (or previous) section."""
        current = self._current()
        if current is None:
            return
        groups: list[str] = []
        for row in self.rows:
            if row.group not in groups:
                groups.append(row.group)
        target = groups[(groups.index(current.group) + delta) % len(groups)]
        self.cursor = next(i for i, r in enumerate(self.rows) if r.group == target)
        self._render()

    # -- actions -----------------------------------------------------------

    async def action_select(self) -> None:
        row = self._current()
        if row is None:
            return
        if row.kind == ui.ROW_CONNECTION:
            await self._toggle_connection()
        elif row.kind == ui.ROW_MODE and row.mode is not None:
            await self._set_mode(row.mode)

    async def action_pick(self, index: int) -> None:
        modes = self.state.profile.modes
        if self.connection is not None and 0 <= index < len(modes):
            await self._set_mode(modes[index])

    async def action_adjust(self, delta: int) -> None:
        row = self._current()
        if row is None:
            return
        if row.kind == ui.ROW_VOLUME:
            await self._set_volume((self.volume or 0) + delta * VOLUME_STEP)
        elif row.kind == ui.ROW_LEVEL:
            await self._set_level(ui.level_value(self.state) + delta)
        # Connection and mode rows have nothing to adjust: acting on them is an
        # explicit enter, so that moving the cursor can never change anything.

    async def _set_mode(self, mode: NoiseControlMode) -> None:
        if self.connection is None:
            return
        # Update straight away so the UI is responsive; the ack confirms it.
        self.connection.update_state(noise_mode=mode)
        self._sync_rows()
        index = next(
            (i for i, r in enumerate(self.rows) if r.kind == ui.ROW_MODE and r.mode == mode), None
        )
        if index is not None:
            self.cursor = index
        self._render()
        try:
            await self.connection.set_noise_mode(mode)
        except Exception as exc:
            self.status = f"failed to set mode: {exc}"
            self._render()

    async def _set_level(self, level: int) -> None:
        if self.connection is None:
            return
        levels = ui.level_levels(self.state)
        if levels <= 1:
            return
        level = max(0, min(levels - 1, level))
        mode = self.state.noise_mode
        if mode == NoiseControlMode.AMBIENT:
            self.connection.update_state(ambient_level=level)
            message = MsgId.AMBIENT_VOLUME
        elif mode == NoiseControlMode.ANC:
            self.connection.update_state(anc_level=level)
            message = MsgId.NOISE_REDUCTION_LEVEL
        else:
            return
        self._render()
        try:
            await self.connection.send(message, bytes([level]))
        except Exception as exc:
            self.status = f"failed to set level: {exc}"
            self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        if not self.is_mounted:
            return
        try:
            level_box = self.query_one("#level")
            status_box = self.query_one("#status", Static)
        except Exception:
            return

        connected = self.state.connected
        self.query_one("#battery").display = connected
        self.query_one("#mode").display = connected
        level_box.display = self._row_index(ui.ROW_LEVEL) is not None
        level_box.border_title = ui.level_title(self.state)
        self.query_one("#volume").display = self._row_index(ui.ROW_VOLUME) is not None

        status_box.display = bool(self.status)
        status_box.update(Text(self.status, style=ui.STYLES["warn"]))

        current = self._current()
        group = current.group if current is not None else ""
        for widget_id, name in (
            ("#battery", ui.GROUP_BATTERY),
            ("#mode", ui.GROUP_MODE),
            ("#level", ui.GROUP_LEVEL),
            ("#volume", ui.GROUP_VOLUME),
        ):
            self.query_one(widget_id).set_active(group == name)

        for widget_id in ("#header", "#battery", "#mode", "#level", "#volume", "#hint"):
            self.query_one(widget_id).refresh(layout=True)

    async def action_quit(self) -> None:
        await self._close_session()
        self.exit()


def ui_panel(builder, **kwargs):
    from .widgets import Panel

    return Panel(builder, **kwargs)


def group_box(title, builder, **kwargs):
    from .widgets import GroupBox

    return GroupBox(title, builder, **kwargs)
