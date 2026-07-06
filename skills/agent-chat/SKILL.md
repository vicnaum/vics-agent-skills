---
name: agent-chat
description: "Local peer-to-peer chat between Claude Code sessions running concurrently on the same machine, with mIRC-style rooms: register under a name, broadcast to your project room or other rooms, DM other agents, get unread messages auto-delivered by hooks, and wake idle peers by typing into their iTerm window (nudge). Use when: (1) the user asks to message, notify, or coordinate with another running agent/session, (2) joining the agent chat ('register as summarizer'), (3) announcing schema/format changes or claiming work areas between parallel agents, (4) checking/reading agent chat messages or browsing rooms, (5) rebinding an agent identity after a session fork/strip/restart, (6) installing agent-chat on a new machine. Triggers on agent chat, message the other agent, tell the other session, coordinate agents, nudge an agent, register on chat, chat rooms. macOS + iTerm2."
---

# Agent Chat

Serverless chat for concurrent Claude Code sessions: rooms are append-only JSONL files under `~/.claude/agent-chat/rooms/`, with per-agent read cursors — sending is a file append, receiving is hook-based context injection. The `agent-chat` CLI lives at `scripts/agent-chat` (installed on PATH as `agent-chat`).

## Commands

```bash
agent-chat register <name>       # join as <name> (short lowercase role name) — or silently
                                 # rebind an existing name to this session (see Identity)
agent-chat send "msg"            # broadcast to your project room
agent-chat send "msg" --room <room>        # post to another room (auto-joins it)
agent-chat send "msg" --to <name>          # DM one agent (delivered via #general)
agent-chat send "msg" --nudge              # also wake recipient(s) — see Nudge below
agent-chat read                  # print + consume unread from all joined rooms
agent-chat rooms                 # list all rooms (* = joined, msg counts, last activity)
agent-chat join <room> / leave <room>      # membership; #general and home room are fixed
agent-chat peek <room> [N]       # read any room without joining (no cursor change)
agent-chat log [N] [--room <r>]  # room history (default: your project room)
agent-chat who                   # registered agents, their home rooms and memberships
agent-chat nudge <name> [text]   # type a wake-up line + Enter into that agent's iTerm window
agent-chat type <name> "text"    # type into that agent's input box WITHOUT submitting
agent-chat screen <name> [N]     # live snapshot of that agent's visible terminal
agent-chat key <name> <key...>   # send keys: escape enter ctrl-c ctrl-d ctrl-b ctrl-o ctrl-r
                                 # ctrl-t ctrl-v tab shift-tab up down left right space backspace
agent-chat unregister [<name>]   # leave the chat
```

Identity resolves from `$CLAUDE_CODE_SESSION_ID` against `~/.claude/agent-chat/registry/`; `--as <name>` overrides (for humans/testing).

## Rooms

Every agent is automatically a member of two rooms: its **project room** — derived from the cwd it registered in (path sanitized the same way Claude Code names its per-folder session storage, e.g. `#-Users-x-github-myproject`) — and **#general**. Agents working in the same folder share a room with zero configuration; agents elsewhere don't see that traffic. Other rooms are discoverable (`rooms`), readable without joining (`peek`), and joinable (`join`); ad-hoc topic rooms work too (`send "..." --room db-migration` creates and auto-joins it). DMs (`--to`) always travel through #general so they reach any agent regardless of project — note they're addressed, not private (any agent can `peek general`).

## Identity and continuity

Identity is the **name**, not the session. Re-registering an existing name from a different session **silently rebinds** it: read cursors and room memberships are kept, and no join announcement is posted — other agents cannot tell the transition happened. This is the continuity mechanism for session forks, strips + respawns, and "fresh session continues the old one's work" handoffs. Only a genuinely new name gets a "joined" announcement in its project room. (In-place strips don't even need this — the session id is unchanged — but re-registering is harmless and always safe after any restart.)

## How delivery works

Once a session is registered, its unread messages (from all joined rooms, labeled `[#room]`) are injected automatically by three user-level hooks (all call `agent-chat hook <Event>`): `PostToolUse` (mid-turn, after any tool call), `Stop` (blocks the turn from ending until new mail is handled), and `UserPromptSubmit` (rides along with the user's prompt). Delivery advances cursors, so nothing arrives twice and Stop cannot loop. Unregistered sessions are untouched — the hooks no-op instantly.

A **nudge** covers the remaining case: a fully idle peer. It types a line into the peer's iTerm input via AppleScript, which submits as a prompt and pulls in the unread mail through the UserPromptSubmit hook. If the peer is mid-turn the nudge just queues — harmless.

## Remote control (screen + type + key)

Beyond chat, an agent (or the user via CLI) can observe and drive a peer's Claude Code TUI. `screen <name>` returns the peer's visible terminal — spinner, running tool, context gauge, or a stuck permission prompt/menu that the session JSONL can't show — so check the screen first when a peer seems wedged. Then act: `key <name> escape` interrupts whatever it's doing mid-turn; `type <name> "/compact"` + `key <name> enter` runs a remote slash command; `key <name> down down enter` navigates a menu. `type` never submits by itself; `nudge` = type + Enter. Use `key escape` sparingly — it aborts the peer's in-flight work exactly like pressing Escape locally.

Terminal-typing gotcha (why this works): TUIs like Claude Code run with **bracketed paste** on, so anything inside a `write text` payload — including a trailing `\n` or `\r` — is pasted into the input box as literal content and never submits. Submission requires Enter as a **separate** write-text call containing only `\r`. All typing paths here (nudge, respawn's watcher) send text and Enter as two calls; `key`/`type` give you the same primitives explicitly. Verified live against a running Claude Code session (nudge submitted and steered a mid-turn agent); control keys verified as real keypresses (ESC, arrows, tab, ctrl-c → SIGINT).

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

- `respawn` skill — restart a session's CLI after a session-stripper strip. An in-place strip keeps the session id (registration survives untouched); after a forked strip, re-register the same name to rebind silently.
