#!/usr/bin/env bash
# respawn.sh — schedule a restart of THIS Claude Code or Codex CLI in its terminal
# (iTerm window or tmux pane) — or of THIS Claude Code background session (no
# terminal: stopped and resumed through the daemon) — resuming a session
# (typically right after a session-stripper strip). The agent calls this as its
# LAST action, then ends its turn; a detached watcher does the rest.
#
# Usage:
#   respawn.sh [<session-id>] [--swap <pending>] [--prompt "kickoff"] [--grace N] [--force] [--cmd "claude ..."] [--dry-run]
#
# <session-id>  session to resume. Default: the verified calling session (this
#               session) — correct for in-place strips. Pass the NEW id after
#               a forked strip.
# --swap        path to a stripped `<session>.jsonl.pending` copy (made by
#               session-stripper's copy-first flow). After the CLI process is
#               confirmed dead — the only moment nothing writes the session
#               file — the watcher runs `stripper.py apply` to atomically swap
#               it over the original, then relaunches. If apply refuses or
#               fails, the session is relaunched UNSTRIPPED (fail-open) and
#               the kickoff prompt says so. The pending's original must be the
#               session being resumed.
# --prompt      initial prompt passed on the resumed CLI's command line
# --grace       seconds before rechecking ownership and stopping the CLI (default 15)
# --force       escalate to SIGTERM after 20s instead of waiting out a long turn
# --cmd         full relaunch command override, e.g. --cmd "claude --model opus".
#               Skips flag reconstruction; the CLI's resume selector is appended.
# --dry-run     synchronously verifies and prints the plan; starts no watcher
#
# Terminal backend and caller identity come from the companion agent-chat CLI.
# Codex requires a current, unique thread-id title, never inherited terminal env.
#
# The relaunch command is rebuilt from the running Claude Code's exact argv: all
# flags are kept (e.g. --dangerously-skip-permissions, --model) EXCEPT session
# selectors, which are stripped so the wrong session can't be resumed:
#   -c/--continue, -r/--resume [id], --from-pr [ref], --session-id <id>,
#   --fork-session, -w/--worktree [name]/--tmux (would create a new worktree
#   instead of resuming in place), --bg; and any prompt it was launched with.
# A background session is resumed with `claude --bg --resume <id>`: the daemon
# wakes the same job with its saved options.
# Watcher log: ~/.claude/respawn/respawn.log

set -u
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
BASE="$HOME/.claude/respawn"
mkdir -p "$BASE"

die() { echo "respawn: $*" >&2; exit 1; }
sq() { local s="$1" q="'"; s=${s//$q/$q\\$q$q}; printf '%s%s%s' "$q" "$s" "$q"; }

SID="" KICKOFF="" GRACE=15 FORCE="" DRY="" CMD="" SWAP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt) KICKOFF="$2"; shift 2;;
    --grace) GRACE="$2"; shift 2;;
    --force) FORCE=1; shift;;
    --cmd) CMD="$2"; shift 2;;
    --swap) SWAP="$2"; shift 2;;
    --dry-run) DRY="dry"; shift;;
    -*) die "unknown arg '$1' (see header of $0)";;
    *) [ -n "$SID" ] && die "multiple session ids given"; SID="$1"; shift;;
  esac
done

# Use the same identity and ownership implementation as peer control. Resolve
# the sibling checkout first so an older PATH install cannot bypass this guard.
AC="$SCRIPT_DIR/../../agent-chat/scripts/agent-chat"
[ -x "$AC" ] || AC="$(command -v agent-chat 2>/dev/null || true)"
[ -x "$AC" ] || die "agent-chat is required for verified session/terminal identity; install the companion skill"
IDENTITY=$("$AC" whereami --porcelain) || die "couldn't resolve caller identity"
read -r CLI ORIGIN_SID TB TA TERM_SOURCE <<< "$IDENTITY"
case "$CLI" in claude|codex) ;; *) die "caller identity is missing, ambiguous, or unsupported ($CLI)";; esac
if [ -z "$TB" ] || [ "$TB" = none ]; then
  [ "$CLI" = codex ] && die "can't prove this session's pane; enable 'agent-chat setup-codex --title-id', restart the TUI, and register again"
  die "can't prove this session's pane: it runs neither in an iTerm2/tmux pane nor as a Claude Code background session"
