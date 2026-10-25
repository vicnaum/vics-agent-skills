#!/usr/bin/env bash
# Internal detached watcher. The scheduler supplies a verified original session,
# process birth token and parent shell. Recheck them after every wait; never
# discover and quit an arbitrary CLI in the saved pane.
# args: backend addr resume-sid relaunch kickoff grace exit-wait dry cli swap stripper
#       original-sid tty cli-pid cli-birth shell-pid shell-birth agent-chat-path
# Backend "bg" is a Claude Code background session (addr = its job id): there is
# no pane and no shell, so tty/shell are "-" and the daemon stops and resumes it.
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
# This watcher was started by one of the session's own tool commands: what it
# launches must not inherit that session's identity (CLAUDE_CODE_SESSION_ID,
# CLAUDE_JOB_DIR, its inbox socket ...). Typed relaunches use the pane's shell.
for _v in $(env | sed -n 's/^\(CLAUDE[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$_v"; done

same_process() { [ "$("$AC" _process-token "$PID" 2>/dev/null)" = "$BIRTH" ]; }
prove_session() { [ "$("$AC" _session-proof "$CLI" "$ORIGIN_SID" "$TB" "$TA" 2>/dev/null)" = "$EXPECTED" ]; }
prove_shell() { "$AC" _shell-proof "$TB" "$TA" "$TTY_PATH" "$SHELL_PID" "$SHELL_BIRTH"; }

iterm_write() { # session-id text
  local result
  result=$(osascript - "$1" "$2" <<'AS'
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

pane_text() { # backend addr text
  case "$1" in
    tmux) tmux send-keys -t "$2" -l -- "$3";;
    iterm) iterm_write "$2" "$3";;
    *) return 1;;
  esac
}

pane_key() { # backend addr enter|ctrl-c
  case "$1:$3" in
    tmux:enter) tmux send-keys -t "$2" Enter;;
    tmux:ctrl-c) tmux send-keys -t "$2" C-c;;
    iterm:enter) iterm_write "$2" $'\r';;
    iterm:ctrl-c) iterm_write "$2" $'\003';;
    *) return 1;;
  esac
}

write_text() { pane_text "$TB" "$TA" "$1"; }
write_key() { pane_key "$TB" "$TA" "$1"; }

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

# Claude Code asks before exiting while background shells or agents run:
# "Background work is running … ❯ 1. Exit and stop tasks / 2. Move to background
# and exit / 3. Stay". The queued /exit only reaches it when the turn ends, so
# watch for the dialog while waiting, and confirm its default (exit, stopping
# the tasks) only while the selection is on that choice.
exit_dialog_up() {
  [ "$CLI" = claude ] || return 1
  "$AC" _screen "$TB" "$TA" 2>/dev/null | tail -n 30 \
    | grep -A6 'Background work is running' | grep -qE '❯ *1\. Exit and stop tasks'
}

wait_gone() {
  local deadline=$((SECONDS + $1)) confirmed=0
  while same_process; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    if [ "$TB" != bg ] && [ "$confirmed" -lt 2 ] && exit_dialog_up; then
      prove_session || abort "session changed before confirming the exit dialog"
      write_key enter || abort "exit dialog Enter failed"
      confirmed=$((confirmed + 1))
      log "confirmed Claude Code's exit dialog (it stops the session's background tasks)"
    fi
    sleep 2
  done
}

# ---------- background (daemon-hosted) Claude Code sessions ----------

holder() { "$AC" _cc-holder "$1" 2>/dev/null; } # sid -> "pid birth kind job status cwd"
prove_bg() { # the original worker still holds the original session as the same job
  local pid birth kind job _
  read -r pid birth kind job _ <<< "$(holder "$ORIGIN_SID")"
  [ "$pid $birth $job" = "$PID $BIRTH $TA" ]
}
bg_status() { local _ st; read -r _ _ _ _ st _ <<< "$(holder "$ORIGIN_SID")"; echo "$st"; }

# Terminals showing this job through `claude attach` exit when it stops. Record
# each one that runs straight under a shell, so it can be re-attached in place.
ATTACHED=()
find_attached() {
  local pid ppid tty cmd pane pb
  while read -r pid ppid tty cmd; do
    case " $cmd " in *" attach $TA "*) ;; *) continue;; esac
    case "$(printf '%s' "$cmd" | awk '{print $1}')" in */claude|claude) ;; *) continue;; esac
    case "$tty" in ''|'?'|'??') continue;; esac
    case "$(ps -o comm= -p "$ppid" 2>/dev/null)" in *zsh|*bash|*fish|*/sh|sh|-*) ;; *) continue;; esac
    pane=$("$AC" _term-by-tty "/dev/$tty" 2>/dev/null) || continue
    pb=$("$AC" _process-token "$ppid" 2>/dev/null) || continue
    ATTACHED+=("$pane /dev/$tty $pid $("$AC" _process-token "$pid" 2>/dev/null) $ppid $pb")
  done < <(ps -axo pid=,ppid=,tty=,command= 2>/dev/null)
}

