"""
Claude Code → Keyboard LED bridge.

Keyboard untouched until Claude needs you.
  Your turn (Stop / permission / question) → pulse orange
  Claude working (PreToolUse / UserPromptSubmit) → restore keyboard
"""

import json
import signal
import sys
import time
from pathlib import Path

import qmk_via_api
from qmk_via_api import scan_keyboards

STATE_FILE = Path("/tmp/claude-kbd-events.jsonl")

# Orange pulse: #DE7356
HUE = 9
SAT = 255
MAX_BRIGHT = 222

# Events that mean "your turn"
YOUR_TURN = {"Stop"}
YOUR_TURN_NOTIF = {"permission_prompt", "elicitation_dialog"}

# Events that mean "claude is working"
CLAUDE_WORKING = {"PreToolUse", "UserPromptSubmit"}


def try_call(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def connect():
    devices = scan_keyboards()
    wl = [d for d in devices if d.vendor_id == 0x574C]
    if not wl:
        print("No Work Louder keyboard found.")
        sys.exit(1)
    d = wl[0]
    api = qmk_via_api.KeyboardApi(d.vendor_id, d.product_id, d.usage_page)
    print(f"Keyboard connected: PID=0x{d.product_id:04X}")
    return api


def save_state(api):
    return {
        "mb": try_call(api.get_rgb_matrix_brightness),
        "lb": try_call(api.get_rgblight_brightness),
        "le": try_call(api.get_rgblight_effect),
        "lc": try_call(api.get_rgblight_color),
    }


def restore_state(api, orig):
    if orig["mb"] is not None: try_call(api.set_rgb_matrix_brightness, orig["mb"])
    if orig["lb"] is not None: try_call(api.set_rgblight_brightness, orig["lb"])
    if orig["le"] is not None: try_call(api.set_rgblight_effect, orig["le"])
    if orig["lc"] is not None: try_call(api.set_rgblight_color, *orig["lc"])


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


def main():
    api = connect()
    orig = save_state(api)
    pulsing = False

    # Skip old events
    pos = STATE_FILE.stat().st_size if STATE_FILE.exists() else 0

    def quit_handler(sig=None, frame=None):
        if pulsing:
            restore_state(api, orig)
        print("\nBye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, quit_handler)
    signal.signal(signal.SIGTERM, quit_handler)

    # Start in "your turn" mode — you launched this, Claude is waiting
    orig = save_state(api)
    try_call(api.set_rgb_matrix_brightness, 0)
    try_call(api.set_rgblight_effect, 1)
    try_call(api.set_rgblight_color, HUE, SAT)
    try_call(api.set_rgblight_brightness, MAX_BRIGHT)
    pulsing = True
    print("Ready — orange on. Waiting for Claude events.\n")

    while True:
        events, pos = read_new_events(pos)

        for ev in events:
            event = ev.get("event", "")
            notif = ev.get("notif", "")

            if event in YOUR_TURN or (event == "Notification" and notif in YOUR_TURN_NOTIF):
                if not pulsing:
                    orig = save_state(api)  # snapshot current state
                    try_call(api.set_rgb_matrix_brightness, 0)
                    try_call(api.set_rgblight_effect, 1)
                    try_call(api.set_rgblight_color, HUE, SAT)
                    try_call(api.set_rgblight_brightness, MAX_BRIGHT)
                    pulsing = True
                    print(f"  >>> Your turn ({event} {notif})")

            elif event in CLAUDE_WORKING:
                if pulsing:
                    restore_state(api, orig)
                    pulsing = False
                    print(f"  <<< Claude working ({event})")

        time.sleep(0.05)


if __name__ == "__main__":
    main()
