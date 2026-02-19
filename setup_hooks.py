"""
Adds keyboard bridge hooks to ~/.claude/settings.json alongside existing hooks.
Run once to set up. Run with --remove to undo.
"""

import json
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_SCRIPT = str(Path(__file__).parent / "hook.sh")
HOOK_COMMAND = f"{HOOK_SCRIPT}"

# We need hooks on these events to track session state
EVENTS_TO_HOOK = ["Notification", "Stop", "PreToolUse", "PostToolUse", "UserPromptSubmit"]

MARKER = "claude-kbd-bridge"  # identify our hooks for clean removal


def add_hooks():
    settings = json.loads(SETTINGS_PATH.read_text())
    hooks = settings.setdefault("hooks", {})

    for event in EVENTS_TO_HOOK:
        matchers = hooks.setdefault(event, [])

        # Check if we already added our hook
        already = any(
            MARKER in h.get("command", "")
            for m in matchers
            for h in m.get("hooks", [])
        )
        if already:
            print(f"  {event}: already configured")
            continue

        # Append a new matcher entry with our hook
        matchers.append({
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    # Tag with marker comment so we can find/remove it
                    "command": f"{HOOK_COMMAND}  # {MARKER}",
                }
            ],
        })
        print(f"  {event}: added keyboard hook")

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    print("\nDone! Hooks saved to", SETTINGS_PATH)


def remove_hooks():
    settings = json.loads(SETTINGS_PATH.read_text())
    hooks = settings.get("hooks", {})

    for event in EVENTS_TO_HOOK:
        matchers = hooks.get(event, [])
        hooks[event] = [
            m for m in matchers
            if not any(MARKER in h.get("command", "") for h in m.get("hooks", []))
        ]
        # Remove empty event arrays
        if not hooks[event]:
            del hooks[event]

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    print("Removed keyboard hooks from", SETTINGS_PATH)


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove_hooks()
    else:
        add_hooks()
