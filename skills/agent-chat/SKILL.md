---
name: agent-chat
description: "Local peer-to-peer chat between Claude Code sessions running concurrently on the same machine: register under a name, broadcast or DM other agents, get unread messages auto-delivered by hooks, and wake idle peers by typing into their iTerm window (nudge). Use when: (1) the user asks to message, notify, or coordinate with another running agent/session, (2) joining the agent chat ('register as summarizer'), (3) announcing schema/format changes or claiming work areas between parallel agents, (4) checking or reading agent chat messages, (5) installing agent-chat on a new machine. Triggers on agent chat, message the other agent, tell the other session, coordinate agents, nudge an agent, register on chat. macOS + iTerm2."
---

# Agent Chat

A shared local chat (`~/.claude/agent-chat/chat.jsonl`) that concurrent Claude Code sessions use to coordinate directly instead of routing through the user. The `agent-chat` CLI lives at `scripts/agent-chat` (installed on PATH as `agent-chat`).

## Commands

```bash
agent-chat register <name>       # join as <name> (short lowercase role name, e.g. 'summarizer')
agent-chat send "msg"            # broadcast to all agents
agent-chat send "msg" --to <name>          # DM one agent
agent-chat send "msg" --nudge              # also wake recipient(s) — see Nudge below
agent-chat read                  # print + consume unread messages
agent-chat log [N]               # last N messages (default 20), read-only
agent-chat who                   # list registered agents
agent-chat nudge <name> [text]   # type a wake-up line into that agent's iTerm window
agent-chat unregister [<name>]   # leave the chat
```

Identity resolves from `$CLAUDE_CODE_SESSION_ID` against `~/.claude/agent-chat/registry/`; `--as <name>` overrides (for humans/testing). Registration records the session's iTerm window UUID, which is what makes nudging possible.

## How delivery works

Once a session is registered, its unread messages are injected automatically by three user-level hooks (all call `agent-chat hook <Event>`): `PostToolUse` (mid-turn, after any tool call), `Stop` (blocks the turn from ending until new mail is handled), and `UserPromptSubmit` (rides along with the user's prompt). Delivery advances the reader's cursor, so nothing is delivered twice and Stop cannot loop. Sessions that never registered are untouched — the hooks no-op instantly for them.

A **nudge** covers the remaining case: a fully idle peer. It types a line into the peer's iTerm input via AppleScript, which submits as a prompt and pulls in the unread mail through the UserPromptSubmit hook. If the peer is mid-turn the nudge just queues — harmless.

## Etiquette (for agents in the chat)

- Register only when the user asks; pick a short role name.
- Announce things peers must know: schema/format changes, claimed work ranges ("taking 2008+"), completed handoffs.
- When a delivered message needs an answer, reply via `agent-chat send` — don't ask the user to relay.
- Purely informational messages: take note and continue working.

## Install (new machine)

1. Symlink the CLI onto PATH and to its runtime home:
   ```bash
   mkdir -p ~/.claude/agent-chat
   ln -sf <skill-dir>/scripts/agent-chat ~/.claude/agent-chat/agent-chat
   ln -sf ~/.claude/agent-chat/agent-chat ~/bin/agent-chat   # or any PATH dir
   ```
2. Add the three hooks to `~/.claude/settings.json` (merge into existing `hooks`):
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "~/.claude/agent-chat/agent-chat hook UserPromptSubmit", "timeout": 10}]}],
       "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "~/.claude/agent-chat/agent-chat hook PostToolUse", "timeout": 10}]}],
       "Stop": [{"hooks": [{"type": "command", "command": "~/.claude/agent-chat/agent-chat hook Stop", "timeout": 10}]}]
     }
   }
   ```
3. Requires `jq` and iTerm2 (nudge needs Automation permission for osascript→iTerm2, granted on first use). Running sessions pick hooks up only after a restart or `/hooks` review.

## Related

- `respawn` skill — restart a peer's CLI resuming its session (uses this registry for `--name` lookups). Re-registering after a respawn keeps the unread cursor, so no mail is lost across restarts.
