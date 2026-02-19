#!/usr/bin/env python3
"""
Per-key RGB control for Work Louder Micro.

Replaces claude_kbd.py with per-key LED control instead of global RGB.
Supports two firmware protocols:
  1. VIALRGB  — if running VIAL firmware (vial-qmk build)
  2. Raw HID  — if running custom Raw HID firmware (qmk build)

LED layout (from micro.c g_led_config):
    Row 0: [--], LED10, LED11, [--]    encoders (no LED)
    Row 1: LED9, LED8,  LED7,  LED6
    Row 2: LED2, LED3,  LED4,  LED5
    Row 3: [--], LED1,  LED0,  [--]    corner keys (no LED)

12 per-key LEDs indexed 0-11, grouped by physical row:
    Row 0 (top):    LEDs 10, 11
    Row 1:          LEDs 9, 8, 7, 6
    Row 2:          LEDs 2, 3, 4, 5
    Row 3 (bottom): LEDs 1, 0

Usage:
    pip install hidapi
    python3 vial_kbd.py
"""

import json
import signal
import struct
import sys
import time
from pathlib import Path

try:
    import hid
except ImportError:
    print("Missing 'hidapi' package. Install with: pip install hidapi")
    sys.exit(1)

STATE_FILE = Path("/tmp/claude-kbd-events.jsonl")

# --- LED layout ---
# Physical row → LED indices (from g_led_config in micro.c)
ROW_LEDS = {
    0: [10, 11],         # top row (between encoders)
    1: [9, 8, 7, 6],    # second row
    2: [2, 3, 4, 5],    # third row
    3: [1, 0],           # bottom row (between corner keys)
}
ALL_LEDS = [i for row in sorted(ROW_LEDS) for i in ROW_LEDS[row]]
NUM_LEDS = 12

# Orange pulse color in HSV (QMK scale: H=0-255, S=0-255, V=0-255)
# #DE7356 ≈ hue 9°/360° → 9/360*255 ≈ 6, but QMK hue 9 looked right before
ORANGE_H, ORANGE_S, ORANGE_V = 9, 255, 200

# Events that mean "your turn"
YOUR_TURN = {"Stop"}
YOUR_TURN_NOTIF = {"permission_prompt", "elicitation_dialog"}

# Events that mean "claude is working"
CLAUDE_WORKING = {"PreToolUse", "UserPromptSubmit"}

# --- USB constants ---
WL_VID = 0x574C
WL_PID = 0xE6E3
VIAL_SERIAL_MAGIC = "vial:f64c2b3c"
RAW_HID_USAGE_PAGE = 0xFF60
RAW_HID_USAGE = 0x61
MSG_LEN = 32


# === Protocol abstraction ===

