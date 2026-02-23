#!/bin/bash
INPUT=$(cat)
echo "$INPUT" | /usr/bin/jq -c --arg iterm "$ITERM_SESSION_ID" '{
  ts: now,
  session: .session_id,
  event: .hook_event_name,
  tool: (.tool_name // ""),
  notif: (.notification_type // ""),
  iterm_session: $iterm
}' >> /tmp/claude-kbd-events.jsonl
