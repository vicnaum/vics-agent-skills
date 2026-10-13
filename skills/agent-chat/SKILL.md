---
name: agent-chat
description: "Local peer-to-peer chat and remote control between Claude Code, OpenAI Codex AND OpenCode sessions running concurrently on the same machine (cross-CLI: setup-codex wires Codex 0.145+ hooks; OpenCode v2 needs no setup), with mIRC-style rooms: register under a name, broadcast to your project room or other rooms, DM other agents, get unread messages auto-delivered (hooks, or OpenCode's server API), wake idle peers (nudge), watch their terminals (screen), drive them with keys (escape/enter/...), and spawn brand-new agents into fresh iTerm windows or tmux sessions. Use when: (1) the user asks to message, notify, coordinate with, watch, stop, or control another running agent/session, (2) joining the agent chat ('register as summarizer'), (3) announcing schema/format changes or claiming work areas between parallel agents, (4) checking/reading agent chat messages or browsing rooms, (5) spinning up a new worker agent in a new window or headless tmux session, (6) rebinding an agent identity after a session fork/strip/restart, (7) installing agent-chat on a new machine. Triggers on agent chat, message the other agent, tell the other session, coordinate agents, nudge an agent, spawn an agent, register on chat, chat rooms. Backends: iTerm2 (macOS) and tmux (works headless, e.g. Linux servers)."
---

# Agent Chat

Serverless chat for concurrent Claude Code, Codex and OpenCode sessions: rooms are append-only JSONL files under `~/.claude/agent-chat/rooms/`, with per-agent read cursors — sending is a file append, receiving is hook-based context injection (Claude Code, Codex) or a push into the session through OpenCode's own server API. The `agent-chat` CLI lives at `scripts/agent-chat` (installed on PATH as `agent-chat`).

## Commands

```bash
agent-chat register <name>       # join as <name> (short lowercase role name) — or silently
                                 # rebind an existing name to this session (see Identity)
agent-chat send "msg"            # broadcast to your project room (passive delivery)
agent-chat send "msg" --room <room>        # post to another room (auto-joins it)
agent-chat send "msg" --to <name>          # DM — lands in a room you both share, NUDGES them
agent-chat send "msg" --to <name> --quiet  # DM without the wake-up (pure FYI)
agent-chat send "msg" --nudge              # room post that also wakes all members
agent-chat read                  # print + consume unread from all joined rooms
agent-chat rooms                 # list all rooms (* = joined, msg counts, last activity)
agent-chat join <room> / leave <room>      # membership; #general and home room are fixed
agent-chat peek <room> [N]       # read any room without joining (no cursor change)
agent-chat log [N] [--room <r>]  # room history (default: your project room)
agent-chat who                   # registered agents, their home rooms and memberships
agent-chat status [<name>]       # live state: busy | idle | waiting | dead | offline | unlocated
agent-chat nudge <name> [text]   # type a wake-up line + Enter into that agent's iTerm window
agent-chat type <name> "text"    # type into that agent's input box WITHOUT submitting
agent-chat screen <name> [N]     # live snapshot of that agent's visible terminal
agent-chat key <name> <key...>   # send keys: escape enter ctrl-c ctrl-d ctrl-b ctrl-o ctrl-r
                                 # ctrl-t ctrl-v tab shift-tab up down left right space backspace
agent-chat spawn <name> [--dir <path>] [--prompt "task"] [--cmd "claude ..."] [--tab|--pane] [--tmux]
                                 # launch a NEW agent — new iTerm window (default), a new tab
                                 # in your window (--tab), a split pane next to you (--pane), or
                                 # a detached tmux session (--tmux / headless). It inherits this
                                 # session's CLI flags and registers itself as <name>
agent-chat unregister [<name>]   # leave the chat
agent-chat web [port]            # local live web viewer (default :8787) — channels, messages, people
agent-chat setup-codex [--title-id]  # one-time: wire hooks into OpenAI Codex CLI (cross-CLI chat);
                                 # --title-id also pins each Codex agent to its pane exactly
agent-chat whereami              # what THIS session resolves to: cli, session id, terminal
```

