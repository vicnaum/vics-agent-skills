#!/usr/bin/env bash
# Internal: detached watcher spawned by respawn.sh — do not run directly.
# Exits the Claude Code CLI in one terminal (iTerm session or tmux pane),
# relaunches it with the given command, and types a kickoff prompt.
#
# args: <backend: iterm|tmux> <addr> <session-id> <relaunch-cmd> <kickoff-prompt> <grace-secs> <exit-wait-secs> [dry] [cli: claude|codex]

BASE="$HOME/.claude/respawn"
LOG="$BASE/respawn.log"
TB="$1"; TA="$2"; SID="$3"; RELAUNCH="$4"; KICKOFF="$5"; GRACE="${6:-15}"; WAIT_EXIT="${7:-900}"; DRY="${8:-}"; CLI="${9:-claude}"

log() { echo "[$(date '+%F %T')] [$SID] $*" >> "$LOG"; }

iterm_write() { # payload — verbatim, no auto-newline
  osascript - "$TA" "$1" <<'AS'
on run argv
  set targetId to item 1 of argv
  set payload to item 2 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s as text) is equal to targetId then
            tell s to write text payload newline NO
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

itype() { # type text + Enter. Enter is a SEPARATE keystroke: TUIs run with
  # bracketed paste on, so a newline inside the payload is pasted as literal
  # content into the input box without submitting.
  [ -n "$DRY" ] && { log "DRY: would type: $1"; return 0; }
  case "$TB" in
    tmux)
      tmux send-keys -t "$TA" -l -- "$1"
      tmux send-keys -t "$TA" Enter
      ;;
    iterm)
      iterm_write "$1" >/dev/null
      sleep 0.2
      iterm_write $'\r' >/dev/null
      ;;
  esac
}

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

claude_pid() { # tty short name -> pid of the claude/codex CLI on it (empty if none)
  ps -t "$1" -o pid=,command= 2>/dev/null | grep -E '^ *[0-9]+ +(\S*/)?(claude|codex)( |$)' | awk '{print $1}' | head -n1
}

send_exit() { # graceful-quit keystrokes, per CLI
  case "$CLI" in
    codex)
      # codex quits on double ctrl-c at the prompt (no /exit slash command)
      log "sending double ctrl-c (codex quit)"
      if [ -n "$DRY" ]; then log "DRY: would send ctrl-c x2"; return 0; fi
      case "$TB" in
        tmux)  tmux send-keys -t "$TA" C-c; sleep 0.7; tmux send-keys -t "$TA" C-c;;
        iterm) iterm_write $'\003' >/dev/null; sleep 0.7; iterm_write $'\003' >/dev/null;;
      esac
      ;;
    *)
      itype "/exit"
      ;;
  esac
}

wait_pid_gone() { # pid timeout-secs -> rc 0 if gone
  local pid="$1" deadline=$((SECONDS + $2))
  while kill -0 "$pid" 2>/dev/null; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
  return 0
}

log "watcher started ($TB $TA, grace=${GRACE}s, exit-wait=${WAIT_EXIT}s${DRY:+, DRY RUN})"
log "relaunch command: $RELAUNCH"
sleep "$GRACE"

TTY_PATH=$(get_tty)
if [ -z "$TTY_PATH" ]; then
  log "ABORT: terminal $TB $TA not found (window/pane closed?)"
  exit 1
fi
SHORT="${TTY_PATH#/dev/}"
log "target tty: $SHORT"

PID=$(claude_pid "$SHORT")
if [ -n "$PID" ]; then
  log "cli pid $PID — sending graceful quit ($CLI)"
  send_exit
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