fi
[ -n "$SID" ] || SID="$ORIGIN_SID"
[[ "$SID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
  || die "'$SID' does not look like a session id (pass one explicitly)"
[[ "$GRACE" =~ ^[0-9]+$ ]] || die "--grace must be a nonnegative number of seconds"
if [ "$TB" = bg ]; then
  # A Claude Code background session: its worker runs on a daemon-owned pty,
  # so the proof is Claude Code's own live record (pid + start time + job).
  HOLDER=$("$AC" _cc-holder "$ORIGIN_SID") || die "can't prove this background session's process — nothing scheduled"
  read -r CLI_PID CLI_BIRTH _ JOB _ BG_CWD <<< "$HOLDER"
  [ "$JOB" = "$TA" ] || die "session $ORIGIN_SID runs as job $JOB, not $TA — nothing scheduled"
  TTY_PATH="-" SHELL_PID="-" SHELL_BIRTH="-"
else
  PROOF=$("$AC" _session-proof "$CLI" "$ORIGIN_SID" "$TB" "$TA") \
    || die "the pane's current foreground CLI isn't provably this session — nothing scheduled"
  read -r TTY_PATH CLI_PID CLI_BIRTH SHELL_PID SHELL_BIRTH <<< "$PROOF"
  case "$(ps -o comm= -p "$SHELL_PID" 2>/dev/null)" in
    *zsh|*bash|*fish|*/sh|sh|-*) ;;
    *) die "the CLI isn't running under a shell; quitting it could close the pane";;
  esac
fi

# --swap: validate up front so a typo dies HERE, not in the detached watcher.
STRIPPER=""
if [ -n "$SWAP" ]; then
  [ "$CLI" = "claude" ] || die "--swap is for Claude Code sessions only"
  case "$SWAP" in
    *.pending) ;;
    *) die "--swap expects a <session>.jsonl.pending file, got: $SWAP";;
  esac
  SWAP="$(cd "$(dirname "$SWAP")" 2>/dev/null && pwd)/$(basename "$SWAP")" \
    || die "--swap path not found: $SWAP"
  [ -f "$SWAP" ] || die "--swap pending file not found: $SWAP"
  ORIG="${SWAP%.pending}"
  [ -f "$ORIG" ] || die "--swap original not found: $ORIG"
  ORIG_SID="$(basename "$ORIG" .jsonl)"
  [ "$ORIG_SID" = "$SID" ] \
    || die "--swap pending belongs to session $ORIG_SID but resuming $SID — swapping one session's file while resuming another is almost certainly a mistake"
  [ "$ORIG_SID" = "$ORIGIN_SID" ] || die "--swap must belong to the session being stopped"
  STRIPPER="$SCRIPT_DIR/../../session-stripper/scripts/stripper.py"
  STRIPPER="$(cd "$(dirname "$STRIPPER")" && pwd)/$(basename "$STRIPPER")" 2>/dev/null
  [ -f "$STRIPPER" ] || die "session-stripper not found next to respawn (needed for --swap): $STRIPPER"
fi

# Rebuild the relaunch command (shell syntax: it is typed into the pane's shell,
# or run by the watcher for a background session) and resume $SID. Claude Code's
# flags come from its exact argv, minus session selectors, worktree/background
# creation and any prompt it was launched with.
if [ "$CLI" = "codex" ]; then
  # codex: resume is a subcommand; flags aren't reliably re-derivable from ps,
  # so keep it simple (config/profile carries the settings anyway)
  RELAUNCH="${CMD:-codex} resume $SID"
