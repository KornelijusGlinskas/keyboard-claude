#!/bin/bash
INPUT=$(cat)
echo "$INPUT" | /usr/bin/jq -c '{
  ts: now,
  session: .session_id,
  event: .hook_event_name,
  tool: (.tool_name // ""),
  notif: (.notification_type // "")
}' >> /tmp/claude-kbd-events.jsonl
