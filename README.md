# buds-tui

A terminal UI for managing Samsung Galaxy Buds on Linux. Built and verified against the **Galaxy Buds4 Pro**, with support for other models in the same protocol family.

```
    Danilo's Buds4 Pro  ● connected                       L:in  R:in
  ╭─ battery ──────────────────────────────────────────────────────╮
  │   left    █████████████████████████████░░░░░░░░░░░░░░░░░░  62% │
  │   right   ██████████████████████████████░░░░░░░░░░░░░░░░░  63% │
  │   case    ████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░  43% │
  ╰────────────────────────────────────────────────────────────────╯
  ╭─ sound mode ───────────────────────────────────────────────────╮
  │   off                                                          │
  │ ▸ ambient sound                                       (active) │
  │   adaptive                                                     │
  │   active noise canceling                                       │
  ╰────────────────────────────────────────────────────────────────╯
  ╭─ ambient sound level ──────────────────────────────────────────╮
  │   - [──────────────────────────────────────────●] +    lvl 5/5 │
  ╰────────────────────────────────────────────────────────────────╯
  ╭─ volume ───────────────────────────────────────────────────────╮
  │   - [─────────────●─────────────────────────────] +        30% │
  ╰────────────────────────────────────────────────────────────────╯
  ╭─ settings ─────────────────────────────────────────────────────╮
  │   conversation detect                                       on │
  │   conversation timeout                                  10 sec │
  │   noise control with one earbud                            off │
  │   sidetone on calls                                        off │
  │   seamless connection                                       on │
  │   call path control                                         on │
  │   pause when a bud is removed                               on │
  │   stereo balance                             [────●───] center │
  ╰────────────────────────────────────────────────────────────────╯
  j/k navigate   enter select   h/l adjust   q quit
```

## What it does

- **Connect / disconnect** on the top line, focused the moment the app opens, so `enter` toggles the link. This drops the Bluetooth connection itself, not just the control session, so audio is released too.
- **Battery** for each earbud and the case, with a charging indicator.
- **Sound mode**: off, ambient sound, adaptive, active noise canceling.
- **Level slider** that appears only for the modes that have one — ambient sound volume, or ANC strength — and retitles itself to match.
- **Overall volume**, which is the PipeWire/PulseAudio sink for the earbuds (this is what AVRCP mirrors onto the device).
- **Settings**: conversation detect (and its timeout), noise control with one earbud, sidetone, seamless connection, call path control, pause when a bud is removed, and stereo balance.

The layout is responsive. Bars and slider tracks grow with the terminal, labels shorten, and below roughly 30 columns the bars drop out entirely rather than wrap — down to about 20 columns everything stays readable.

## Requirements

- Linux with BlueZ, and the earbuds already paired and connected.
- PipeWire or PulseAudio with `pactl` for the volume slider.
- A Python built with Bluetooth socket support. Distribution interpreters have it; the standalone builds `uv` downloads by default do **not**, which is why `pyproject.toml` pins `python-preference = "only-system"`.

## Running

```sh
uv run buds-tui              # or: uv run python -m budstui
uv run buds-tui -a AA:BB:CC:DD:EE:FF   # pick a specific pair
```

Without `--address` it connects to the first connected device that advertises the Galaxy Buds service.

## Keys

| Key | Action |
| --- | --- |
| `j` / `k`, `↓` / `↑` | move the cursor |
| `h` / `l`, `←` / `→` | adjust the slider under the cursor |
| `enter` / `space` | connect/disconnect, pick the sound mode, or flip the setting under the cursor |
| `tab` / `shift+tab` | jump to the next / previous section |
| `1`–`4` | pick a sound mode directly |
| `m` | mute / unmute |
| `r` | reconnect the control session |
| `g` / `G` | first / last row |
| `q` | quit |

Moving the cursor never changes what you are listening to: picking a mode is always an explicit `enter` or number key.

On startup the app attaches to earbuds that are already connected, but it will not bring the link up on its own — that is what the connection row is for.

## Settings

