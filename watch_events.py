"""Step 2: Just print events as they arrive. No keyboard, no logic.
Run this in a separate terminal and use Claude in another — you should
see events printed instantly.
"""
import json
import time
from pathlib import Path

STATE_FILE = Path("/tmp/claude-kbd-events.jsonl")

# Start from end of file — only show new events
pos = STATE_FILE.stat().st_size if STATE_FILE.exists() else 0
print("Watching events (only new ones)...\n")

while True:
    if not STATE_FILE.exists():
        time.sleep(0.1)
        continue

    size = STATE_FILE.stat().st_size
    if size < pos:
        pos = 0  # file was truncated

    if size > pos:
        with open(STATE_FILE) as f:
            f.seek(pos)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    event = ev.get("event", "?")
                    tool = ev.get("tool", "")
                    notif = ev.get("notif", "")
                    detail = tool or notif or ""
                    print(f"  {event:20s} {detail}")
                except json.JSONDecodeError:
                    print(f"  [bad json] {line[:60]}")
            pos = f.tell()

    time.sleep(0.05)