class KeyboardProtocol:
    """Base class for keyboard LED control protocols."""

    def connect(self):
        raise NotImplementedError

    def enter_direct_mode(self):
        raise NotImplementedError

    def set_led(self, idx, h, s, v):
        raise NotImplementedError

    def set_all_leds(self, h, s, v):
        raise NotImplementedError

    def set_led_range(self, start, count, hsv_list):
        raise NotImplementedError

    def restore_effect(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class RawHIDProtocol(KeyboardProtocol):
    """Custom Raw HID protocol for per-key LED control."""

    def __init__(self):
        self.dev = None

    def connect(self):
        for desc in hid.enumerate(WL_VID, WL_PID):
            if desc["usage_page"] == RAW_HID_USAGE_PAGE and desc["usage"] == RAW_HID_USAGE:
                try:
                    dev = hid.device()
                    dev.open_path(desc["path"])
                    self.dev = dev
                except OSError:
                    continue  # device busy or no permission
                # Ping to verify firmware supports our protocol
                resp = self._send(bytes([0xF0]))
                if resp and resp[0] == 0xF0 and resp[1] == 0x01:
                    led_count = resp[2]
                    print(f"Raw HID connected ({led_count} LEDs)")
                    return True
                self.dev.close()
                self.dev = None
        return False

    def _send(self, msg):
        if not self.dev:
            return None
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        try:
            self.dev.write(b"\x00" + padded)
            return bytes(self.dev.read(MSG_LEN, timeout_ms=500))
        except OSError:
            return None

    def enter_direct_mode(self):
        self._send(bytes([0x05]))

    def set_led(self, idx, h, s, v):
        self._send(bytes([0x01, idx, h, s, v]))

    def set_all_leds(self, h, s, v):
        self._send(bytes([0x04, h, s, v]))

    def set_led_range(self, start, count, hsv_list):
        payload = bytes([0x02, start, count])
        for h, s, v in hsv_list:
            payload += bytes([h, s, v])
        self._send(payload)

    def restore_effect(self):
        self._send(bytes([0x03]))

    def close(self):
        if self.dev:
            self.dev.close()


class VialRGBProtocol(KeyboardProtocol):
    """VIALRGB direct LED control protocol."""

    CMD_VIA_LIGHTING_SET_VALUE = 0x07
    CMD_VIA_LIGHTING_GET_VALUE = 0x08
    VIALRGB_GET_INFO = 0x40
    VIALRGB_GET_NUMBER_LEDS = 0x43
    VIALRGB_SET_MODE = 0x41
    VIALRGB_DIRECT_FASTSET = 0x42
    VIALRGB_EFFECT_DIRECT = 1

    def __init__(self):
        self.dev = None

    def connect(self):
        for desc in hid.enumerate():
            sn = desc.get("serial_number", "")
            if VIAL_SERIAL_MAGIC not in sn:
                continue
            if desc["usage_page"] != RAW_HID_USAGE_PAGE or desc["usage"] != RAW_HID_USAGE:
                continue
            try:
                dev = hid.device()
                dev.open_path(desc["path"])
                self.dev = dev
                # Check VIA protocol version (>= 9 required for VIALRGB)
                resp = self._send(b"\x01")
                via_ver = (resp[1] << 8 | resp[2]) if resp else 0
                if not resp or resp[0] != 0x01 or via_ver < 9:
                    self.dev.close()
                    self.dev = None
                    continue
                # Check VIALRGB info
                resp = self._send(struct.pack("BB", self.CMD_VIA_LIGHTING_GET_VALUE,
                                              self.VIALRGB_GET_INFO))
                if resp and (resp[2] | (resp[3] << 8)) == 1:
                    # Get LED count
                    resp = self._send(struct.pack("BB", self.CMD_VIA_LIGHTING_GET_VALUE,
                                                  self.VIALRGB_GET_NUMBER_LEDS))
                    if resp:
                        count = struct.unpack("<H", resp[2:4])[0]
                        print(f"VIALRGB connected ({count} LEDs)")
                        return True
            except OSError:
                if self.dev:
                    self.dev.close()
                self.dev = None
        return False

    def _send(self, msg):
        if not self.dev:
            return None
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        try:
            self.dev.write(b"\x00" + padded)
            return bytes(self.dev.read(MSG_LEN, timeout_ms=500))
        except OSError:
            return None

    def enter_direct_mode(self):
        self._send(struct.pack("<BBHBBBB",
                               self.CMD_VIA_LIGHTING_SET_VALUE,
                               self.VIALRGB_SET_MODE,
                               self.VIALRGB_EFFECT_DIRECT,
                               128, 128, 128, 128))

    def set_led(self, idx, h, s, v):
        payload = struct.pack("<BBHB", self.CMD_VIA_LIGHTING_SET_VALUE,
                              self.VIALRGB_DIRECT_FASTSET, idx, 1)
        payload += bytes([h, s, v])
        self._send(payload)

    def set_all_leds(self, h, s, v):
        # Send in batches of 9 LEDs
        for start in range(0, NUM_LEDS, 9):
            batch = min(9, NUM_LEDS - start)
            payload = struct.pack("<BBHB", self.CMD_VIA_LIGHTING_SET_VALUE,
                                  self.VIALRGB_DIRECT_FASTSET, start, batch)
            for _ in range(batch):
                payload += bytes([h, s, v])
            self._send(payload)

    def set_led_range(self, start, count, hsv_list):
        payload = struct.pack("<BBHB", self.CMD_VIA_LIGHTING_SET_VALUE,
                              self.VIALRGB_DIRECT_FASTSET, start, count)
        for h, s, v in hsv_list:
            payload += bytes([h, s, v])
        self._send(payload)

    def restore_effect(self):
        # Set mode back to 0 (normal/default effect)
        self._send(struct.pack("<BBHBBBB",
                               self.CMD_VIA_LIGHTING_SET_VALUE,
                               self.VIALRGB_SET_MODE,
                               0, 128, 128, 128, 128))

    def close(self):
        if self.dev:
            self.dev.close()


# === Event processing ===

def read_new_events(pos):
    if not STATE_FILE.exists():
        return [], pos
    size = STATE_FILE.stat().st_size
    if size < pos:
        pos = 0
    if size == pos:
        return [], pos
    events = []
    with open(STATE_FILE) as f:
        f.seek(pos)
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        pos = f.tell()
    return events, pos


# === LED patterns ===

def set_your_turn(kb):
    """All per-key LEDs orange — Claude needs your input."""
    kb.set_all_leds(ORANGE_H, ORANGE_S, ORANGE_V)


def set_claude_working(kb):
    """All LEDs off — Claude is busy, keyboard is ambient."""
    kb.set_all_leds(0, 0, 0)


def set_row_color(kb, row, h, s, v):
    """Set a specific row to a color (for future per-tab use)."""
    leds = ROW_LEDS.get(row, [])
    for led_idx in leds:
        kb.set_led(led_idx, h, s, v)


# === Main loop ===

def connect():
    """Try VIALRGB first, fall back to Raw HID."""
    vial = VialRGBProtocol()
    if vial.connect():
        return vial

    raw = RawHIDProtocol()
    if raw.connect():
        return raw

    print("No compatible keyboard found.")
    print("Expected: Work Louder Micro with VIALRGB or Raw HID firmware")
    print(f"  VID=0x{WL_VID:04X} PID=0x{WL_PID:04X}")
    sys.exit(1)


def main():
    kb = connect()
    pulsing = False

    # Skip old events
    pos = STATE_FILE.stat().st_size if STATE_FILE.exists() else 0

    def quit_handler(sig=None, frame=None):
        if pulsing:
            kb.restore_effect()
        kb.close()
        print("\nBye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, quit_handler)
    signal.signal(signal.SIGTERM, quit_handler)

    # Start in "your turn" mode — you launched this, Claude is waiting
    kb.enter_direct_mode()
    set_your_turn(kb)
    pulsing = True
    print("Ready — orange on. Waiting for Claude events.\n")

    while True:
        events, pos = read_new_events(pos)

        for ev in events:
            event = ev.get("event", "")
            notif = ev.get("notif", "")

            if event in YOUR_TURN or (event == "Notification" and notif in YOUR_TURN_NOTIF):
                if not pulsing:
                    kb.enter_direct_mode()
                    set_your_turn(kb)
                    pulsing = True
                    print(f"  >>> Your turn ({event} {notif})")

            elif event in CLAUDE_WORKING:
                if pulsing:
                    kb.restore_effect()
                    pulsing = False
                    print(f"  <<< Claude working ({event})")

        time.sleep(0.05)


if __name__ == "__main__":
    main()
