#!/usr/bin/env python3
"""Web UI for controlling Work Louder Micro LEDs over Raw HID."""

import json
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

try:
    import hid
except ImportError:
    print("Missing 'hidapi' package. Install with: pip install hidapi")
    sys.exit(1)

WL_VID = 0x574C
WL_PID = 0xE6E3
RAW_HID_USAGE_PAGE = 0xFF60
RAW_HID_USAGE = 0x61
MSG_LEN = 32

dev = None
dev_lock = threading.Lock()


def connect():
    global dev
    d = hid.device()
    for desc in hid.enumerate(WL_VID, WL_PID):
        if desc["usage_page"] == RAW_HID_USAGE_PAGE and desc["usage"] == RAW_HID_USAGE:
            try:
                d.open_path(desc["path"])
                dev = d
                return True
            except OSError:
                continue
    return False


def send(msg):
    with dev_lock:
        if not dev:
            return None
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        try:
            dev.write(b"\x00" + padded)
            return bytes(dev.read(MSG_LEN, timeout_ms=500))
        except OSError:
            return None


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            content = (Path(__file__).parent / "led_ui.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/init":
            send(bytes([0x05]))
            send(bytes([0x04, 0, 0, 0]))
            result = {"ok": True}

        elif self.path == "/api/led":
            idx = body["idx"]
            h, s, v = body["h"], body["s"], body["v"]
            send(bytes([0x01, idx, h, s, v]))
            result = {"ok": True}

        elif self.path == "/api/all":
            h, s, v = body["h"], body["s"], body["v"]
            send(bytes([0x04, h, s, v]))
            result = {"ok": True}

        elif self.path == "/api/blink":
            idx = body["idx"]
            enable = body["enable"]
            send(bytes([0x07, idx, 1 if enable else 0]))
            result = {"ok": True}

        elif self.path == "/api/blink-speed":
            ms = body["ms"]
            send(bytes([0x08, ms & 0xFF, (ms >> 8) & 0xFF]))
            result = {"ok": True}

        elif self.path == "/api/underglow":
            h, s, v = body["h"], body["s"], body["v"]
            send(bytes([0x06, h, s, v]))
            result = {"ok": True}

        elif self.path == "/api/restore":
            send(bytes([0x03]))
            result = {"ok": True}

        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        pass  # quiet


def main():
    if not connect():
        print("Could not connect to keyboard.")
        print("Make sure vial_kbd.py is not running (it holds exclusive HID access).")
        sys.exit(1)

    # Enter direct mode, turn off underglow default effect
    send(bytes([0x05]))
    send(bytes([0x06, 0, 0, 0]))

    port = 8787
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"LED UI → http://localhost:{port}")
    print("Ctrl+C to quit\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        send(bytes([0x03]))  # restore normal effect
        dev.close()
        print("\nBye.")


if __name__ == "__main__":
    main()
