# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bridge between Claude Code lifecycle events and the per-key RGB LEDs on a Work Louder Micro keyboard. Custom QMK firmware exposes a Raw HID protocol; a Python daemon translates Claude Code hook events into per-LED color commands.

## Architecture

```
Claude Code hook event → hook.sh (jq) → /tmp/claude-kbd-events.jsonl → vial_kbd.py → USB Raw HID → QMK firmware → 12 WS2812 LEDs
```

**Two-state machine:** Orange (your turn) ↔ Dark (Claude working). Transitions driven by event types in the JSONL file.

**Firmware** (`firmware/raw_hid/keymap.c`): Custom `raw_hid_receive()` handler with 6 commands over 32-byte HID reports. `rgb_matrix_indicators_user()` paints a buffer onto LEDs each frame (~30Hz), decoupling USB I/O from the render loop. Direct mode overrides the normal RGB effect; restoring exits direct mode.

**Daemon** (`vial_kbd.py`): Polls the JSONL file at 50ms. Has two protocol backends (`RawHIDProtocol`, `VialRGBProtocol`) behind a `KeyboardProtocol` abstraction. Tries VIALRGB first, falls back to Raw HID. Only Raw HID is currently used (VIAL doesn't fit ATmega32u4's 28KB flash).

## LED Index Mapping

The LED indices in `g_led_config` (upstream `micro.c`) don't match physical row order:
- Row 0 (top, between encoders): LEDs 10, 11
- Row 1: LEDs 9, 8, 7, 6
- Row 2: LEDs 2, 3, 4, 5
- Row 3 (bottom, between corners): LEDs 1, 0

The 4 corner positions (2 encoders + 2 bottom keys) have `NO_LED`.

## Build & Flash Firmware

```bash
brew install qmk/qmk/qmk && brew tap osx-cross/avr && brew install avr-gcc
qmk setup -y                          # first time: clones ~/qmk_firmware
./firmware/build.sh raw_hid           # copies keymap → ~/qmk_firmware, compiles
./firmware/build.sh flash             # hold top-left encoder + replug USB first
```

Firmware uses 85% of flash (4KB free). The compiled `.hex` is gitignored.

## Run the Daemon

```bash
pip install hidapi                     # NOT 'hid' — they conflict
python3 vial_kbd.py
```

macOS HID is exclusive-access — only one process can open the device. Kill `vial_kbd.py` before using VIA/Vial apps or running test scripts.

## Install Claude Code Hooks

```bash
python3 setup_hooks.py                 # adds hooks to ~/.claude/settings.json
python3 setup_hooks.py --remove        # removes only our hooks (tagged with marker)
```

## Key Constraints

- **`hidapi` not `hid`**: Both pip packages provide `import hid` but conflict. `hidapi` bundles its own `.so` and works on Apple Silicon. `hid` (ctypes wrapper) fails to find the dylib.
- **`timeout_ms` not `timeout`**: The `hidapi` package's `device.read()` uses `timeout_ms=` as the keyword argument.
- **Device open API**: Use `dev = hid.device(); dev.open_path(path)`, not `hid.Device(path=...)`.
- **ATmega32u4 flash limit**: 28KB usable. Current firmware is 24.5KB. Don't enable heavy QMK features (VIAL, many RGB effects, console).
- **Bootloader entry**: Hold top-left encoder knob + plug USB cable. Atmel DFU protocol.
- **Stock firmware recovery**: Download `.hex` from worklouder.cc/setup, flash via QMK Toolbox.
