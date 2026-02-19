"""
Orange breathing pulse for keyboard backlight via QMK VIA API.
Ctrl+C to stop (restores original settings).
"""

import math
import signal
import sys
import time

import qmk_via_api
from qmk_via_api import scan_keyboards

# #DE7356 → HSV 12.8°, 61%, 87%
# QMK hue 0-255 maps to 0-360°, sat/val 0-255
HUE = 9
SAT = 255  # max sat so LEDs don't wash out to white

# Pulse speed: full cycle in seconds
CYCLE = 2.5
MIN_BRIGHT = 30
MAX_BRIGHT = 222  # 87% of 255, matching #DE7356 value


def try_call(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def main():
    devices = scan_keyboards()
    if not devices:
        print("No VIA keyboard found.")
        return

    # Work Louder Micro only
    WORK_LOUDER_VID = 0x574C
    devices = [d for d in devices if d.vendor_id == WORK_LOUDER_VID]
    if not devices:
        print("No Work Louder keyboard found.")
        return

    apis = []
    for d in devices:
        try:
            apis.append(qmk_via_api.KeyboardApi(d.vendor_id, d.product_id, d.usage_page))
            print(f"  Connected VID=0x{d.vendor_id:04X} PID=0x{d.product_id:04X}")
        except Exception as e:
            print(f"  Skip VID=0x{d.vendor_id:04X}: {e}")

    if not apis:
        print("Could not connect to any keyboard.")
        return

    # Save originals
    originals = []
    for api in apis:
        originals.append({
            "mb": try_call(api.get_rgb_matrix_brightness),
            "lb": try_call(api.get_rgblight_brightness),
            "le": try_call(api.get_rgblight_effect),
            "lc": try_call(api.get_rgblight_color),
        })

    def restore(sig=None, frame=None):
        print("\nRestoring...")
        for api, orig in zip(apis, originals):
            if orig["mb"] is not None:
                try_call(api.set_rgb_matrix_brightness, orig["mb"])
            if orig["lb"] is not None:
                try_call(api.set_rgblight_brightness, orig["lb"])
            if orig["le"] is not None:
                try_call(api.set_rgblight_effect, orig["le"])
            if orig["lc"] is not None:
                try_call(api.set_rgblight_color, *orig["lc"])
        sys.exit(0)

    signal.signal(signal.SIGINT, restore)
    signal.signal(signal.SIGTERM, restore)

    # Turn off per-key LEDs, static effect for backlight
    for api in apis:
        try_call(api.set_rgb_matrix_brightness, 0)
        try_call(api.set_rgblight_effect, 1)

    print(f"Pulsing backlight orange on {len(apis)} keyboard(s) — Ctrl+C to stop\n")

    while True:
        t = (time.monotonic() % CYCLE) / CYCLE
        wave = (math.sin(t * 2 * math.pi - math.pi / 2) + 1) / 2
        bright = int(MIN_BRIGHT + wave * (MAX_BRIGHT - MIN_BRIGHT))

        for api in apis:
            try_call(api.set_rgblight_brightness, bright)
            try_call(api.set_rgblight_color, HUE, SAT)

        bar = "█" * (bright // 16)
        print(f"\r  {bar:<16} {bright:3d}", end="", flush=True)

        time.sleep(1 / 60)


if __name__ == "__main__":
    main()