reattach() { # only into the original shell, at its prompt, once the old client is gone
  local entry tb ta tty cpid cbirth spid sbirth
  for entry in ${ATTACHED[@]+"${ATTACHED[@]}"}; do
    read -r tb ta tty cpid cbirth spid sbirth <<< "$entry"
    local i=0
    while [ "$i" -lt 10 ] && [ "$("$AC" _process-token "$cpid" 2>/dev/null)" = "$cbirth" ]; do sleep 1; i=$((i + 1)); done
    if [ "$("$AC" _process-token "$cpid" 2>/dev/null)" = "$cbirth" ]; then log "re-attach: client $cpid in $tb $ta still running — left alone"; continue; fi
    "$AC" _shell-proof "$tb" "$ta" "$tty" "$spid" "$sbirth" || { log "re-attach: $tb $ta isn't back at its shell prompt — left alone"; continue; }
    pane_text "$tb" "$ta" "claude attach $TA" || { log "re-attach: typing into $tb $ta failed"; continue; }
    sleep 1
    "$AC" _shell-proof "$tb" "$ta" "$tty" "$spid" "$sbirth" || { log "re-attach: $tb $ta changed before Enter — line left unsubmitted"; continue; }
    pane_key "$tb" "$ta" enter && log "re-attached $tb $ta ($tty)"
  done
}

respawn_bg() {
  local deadline new npid nbirth nkind njob _
  if same_process; then
    prove_bg || abort "job $TA no longer runs the original session in its original process"
  elif holder "$ORIGIN_SID" >/dev/null; then
    abort "the original worker is gone and session $ORIGIN_SID is running again elsewhere — left alone"
  fi
  [ -z "$DRY" ] || { log "DRY: verified background job $TA; would stop it, apply swap and run $RELAUNCH with a prompt argument"; exit 0; }

  # Stop only once the agent's turn has ended (it was told to end it at once):
  # a stop mid-turn would cut off its last reply.
  deadline=$((SECONDS + WAIT_EXIT))
  while same_process && [ "$(bg_status)" != idle ] && [ "$SECONDS" -lt "$deadline" ]; do sleep 2; done
  if same_process; then
    find_attached
    prove_bg || abort "ownership changed before stop"
    local dlog="$HOME/.claude/daemon.log" off
    off=$(wc -c < "$dlog" 2>/dev/null | tr -d ' '); off=${off:-0}
    "$(command -v claude || echo "$HOME/.local/bin/claude")" stop "$TA" >> "$LOG" 2>&1 || abort "claude stop $TA failed"
    log "stopped background job $TA (pid $PID)"
    if ! wait_gone 60; then
      prove_bg || abort "ownership changed before SIGTERM"
      kill -TERM "$PID" 2>/dev/null || abort "cannot signal captured worker"
      wait_gone 15 || abort "captured worker is still alive"
    fi
    # The daemon writes the stopped job off a moment after its worker exits
    # ("bg settled <job>"). Waking it before that starts a COPY under a new id.
    deadline=$((SECONDS + 20))
    until tail -c +"$((off + 1))" "$dlog" 2>/dev/null | grep -q "bg settled $TA "; do
      [ "$SECONDS" -lt "$deadline" ] || { log "no 'settled' line from the daemon in 20s — waking anyway"; break; }
      sleep 1
    done
  fi
  holder "$ORIGIN_SID" >/dev/null && abort "session $ORIGIN_SID was restarted by someone else after the stop — nothing swapped or launched"

  apply_swap

  # The daemon resumes a stopped job under its own id with its saved options;
  # the kickoff rides on the command line like a terminal relaunch.
  local out
  out=$(/bin/sh -c "$RELAUNCH $(sq "$KICKOFF")" 2>&1 </dev/null) || { printf '%s\n' "$out" >> "$LOG"; abort "relaunch failed: $RELAUNCH"; }
  printf '%s\n' "$out" >> "$LOG"
  case "$out" in *"started a copy"*|*"started a restricted copy"*)
    abort "the daemon started a COPY instead of resuming $SID (see above) — the original is stopped with the swap applied; resume it: claude attach $TA";;
  esac
  deadline=$((SECONDS + 60))
  while [ "$SECONDS" -lt "$deadline" ]; do
    new=$(holder "$SID")
    if [ -n "$new" ]; then
      read -r npid nbirth nkind njob _ <<< "$new"
      if [ "$npid $nbirth" != "$PID $BIRTH" ]; then
        [ "$SID" != "$ORIGIN_SID" ] || [ "$njob" = "$TA" ] || log "WARN: resumed as job $njob, not $TA (a copy?)"
        log "done: resumed session $SID in the background (job $njob, pid $npid); kickoff supplied on command line"
        reattach
        exit 0
      fi
    fi
    sleep 2
  done
  abort "resumed background session was not verified within 60s"
}

# Only the stopped original Claude session may be swapped. On apply refusal,
# retain the original and explain it in the prompt argument passed at launch.
apply_swap() {
  [ -n "$SWAP" ] || return 0
  if python3 "$STRIPPER" apply "$SWAP" >> "$LOG" 2>&1; then
    log "swap applied: $SWAP"
  else
    log "WARN: swap refused; relaunching UNSTRIPPED"
    KICKOFF="[respawn] WARNING: the strip was NOT applied. You are resuming the UNSTRIPPED session; pending copy: $SWAP. See ~/.claude/respawn/respawn.log."
  fi
}

log "watcher started ($TB $TA, original=$ORIGIN_SID pid=$PID birth=$BIRTH, grace=${GRACE}s)"
sleep "$GRACE"
# A second scheduled watcher cannot race the first. A later one will fail the
# original process/shell proof even after the first releases this lock.
LOCK="$BASE/pane-${TB}-${TA//[^a-zA-Z0-9_-]/_}.lock"
mkdir "$LOCK" 2>/dev/null || abort "another watcher owns this pane"
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
trap 'exit 1' HUP INT TERM

[ "$TB" != bg ] || respawn_bg

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

apply_swap

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
