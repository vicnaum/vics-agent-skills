#!/usr/bin/env bash
# respawn.sh — schedule a restart of a Claude Code CLI in its iTerm window,
# resuming its (typically just-stripped) session. The agent calls this as its
# LAST action, then ends its turn; a detached watcher does the rest.
#
# Usage (self-respawn, the common case):
#   respawn.sh [--session <sid>] [--prompt "kickoff text"] [--grace N] [--force] [--dry-run]
# Peer respawn (requires the peer to be registered in agent-chat):
#   respawn.sh --name <agent-name> [...]
#
# --session   session id to resume (default: this session; pass the NEW id when
#             session-stripper forked to a new session instead of in-place)
# --prompt    first prompt typed into the resumed CLI (default: a continue-where-
#             you-left-off instruction)
# --grace     seconds the watcher waits before typing /exit (default 15)
# --force     escalate to SIGTERM after 20s instead of waiting out a long turn
# --dry-run   watcher logs what it would do, types/kills nothing
#
# The relaunch reuses the CLI's original command line captured from ps (flags
# like --dangerously-skip-permissions are preserved), swapping in --resume <sid>.
# Watcher log: ~/.claude/respawn/respawn.log

set -u
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
BASE="$HOME/.claude/respawn"
REG="$HOME/.claude/agent-chat/registry"
JQ="/usr/bin/jq"
mkdir -p "$BASE"

die() { echo "respawn: $*" >&2; exit 1; }

NAME="" SID="" KICKOFF="" GRACE=15 FORCE="" DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --session) SID="$2"; shift 2;;
    --prompt) KICKOFF="$2"; shift 2;;
    --grace) GRACE="$2"; shift 2;;
    --force) FORCE=1; shift;;
    --dry-run) DRY="dry"; shift;;
    *) die "unknown arg '$1' (see header of $0)";;
  esac
done

if [ -n "$NAME" ]; then
  f="$REG/$NAME.json"
  [ -e "$f" ] || die "no agent named '$NAME' in the agent-chat registry"
  UUID=$($JQ -r '.iterm_session_id // empty' "$f"); UUID="${UUID#*:}"
  [ -n "$SID" ] || SID=$($JQ -r '.session_id // empty' "$f")
else
  UUID="${ITERM_SESSION_ID:-}"; UUID="${UUID#*:}"
  [ -n "$SID" ] || SID="${CLAUDE_CODE_SESSION_ID:-}"
  # personalize the kickoff if this session is registered in agent-chat
  if [ -z "$NAME" ] && [ -d "$REG" ] && [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
    for f in "$REG"/*.json; do
      [ -e "$f" ] || break
      n=$($JQ -r --arg sid "$CLAUDE_CODE_SESSION_ID" 'select(.session_id == $sid) | .name' "$f")
      [ -n "$n" ] && { NAME="$n"; break; }
    done
  fi
fi

[ -n "$UUID" ] || die "no iTerm session id (not running inside iTerm?)"
case "$SID" in ''|manual-*) die "no session id to resume (pass --session <sid>)";; esac
[[ "$SID" =~ ^[0-9a-f-]{36}$ ]] || die "'$SID' does not look like a session id"

if [ -z "$KICKOFF" ]; then
  KICKOFF="[respawn] Your CLI was restarted, resuming your session (after a strip). Re-read your recent context and continue exactly where you left off."
  [ -n "$NAME" ] && KICKOFF="$KICKOFF First run: agent-chat register $NAME"
fi

WAIT_EXIT=900
[ -n "$FORCE" ] && WAIT_EXIT=20

nohup "$SCRIPT_DIR/respawn-watcher.sh" "$UUID" "$SID" "$KICKOFF" "$GRACE" "$WAIT_EXIT" "$DRY" \
  >> "$BASE/respawn.log" 2>&1 &
disown

echo "Respawn scheduled (session $SID${NAME:+, agent '$NAME'}${DRY:+, DRY RUN})."
echo "Watcher: waits ${GRACE}s, types /exit (queues if a turn is still running), relaunches with --resume, then types the kickoff prompt."
echo "Log: $BASE/respawn.log"
if [ -z "$NAME" ] || [ -z "$DRY" ]; then
  echo "IMPORTANT: if you are respawning yourself, this must be your LAST action — end your turn NOW."
fi
