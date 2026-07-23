#!/usr/bin/env bash
# respawn.sh — schedule a restart of THIS Claude Code CLI in its own terminal
# (iTerm window or tmux pane), resuming a session (typically right after a
# session-stripper strip). The agent calls this as its LAST action, then ends
# its turn; a detached watcher does the rest.
#
# Usage:
#   respawn.sh [<session-id>] [--prompt "kickoff"] [--grace N] [--force] [--cmd "claude ..."] [--dry-run]
#
# <session-id>  session to resume. Default: $CLAUDE_CODE_SESSION_ID (this
#               session) — correct for in-place strips. Pass the NEW id after
#               a forked strip.
# --prompt      first prompt typed into the resumed CLI
# --grace       seconds the watcher waits before typing /exit (default 15)
# --force       escalate to SIGTERM after 20s instead of waiting out a long turn
# --cmd         full relaunch command override, e.g. --cmd "claude --model opus".
#               Skips ps-based reconstruction; --resume <sid> is appended.
# --dry-run     watcher logs what it would do, types/kills nothing
#
# Terminal backends: tmux pane ($TMUX_PANE) or iTerm2 window ($ITERM_SESSION_ID);
# tmux wins when both are set. Works headless (e.g. Linux server) under tmux.
#
# The relaunch command is rebuilt from the running CLI's ps entry: all flags
# are kept (e.g. --dangerously-skip-permissions, --model) EXCEPT session
# selectors, which are stripped so the wrong session can't be resumed:
#   -c/--continue, -r/--resume [id], --from-pr [ref], --session-id <id>,
#   --fork-session, and -w/--worktree [name]/--tmux (would create a new
#   worktree instead of resuming in place).
# Caveat: ps loses shell quoting — if the CLI was launched with quoted args
# containing spaces (e.g. --append-system-prompt "be brief"), pass --cmd.
# Watcher log: ~/.claude/respawn/respawn.log

set -u
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
BASE="$HOME/.claude/respawn"
mkdir -p "$BASE"

die() { echo "respawn: $*" >&2; exit 1; }

SID="" KICKOFF="" GRACE=15 FORCE="" DRY="" CMD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt) KICKOFF="$2"; shift 2;;
    --grace) GRACE="$2"; shift 2;;
    --force) FORCE=1; shift;;
    --cmd) CMD="$2"; shift 2;;
    --dry-run) DRY="dry"; shift;;
    -*) die "unknown arg '$1' (see header of $0)";;
    *) [ -n "$SID" ] && die "multiple session ids given"; SID="$1"; shift;;
  esac
done

# terminal backend of this very session
if [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ]; then
  TB="tmux"; TA="$TMUX_PANE"
elif [ -n "${ITERM_SESSION_ID:-}" ]; then
  TB="iterm"; TA="${ITERM_SESSION_ID#*:}"
else
  die "no supported terminal (need iTerm2 or tmux)"
fi

# which CLI is this? Claude Code exports CLAUDE_CODE_SESSION_ID, Codex exports
# CODEX_THREAD_ID (same UUID `codex resume` takes). Determines the exit method
# (/exit vs double ctrl-c) and the relaunch form (--resume vs resume).
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then CLI="claude"
elif [ -n "${CODEX_THREAD_ID:-}" ]; then CLI="codex"
else CLI="claude"; fi

[ -n "$SID" ] || SID="${CLAUDE_CODE_SESSION_ID:-${CODEX_THREAD_ID:-}}"
[[ "$SID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
  || die "'$SID' does not look like a session id (pass one explicitly)"

get_tty() {
  case "$TB" in
    tmux) tmux display -p -t "$TA" '#{pane_tty}' 2>/dev/null;;
    iterm)
      osascript - "$TA" <<'AS'
on run argv
  set targetId to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s as text) is equal to targetId then return (tty of s)
        end repeat
      end repeat
    end repeat
  end tell
  return ""
end run
AS
      ;;
  esac
}

# Rebuild the relaunch command from the live process: keep every flag except
# session selectors (and worktree/tmux creation), then resume $SID.
build_relaunch() {
  local orig="$1"
  local -a toks out=()
  read -r -a toks <<< "$orig"
  local i=0 n=${#toks[@]} t
  while [ "$i" -lt "$n" ]; do
    t="${toks[$i]}"
    case "$t" in
      -c|--continue|--fork-session|--tmux) ;;                    # drop, no arg
      --session-id) i=$((i+1));;                                 # drop + required arg
      --session-id=*|--resume=*|--from-pr=*|--worktree=*) ;;     # drop inline-arg forms
      -r|--resume|--from-pr|-w|--worktree)                       # drop + optional arg
        if [ $((i+1)) -lt "$n" ] && [[ "${toks[$((i+1))]}" != -* ]]; then i=$((i+1)); fi;;
      *) out+=("$t");;
    esac
    i=$((i+1))
  done
  echo "${out[*]}"
}

if [ "$CLI" = "codex" ]; then
  # codex: resume is a subcommand; flags aren't reliably re-derivable from ps,
  # so keep it simple (config/profile carries the settings anyway)
  RELAUNCH="${CMD:-codex} resume $SID"
elif [ -n "$CMD" ]; then
  RELAUNCH="$CMD --resume $SID"
else
  TTY_PATH=$(get_tty)
  [ -n "$TTY_PATH" ] || die "terminal for this session not found ($TB $TA)"
  SHORT="${TTY_PATH#/dev/}"
  ORIG=$(ps -t "$SHORT" -o command= 2>/dev/null | grep -E '^(\S*/)?claude( |$)' | head -n1)
  [ -n "$ORIG" ] || ORIG="claude"
  RELAUNCH="$(build_relaunch "$ORIG") --resume $SID"
fi

[ -n "$KICKOFF" ] || KICKOFF="[respawn] Your CLI was restarted, resuming your session (after a strip). Re-read your recent context and continue exactly where you left off."

WAIT_EXIT=900
[ -n "$FORCE" ] && WAIT_EXIT=20

nohup "$SCRIPT_DIR/respawn-watcher.sh" "$TB" "$TA" "$SID" "$RELAUNCH" "$KICKOFF" "$GRACE" "$WAIT_EXIT" "$DRY" "$CLI" \
  >> "$BASE/respawn.log" 2>&1 &
disown

echo "Respawn scheduled${DRY:+ (DRY RUN)} on $TB ($TA), cli=$CLI."
echo "Relaunch command: $RELAUNCH"
echo "Log: $BASE/respawn.log"
echo "IMPORTANT: this must be your LAST action — end your turn NOW (the watcher types /exit in ${GRACE}s; if your turn is still running it queues and fires at turn end)."
