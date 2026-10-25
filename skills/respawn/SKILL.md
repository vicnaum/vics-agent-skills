---
name: respawn
description: "Restart the current Claude Code or Codex CLI in its own terminal (iTerm2 window or tmux pane), or the current Claude Code background session (claude --bg / ←, no terminal), and resume a session — the external relaunch step an agent cannot perform on itself. Use when: (1) the session's context is nearly full and was just stripped with session-stripper, so the stripped pending copy must be swapped in (--swap) and the CLI restarted to load the smaller transcript, (2) resuming a forked stripped session under a new session id, (3) the user or the agent says respawn, restart yourself, restart the CLI, reload the session, or resume after strip. Backends: iTerm2 (macOS), tmux (works headless, e.g. Linux servers) and Claude Code background sessions."
---

# Respawn

Restarts the calling Claude Code or Codex CLI in its verified iTerm2 session or tmux pane, or the calling Claude Code background session through its daemon, and resumes a session. Requires the companion `agent-chat` skill/CLI, which provides the shared identity and terminal ownership checks. Companion to `session-stripper`: a CLI restart loads the stripped transcript as smaller context.

## Workflow (context nearly full)

Run inside the session that needs restarting:

1. **Strip first** — use the `session-stripper` skill. Its mutating commands are copy-first: they write `<session>.jsonl.pending` and never touch the live file, so stripping your own running session is safe. Verify the pending copy. (If stripping forked to a NEW session id instead, note that id for step 2 — forks need no swap.)
2. **Schedule the respawn** — this must be the LAST tool call of the turn:

```bash
<skill-dir>/scripts/respawn.sh --swap <session.jsonl>.pending   # stripped in place: swap + resume this session's id
<skill-dir>/scripts/respawn.sh <new-session-id>                 # forked strip: resume the fork (no swap)
<skill-dir>/scripts/respawn.sh                                  # plain restart, nothing to swap
```

3. **End the turn immediately** — reply with one short line (e.g. "Respawning now, back shortly") and stop. Do not keep working after scheduling a real respawn. The watcher will recheck ownership, then stop the captured CLI. A dry run does not schedule anything and need not be the last tool call.

`--swap` applies the session surgery at the only race-free moment: after the watcher has confirmed the CLI process is dead, before relaunch. It runs session-stripper's `apply` (atomic rename; refuses on lineage/liveness/verify failures). If apply refuses, the watcher relaunches the UNSTRIPPED session and rewrites the kickoff prompt to say so — fail-open, nothing is lost. Whatever the CLI appended after the snapshot (the stripping turn itself) is discarded by design.

## How it works

`respawn.sh` asks `agent-chat whereami` for the calling CLI, session and pane. It uses the sibling checkout's CLI when available, so an older PATH install cannot bypass the guard. Conflicting inherited session variables are resolved through the nearest CLI ancestor; ambiguous identity refuses. The scheduler verifies the foreground session and captures its pid/birth time, tty, and parent shell pid/birth time before detaching the watcher. The resume id may differ after a fork; the identity stopped is always the original calling session.

For Claude Code, the launch command is rebuilt from its exact argv (sysctl on macOS, `/proc` on Linux; `ps` joins arguments and loses quoting). Flags are preserved except session/worktree selectors: `-c/--continue`, `-r/--resume [id]`, `--from-pr [ref]`, `--session-id <id>`, `--fork-session`, `-w/--worktree [name]`/`--tmux`, `--bg`. Any prompt the CLI was launched with is dropped too; agents started by agent-chat carry their kickoff there. Then `--resume <sid>` is appended. `--cmd "claude ..."` overrides reconstruction.

The detached watcher then:

