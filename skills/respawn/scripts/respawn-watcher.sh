#!/usr/bin/env bash
# Internal detached watcher. The scheduler supplies a verified original session,
# process birth token and parent shell. Recheck them after every wait; never
# discover and quit an arbitrary CLI in the saved pane.
# args: backend addr resume-sid relaunch kickoff grace exit-wait dry cli swap stripper
#       original-sid tty cli-pid cli-birth shell-pid shell-birth agent-chat-path
set -u
BASE="$HOME/.claude/respawn"
LOG="$BASE/respawn.log"
TB="$1"; TA="$2"; SID="$3"; RELAUNCH="$4"; KICKOFF="$5"; GRACE="$6"; WAIT_EXIT="$7"; DRY="$8"; CLI="$9"
SWAP="${10}"; STRIPPER="${11}"; ORIGIN_SID="${12}"; TTY_PATH="${13}"; PID="${14}"; BIRTH="${15}"
SHELL_PID="${16}"; SHELL_BIRTH="${17}"; AC="${18}"
mkdir -p "$BASE"
log() { echo "[$(date '+%F %T')] [$SID] $*" >> "$LOG"; }
abort() { log "ABORT: $*"; exit 1; }
sq() { local s="$1" q="'"; s=${s//$q/$q\\$q$q}; printf '%s%s%s' "$q" "$s" "$q"; }
[ -x "$AC" ] || abort "verified agent-chat helper is unavailable"
EXPECTED="$TTY_PATH $PID $BIRTH $SHELL_PID $SHELL_BIRTH"

same_process() { [ "$("$AC" _process-token "$PID" 2>/dev/null)" = "$BIRTH" ]; }
prove_session() { [ "$("$AC" _session-proof "$CLI" "$ORIGIN_SID" "$TB" "$TA" 2>/dev/null)" = "$EXPECTED" ]; }
prove_shell() { "$AC" _shell-proof "$TB" "$TA" "$TTY_PATH" "$SHELL_PID" "$SHELL_BIRTH"; }

iterm_write() {
  local result
  result=$(osascript - "$TA" "$1" <<'AS'
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s as text) is equal to (item 1 of argv) then
            tell s to write text (item 2 of argv) newline NO
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run
AS
) || return 1
  [ "$result" = ok ]
}

write_text() {
  case "$TB" in
    tmux) tmux send-keys -t "$TA" -l -- "$1";;
    iterm) iterm_write "$1";;
    *) return 1;;
  esac
}

write_key() {
  case "$TB:$1" in
    tmux:enter) tmux send-keys -t "$TA" Enter;;
    tmux:ctrl-c) tmux send-keys -t "$TA" C-c;;
    iterm:enter) iterm_write $'\r';;
    iterm:ctrl-c) iterm_write $'\003';;
    *) return 1;;
  esac
}

quit_session() {
  prove_session || abort "session changed before quit — no keys sent"
  if [ "$CLI" = codex ]; then
    # The frontend process is the target, not its terminal input stream. This
    # also avoids a fresh ownership check exceeding Codex's double-Ctrl-C timer.
    kill -TERM "$PID" 2>/dev/null || abort "cannot signal captured Codex frontend"
  else
    write_text /exit || abort "quit text failed"
    sleep 1
    same_process || return 0
    prove_session || abort "session changed before quit Enter"
    write_key enter || abort "quit Enter failed"
  fi
}

wait_gone() {
  local deadline=$((SECONDS + $1))
  while same_process; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
}

log "watcher started ($TB $TA, original=$ORIGIN_SID pid=$PID birth=$BIRTH, grace=${GRACE}s)"
sleep "$GRACE"
# A second scheduled watcher cannot race the first. A later one will fail the
# original process/shell proof even after the first releases this lock.
LOCK="$BASE/pane-${TB}-${TA//[^a-zA-Z0-9_-]/_}.lock"
mkdir "$LOCK" 2>/dev/null || abort "another watcher owns this pane"
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
trap 'exit 1' HUP INT TERM

if same_process; then
  prove_session || abort "saved pane no longer holds the original foreground session"
else
  prove_shell || abort "original CLI exited but its verified shell isn't ready"
fi
[ -z "$DRY" ] || { log "DRY: verified target; would quit, apply swap and launch $RELAUNCH with a prompt argument"; exit 0; }

if same_process; then
  quit_session
  if ! wait_gone "$WAIT_EXIT"; then
    prove_session || abort "ownership changed before SIGTERM"
    kill -TERM "$PID" 2>/dev/null || abort "cannot signal captured CLI"
    if ! wait_gone 15; then
      prove_session || abort "ownership changed before SIGKILL"
      kill -KILL "$PID" 2>/dev/null || abort "cannot kill captured CLI"
      wait_gone 10 || abort "captured CLI is still alive"
    fi
  fi
fi
prove_shell || abort "original shell is not at the foreground prompt after quit"

# Only the stopped original Claude session may be swapped. On apply refusal,
# retain the original and explain it in the prompt argument passed at launch.
if [ -n "$SWAP" ]; then
  if python3 "$STRIPPER" apply "$SWAP" >> "$LOG" 2>&1; then
    log "swap applied: $SWAP"
  else
    log "WARN: swap refused; relaunching UNSTRIPPED"
    KICKOFF="[respawn] WARNING: the strip was NOT applied. You are resuming the UNSTRIPPED session; pending copy: $SWAP. See ~/.claude/respawn/respawn.log."
  fi
fi

# No prompt is ever typed into a booting TUI or into a shell after failed boot.
# Codex and Claude both accept their initial prompt as a command-line argument.
LAUNCH="$RELAUNCH $(sq "$KICKOFF")"
prove_shell || abort "shell changed before launch"
write_text "$LAUNCH" || abort "launch text failed"
sleep 1
prove_shell || abort "shell changed before launch Enter — line left unsubmitted"
write_key enter || abort "launch Enter failed"

# A process existing on the tty is not enough: prove the resumed session and
# unchanged parent shell. Startup dialogs/timeouts get an error, never more keys.
deadline=$((SECONDS + 60))
while [ "$SECONDS" -lt "$deadline" ]; do
  NEW_PROOF=$("$AC" _session-proof "$CLI" "$SID" "$TB" "$TA" 2>/dev/null) || NEW_PROOF=""
  if [ -n "$NEW_PROOF" ]; then
    read -r newtty newpid newbirth newparent newpb <<< "$NEW_PROOF"
    if [ "$newtty" = "$TTY_PATH" ] && [ "$newparent $newpb" = "$SHELL_PID $SHELL_BIRTH" ] \
       && [ "$newpid $newbirth" != "$PID $BIRTH" ]; then
      log "done: resumed session $SID, pid $newpid; kickoff supplied on command line"
      exit 0
    fi
    abort "new CLI doesn't belong to the original shell"
  fi
  sleep 2
done
abort "resumed session was not verified within 60s — no further input sent"
