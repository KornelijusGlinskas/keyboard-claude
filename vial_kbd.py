#!/usr/bin/env python3
"""
Multi-session per-key RGB control for Work Louder Micro.

Each Claude Code session gets its own LED. Pressing the physical key
under a blinking LED switches to that session's iTerm2 tab.

Architecture:
  hook.sh (with $ITERM_SESSION_ID) → JSONL → this daemon → USB HID → LEDs
  firmware key press (0xEE) → this daemon → osascript → iTerm2 tab switch

LED slot mapping (rows 1-2, left-to-right, top-to-bottom):
  Slot 0: LED 9  (row 1, col 0)    Slot 4: LED 2  (row 2, col 0)
  Slot 1: LED 8  (row 1, col 1)    Slot 5: LED 3  (row 2, col 1)
  Slot 2: LED 7  (row 1, col 2)    Slot 6: LED 4  (row 2, col 2)
  Slot 3: LED 6  (row 1, col 3)    Slot 7: LED 5  (row 2, col 3)

Top row LEDs 10, 11: global attention indicator.
"""

import json
import signal
import struct
import subprocess
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
ROW_LEDS = {
    0: [10, 11],
    1: [9, 8, 7, 6],
    2: [2, 3, 4, 5],
    3: [1, 0],
}
NUM_LEDS = 12

# Slot → LED index (rows 1-2, left-to-right, top-to-bottom)
SLOT_LEDS = [9, 8, 7, 6, 2, 3, 4, 5]
MAX_SLOTS = len(SLOT_LEDS)

# (row, col) → slot index for key press mapping
KEY_TO_SLOT = {
    (1, 0): 0, (1, 1): 1, (1, 2): 2, (1, 3): 3,
    (2, 0): 4, (2, 1): 5, (2, 2): 6, (2, 3): 7,
}

# Global indicator LEDs (top row)
GLOBAL_LEDS = [10, 11]

# Orange in HSV (QMK scale: H=0-255, S=0-255, V=0-255)
ORANGE_H, ORANGE_S, ORANGE_V = 9, 255, 200
DIM_V = 80  # dimmed brightness for "working" state

# Events that mean "your turn"
YOUR_TURN = {"Stop"}
YOUR_TURN_NOTIF = {"permission_prompt", "elicitation_dialog"}

# Events that mean "claude is working"
CLAUDE_WORKING = {"PreToolUse", "UserPromptSubmit"}

# Session timeouts
DIM_TIMEOUT = 300    # 5 min: dim LED if no events
RELEASE_TIMEOUT = 600  # 10 min: release slot

# --- USB constants ---
WL_VID = 0x574C
WL_PID = 0xE6E3
VIAL_SERIAL_MAGIC = "vial:f64c2b3c"
RAW_HID_USAGE_PAGE = 0xFF60
RAW_HID_USAGE = 0x61
MSG_LEN = 32

CMD_KEY_EVENT = 0xEE


# === Session tracking ===

class Session:
    __slots__ = ("session_id", "iterm_session", "state", "slot", "last_event_time")

    def __init__(self, session_id, iterm_session, slot):
        self.session_id = session_id
        self.iterm_session = iterm_session
        self.state = "working"  # "your_turn" or "working"
        self.slot = slot
        self.last_event_time = time.monotonic()


class SessionManager:
    def __init__(self):
        self.sessions = {}       # session_id → Session
        self.slot_used = [False] * MAX_SLOTS

    def get_or_create(self, session_id, iterm_session=None):
        if session_id in self.sessions:
            sess = self.sessions[session_id]
            # Update iterm_session if we get a better one
            if iterm_session and not sess.iterm_session:
                sess.iterm_session = iterm_session
            sess.last_event_time = time.monotonic()
            return sess

        slot = self._next_slot()
        if slot is None:
            return None  # all slots full

        sess = Session(session_id, iterm_session, slot)
        self.sessions[session_id] = sess
        self.slot_used[slot] = True
        return sess

    def _next_slot(self):
        for i, used in enumerate(self.slot_used):
            if not used:
                return i
        return None

    def release(self, session_id):
        sess = self.sessions.pop(session_id, None)
        if sess:
            self.slot_used[sess.slot] = False

    def get_by_slot(self, slot):
        for sess in self.sessions.values():
            if sess.slot == slot:
                return sess
        return None

    def any_your_turn(self):
        return any(s.state == "your_turn" for s in self.sessions.values())

    def all_working(self):
        return bool(self.sessions) and all(
            s.state == "working" for s in self.sessions.values()
        )

    def cleanup_stale(self):
        """Release slots for sessions with no recent events."""
        now = time.monotonic()
        stale = [
            sid for sid, s in self.sessions.items()
            if now - s.last_event_time > RELEASE_TIMEOUT
        ]
        for sid in stale:
            self.release(sid)
        return stale

    def get_dimmed(self):
        """Return sessions that should be dimmed (no events for DIM_TIMEOUT)."""
        now = time.monotonic()
        return [
            s for s in self.sessions.values()
            if now - s.last_event_time > DIM_TIMEOUT
        ]


