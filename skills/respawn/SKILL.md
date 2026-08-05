---
name: respawn
description: "Restart the current Claude Code CLI in its own terminal (iTerm2 window or tmux pane) and resume a session — the external relaunch step an agent cannot perform on itself. Use when: (1) the session's context is nearly full and was just stripped with session-stripper, so the stripped pending copy must be swapped in (--swap) and the CLI restarted to load the smaller transcript, (2) resuming a forked stripped session under a new session id, (3) the user or the agent says respawn, restart yourself, restart the CLI, reload the session, or resume after strip. Backends: iTerm2 (macOS) and tmux (works headless, e.g. Linux servers)."
---

# Respawn

Restarts the Claude Code CLI in the current terminal (iTerm2 window via AppleScript, or tmux pane via send-keys — works headless) and resumes a session, via a detached watcher that types into it. Companion to the `session-stripper` skill: stripping shrinks the transcript on disk, but only a CLI restart loads it as the new (smaller) context.

## Workflow (context nearly full)

Run inside the session that needs restarting:

1. **Strip first** — use the `session-stripper` skill. Its mutating commands are copy-first: they write `<session>.jsonl.pending` and never touch the live file, so stripping your own running session is safe. Verify the pending copy. (If stripping forked to a NEW session id instead, note that id for step 2 — forks need no swap.)
2. **Schedule the respawn** — this must be the LAST tool call of the turn:

```bash
<skill-dir>/scripts/respawn.sh --swap <session.jsonl>.pending   # stripped in place: swap + resume this session's id
<skill-dir>/scripts/respawn.sh <new-session-id>                 # forked strip: resume the fork (no swap)
<skill-dir>/scripts/respawn.sh                                  # plain restart, nothing to swap
```

3. **End the turn immediately** — reply with one short line (e.g. "Respawning now, back shortly") and stop. Do NOT keep working after calling respawn.sh: `/exit` is coming for the window.

`--swap` applies the session surgery at the only race-free moment: after the watcher has confirmed the CLI process is dead, before relaunch. It runs session-stripper's `apply` (atomic rename; refuses on lineage/liveness/verify failures). If apply refuses, the watcher relaunches the UNSTRIPPED session and rewrites the kickoff prompt to say so — fail-open, nothing is lost. Whatever the CLI appended after the snapshot (the stripping turn itself) is discarded by design.

## How it works

`respawn.sh` targets its own terminal — a tmux pane via `$TMUX_PANE` (wins when both are set) or an iTerm window via `$ITERM_SESSION_ID` — and defaults the session to `$CLAUDE_CODE_SESSION_ID` (all exported to Bash tool calls; the env session id matches the real transcript id). It rebuilds the relaunch command from the live CLI's `ps` entry: **all flags are preserved** (`--dangerously-skip-permissions`, `--model`, ...) **except session selectors, which are stripped** so a stale selector can't resume the wrong session: `-c/--continue`, `-r/--resume [id]`, `--from-pr [ref]`, `--session-id <id>`, `--fork-session`, and `-w/--worktree [name]`/`--tmux`. Then `--resume <sid>` is appended. The command is printed for sanity-checking before the turn ends.

A detached watcher (`scripts/respawn-watcher.sh`, survives the CLI exiting) then:

1. Sleeps a grace period (`--grace N`, default 15s).
2. Types `/exit`. If a turn is still running, the input queues and executes at turn end — the watcher waits up to 15 min for the process to die before escalating to SIGTERM/SIGKILL (`--force` shortens the wait to 20s, for hung CLIs).
3. With `--swap`: now that the process is confirmed dead (nothing can append to the JSONL), runs session-stripper's `apply` to atomically swap the stripped pending copy over the original. On refusal/failure: logs, keeps the original, and rewrites the kickoff to say the session is UNSTRIPPED.
4. Types the relaunch command at the shell prompt (same shell, same cwd → same project), waits for the CLI to boot.
5. Types the kickoff prompt (`--prompt "..."` to customize).

Everything is logged to `~/.claude/respawn/respawn.log` — check it when a respawn didn't come back.

## OpenAI Codex sessions

Works for Codex CLI too, auto-detected via `$CODEX_THREAD_ID`: the relaunch is `codex resume <thread-id>` (flags come from your codex config/profile; use `--cmd "codex --profile x"` to override) and the graceful quit is a double ctrl-c instead of `/exit`. Caveat: ctrl-c *interrupts* a running Codex turn rather than queueing politely, so only self-respawn when about to go idle — which is the normal strip-then-respawn flow anyway.

## Verification / caveats

- `--dry-run` runs the whole watcher against the real window but only logs what it would type or kill — use it to sanity-check before the first real respawn on a machine (grants the osascript→iTerm automation permission on first use).
- `ps` loses shell quoting: if the CLI was launched with quoted args containing spaces (e.g. `--append-system-prompt "be brief"`), reconstruction would mangle them — pass the full command explicitly with `--cmd "claude ..."` instead.
- Typed text lands in the window's input box, so a half-typed human draft there would be polluted.
- After a forked strip (new session id), anything keyed to the old id (e.g. an agent-chat registration) is stale — redo it in the resumed session.
