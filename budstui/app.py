"""The buds-tui Textual application."""

from __future__ import annotations

import asyncio
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from . import ui, volume as volume_ctl
from .device import BudsConnection, BudsState, list_connected_buds, profile_for
from .protocol import MsgId, NoiseControlMode

VOLUME_STEP = 5


class BudsApp(App):
    CSS = """
    Screen {
        background: #04120a;
        align: center middle;
    }
    #panel {
        width: 54;
        height: auto;
        padding: 1 2;
        border: round #4ade80;
        border-title-color: #4ade80;
        border-title-style: bold;
        background: #04120a;
        color: #7dd3a0;
    }
    #hint {
        width: 54;
        padding: 0 2;
        color: #2f6b47;
    }
    """

    BINDINGS = [
        Binding("j,down", "cursor(1)", "down", show=False),
        Binding("k,up", "cursor(-1)", "up", show=False),
        Binding("l,right", "adjust(1)", "increase", show=False),
        Binding("h,left", "adjust(-1)", "decrease", show=False),
        Binding("enter,space", "select", "select", show=False),
        Binding("tab", "cycle_mode", "cycle mode", show=False, priority=True),
        Binding("m", "toggle_mute", "mute", show=False),
        Binding("r", "reconnect", "reconnect", show=False),
        Binding("g", "cursor_home", "first", show=False),
        Binding("G", "cursor_end", "last", show=False),
        Binding("q,ctrl+c", "quit", "quit", show=False),
    ]

    def __init__(self, address: str | None = None) -> None:
        super().__init__()
        self._address = address
        self.connection: BudsConnection | None = None
        self._offline = BudsState(profile=profile_for(""))
        self.rows: list[ui.Row] = []
        self.cursor = 0
        self.sink: str | None = None
        self.volume: int | None = None
        self.muted = False
        self.status = "searching for earbuds..."

    @property
    def state(self) -> BudsState:
        """The connection owns the state; fall back to an empty one when offline."""
        return self.connection.state if self.connection is not None else self._offline

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        panel = Static(id="panel")
        panel.border_title = "galaxy buds"
        yield Container(panel, Static(_HINT, id="hint"), id="wrap")

    async def on_mount(self) -> None:
        self.rows = ui.build_rows(self.state)
        self._render()
        self.set_interval(2.0, self._poll_volume)
        self.run_worker(self._connect(), exclusive=True)

    # -- connection --------------------------------------------------------

    async def _connect(self) -> None:
        self.status = "searching for earbuds..."
        self._render()

        devices = await asyncio.to_thread(list_connected_buds)
        if self._address:
            devices = [d for d in devices if d[0].upper() == self._address.upper()] or [
                (self._address.upper(), "")
            ]
        if not devices:
            self.status = "no connected Galaxy Buds found - pair and connect them first, then press r"
            self._render()
            return

        address, name = devices[0]
        self.status = f"connecting to {name or address}..."
        self._render()

        connection = BudsConnection(address, name)
        connection.on_update.append(self._on_device_update)
        try:
            await connection.connect()
        except Exception as exc:  # surfacing the reason beats a blank panel
            self.status = f"could not connect: {exc}  (press r to retry)"
            self._render()
            return

        self.connection = connection
        self.sink = await asyncio.to_thread(volume_ctl.sink_name, address)
        self.status = ""
        self._sync_rows()
        await self._poll_volume()

    def _on_device_update(self, state: BudsState) -> None:
        if not state.connected and self.connection is not None:
            self.status = "earbuds disconnected  (press r to reconnect)"
        self._sync_rows()

    async def action_reconnect(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
        self.run_worker(self._connect(), exclusive=True)

    # -- volume ------------------------------------------------------------

    async def _poll_volume(self) -> None:
        if not self.sink:
            return
        volume, muted = await asyncio.to_thread(
            lambda: (volume_ctl.get_volume(self.sink), volume_ctl.get_mute(self.sink))
        )
        if volume is None:
            # The sink disappears when audio routing changes; look it up again.
            self.sink = await asyncio.to_thread(volume_ctl.sink_name, self.state.address)
            return
        if (volume, muted) != (self.volume, self.muted):
            self.volume, self.muted = volume, bool(muted)
            self._render()

    async def _set_volume(self, percent: int) -> None:
        if not self.sink:
            return
        percent = max(0, min(100, percent))
        self.volume = percent
        self._render()
        await asyncio.to_thread(volume_ctl.set_volume, self.sink, percent)

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
        self.rows = ui.build_rows(self.state)
        if previous is not None:
            for index, row in enumerate(self.rows):
                if row == previous:
                    self.cursor = index
                    break
            else:
                self.cursor = min(self.cursor, len(self.rows) - 1)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self._render()

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

    # -- actions -----------------------------------------------------------

    def _current(self) -> ui.Row | None:
        if 0 <= self.cursor < len(self.rows):
            return self.rows[self.cursor]
        return None

    async def action_select(self) -> None:
        row = self._current()
        if row is not None and row.kind == ui.ROW_MODE and row.mode is not None:
            await self._set_mode(row.mode)

    async def action_adjust(self, delta: int) -> None:
        row = self._current()
        if row is None:
            return
        if row.kind == ui.ROW_VOLUME:
            await self._set_volume((self.volume or 0) + delta * VOLUME_STEP)
        elif row.kind == ui.ROW_LEVEL:
            await self._set_level(ui.level_value(self.state) + delta)
        # Mode rows have nothing to adjust: picking one is an explicit enter/tab,
        # so that moving the cursor can never change what you are listening to.

    async def action_cycle_mode(self) -> None:
        modes = self.state.profile.modes
        index = modes.index(self.state.noise_mode) if self.state.noise_mode in modes else 0
        await self._set_mode(modes[(index + 1) % len(modes)])

    async def _set_mode(self, mode: NoiseControlMode) -> None:
        if self.connection is None:
            return
        # Update straight away so the UI is responsive; the ack confirms it.
        self.connection.update_state(noise_mode=mode)
        self._sync_rows()
        for index, row in enumerate(self.rows):
            if row.kind == ui.ROW_MODE and row.mode == mode:
                self.cursor = index
                break
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
        try:
            panel = self.query_one("#panel", Static)
        except Exception:
            return
        title = self.state.name or "galaxy buds"
        panel.border_title = title.lower()
        panel.update(
            ui.render_panel(
                self.state,
                volume=self.volume,
                muted=self.muted,
                rows=self.rows,
                cursor=self.cursor,
                status=self.status,
            )
        )

    async def action_quit(self) -> None:
        if self.connection is not None:
            await self.connection.close()
        self.exit()


_HINT = (
    "j/k move   h/l adjust   enter select   tab cycle\n"
    "m mute     r reconnect  q quit"
)
