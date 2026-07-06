#!/usr/bin/env bash
# Internal: detached watcher spawned by respawn.sh — do not run directly.
# Exits the Claude Code CLI in one iTerm session (by UUID), relaunches it with
# the given command, and types a kickoff prompt.
#
# args: <iterm-uuid> <session-id> <relaunch-cmd> <kickoff-prompt> <grace-secs> <exit-wait-secs> [dry]

BASE="$HOME/.claude/respawn"
LOG="$BASE/respawn.log"
UUID="$1"; SID="$2"; RELAUNCH="$3"; KICKOFF="$4"; GRACE="${5:-15}"; WAIT_EXIT="${6:-900}"; DRY="${7:-}"

log() { echo "[$(date '+%F %T')] [$SID] $*" >> "$LOG"; }

itype() { # type text + Enter into the target iTerm session.
  # Enter is a SEPARATE write-text call: TUIs run with bracketed paste on, so a
  # newline/\r inside the text payload is pasted as a literal line break into the
  # input box without submitting. A lone \r in its own call is a real Enter, and
  # also executes at a shell prompt. (Verified live against a Claude Code session.)
  [ -n "$DRY" ] && { log "DRY: would type: $1"; return 0; }
  osascript - "$UUID" "$1" <<'AS'
on run argv
  set targetId to item 1 of argv
  set msg to item 2 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s as text) is equal to targetId then
            tell s to write text msg newline NO
            delay 0.2
            tell s to write text (return) newline NO
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run
AS
}

get_tty() {
  osascript - "$UUID" <<'AS'
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
}

claude_pid() { # tty short name -> pid of the claude CLI on it (empty if none)
  ps -t "$1" -o pid=,command= 2>/dev/null | grep -E '^ *[0-9]+ +(\S*/)?claude( |$)' | awk '{print $1}' | head -n1
}

wait_pid_gone() { # pid timeout-secs -> rc 0 if gone
  local pid="$1" deadline=$((SECONDS + $2))
  while kill -0 "$pid" 2>/dev/null; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
  return 0
}

log "watcher started (grace=${GRACE}s, exit-wait=${WAIT_EXIT}s${DRY:+, DRY RUN})"
log "relaunch command: $RELAUNCH"
sleep "$GRACE"

TTY_PATH=$(get_tty)
if [ -z "$TTY_PATH" ]; then
  log "ABORT: iTerm session $UUID not found (window closed?)"
  exit 1
fi
SHORT="${TTY_PATH#/dev/}"
log "target tty: $SHORT"

PID=$(claude_pid "$SHORT")
if [ -n "$PID" ]; then
  log "claude pid $PID — typing /exit (queues and runs at turn end if a turn is active)"
  itype "/exit"
  if [ -z "$DRY" ] && ! wait_pid_gone "$PID" "$WAIT_EXIT"; then
    log "still alive after ${WAIT_EXIT}s — SIGTERM"
    kill "$PID" 2>/dev/null
    if ! wait_pid_gone "$PID" 15; then
      log "still alive — SIGKILL"
      kill -9 "$PID" 2>/dev/null
      wait_pid_gone "$PID" 10 || { log "ABORT: cannot kill $PID"; exit 1; }
    fi
  fi
  log "CLI exited"
else
  log "no claude process on tty — going straight to relaunch"
fi

[ -z "$DRY" ] && sleep 2
itype "$RELAUNCH"

if [ -z "$DRY" ]; then
  deadline=$((SECONDS + 60))
  NEWPID=""
  while [ -z "$NEWPID" ] && [ "$SECONDS" -lt "$deadline" ]; do
    sleep 2
    NEWPID=$(claude_pid "$SHORT")
  done
  if [ -n "$NEWPID" ]; then
    log "new claude pid $NEWPID — waiting for UI to settle"
  else
    log "WARN: new claude process not seen after 60s — typing kickoff anyway"
  fi
  sleep 8
fi

if [ -n "$KICKOFF" ]; then
  log "typing kickoff prompt"
  itype "$KICKOFF"
fi
log "done"