# === Protocol abstraction ===

class KeyboardProtocol:
    def connect(self):
        raise NotImplementedError

    def enter_direct_mode(self):
        raise NotImplementedError

    def set_led(self, idx, h, s, v):
        raise NotImplementedError

    def set_all_leds(self, h, s, v):
        raise NotImplementedError

    def set_blink(self, idx, enable):
        raise NotImplementedError

    def set_underglow(self, h, s, v):
        raise NotImplementedError

    def set_underglow_breathe(self, h, s, v):
        raise NotImplementedError

    def restore_effect(self):
        raise NotImplementedError

    def poll_key_event(self):
        """Non-blocking read for 0xEE key events. Returns (row, col) or None."""
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class RawHIDProtocol(KeyboardProtocol):
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
                    continue
                resp = self._send(bytes([0xF0]))
                if resp and resp[0] == 0xF0 and resp[1] == 0x01:
                    led_count = resp[2]
                    print(f"Raw HID connected ({led_count} LEDs)")
                    return True
                self.dev.close()
                self.dev = None
        return False

    def _send(self, msg):
        """Send a command and read until we get a matching response.

        Stashes any 0xEE key events received while waiting.
        """
        if not self.dev:
            return None
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        try:
            self.dev.write(b"\x00" + padded)
        except OSError:
            return None
        return self._read_response(msg[0])

    def _read_response(self, expected_cmd, timeout_ms=500):
        """Read HID reports until we get one matching expected_cmd.

        Any 0xEE key events received in the meantime are stashed
        in self._pending_keys for later polling.
        """
        if not hasattr(self, "_pending_keys"):
            self._pending_keys = []
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                data = self.dev.read(MSG_LEN, timeout_ms=remaining)
            except OSError:
                return None
            if not data:
                continue
            raw = bytes(data)
            if raw[0] == CMD_KEY_EVENT:
                self._pending_keys.append((raw[1], raw[2]))
                continue
            if raw[0] == expected_cmd:
                return raw
        return None

    def enter_direct_mode(self):
        self._send(bytes([0x05]))

    def set_led(self, idx, h, s, v):
        self._send(bytes([0x01, idx, h, s, v]))

    def set_all_leds(self, h, s, v):
        self._send(bytes([0x04, h, s, v]))

    def set_blink(self, idx, enable):
        self._send(bytes([0x07, idx, 1 if enable else 0]))

    def set_underglow(self, h, s, v):
        self._send(bytes([0x06, h, s, v]))

    def set_underglow_breathe(self, h, s, v):
        self._send(bytes([0x0A, h, s, v]))

    def restore_effect(self):
        self._send(bytes([0x03]))

    def poll_key_event(self):
        if not hasattr(self, "_pending_keys"):
            self._pending_keys = []
        # Check for stashed events first
        if self._pending_keys:
            return self._pending_keys.pop(0)
        # Non-blocking read
        if not self.dev:
            return None
        try:
            data = self.dev.read(MSG_LEN, timeout_ms=5)
        except OSError:
            return None
        if data:
            raw = bytes(data)
            if raw[0] == CMD_KEY_EVENT:
                return (raw[1], raw[2])
        return None

    def close(self):
        if self.dev:
            self.dev.close()


class VialRGBProtocol(KeyboardProtocol):
    """VIALRGB direct LED control protocol (unused but kept for completeness)."""

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
                resp = self._send(b"\x01")
                via_ver = (resp[1] << 8 | resp[2]) if resp else 0
                if not resp or resp[0] != 0x01 or via_ver < 9:
                    self.dev.close()
                    self.dev = None
                    continue
                resp = self._send(struct.pack("BB", self.CMD_VIA_LIGHTING_GET_VALUE,
                                              self.VIALRGB_GET_INFO))
                if resp and (resp[2] | (resp[3] << 8)) == 1:
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
        for start in range(0, NUM_LEDS, 9):
            batch = min(9, NUM_LEDS - start)
            payload = struct.pack("<BBHB", self.CMD_VIA_LIGHTING_SET_VALUE,
                                  self.VIALRGB_DIRECT_FASTSET, start, batch)
            for _ in range(batch):
                payload += bytes([h, s, v])
            self._send(payload)

    def set_blink(self, idx, enable):
        pass  # VIALRGB doesn't support firmware-side blink

    def set_underglow(self, h, s, v):
        pass

    def set_underglow_breathe(self, h, s, v):
        pass

    def restore_effect(self):
        self._send(struct.pack("<BBHBBBB",
                               self.CMD_VIA_LIGHTING_SET_VALUE,
                               self.VIALRGB_SET_MODE,
                               0, 128, 128, 128, 128))

    def poll_key_event(self):
        return None  # VIALRGB firmware doesn't send key events

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


