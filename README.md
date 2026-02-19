# keyboard-claude

Per-key RGB control for [Work Louder Micro](https://worklouder.cc/) keyboard, driven by [Claude Code](https://claude.ai/code) hooks.

When Claude needs your input, the keyboard lights up orange. When Claude is working, it goes dark.

## How It Works

Claude Code fires hooks on lifecycle events (tool use, stop, permission prompts). A shell hook (`hook.sh`) extracts the event type and appends it to a JSONL file. A Python daemon (`vial_kbd.py`) tails that file and sends per-key LED commands to the keyboard over USB Raw HID.

The keyboard runs custom QMK firmware with a `raw_hid_receive()` handler that accepts HSV color commands for each of the 12 per-key LEDs.

## Setup

### 1. Flash Firmware

```bash
brew install qmk/qmk/qmk && brew tap osx-cross/avr && brew install avr-gcc
qmk setup -y
./firmware/build.sh raw_hid
./firmware/build.sh flash    # hold top-left encoder + plug USB first
```

### 2. Install Hooks

```bash
python3 setup_hooks.py
```

### 3. Run Daemon

```bash
pip install hidapi
python3 vial_kbd.py
```

## Hardware

- **Keyboard**: Work Louder Micro (ATmega32u4, VID `0x574C`)
- **LEDs**: 12 per-key WS2812 + 8 underglow (separate driver)
- **Encoders**: 2 rotary at top corners
- **Firmware**: 85% of 28KB flash used