1. Waits `--grace N` seconds (default 15), then verifies the original foreground session and process identity again. A changed thread, replaced process or pane causes refusal before input/signals. A CLI that already exited is accepted only in its verified original shell.
2. Stops the captured CLI: Codex receives SIGTERM by verified pid, with no terminal quit keys. Claude receives `/exit` plus a separately guarded Enter; it can queue until turn end. When background shells or agents are running, Claude Code then asks "Background work is running": 1. Exit and stop tasks / 2. Move to background and exit / 3. Stay. The watcher confirms the default with Enter, and only while the selection is on "Exit and stop tasks". Those shells die with the CLI, and the default kickoff tells the agent to re-arm the ones it needs, such as an `agent-chat watch`. The watcher waits up to 15 minutes, or 20s with `--force`, and rechecks ownership before signal escalation. It never retargets a different pid that appeared on the tty.
3. With `--swap`, applies the pending Claude transcript only after the original CLI is gone. On apply refusal it retains the original and rewrites the kickoff to explain that the session is unstripped.
4. Verifies the original foreground shell before typing the launch line and again before Enter. The kickoff is a quoted command-line argument, including after a failed swap; nothing is typed into a booting TUI.
5. Waits up to 60s for the resumed session to be proved in the same pane under the original shell. Startup failure or a dialog produces a logged failure and no additional input.

Log: `~/.claude/respawn/respawn.log`.

## Claude Code background sessions

A session started with `claude --bg`, or moved there with `←` or `/background`, has no pane: its worker runs on a pty the daemon owns. Typing `/exit` in a terminal attached to it only detaches, and `claude --resume <id>` refuses while it runs. `agent-chat whereami` reports it as `bg <job>`, and the proof is Claude Code's own live record (pid, start time, job). The watcher:

1. After the grace period, re-proves the worker and waits until the session reports `idle`, so its last reply is never cut off (up to 15 minutes, or 20s with `--force`).
2. Records every terminal running `claude attach <job>` directly under a shell, then runs `claude stop <job>` and waits for the worker to exit. It then waits for the daemon's `bg settled <job>` line: a wake sent before that starts a copy under a new id instead of resuming.
3. Applies `--swap` while nothing runs, as in the terminal flow.
4. Runs `claude --bg --resume <sid> '<kickoff>'`. The daemon wakes the same job with its saved options. A forked strip resumes the new id with the job's saved options passed explicitly. If the daemon reports that it started a copy, the watcher stops and logs it.
5. Verifies the new worker holds the session, then types `claude attach <job>` back into each recorded terminal, but only once its old client has exited and its original shell is at the prompt.

Claude Code's built-in `claude respawn <job>` restarts a background worker (e.g. onto a new version) in one step. That leaves no moment to swap a transcript, so this skill does not use it.

## OpenAI Codex sessions

Codex resumes with `codex resume <thread-id> '<kickoff>'`. Settings come from Codex configuration; use `--cmd "codex --profile x"` when an explicit profile/flags are needed. Only self-respawn when about to end the turn: stopping the frontend interrupts interactive use.

Codex tool shells run in a shared daemon, so terminal environment variables and tty ancestry cannot select this session's pane. A unique **current thread-id title** is required. Run `agent-chat setup-codex --title-id`, restart the affected TUI, then register. Original launch arguments, registration text and cached bindings are insufficient after a thread switch. The watcher checks the same current-thread evidence again after its delay. There is no ownership override.

## Verification / caveats

- `--dry-run` synchronously verifies the pane, session, process and parent shell and prints the proposed restart. It starts no watcher and sends no input/signals or swaps.
- Avoid scheduling while a human is editing that terminal. Ownership checks do not preserve a draft already sitting at its prompt.
- After a forked strip (new session id), anything keyed to the old id (e.g. an agent-chat registration) is stale — redo it in the resumed session.

The shared regression suite is `python3 -m unittest discover -s skills/agent-chat/tests -v` from the repository root. Unit tests mock terminal and signal operations; an optional private-tmux test uses a dummy TUI. They exercise successful restarts and changed-session, PID-reuse, failed-startup, failed-write and swap-refusal paths without contacting real agents.