# === iTerm2 tab switching ===

def activate_iterm_tab(iterm_session_id):
    """Switch iTerm2 to the tab containing the given session ID.

    iterm_session_id format: "w0t0p0:GUID"
    We extract the tab identifier (e.g. "w0t0") to target the right tab.
    """
    if not iterm_session_id:
        return

    # $ITERM_SESSION_ID format: "w0t0p0:GUID" — extract the GUID part
    # iTerm2's AppleScript "unique ID" is just the GUID
    guid = iterm_session_id.split(":")[-1] if ":" in iterm_session_id else iterm_session_id

    script = f'''
    tell application "iTerm2"
        activate
        repeat with w in windows
            if miniaturized of w then
                set miniaturized of w to false
            end if
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if unique ID of s is "{guid}" then
                        select t
                        return
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    '''
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# === LED update logic ===

def update_leds(kb, mgr):
    """Push current session states to LEDs."""
    kb.enter_direct_mode()
    kb.set_all_leds(0, 0, 0)

    # Turn off all blinks first
    for i in range(NUM_LEDS):
        kb.set_blink(i, False)

    dimmed = {s.session_id for s in mgr.get_dimmed()}

    for sess in mgr.sessions.values():
        led = SLOT_LEDS[sess.slot]
        is_dim = sess.session_id in dimmed

        if sess.state == "your_turn":
            if is_dim:
                kb.set_led(led, ORANGE_H, ORANGE_S, DIM_V)
            else:
                kb.set_led(led, ORANGE_H, ORANGE_S, ORANGE_V)
                kb.set_blink(led, True)
        elif sess.state == "working":
            kb.set_led(led, ORANGE_H, ORANGE_S, DIM_V if is_dim else DIM_V)

    # Global indicator (top row): only useful with 2+ sessions
    if len(mgr.sessions) >= 2 and mgr.any_your_turn():
        for led in GLOBAL_LEDS:
            kb.set_led(led, ORANGE_H, ORANGE_S, ORANGE_V)
            kb.set_blink(led, True)

    # Underglow: always breathing while daemon runs
    kb.set_underglow_breathe(ORANGE_H, ORANGE_S, ORANGE_V)


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
    mgr = SessionManager()
    leds_dirty = False

    # Underglow breathing = daemon is active
    kb.set_underglow_breathe(ORANGE_H, ORANGE_S, ORANGE_V)

    pos = STATE_FILE.stat().st_size if STATE_FILE.exists() else 0
    last_cleanup = time.monotonic()

    def quit_handler(sig=None, frame=None):
        kb.enter_direct_mode()
        kb.set_all_leds(0, 0, 0)
        for i in range(NUM_LEDS):
            kb.set_blink(i, False)
        kb.set_underglow(0, 0, 0)
        kb.close()
        print("\nBye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, quit_handler)
    signal.signal(signal.SIGTERM, quit_handler)

    print(f"Ready — {MAX_SLOTS} session slots. Waiting for Claude events.\n")

    while True:
        # 1. Read JSONL events
        events, pos = read_new_events(pos)

        for ev in events:
            event = ev.get("event", "")
            notif = ev.get("notif", "")
            session_id = ev.get("session", "")
            iterm_session = ev.get("iterm_session", "")

            if not session_id:
                continue

            sess = mgr.get_or_create(session_id, iterm_session)
            if not sess:
                continue  # no slots available

            if event in YOUR_TURN or (event == "Notification" and notif in YOUR_TURN_NOTIF):
                if sess.state != "your_turn":
                    sess.state = "your_turn"
                    leds_dirty = True
                    print(f"  [{sess.slot}] >>> Your turn ({event} {notif})")

            elif event in CLAUDE_WORKING:
                if sess.state != "working":
                    sess.state = "working"
                    leds_dirty = True
                    print(f"  [{sess.slot}] <<< Working ({event})")

        # 2. Poll for key events from firmware
        key = kb.poll_key_event()
        if key:
            row, col = key
            slot = KEY_TO_SLOT.get((row, col))
            if slot is not None:
                sess = mgr.get_by_slot(slot)
                if sess and sess.iterm_session:
                    print(f"  [{slot}] KEY row={row} col={col} → iTerm {sess.iterm_session}")
                    activate_iterm_tab(sess.iterm_session)
                else:
                    print(f"  [{slot}] KEY row={row} col={col} (no session)")

        # 3. Periodic cleanup of stale sessions
        now = time.monotonic()
        if now - last_cleanup > 30:
            stale = mgr.cleanup_stale()
            if stale:
                leds_dirty = True
                for sid in stale:
                    print(f"  Released stale session {sid[:8]}...")
            last_cleanup = now

        # 4. Update LEDs if anything changed
        if leds_dirty:
            update_leds(kb, mgr)
            leds_dirty = False

        time.sleep(0.05)


if __name__ == "__main__":
    main()
