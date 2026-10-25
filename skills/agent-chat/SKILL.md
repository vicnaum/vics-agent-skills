---
name: agent-chat
description: "Local chat and terminal control for concurrent Claude Code, Codex and OpenCode sessions. Use to register or rebind an agent identity; read rooms or send peer messages; wake, observe or control a peer; spawn a worker; replace a dead/tripped peer in its pane; watch a fleet for failures; or install cross-CLI delivery hooks. Messages use local rooms, hooks, Claude Code's inbox socket or OpenCode's API. Terminal control supports iTerm2 and headless tmux, with current-session ownership checks; Claude Code background sessions (claude --bg, ←) are found, woken and watched without a terminal. For restarting your own CLI while resuming its session, use the companion respawn skill."
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
agent-chat status [<name>]       # live state: busy | idle | waiting | dead | stopped | offline | unlocated
agent-chat nudge <name> [text]   # wake it: Claude inbox socket, else type a line + Enter (waits while someone types there; --force)
agent-chat type <name> "text"    # type into that agent's input box WITHOUT submitting
agent-chat screen <name> [N]     # live snapshot of that agent's visible terminal
agent-chat key <name> <key...>   # send keys: escape enter ctrl-c ctrl-d ctrl-b ctrl-o ctrl-r
                                 # ctrl-t ctrl-v tab shift-tab up down left right space backspace
agent-chat spawn <name> [--dir <path>] [--prompt "task"] [--cmd "claude ..."] [--tab|--pane|--bg] [--tmux]
                                 # launch a NEW agent — new iTerm window (default), a new tab
                                 # in your window (--tab), a split pane next to you (--pane),
                                 # a detached tmux session (--tmux / headless), or a Claude Code
                                 # background session with no window at all (--bg). It inherits
                                 # this session's CLI flags and registers itself as <name>
agent-chat respawn <name> [--prompt "task"] [--dry-run] [--no-wait] [--report-to <name>]
                                 # replace a tripped/dead codex|claude agent IN PLACE: kill its CLI
                                 # by signal, start a FRESH session in the SAME pane (a background
                                 # agent: claude stop + a fresh background session) that re-registers
                                 # as <name>. Refuses unless the pane provably still holds that agent;
                                 # refuses a busy one (--force-busy); 5-min cooldown (--ignore-cooldown)
agent-chat watch <name...> [--interval 60] [--idle-alert MIN] [--respawn] [--report-to <name>]
                                 # blocking fleet watchdog for an orchestrator's BACKGROUND shell:
                                 # exits with a report when an agent is dead / filter-tripped /
                                 # errored / usage-limited / has stuck input / idles too long
agent-chat unregister [<name>]   # leave the chat
agent-chat web [port]            # local live web viewer (default :8787) — channels, messages, people
agent-chat setup-codex [--title-id]  # one-time: wire hooks into OpenAI Codex CLI (cross-CLI chat);
                                 # --title-id also pins each Codex agent to its pane exactly
agent-chat whereami              # what THIS session resolves to: cli, session id, terminal
```

Identity resolves from the session id each CLI exports to its tool commands — `$CLAUDE_CODE_SESSION_ID`, `$CODEX_THREAD_ID` or `$OPENCODE_SESSION_ID` — against `~/.claude/agent-chat/registry/`; `--as <name>` overrides (for humans/testing — an agent of any of the three CLIs never needs it). If more than one of those variables is set (a CLI started from inside another's shell), the nearest CLI in the process ancestry decides; if it cannot disambiguate them, identity resolution refuses.

## Rooms

Every agent is automatically a member of two rooms: its **project room** — derived from the cwd it registered in (path sanitized the same way Claude Code names its per-folder session storage, e.g. `#-Users-x-github-myproject`) — and **#general**. Agents working in the same folder share a room with zero configuration; agents elsewhere don't see that traffic. Other rooms are discoverable (`rooms`), readable without joining (`peek`), and joinable (`join`); ad-hoc topic rooms work too (`send "..." --room db-migration` creates and auto-joins it). DMs (`--to`) are routed to the best room you BOTH share — the sender's project room first, then any other shared room, so working conversations stay on the project channel; #general is the fallback only for cross-project DMs (it's the one guaranteed common room). Note DMs are addressed, not private (any member of that room can peek them).