Settings live in their own group at the bottom. Toggles flip with `enter` or `h`/`l`; the timeout is a choice list; stereo balance is a slider, so it takes `h`/`l` only. `conversation timeout` appears only while `conversation detect` is on.

Every setting here was confirmed against real hardware: each was written with both values and the resulting status payloads diffed, which pins down both that the write lands and which payload offset reads it back. The earbuds acknowledge each write with the value they actually applied, so the display also self-corrects as you use it.

`conversation timeout` is write-only — the earbuds acknowledge it but never report it in the status payload, so it is tracked from the acknowledgement alone and reads `--` until you set it.

Three settings that the reference implementation exposes are deliberately **not** shipped: gaming mode (135), double-tap edge for volume (149) and adaptive volume (197). On Buds4 Pro firmware, writing either value to any of them produces no acknowledgement and changes nothing in the status payload, while all eight shipped settings acknowledge immediately. They are recorded in `UNSUPPORTED` in `budstui/settings.py` so another model can promote them.

Adding another setting is one entry in `SETTINGS` in `budstui/settings.py`: a label, the message id, the kind, and the payload offset it reads back from.

## Supported models

The wire protocol is shared across the Buds Pro generation and later. Model detection is by advertised name, and only the number of level steps and the presence of the adaptive mode differ:

| Model | Ambient levels | ANC levels | Adaptive |
| --- | --- | --- | --- |
| Buds4 Pro, Buds4 | 5 | 5 | yes |
| Buds3 Pro, Buds3 | 5 | 2 | yes |
| Buds2 Pro | 4 | 2 | no |
| Buds2 | 4 | — | no |
| Buds Pro | 4 | 2 | no |
| Buds Live | — | — | no |

Only the Buds4 Pro row is verified against hardware; the others follow the reference protocol implementation and are best-effort. Adding a model means adding one row to `MODEL_PROFILES` in `budstui/device.py`.

## How it talks to the earbuds

The earbuds expose a vendor RFCOMM service (`2e73a4ad-332d-41fc-90e2-16bef06523f2`, service name `GEARMANAGER`). BlueZ does not report the channel that service lives on, so `budstui/sdp.py` runs its own SDP query over L2CAP and parses the ProtocolDescriptorList.

Frames look like this:

```
FD | size_lo size_hi | msg_id | payload... | crc_lo crc_hi | DD
```

`size` counts the message id, payload and CRC. The CRC is CRC-16/CCITT (XMODEM), little endian. The earbuds push a 62-byte `EXTENDED_STATUS_UPDATED` (`0x61`) snapshot as soon as the socket opens, and acknowledge every command with `0x42` carrying `[echoed_msg_id, value]`.

The fields this app uses, at these payload offsets:

| Offset | Meaning |
| --- | --- |
| 2, 3 | left / right battery |
| 6 | placement nibbles (worn, idle, in case) |
| 12 | noise control mode — 0 off, 1 ANC, 2 ambient, 3 adaptive |
| 23 | ambient sound level |
| 24 | ANC level |

Case battery and charging bits arrive in the shorter `STATUS_UPDATED` (`0x60`) frame, which the earbuds emit when something actually changes rather than on request.

### A note on level ranges

The reference implementation ([GalaxyBudsClient](https://github.com/timschneeb/GalaxyBudsClient)) marks Buds4/Buds4 Pro as not fully implemented, so offsets 12, 23 and 24 were mapped by sweeping each value and diffing the resulting status payloads.

The firmware stores whatever level you write, up to at least 7, without clamping, so the register itself does not reveal the intended maximum. Both levels sit at 4 out of the box, so this app treats them as 0–4 and displays them as `lvl 1/5` through `lvl 5/5`. Values above that are accepted by the earbuds but may not map to a distinct audible step.

## Development

```sh
uv run python -m pytest tests -q
```

The tests run against frames captured from real hardware, so they need no earbuds present. `budstui/ui.py` is deliberately free of Textual and of I/O, so the panel can be rendered to a plain Rich console in tests.
