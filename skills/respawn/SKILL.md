---
name: respawn
description: "Restart a Claude Code CLI in its iTerm window and resume its session — the external relaunch step an agent cannot perform on itself. Use when: (1) a session's context is nearly full and was just stripped in place with session-stripper, so the CLI must restart to load the smaller transcript, (2) resuming a forked stripped session under a new session id, (3) restarting a stuck or context-exhausted peer agent (via its agent-chat name), (4) the user or an agent says respawn, restart yourself, restart the CLI, reload the session, or resume after strip. macOS + iTerm2 only."
---

# Respawn

Restarts the Claude Code CLI in an iTerm window and resumes a session, via a detached watcher that types into the window over AppleScript. Companion to the `session-stripper` skill: stripping shrinks the transcript on disk, but only a CLI restart loads it as the new (smaller) context.

## Self-respawn workflow (the common case: context nearly full)

Run inside the session that needs restarting:

1. **Strip first** — use the `session-stripper` skill, in-place mode with a backup. In-place keeps the session id stable, which preserves agent-chat registration and this skill's defaults. (If stripping forked to a NEW session id instead, note that id for step 2.)
2. **Schedule the respawn** — this must be the LAST tool call of the turn:

```bash
<skill-dir>/scripts/respawn.sh                          # in-place strip: same session id
<skill-dir>/scripts/respawn.sh --session <new-sid>      # forked strip: resume the fork
```

3. **End the turn immediately** — reply with one short line (e.g. "Respawning now, back shortly") and stop. The watcher waits ~15s, types `/exit`, relaunches, and types a kickoff prompt so the resumed session continues the work.

Do NOT keep working after calling respawn.sh: `/exit` is coming for the window.

## How it works

A detached watcher (`scripts/respawn-watcher.sh`, survives the CLI exiting):

1. Sleeps a grace period (`--grace N`, default 15s).
2. Resolves the iTerm window (by `$ITERM_SESSION_ID` UUID) to its tty; captures the running CLI's exact command line from `ps` — flags like `--dangerously-skip-permissions` are preserved in the relaunch.
3. Types `/exit`. If a turn is still running, the input queues and executes when the turn ends — the watcher waits up to 15 min for the process to die before escalating to SIGTERM/SIGKILL (`--force` shortens the wait to 20s, for hung CLIs).
4. Types `<original command> --resume <sid>` at the shell prompt, waits for the CLI to boot.
5. Types the kickoff prompt (`--prompt "..."` to customize). If the session was registered in agent-chat, the default kickoff tells it to re-register, and pending chat messages get auto-delivered with that first prompt.

Everything is logged to `~/.claude/respawn/respawn.log` — check it when a respawn didn't come back.

## Respawning a peer

If the target is registered in the `agent-chat` skill's registry:

```bash
<skill-dir>/scripts/respawn.sh --name <agent-name>            # graceful
<skill-dir>/scripts/respawn.sh --name <agent-name> --force    # stuck CLI: SIGTERM after 20s
```

Graceful mode lets a busy peer finish its current turn (the queued `/exit` fires at turn end). Only use `--force` on a genuinely hung session — it kills mid-turn.

## Verification / caveats

- `--dry-run` runs the whole watcher against the real window but only logs what it would type or kill — use it to sanity-check before the first real respawn on a machine (grants the osascript→iTerm automation permission on first use).
- Requires iTerm2; typed text lands in the window's input box, so a half-typed human draft there would be polluted.
- `claude --resume` must run from the shell the CLI exited to (same cwd → same project); the watcher types into that same shell, so this holds automatically.