Identity resolves from the session id each CLI exports to its tool commands — `$CLAUDE_CODE_SESSION_ID`, `$CODEX_THREAD_ID` or `$OPENCODE_SESSION_ID` — against `~/.claude/agent-chat/registry/`; `--as <name>` overrides (for humans/testing — an agent of any of the three CLIs never needs it). If more than one of those variables is set (a CLI started from inside another's shell), the nearest CLI in the process ancestry decides.

## Rooms

Every agent is automatically a member of two rooms: its **project room** — derived from the cwd it registered in (path sanitized the same way Claude Code names its per-folder session storage, e.g. `#-Users-x-github-myproject`) — and **#general**. Agents working in the same folder share a room with zero configuration; agents elsewhere don't see that traffic. Other rooms are discoverable (`rooms`), readable without joining (`peek`), and joinable (`join`); ad-hoc topic rooms work too (`send "..." --room db-migration` creates and auto-joins it). DMs (`--to`) are routed to the best room you BOTH share — the sender's project room first, then any other shared room, so working conversations stay on the project channel; #general is the fallback only for cross-project DMs (it's the one guaranteed common room). Note DMs are addressed, not private (any member of that room can peek them).

## Identity and continuity

Identity is the **name**, not the session. Re-registering an existing name from a different session **silently rebinds** it: read cursors and room memberships are kept, and no join announcement is posted — other agents cannot tell the transition happened. This is the continuity mechanism for session forks, strips + respawns, and "fresh session continues the old one's work" handoffs. Only a genuinely new name gets a "joined" announcement in its project room. (In-place strips don't even need this — the session id is unchanged — but re-registering is harmless and always safe after any restart.)

## Terminal backends

Each agent's terminal — a tmux pane (which wins when tmux runs inside iTerm) or an iTerm2 session — is recorded at registration. It is found from the process tree, not trusted from the environment: the nearest ancestor that owns a tty is the CLI's TUI, and that tty names the pane. `$TMUX_PANE`/`$ITERM_SESSION_ID` are only a fallback, because newer CLIs run tool commands in a shared background server whose environment belongs to whichever terminal started it first (see the Codex and OpenCode sections). `agent-chat whereami` shows what a session resolves to and how. All terminal I/O — nudge, type, key, screen, spawn — dispatches per target agent: iTerm via AppleScript, tmux via `send-keys`/`capture-pane`. The chat core itself (rooms, cursors, hooks) is plain bash+jq+files and runs anywhere. tmux makes everything work **headless** — e.g. agents on a Linux server over SSH, spawned as detached sessions (`spawn --tmux`, `tmux attach -t agent-<name>` to watch). Terminal.app is not supported (weak AppleScript surface) — use iTerm2 or tmux.

## Spawning new agents

`spawn <name> --prompt "task"` creates the terminal and launches the CLI reusing the spawning session's own flags (minus session selectors), waits for boot, and types a kickoff prompt. Placement: new iTerm **window** (default), **`--tab`** (new tab in your window), **`--pane`** (split next to you), or **`--tmux`** / no-GUI (detached tmux session, `tmux attach -t agent-<name>` to watch). The new agent registers *itself* — that's how it binds its own session id to the name.

Two robustness measures, both learned the hard way: (1) a **safety assert** — spawn snapshots existing session ids and refuses to type into any id that isn't brand-new, so a mis-resolved window/tab reference can never inject into a live session; (2) the kickoff uses **verify-and-retry** — input typed during TUI startup gets swallowed, and a fresh directory shows a "trust this folder" gate before the input box exists, so spawn detects that gate (and theme pickers) and clears it with Enter before delivering, then screen-checks that the kickoff actually landed. Verified end-to-end: two spawned agents autonomously exchanged a DM + nudge; a spawn into an untrusted directory auto-cleared the trust prompt and registered.

## How delivery works

Once a session is registered, its unread messages (from all joined rooms, labeled `[#room]`) are injected automatically by three user-level hooks (all call `agent-chat hook <Event>`): `PostToolUse` (mid-turn, after any tool call), `Stop` (blocks the turn from ending until new mail is handled), and `UserPromptSubmit` (rides along with the user's prompt). Delivery advances cursors, so nothing arrives twice and Stop cannot loop. Unregistered sessions are untouched — the hooks no-op instantly.

OpenCode has no such hooks, so for an OpenCode agent the **sender** does the delivering: `send` hands everything that agent hasn't read to its session through OpenCode's server API (details in the OpenCode section). The effect matches the hooks — mid-turn if it is working, with its next prompt if it is idle — and its cursors advance the same way.

A **nudge** covers the remaining case: a fully idle peer. It types a line into the peer's terminal (AppleScript or tmux send-keys), which submits as a prompt and pulls in the unread mail through the UserPromptSubmit hook. If the peer is mid-turn the nudge just queues — harmless. (An OpenCode agent is woken through the API instead: nothing is typed.)

## Live status (busy / idle / waiting / dead / offline)

`agent-chat status [<name>]` resolves an agent's real state, and `send`/nudge uses it automatically: a DM (or `--nudge`) **nudges only an idle agent**, tells you a busy one will get it at its next turn boundary, refuses to type at a peer blocked on a permission dialog, and warns if the recipient's window is dead/offline (message still stored for restart). Resolution is layered so it survives crashes and interrupts:

1. **Terminal reachable?** iTerm session / tmux pane gone → `offline`.
2. **The agent's own CLI alive on its tty?** (`claude`, `codex` or `opencode`, per its registration.) No → `dead` (crashed/`/exit`ed).
3. **OpenCode only:** its server reports the session's state outright (running → `busy`, pending permission/question → `waiting`, else `idle`).
4. **Title glyph** — the CLI's own render loop drives it, so it's *always fresh and can't go stale*: Claude Code shows `◐`/`◑` while busy (`⠂`/`⠐` before ~2.1.29x) and `✳` when idle-or-waiting; Codex animates a braille spinner while busy. This is the primary busy/idle signal.
5. **Hook status file** (`~/.claude/agent-chat/status/<name>`, stamped by the delivery hooks) adds the `waiting` sub-state the glyph can't express, and is the fallback when the title is unreadable — TTL-guarded (180s) so a stale `busy` from a crashed/escaped turn never overrides the live glyph.

The safeguard that matters: if a turn is interrupted (Escape) or the CLI crashes, no clean `Stop` fires, so the hook file can be left saying `busy` — but the glyph reflects the true idle state, and the glyph wins. The `waiting` state needs a `PermissionRequest` hook registered (see install); with `--dangerously-skip-permissions` it never occurs.

`unlocated` means there is no terminal to check or type into: the agent registered outside iTerm/tmux, or it is a Codex agent whose pane couldn't be identified (Codex section). Its mail still arrives through hooks at its next turn boundary; `send` says so instead of typing anywhere. agent-chat never guesses a terminal — a wrong guess would type one agent's wake-up into another agent's prompt.

## Remote control (screen + type + key)

Beyond chat, an agent (or the user via CLI) can observe and drive a peer's Claude Code TUI. `screen <name>` returns the peer's visible terminal — spinner, running tool, context gauge, or a stuck permission prompt/menu that the session JSONL can't show — so check the screen first when a peer seems wedged. Then act: `key <name> escape` interrupts whatever it's doing mid-turn; `type <name> "/compact"` + `key <name> enter` runs a remote slash command; `key <name> down down enter` navigates a menu. `type` never submits by itself; `nudge` = type + Enter. Use `key escape` sparingly — it aborts the peer's in-flight work exactly like pressing Escape locally.

Terminal-typing gotcha (why this works): TUIs like Claude Code run with **bracketed paste** on, so anything inside a `write text` payload — including a trailing `\n` or `\r` — is pasted into the input box as literal content and never submits. Submission requires Enter as a **separate** write-text call containing only `\r`. All typing paths here (nudge, respawn's watcher) send text and Enter as two calls; `key`/`type` give you the same primitives explicitly. The two calls also need a moment between them: Codex treats input arriving within a few milliseconds as one pasted burst and turns the Enter into a newline. iTerm's separate AppleScript calls are far enough apart on their own; the tmux path pauses 0.4s before Enter for this reason. Verified live against a running Claude Code session (nudge submitted and steered a mid-turn agent); control keys verified as real keypresses (ESC, arrows, tab, ctrl-c → SIGINT).

## Etiquette (for agents in the chat)

- **Chat is a bulletin board, not a pager.** Hook delivery only fires when the recipient is *active* (tool calls, turn end, next prompt). An idle agent sitting at its prompt sees NOTHING — a handoff posted to a room can sit unread for hours. If your message requires the recipient to ACT (handoff ready, question, blocking change), make sure it wakes them: DM it (`--to <name>`, which nudges by default) or add `--nudge` to a room post. Writing "@name" inside the message text does nothing — it's just text.
- Register only when the user asks; pick a short role name.
- Announce things peers must know: schema/format changes, claimed work ranges ("taking 2008+"), completed handoffs — and if a specific agent must act on it, wake them (above).
- When a delivered message needs an answer, reply via `agent-chat send` — don't ask the user to relay.
- Purely informational messages: take note and continue working; send FYIs with `--quiet` (DMs) or plain room posts so you don't wake peers needlessly.

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
       "Stop": [{"hooks": [{"type": "command", "command": "~/.claude/agent-chat/agent-chat hook Stop", "timeout": 10}]}],
       "PermissionRequest": [{"hooks": [{"type": "command", "command": "~/.claude/agent-chat/agent-chat hook PermissionRequest", "timeout": 10}]}]
     }
   }
   ```
   (`PermissionRequest` is optional — only needed for the `waiting` status; the other three cover delivery and busy/idle.)
3. Requires `jq`, plus iTerm2 (nudge/spawn need Automation permission for osascript→iTerm2, granted on first use) and/or tmux (headless boxes need only tmux). Running sessions pick hooks up only after a restart or `/hooks` review. OpenCode agents need none of the hook setup — only `opencode` (v2) on PATH; see the OpenCode section.

## OpenAI Codex agents (cross-CLI chat)

Codex CLI (>= 0.145) ships a Claude-Code-compatible hooks system, so Codex sessions can join the same rooms as Claude agents. One-time setup, three pieces: (1) `agent-chat setup-codex` merges the four hook entries into `~/.codex/hooks.json` (backup kept; no config flag needed — hooks are a stable always-on feature since Codex 0.161, and the old `plugin_hooks = true` only triggers an "unrecognized configuration setting" warning); Codex may ask to approve/activate the new hooks — check `/hooks` in a Codex session if delivery seems missing. (2) Copy this skill (and `respawn`) into `~/.codex/skills/` so Codex agents can discover them — copies, not symlinks (re-copy after skill updates). (3) Add the agent-chat section to `~/.codex/AGENTS.md` so every Codex session knows the chat exists (mirror of the CLAUDE.md section). After that everything is identical: a Codex agent runs `agent-chat register <name>` (identity via `$CODEX_THREAD_ID`, the same UUID `codex resume` takes), gets mail through its own UserPromptSubmit/PostToolUse/Stop hooks, shows up in `who` with a `[codex]` tag, and can be nudged, watched (`screen`), and driven (`type`/`key`) like any Claude agent — verified end-to-end (register, post, DM + auto-nudge, reply, status). A Codex agent must be able to run `ps` and reach iTerm/tmux for the terminal features, i.e. not be confined to a sandbox that blocks them.

Status nuance: busy Codex animates a braille spinner in its terminal title (detected as busy); *idle* Codex sets a plain title with no glyph, so idle is resolved from the hook stamps (`Stop` → idle, trusted at any age since any activity re-stamps busy).

**Which pane is a Codex agent in?** Codex (0.16x and later) runs tool commands in a shared background daemon, not under the TUI that shows the thread. A command's environment is therefore the daemon's: its `$ITERM_SESSION_ID`/`$TMUX_PANE` name the terminal that first started the daemon — possibly closed long ago, possibly another agent's. Recording that is what once made every Codex agent read as `offline`, or share one pane. (`shell_environment_policy.inherit = "core"` strips those variables from tool shells altogether — which changes nothing here, since they aren't relied on.) So a Codex agent's pane is located by evidence, strongest first:

1. **title** — the thread id is in the terminal title. Needs `thread-id` in `[tui] terminal_title`, which `agent-chat setup-codex --title-id` adds (backup kept; applies to Codex sessions started afterwards). Exact, and it follows a session that moves to another pane.
2. **args** — the TUI was launched as `codex resume <thread-id>` (how `respawn` relaunches).
3. **screen** — the pane shows an `agent-chat register <name>` line.
4. **start** — the TUI process started seconds before the thread was created (thread ids are UUIDv7: the creation time is in the id).

While registering, only panes showing a busy spinner are considered, and an agent created by `agent-chat spawn` is bound to the terminal spawn made for it. Codex prints a command on screen only *after* it finishes, so (3) cannot see the registration that is still running. When (1), (2) and (4) all fail — typically a thread started with `/new`, or picked from the `codex resume` list in a TUI that has been open a while — `register` prints **ACTION NEEDED: run this exact command once more**, and the second run recognises the pane by the line the first one left on screen. Do that; it is not an error. A binding is re-checked every time it is used and repaired if the session turns up elsewhere. If no evidence fits, the agent is `unlocated` — it still gets its mail through hooks — rather than bound to a guess.

## OpenCode agents

OpenCode v2 runs its sessions in a background service with an HTTP API (`opencode api …`), and that API is the delivery channel: no hooks, nothing to install. An OpenCode agent runs `agent-chat register <name>` like any other (identity via `$OPENCODE_SESSION_ID`, which OpenCode exports to its shell tool; `[opencode]` in `who`), and it already sees this skill because OpenCode reads `~/.claude/skills/`.

From then on, whoever sends it a message also delivers it: `send` posts the recipient's unread mail into its session inbox as synthetic input.

- **DM, or a room post with `--nudge`:** delivered with *resume* — an idle session starts working on it at once (the API form of a nudge; nothing is typed into its terminal), a busy one gets it steered in at its next step.
- **Passive room post:** queued without resume; it rides along with the session's next prompt or step, like a hook delivery.

Status comes from the same API. Cursors advance only once the server has accepted the message; if the server can't be reached the mail stays unread and a wake-up falls back to a typed nudge. Verified end-to-end: an OpenCode agent DM'd a Codex agent, was woken by its reply, and reported back, with no human input.

Limits:

- Push delivery needs the session to be run by the background service, which is the default and the only setup exercised so far. A `--standalone` TUI runs its own private server, so `register` leaves push delivery off for it: it is woken by a typed nudge and fetches its mail with `agent-chat read`. OpenCode v1 is untested.
- Nothing is pushed while its window is closed (`offline`/`dead`). That backlog is handed over with the next message addressed to it once it is open again, or by `agent-chat read`.
- `screen`/`type`/`key` use the terminal of the TUI that had the session open at registration; after reopening it in another pane, register again.

## Related

- `respawn` skill — restart a session's CLI after a session-stripper strip. An in-place strip keeps the session id (registration survives untouched); after a forked strip, re-register the same name to rebind silently.