## Identity and continuity

Identity is the **name**, not the session. Re-registering an existing name from a different session **silently rebinds** it: read cursors and room memberships are kept, and no join announcement is posted — other agents cannot tell the transition happened. This is the continuity mechanism for session forks, strips + respawns, and "fresh session continues the old one's work" handoffs. Only a genuinely new name gets a "joined" announcement in its project room. (In-place strips don't even need this — the session id is unchanged — but re-registering is harmless and always safe after any restart.)

## Claude Code background sessions

Claude Code can run a session with no terminal: `claude --bg`, `←` or `/background` in a session, `claude agents`. Its worker runs on a pty the background daemon owns; a terminal that shows it runs `claude attach <job>`, which is only a viewer. `/exit` there detaches, and `claude --resume <id>` refuses while it runs.

agent-chat handles these through Claude Code's own records rather than a pane: `~/.claude/sessions/<pid>.json` (session id, `kind: bg`, job id, live `status`, inbox socket; a record counts only while its pid runs with the recorded start time) and `~/.claude/jobs/<job>/state.json`.

- `register` records `term_backend: bg`, `term_addr: <job>`. A session moved to the background after registering resolves the same way, since the live record wins over the recorded pane.
- `status` reads Claude Code's own `busy`/`idle`/`waiting`. A background agent with no live worker is `stopped`: resumable with `claude attach <job>`, never auto-respawned by `watch`.
- `send`/`nudge` wake it through its inbox socket. `type`/`key` refuse, because there is nothing to type into. `screen` prints the job's state, what it needs and its last reply.
- `spawn --bg` starts a new agent this way. `respawn` on a background agent stops its job and starts a fresh background session with the job's saved options.

## Terminal backends

Each agent's terminal is an iTerm2 session or tmux pane. `agent-chat whereami` shows this session's CLI, id and resolved terminal. Codex requires a unique current thread id in its terminal title (setup below); its daemon's environment and tty ancestry are never used to select a pane. Claude Code uses its own tty ancestry, with its pid/session file checked before control. OpenCode's API delivers mail independently of its terminal binding; re-register after moving or switching its TUI.

Terminal I/O dispatches per target: iTerm via AppleScript, tmux via `send-keys`/`capture-pane`. Failed writes return nonzero. Keyboard operations pin the requested session and recheck ownership before every write/key, including after nudge delays. If ownership is missing, ambiguous or changes, the operation stops. `--force` on nudge only bypasses the human-typing guard; it cannot bypass ownership checks.

The chat core is bash+jq+files and works headless. `spawn --tmux` creates a detached session (`tmux attach -t agent-<name>` to watch). Terminal.app is unsupported; use iTerm2 or tmux.

## Spawning new agents

`spawn <name> --prompt "task"` creates the terminal and launches the CLI reusing the spawning session's own flags, read from its exact argv: session selectors, its name and any prompt it was launched with are dropped (a background spawner passes its job's saved options). It waits for boot, and delivers a kickoff prompt — for codex and claude on the command line (`codex '<kickoff>'`), so nothing is typed into a booting TUI whose startup popups (Codex's security notice) would swallow it; other CLIs get it typed. Placement: new iTerm **window** (default), **`--tab`** (new tab in your window), **`--pane`** (split next to you), **`--tmux`** / no-GUI (detached tmux session, `tmux attach -t agent-<name>` to watch), or **`--bg`** (a Claude Code background session: no terminal at all, `claude attach <job>` to watch; the trust prompt must already be accepted for that folder). The new agent registers *itself*. Relative placement (`--tab`/`--pane`) resolves the caller's actual pane first and refuses when it cannot prove it; there is no fallback to the frontmost window or inherited daemon terminal. `--tmux --pane`/`--tmux --tab` requires the caller to be in tmux.

Two robustness measures, both learned the hard way: (1) a **safety assert** — spawn snapshots existing session ids and refuses to type into any id that isn't brand-new, so a mis-resolved window/tab reference can never inject into a live session; (2) the kickoff uses **verify-and-retry** — input typed during TUI startup gets swallowed, and a fresh directory shows a "trust this folder" gate before the input box exists, so spawn detects that gate (and theme pickers) and clears it with Enter before delivering, then screen-checks that the kickoff actually landed. Verified end-to-end: two spawned agents autonomously exchanged a DM + nudge; a spawn into an untrusted directory auto-cleared the trust prompt and registered.

## Replacing tripped agents (respawn) and watching a fleet (watch)

**`respawn <name>`** is the in-place replacement for an agent whose session died or tripped (e.g. Codex's content gate, `ⓘ This content can't be shown`). It never types into the tripped chat and never opens a window:

1. Proves the current foreground session: Codex needs one matching thread title, Claude Code a matching `~/.claude/sessions/<pid>.json`. Captures the CLI's pid and birth time plus its parent shell's identity. A dead agent is recoverable only in that previously verified, unchanged shell at its foreground prompt. Legacy records without a shell fingerprint cannot authorize dead-agent recovery. There is no ownership override; use `spawn` for a new window when recovery cannot be proved.
2. Refuses a **busy** agent (spinner / `esc to interrupt`) unless `--force-busy`; refuses within 5 min of the last respawn unless `--ignore-cooldown`.
3. Builds and UTF-8-validates the launch line *before* killing anything, then signals only the captured CLI (TERM, then KILL), rechecking before escalation. Verifies the original parent shell before launch and again before Enter; concurrent replacements of the same name are refused.
4. Types `reset; cd <cwd> && codex '<kickoff>'` — the kickoff rides on the command line (nothing is typed into a booting TUI), tells the fresh session to `agent-chat register <name>` (silent rebind: same name, rooms, cursors), then either does `--prompt "task"` or DMs `--report-to` (default: the caller) that it's ready.
5. Confirms a new CLI process started (fails loudly otherwise) and, unless `--no-wait`, that the name was rebound to a verified new session in the same pane.

Always try `--dry-run` first: it prints the exact pane, pids and launch line. Log: `~/.claude/agent-chat/respawn.log`.

**`watch <name...>`** is a watchdog designed to run as the orchestrator's **background shell** (OpenCode `shell` with `background: true`; Claude Code `run_in_background`). It polls every `--interval` seconds and **exits** as soon as something needs attention, printing a report — the harness then wakes the orchestrator with that output automatically (no polling, no chat message needed). Handle it, then re-arm (the report ends with the exact command). Each condition is reported once per session, so re-arming doesn't re-fire. Detected:

- `dead` (pane at a shell prompt: the CLI exited/crashed) and `filter` (the unindented `ⓘ This content can't be shown` block with its `Apply for Daybreak` line on an idle screen — an agent merely printing the phrase in tool output doesn't match) — with `--respawn` these are replaced in place automatically, then reported;
- `error` (stream disconnected / retry limit / bad status), `limit` (usage limit), `paste` (a `[Pasted Content …]` stuck in the input box), `idle` (idle longer than `--idle-alert` minutes — the agent may have stopped without reporting), `offline` (pane closed), `unlocated` (cannot prove which pane to observe) — reported only; the orchestrator decides.

`agent-chat status` alone is not a trip detector: a content-gated Codex is idle with a plain title, so it reads `unknown`/`idle`, never `dead`. A watchdog that treats `unknown` as a failure floods false alarms. `unlocated` is different: it reports lost observation and never triggers auto-respawn. Restart existing background watches after updating this script; they retain their loaded implementation until restarted.

## How delivery works

Once a session is registered, its unread messages (from all joined rooms, labeled `[#room]`) are injected automatically by three user-level hooks (all call `agent-chat hook <Event>`): `PostToolUse` (mid-turn, after any tool call), `Stop` (blocks the turn from ending until new mail is handled), and `UserPromptSubmit` (rides along with the user's prompt). Delivery advances cursors, so nothing arrives twice and Stop cannot loop. Unregistered sessions are untouched — the hooks no-op instantly.

OpenCode has no such hooks, so for an OpenCode agent the **sender** does the delivering: `send` hands everything that agent hasn't read to its session through OpenCode's server API (details in the OpenCode section). The effect matches the hooks — mid-turn if it is working, with its next prompt if it is idle — and its cursors advance the same way.

That leaves a fully idle peer, which has to be **woken**:

- **Claude Code agents: inbox socket, no typing.** Claude Code (≥ 2.1.224) gives every session an inbox — a Unix socket exported to its Bash/hooks as `$CLAUDE_CODE_MESSAGING_SOCKET` (`/tmp/cc-socks/<pid>.sock`). It is taken from the live session record of the process that holds the registered session id (pid and start time proven), so it works for pane and background sessions alike. `send` posts the agent's unread mail there as one JSON line (`{"type":"user","message":{"role":"user","content":…}}`): an idle session starts a turn on it, a working one reads it between tool calls, and whatever a human has half-typed in its input box stays untouched. Cursors advance only once the socket took it. **Caveat:** a session that bypasses permission prompts (`--dangerously-skip-permissions`) *holds* such a message behind an approval dialog — one per message — unless `"crossSessionInbound": "accept"` applies to it (user/project settings). The delivery hooks record each agent's `permission_mode` (`status/<name>.mode`), and the inbox is used only where it is known to deliver: a prompting session, or an explicit `accept` in the agent's project or user settings. Otherwise it falls back to typing.
- **OpenCode agents:** woken through the server API (see the OpenCode section) — nothing is typed.
- **Everything else (Codex, or a Claude agent whose inbox would hold): a guarded typed nudge.** A line typed into the peer's terminal (AppleScript or tmux send-keys) + a separate Enter, which submits as a prompt and pulls in the mail through the UserPromptSubmit hook. Typing into a pane where a human is mid-sentence garbles both, so a nudge only types when (1) nobody is at *that pane's* keyboard — it isn't the focused session of the frontmost iTerm window / the active pane of an attached tmux client, or no key went in for 20s (using the rest of the computer doesn't count), (2) its screen is still across a 1.5s look, and (3) no working spinner is on it. Text that just *sits* in the input box (a forgotten word, an autocomplete suggestion) does not block: the nudge is typed after it. When blocked, `send` returns at once and a background worker (`agent-chat _nudge-worker`, state in `nudge/`) retries every 5s; it drops the nudge as soon as the agent has read its mail some other way (the human submitting a prompt delivers it through the hook) and gives up after 30 min. Nudges to one agent are **combined**: at most one per 30s, naming every sender since (`[agent-chat] new message(s) from a, b — run: agent-chat read`). After typing, a line still sitting in the box (Enter swallowed as part of the paste) gets one more Enter. `agent-chat nudge <name>` applies the same guard (`--force` skips it) and uses the inbox for Claude agents.

## Live status (busy / idle / waiting / dead / offline)

`agent-chat status [<name>]` resolves an agent's real state, and `send`/nudge uses it automatically: a DM (or `--nudge`) **nudges only an idle agent**, tells you a busy one will get it at its next turn boundary, refuses to type at a peer blocked on a permission dialog, and warns if the recipient's window is dead/offline (message still stored for restart). Resolution is layered so it survives crashes and interrupts:

0. **Claude Code agents:** the live session record states `busy` (or `shell`, a tool command running), `idle` or `waiting` itself, for pane and background sessions alike. This needs no terminal and no AppleScript. A background agent without one is `stopped`.
1. **Terminal reachable?** iTerm session / tmux pane gone → `offline`.
2. **The agent's own CLI alive on its tty?** (`claude`, `codex` or `opencode`, per its registration.) No → `dead` (crashed/`/exit`ed).
3. **OpenCode only:** its server reports the session's state outright (running → `busy`, pending permission/question → `waiting`, else `idle`).
4. **Title glyph** — the CLI's own render loop drives it, so it's *always fresh and can't go stale*: Claude Code shows `◐`/`◑` while busy (`⠂`/`⠐` before ~2.1.29x) and `✳` when idle-or-waiting; Codex animates a braille spinner while busy. This is the primary busy/idle signal.
5. **Hook status file** (`~/.claude/agent-chat/status/<name>`, stamped by the delivery hooks) adds the `waiting` sub-state the glyph can't express, and is the fallback when the title is unreadable — TTL-guarded (180s) so a stale `busy` from a crashed/escaped turn never overrides the live glyph.

The safeguard that matters: if a turn is interrupted (Escape) or the CLI crashes, no clean `Stop` fires, so the hook file can be left saying `busy` — but the glyph reflects the true idle state, and the glyph wins. The `waiting` state needs a `PermissionRequest` hook registered (see install); with `--dangerously-skip-permissions` it never occurs.

`unlocated` means there is no terminal to check or type into: the agent registered outside iTerm/tmux, or it is a Codex agent whose pane couldn't be identified (Codex section). Its mail still arrives through hooks at its next turn boundary; `send` says so instead of typing anywhere. agent-chat never guesses a terminal — a wrong guess would type one agent's wake-up into another agent's prompt.

## Remote control (screen + type + key)

Beyond chat, an agent (or the user via CLI) can observe and drive a peer's Claude Code TUI. `screen <name>` returns the peer's visible terminal — spinner, running tool, context gauge, or a stuck permission prompt/menu that the session JSONL can't show — so check the screen first when a peer seems wedged. Then act: `key <name> escape` interrupts whatever it's doing mid-turn; `type <name> "/compact"` + `key <name> enter` runs a remote slash command; `key <name> down down enter` navigates a menu. `type` never submits by itself; `nudge` = type + Enter. Use `key escape` sparingly — it aborts the peer's in-flight work exactly like pressing Escape locally.

Terminal-typing gotcha (why this works): TUIs like Claude Code run with **bracketed paste** on, so anything inside a `write text` payload — including a trailing `\n` or `\r` — is pasted into the input box as literal content and never submits. Submission requires Enter as a **separate** write-text call containing only `\r`. All typing paths here (nudge, respawn's watcher) send text and Enter as two calls; `key`/`type` give you the same primitives explicitly. The two calls also need a moment between them: Codex treats input arriving within a few milliseconds as one pasted burst and turns the Enter into a newline. iTerm's separate AppleScript calls are far enough apart on their own; the tmux path pauses 1s before Enter for this reason (0.4s still missed now and then against Claude Code 2.1.3xx). Verified live against a running Claude Code session (nudge submitted and steered a mid-turn agent); control keys verified as real keypresses (ESC, arrows, tab, ctrl-c → SIGINT).

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

Codex CLI (>= 0.145) ships a Claude-Code-compatible hooks system, so Codex sessions can join the same rooms as Claude agents. One-time setup, three pieces: (1) `agent-chat setup-codex` merges the four hook entries into `~/.codex/hooks.json` (backup kept; no config flag needed — an old `plugin_hooks = true` line in `config.toml` changes nothing and can stay); Codex may ask to approve/activate the new hooks — check `/hooks` in a Codex session if delivery seems missing. (2) Symlink this skill (and `respawn`) into `~/.codex/skills/` so Codex agents can discover them: `ln -s <skill-dir> ~/.codex/skills/agent-chat`. Codex follows symlinked skill directories (checked on 0.162), and a symlink can't go stale the way a copy does. (3) Add the agent-chat section to `~/.codex/AGENTS.md` so every Codex session knows the chat exists (mirror of the CLAUDE.md section). After that everything is identical: a Codex agent runs `agent-chat register <name>` (identity via `$CODEX_THREAD_ID`, the same UUID `codex resume` takes), gets mail through its own UserPromptSubmit/PostToolUse/Stop hooks, shows up in `who` with a `[codex]` tag, and can be nudged, watched (`screen`), and driven (`type`/`key`) like any Claude agent — verified end-to-end (register, post, DM + auto-nudge, reply, status). A Codex agent must be able to run `ps` and reach iTerm/tmux for the terminal features, i.e. not be confined to a sandbox that blocks them.

Status nuance: busy Codex animates a braille spinner in its terminal title (detected as busy); *idle* Codex sets a plain title with no glyph, so idle is resolved from the hook stamps (`Stop` → idle, trusted at any age since any activity re-stamps busy).

**Which pane is a Codex agent in?** Codex (0.16x and later) runs tool commands in a shared daemon. `$ITERM_SESSION_ID`, `$TMUX_PANE`, and even a tty-owning daemon ancestor can belong to another session. They never authorize Codex terminal control.

Enable the thread id in Codex titles once:

```bash
agent-chat setup-codex --title-id
```

Restart the affected TUI to load that setting, then register. Mail delivery through hooks works even without a terminal binding. Repeating registration alone cannot prove a pane.

The resolver requires exactly one reachable Codex TUI whose current title identifies the thread. Normal truncated UUIDv7 titles are supported; ambiguous matches are refused. A TUI's original `codex resume <id>` arguments, an old `register` line, process start time, spawn hints and previously cached bindings cannot prove its current thread after `/new`, `/resume` or a pane move. These hints are no longer used. If current evidence disappears, terminal control stops and watch reports `unlocated`.

`agent-chat respawn <name>` replaces a peer with a fresh session. The separate `respawn` skill restarts the calling session and resumes its id; it uses the same ownership proof and revalidates after its grace period. Neither path has a bypass for missing ownership.

## OpenCode agents

OpenCode v2 runs its sessions in a background service with an HTTP API (`opencode api …`), and that API is the delivery channel: no hooks, nothing to install. An OpenCode agent runs `agent-chat register <name>` like any other (identity via `$OPENCODE_SESSION_ID`, which OpenCode exports to its shell tool; `[opencode]` in `who`), and it already sees this skill because OpenCode reads `~/.claude/skills/`.

From then on, whoever sends it a message also delivers it: `send` posts the recipient's unread mail into its session inbox as synthetic input.

- **DM, or a room post with `--nudge`:** delivered with *resume* — an idle session starts working on it at once (the API form of a nudge; nothing is typed into its terminal), a busy one gets it steered in at its next step.
- **Passive room post:** queued without resume; it rides along with the session's next prompt or step, like a hook delivery.

Status comes from the same API. Cursors advance only once the server has accepted the message; if the server can't be reached the mail stays unread and a wake-up falls back to a typed nudge. Verified end-to-end: an OpenCode agent DM'd a Codex agent, was woken by its reply, and reported back, with no human input.

Limits:

- Push delivery needs the session to be run by the background service, which is the default and the only setup exercised so far. A `--standalone` TUI runs its own private server, so `register` leaves push delivery off for it: it is woken by a typed nudge and fetches its mail with `agent-chat read`. OpenCode v1 is untested.
- `register` decides service vs standalone from the process tree: the **nearest** of "an `opencode serve` process" and "something on a tty" wins. (Not "any tty ancestor": a TUI that finds no service running starts one as its own child, so the service — and every command it runs — can sit under that TUI's tty. That misfire, seen with v2.0.32, silently registered service sessions without push delivery, so their mail arrived as typed user prompts.) `who`/the registry show `delivery: api` when push is on; if an OpenCode agent's messages arrive as typed prompts, have it register again.
- Nothing is pushed while its window is closed (`offline`/`dead`). That backlog is handed over with the next message addressed to it once it is open again, or by `agent-chat read`.
- `screen`/`type`/`key` use the terminal of the TUI that had the session open at registration; after reopening it in another pane, register again.

## Related

- `respawn` skill — restart a session's CLI after a session-stripper strip. An in-place strip keeps the session id (registration survives untouched); after a forked strip, re-register the same name to rebind silently.

## Validation

Run `python3 -m unittest discover -s skills/agent-chat/tests -v` from the repository root. Unit tests use isolated homes and mocked terminals/process metadata. When tmux and a C compiler are available, an integration test also uses a private tmux server and a tiny dummy TUI to verify real PTY routing, signals and restarts. No real agents are contacted.