elif [ "$TB" = bg ]; then
  # The daemon wakes a stopped job under its own id with its saved options.
  # A fork is a new session: it gets the job's saved options explicitly.
  if [ -n "$CMD" ]; then BASE_CMD="$CMD"
  elif [ "$SID" = "$ORIGIN_SID" ]; then BASE_CMD="claude"
  else FLAGS=$("$AC" _job-flags "$JOB"); BASE_CMD="claude${FLAGS:+ $FLAGS}"; fi
  RELAUNCH="cd $(sq "$BG_CWD") && $BASE_CMD --bg --resume $SID"
elif [ -n "$CMD" ]; then
  RELAUNCH="$CMD --resume $SID"
else
  RELAUNCH="$("$AC" _launch-cmd "$CLI_PID" resume 2>/dev/null || echo claude) --resume $SID"
fi

if [ -z "$KICKOFF" ]; then
  if [ -n "$SWAP" ]; then
    KICKOFF="[respawn] Your CLI was restarted after a session strip: the stripped pending copy was swapped in while the CLI was dead. The tail of your final pre-restart turn (the stripping commands themselves) was discarded by design — do NOT re-run the strip. Re-read your recent context and continue where you left off."
  else
    KICKOFF="[respawn] Your CLI was restarted, resuming your session. Re-read your recent context and continue exactly where you left off."
  fi
  # Claude Code stops a session's background shells and monitors when it exits
  [ "$CLI" = claude ] && KICKOFF="$KICKOFF Background shells and monitors from before the restart (e.g. an agent-chat watch) were stopped — re-arm the ones you still need."
fi

WAIT_EXIT=900
[ -n "$FORCE" ] && WAIT_EXIT=20

if [ -n "$DRY" ]; then
  if [ "$TB" = bg ]; then
    echo "DRY RUN: verified Claude Code background session $ORIGIN_SID (job $JOB), pid $CLI_PID born $CLI_BIRTH, cwd $BG_CWD."
    echo "Would wait for its turn to end, run: claude stop $JOB, then: $RELAUNCH (kickoff passed as a command-line argument)."
  else
    echo "DRY RUN: verified $CLI session $ORIGIN_SID on $TB $TA ($TTY_PATH), pid $CLI_PID born $CLI_BIRTH, shell $SHELL_PID born $SHELL_BIRTH."
    echo "Would resume: $RELAUNCH (kickoff passed as a command-line argument)."
  fi
  [ -z "$SWAP" ] || echo "Would apply after exit: $SWAP"
  exit 0
fi

nohup "$SCRIPT_DIR/respawn-watcher.sh" "$TB" "$TA" "$SID" "$RELAUNCH" "$KICKOFF" "$GRACE" "$WAIT_EXIT" "$DRY" "$CLI" "$SWAP" "$STRIPPER" "$ORIGIN_SID" "$TTY_PATH" "$CLI_PID" "$CLI_BIRTH" "$SHELL_PID" "$SHELL_BIRTH" "$AC" \
  >> "$BASE/respawn.log" 2>&1 &
disown

echo "Respawn scheduled${DRY:+ (DRY RUN)} on $TB ($TA), cli=$CLI."
[ -n "$SWAP" ] && echo "Swap after exit: $SWAP → ${SWAP%.pending}"
echo "Relaunch command: $RELAUNCH"
echo "Log: $BASE/respawn.log"
if [ "$TB" = bg ]; then
  echo "IMPORTANT: end your turn NOW. After ${GRACE}s the watcher re-verifies this exact session, waits for your turn to end, then stops background job $JOB (terminals attached to it are re-attached afterwards)."
else
  echo "IMPORTANT: end your turn NOW. After ${GRACE}s the watcher re-verifies this exact session, then quits it ($CLI)."
fi
