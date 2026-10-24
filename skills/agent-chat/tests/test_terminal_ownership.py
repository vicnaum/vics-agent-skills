"""Hermetic terminal ownership regressions; no real GUI input, signals or agents.

Run: python3 -m unittest discover -s skills/agent-chat/tests -v
The fake terminal records actual public control flow and switches occupants at
wait/write boundaries. Both refusal cases and successful restarts are exercised.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CHAT_PATH = ROOT / 'skills/agent-chat/scripts/agent-chat'
WATCHER_PATH = ROOT / 'skills/respawn/scripts/respawn-watcher.sh'
SCHEDULER_PATH = ROOT / 'skills/respawn/scripts/respawn.sh'
CHAT = CHAT_PATH.read_text().split('# ---------- main ----------')[0]
TID = '01a16db1-81c3-7cd1-8f70-f5e8af3ee33a'
OTHER = '01a16db2-1111-7111-8111-111111111111'
SETUP = r'''
# Anything accidentally reaching real terminal/process primitives must fail.
tmux() { echo 'UNEXPECTED tmux' >> "$TRACE"; return 97; }
osascript() { echo 'UNEXPECTED osascript' >> "$TRACE"; return 97; }
ps() { return 1; }
kill() { echo 'UNEXPECTED kill' >> "$TRACE"; return 97; }
sleep() { :; }
term_list() { return 1; }
make_reg() {
  "$JQ" -n --arg sid "$TID" --arg src "${1:-screen}" \
    '{name:"peer",session_id:$sid,cli:"codex",term_backend:"tmux",term_addr:"%old",term_src:$src,registered_at:"2026-10-24T13:35:18+0200",rooms:["general"]}' > "$REG/peer.json"
}
candidate() { printf 'tmux%s%s%s%s%s%s%s%s\n' "$US" "$1" "$US" "$2" "$US" "${4:-0}" "$US" "${3:-codex}"; }
'''


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-chat-test-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = os.environ.copy()
        for key in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'OPENCODE_SESSION_ID',
                    'TMUX', 'TMUX_PANE', 'ITERM_SESSION_ID'):
            self.env.pop(key, None)
        self.trace = self.home / 'trace'
        self.trace.touch()
        self.env.update(HOME=str(self.home), AGENT_CHAT_HOME=str(self.home/'chat'),
                        TRACE=str(self.trace), TID=TID, OTHER=OTHER)

    def shell(self, body, library=True):
        r = subprocess.run(['/bin/bash'], input=(CHAT+SETUP if library else '')+body,
                           text=True, capture_output=True, env=self.env, cwd=ROOT, timeout=15)
        self.assertNotIn('UNEXPECTED', self.trace.read_text(), r.stderr)
        return r


class ShellCase(FixtureCase):
    def test_current_title_locates_and_refreshes_cached_pane(self):
        r = self.shell(r'''
make_reg
codex_cands() { candidate %new "$TID"; }
session_proof() { echo '/dev/new 11 100 12 90'; }
resolve_term peer
echo
reg_get peer term_tty
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), 'tmux %new\n/dev/new')

    def test_historical_hints_never_authorize_a_pane(self):
        for src in ('args', 'screen', 'start', 'spawn', 'ancestry', 'title'):
            with self.subTest(src=src):
                r = self.shell(f'''make_reg {src}
codex_cands() {{ candidate %old 'Generic Codex' 'codex resume "$TID"' 0; }}
term_screen() {{ echo 'Ran agent-chat register peer'; }}
saved_shell_proof() {{ return 1; }}
resolve_term peer
''')
                self.assertEqual(r.returncode, 2, r.stdout+r.stderr)
                self.assertEqual(r.stdout, '')

    def test_conflicting_title_vetoes_all_old_hints(self):
        r = self.shell(r'''
codex_cands() { candidate %wrong "$OTHER" "codex resume $TID" 0; }
term_screen() { echo 'Ran agent-chat register peer'; }
codex_locate "$TID" peer live
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, '')

    def test_duplicate_panes_are_ambiguous_but_wrappers_are_deduplicated(self):
        r = self.shell(r'''
codex_cands() { candidate %one "$TID"; candidate %two "$TID"; }
codex_locate "$TID"
''')
        self.assertNotEqual(r.returncode, 0)
        r = self.shell(r'''
codex_cands() { candidate %one "$TID"; candidate %one "$TID"; }
codex_locate "$TID"
''')
        self.assertEqual(r.stdout.strip(), 'tmux %one title')

    def test_truncated_title_is_supported_and_multiple_ids_are_rejected(self):
        r = self.shell(r'''
codex_cands() { candidate %one "⠴ Task | ${TID:0:28}... (codex)"; }
codex_locate "$TID"
''')
        self.assertEqual(r.stdout.strip(), 'tmux %one title')
        r = self.shell(r'''
codex_cands() { candidate %one "$TID $OTHER"; }
codex_locate "$TID"
''')
        self.assertNotEqual(r.returncode, 0)

    def test_codex_daemon_tty_and_env_are_ignored(self):
        r = self.shell(r'''
export TMUX=daemon TMUX_PANE=%daemon ITERM_SESSION_ID=daemon:wrong
ancestry() { printf '101\tttys99\tcodex app-server\n102\tttys99\t-zsh\n'; }
term_by_tty() { echo 'tmux %daemon'; }
codex_cands() { return 0; }
detect_term codex "$TID" peer
''')
        self.assertEqual(r.stdout.strip(), 'none - unlocated')

    def test_conflicting_environment_uses_nearest_cli_or_refuses(self):
        r = self.shell(r'''
export CLAUDE_CODE_SESSION_ID="$OTHER" CODEX_THREAD_ID="$TID"
ancestry() { printf '101\t??\tcodex app-server\n'; }
self_identity
''')
        self.assertEqual(r.stdout.strip(), 'codex '+TID)
        r = self.shell(r'''
export CLAUDE_CODE_SESSION_ID="$OTHER" CODEX_THREAD_ID="$TID"
ancestry() { return 0; }
self_identity
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, '')

    def test_key_failure_is_nonzero_and_not_ok(self):
        r = self.shell(r'''
make_reg
agent_term() { echo 'tmux %old'; }
control_proof() { return 0; }
term_key() { return 9; }
cmd_key peer enter
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('ok', r.stdout)

    def test_multi_key_sequence_stops_when_name_is_rebound(self):
        r = self.shell(r'''
make_reg
agent_term() { echo 'tmux %old'; }
resolve_term() { echo 'tmux %old'; }
pane_taken_by_other() { :; }
session_proof() { return 0; }
term_key() {
 echo "$3" >> "$TRACE"
 "$JQ" --arg sid "$OTHER" '.session_id=$sid' "$REG/peer.json" > "$REG/new"
 mv "$REG/new" "$REG/peer.json"
}
cmd_key peer down enter
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text().strip(), 'down')
        self.assertNotIn('ok', r.stdout)

    def test_failed_resolution_never_reaches_type(self):
        r = self.shell(r'''
agent_term() { return 2; }
term_type() { echo typed >> "$TRACE"; }
cmd_type peer text
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_nudge_rechecks_after_human_input_settle_delay(self):
        r = self.shell(r'''
make_reg
resolve_term() { echo 'tmux %old'; }
nudge_blocker() {
 "$JQ" --arg sid "$OTHER" '.session_id=$sid' "$REG/peer.json" > "$REG/new"
 mv "$REG/new" "$REG/peer.json"
}
term_type() { echo typed >> "$TRACE"; }
nudge_now peer
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_iterm_missing_session_and_write_failure_propagate(self):
        for behavior in ('echo notfound', 'return 7'):
            with self.subTest(behavior=behavior):
                r = self.shell(f'osascript() {{ {behavior}; }}\niterm_write missing text\n')
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn('ok', r.stdout)

    def test_cli_liveness_checks_program_not_shell_arguments_or_servers(self):
        for command, expected in (('zsh -c echo codex', False), ('codex app-server', False),
                                  ('codex exec-server', False), ('codex resume '+TID, True),
                                  ('/usr/local/bin/codex', True)):
            with self.subTest(command=command):
                r = self.shell('ps() { echo '+shlex.quote(command)+'; }\ntty_has_cli /dev/mock codex\n')
                self.assertEqual(r.returncode == 0, expected)

    def test_unlocated_watch_reports_and_never_auto_respawns(self):
        r = self.shell(r'''
make_reg
warm_terms() { return 0; }
agent_state() { printf 'unlocated\tcannot prove thread\n'; }
cmd_respawn() { echo respawned >> "$TRACE"; }
cmd_watch peer --interval 0 --respawn
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('UNLOCATED', r.stdout)
        self.assertIn('cannot watch', r.stdout)
        self.assertEqual(self.trace.read_text(), '')

    def test_inventory_failure_does_not_mean_offline(self):
        r = self.shell(r'''
_term_list_raw() { return 1; }
agent_state peer
''')
        self.assertIn('unlocated', r.stdout)
        self.assertNotIn('offline', r.stdout)

    def test_spawn_uses_proven_relative_pane_not_inherited_env(self):
        r = self.shell(r'''
export TMUX=daemon TMUX_PANE=%daemon
self_identity() { echo "codex $TID"; }
detect_term() { echo 'tmux %caller title'; }
term_type() { echo 'new pane launch' >> "$TRACE"; }
term_enter() { return 0; }
term_screen() { echo 'fresh agent'; }
tty_has_cli() { return 0; }
tmux() { echo "$*" >> "$TRACE"; case "$1" in split-window) echo %new;; display) echo /dev/mock;; esac; }
cmd_spawn child --tmux --pane --cmd codex
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-t %caller', self.trace.read_text())
        self.assertNotIn('%daemon', self.trace.read_text())

    def test_spawn_unlocated_relative_parent_creates_nothing(self):
        r = self.shell(r'''
self_identity() { echo "codex $TID"; }
detect_term() { echo 'none - unlocated'; }
cmd_spawn child --tmux --pane --cmd codex
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_session_proof_requires_current_unique_foreground_thread(self):
        base = r'''
warm_terms() { return 0; }
agent_tty() { echo /dev/mock; }
agent_pids() { echo 11; }
process_token() { echo 100; }
ps() { case "$*" in *stat*) echo 'S+';; *ppid*) echo 12;; esac; }
term_title() { echo "$TID"; }
codex_cands() { candidate %old "$TID"; }
'''
        r = self.shell(base+'session_proof codex "$TID" tmux %old\n')
        self.assertEqual(r.stdout.strip(), '/dev/mock 11 100 12 100')
        for change in ('term_title() { echo "$OTHER"; }',
                       "ps() { echo S; }", 'agent_pids() { printf "11\\n13\\n"; }'):
            with self.subTest(change=change):
                r = self.shell(base+change+'\nsession_proof codex "$TID" tmux %old\n')
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stdout, '')

    def test_remote_respawn_refuses_mismatched_session_without_signals(self):
        r = self.shell(r'''
make_reg
warm_terms() { return 0; }
resolve_term() { echo 'tmux %old'; }
pane_taken_by_other() { :; }
agent_tty() { echo /dev/mock; }
term_title() { echo "$OTHER"; }
agent_pids() { echo 11; }
session_proof() { return 1; }
cmd_respawn peer --ignore-cooldown --force-busy
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_remote_respawn_does_not_kill_a_replacement_pid(self):
        r = self.shell(r'''
make_reg
echo old > "$HOME/state"
warm_terms() { return 0; }
resolve_term() { echo 'tmux %old'; }
pane_taken_by_other() { :; }
agent_tty() { echo /dev/mock; }
term_title() { echo "$TID"; }
agent_pids() { if [ "$(cat "$HOME/state")" = old ]; then echo 11; else echo 22; fi; }
session_proof() { echo '/dev/mock 11 100 12 90'; }
is_shell_pid() { return 0; }
kill() { echo "$*" >> "$TRACE"; echo replacement > "$HOME/state"; }
same_process() { [ "$(cat "$HOME/state")" = old ]; }
shell_proof() { return 1; }
cmd_respawn peer --ignore-cooldown --force-busy
''')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.trace.read_text().strip(),'-TERM 11')

    def test_dead_recovery_requires_matching_session_fingerprint(self):
        base = r'''
make_reg title
"$JQ" --arg sid "$TID" '.term_verified_session=$sid | .term_cli_pid="11" | .term_cli_birth="100" | .term_tty="/dev/mock" | .term_shell_pid="12" | .term_shell_birth="90"' "$REG/peer.json" > "$REG/new"
mv "$REG/new" "$REG/peer.json"
process_token() { return 1; }
shell_proof() { return 0; }
'''
        r=self.shell(base+'saved_shell_proof peer tmux %old\n')
        self.assertEqual(r.returncode,0,r.stderr)
        for change in ('process_token() { echo 100; }',
                       '"$JQ" --arg sid "$OTHER" \'.session_id=$sid\' "$REG/peer.json" > "$REG/new"; mv "$REG/new" "$REG/peer.json"'):
            with self.subTest(change=change):
                r=self.shell(base+change+'\nsaved_shell_proof peer tmux %old\n')
                self.assertNotEqual(r.returncode,0)

    def test_shell_proof_rejects_reused_parent_and_nonforeground_shell(self):
        base = r'''
agent_tty() { echo /dev/ttys01; }
process_token() { echo 90; }
is_shell_pid() { return 0; }
ps() { echo ttys01; }
tty_fg_shell() { return 0; }
'''
        r=self.shell(base+'shell_proof tmux %old /dev/ttys01 12 90\n')
        self.assertEqual(r.returncode,0,r.stderr)
        for change in ('process_token() { echo 999; }','tty_fg_shell() { return 1; }',
                       'agent_tty() { echo /dev/ttys02; }'):
            with self.subTest(change=change):
                r=self.shell(base+change+'\nshell_proof tmux %old /dev/ttys01 12 90\n')
                self.assertNotEqual(r.returncode,0)

    def test_forced_nudge_cannot_bypass_ownership(self):
        r=self.shell(r'''
make_reg
agent_term() { echo 'tmux %old'; }
control_proof() { return 1; }
term_type() { echo typed >> "$TRACE"; }
cmd_nudge peer text --force
''')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.trace.read_text(),'')

    def test_failed_tmux_creation_never_sends_keys(self):
        r=self.shell(r'''
tmux() { return 1; }
term_type() { echo typed >> "$TRACE"; }
cmd_spawn child --tmux --cmd codex
''')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.trace.read_text(),'')

    def test_claude_inbox_requires_registered_session_identity(self):
        r=self.shell(r'''
make_reg
resolve_term() { echo 'tmux %old'; }
session_proof() { return 1; }
cc_inbox peer
''')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(r.stdout,'')

    def test_liveness_any_accepts_real_cli_program(self):
        r=self.shell(r'''
ps() { echo codex; }
tty_has_cli /dev/mock any
''')
        self.assertEqual(r.returncode,0)


FAKE_AC = r'''#!/usr/bin/env python3
import os, pathlib, sys
state = pathlib.Path(os.environ['STATE']).read_text().strip()
cmd, *args = sys.argv[1:]
old = os.environ['TID']; target = os.environ.get('RESUME_SID', old)
if cmd == 'whereami':
    print('codex',old,'tmux','%saved','title'); sys.exit(0)
if cmd == '_process-token':
    if args[0] == '111' and state in ('old','other'):
        print('100'); sys.exit(0)
    if args[0] == '111' and state == 'reused':
        print('999'); sys.exit(0)
    sys.exit(1)
if cmd == '_shell-proof':
    sys.exit(0 if state == 'shell' else 1)
if cmd == '_session-proof':
    sid = args[1]
    if os.environ.get('MODE') == 'switch-before-signal':
        countpath=pathlib.Path(os.environ['STATE']+'.proofs')
        count=int(countpath.read_text())+1 if countpath.exists() else 1
        countpath.write_text(str(count))
        if count == 2:
            pathlib.Path(os.environ['STATE']).write_text('other'); sys.exit(1)
    if state == 'old' and sid == old:
        print('/dev/mock 111 100 222 90'); sys.exit(0)
    if state == 'new' and sid == target:
        print('/dev/mock 333 200 222 90'); sys.exit(0)
    sys.exit(1)
sys.exit(1)
'''
FAKE_IO = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
statepath=pathlib.Path(os.environ['STATE']); state=statepath.read_text().strip()
mode=os.environ.get('MODE','ok'); tool=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
if tool == 'ps': print('zsh'); sys.exit(0)
if tool == 'osascript':
    payload=args[-1]; kind='key' if payload in ('\r','\x03') else 'text'
    key={'\r':'Enter','\x03':'C-c'}.get(payload,payload)
else:
    kind='text' if '-l' in args else 'key'; payload=args[-1]; key=payload
with open(os.environ['TRACE'],'a') as f: f.write(json.dumps({'kind':kind,'payload':payload})+'\n')
if mode == 'write-fail': sys.exit(7)
if mode == 'missing-iterm': print('notfound'); sys.exit(0)
if kind == 'key' and key == 'C-c':
    countpath=pathlib.Path(os.environ['STATE']+'.keys')
    count=int(countpath.read_text())+1 if countpath.exists() else 1; countpath.write_text(str(count))
    if mode == 'switch-between-keys': statepath.write_text('other')
    elif count == 2 and mode != 'escalate': statepath.write_text('shell')
elif kind == 'text':
    pathlib.Path(os.environ['STATE']+'.text').write_text(payload)
    if mode == 'switch-before-enter' and state == 'shell': statepath.write_text('other')
elif kind == 'key' and key == 'Enter':
    text=pathlib.Path(os.environ['STATE']+'.text').read_text()
    if text == '/exit': statepath.write_text('shell')
    elif state == 'shell' and mode != 'boot-fail': statepath.write_text('new')
if tool == 'osascript': print('ok')
'''


class WatcherCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.state = self.home/'state'; self.state.write_text('old')
        self.bin = self.home/'bin'; self.bin.mkdir()
        self.ac = self.bin/'agent-chat'; self.ac.write_text(FAKE_AC); self.ac.chmod(0o755)
        for tool in ('tmux','osascript','ps'):
            p=self.bin/tool; p.write_text(FAKE_IO); p.chmod(0o755)
        self.env.update(STATE=str(self.state), PATH=str(self.bin)+':'+self.env['PATH'])

    def watcher(self, mode='ok', state='old', cli='codex', backend='tmux', dry='', swap='', stripper=''):
        self.state.write_text(state); self.env['MODE']=mode
        args=[backend,'%saved',self.env.get('RESUME_SID',TID),'codex resume '+self.env.get('RESUME_SID',TID),
              "KICKOFF_SENTINEL 'quote' $literal",'0','1',dry,cli,swap,stripper,TID,'/dev/mock','111','100','222','90',str(self.ac)]
        body='''sleep() { SECONDS=$((SECONDS+65)); }
kill() {
 echo "SIGNAL $*" >> "$TRACE"
 if [ "${MODE:-}" = escalate ] && [ ! -f "$STATE.signaled" ]; then touch "$STATE.signaled";
 else echo shell > "$STATE"; fi
}
set -- '''+' '.join(shlex.quote(a) for a in args)+'\nsource '+shlex.quote(str(WATCHER_PATH))+'\n'
        return self.shell(body,library=False)

    def events(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines() if line.startswith('{')]

    def test_session_changed_during_grace_sends_nothing(self):
        r=self.watcher(state='other')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.events(),[])

    def test_pid_reuse_is_not_the_original_cli(self):
        r=self.watcher(state='reused')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.events(),[])

    def test_thread_switch_before_signal_stops_immediately(self):
        r=self.watcher(mode='switch-before-signal')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.trace.read_text(),'')

    def test_successful_restart_passes_prompt_on_launch_argv(self):
        for cli,backend in (('codex','tmux'),('codex','iterm'),('claude','tmux'),('claude','iterm')):
            with self.subTest(cli=cli,backend=backend):
                self.trace.write_text(''); (self.home/'state.keys').unlink(missing_ok=True)
                r=self.watcher(cli=cli,backend=backend)
                self.assertEqual(r.returncode,0,r.stderr)
                texts=[e['payload'] for e in self.events() if e['kind']=='text']
                launch=texts[-1]
                self.assertIn('KICKOFF_SENTINEL',launch)
                self.assertEqual(shlex.split(launch)[-1], "KICKOFF_SENTINEL 'quote' $literal")
                self.assertEqual(sum('KICKOFF_SENTINEL' in t for t in texts),1)
                self.assertEqual(self.state.read_text(),'new')

    def test_failed_startup_sends_no_standalone_kickoff(self):
        r=self.watcher(mode='boot-fail',state='shell')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.events()),2)  # launch text + Enter only
        log=(self.home/'.claude/respawn/respawn.log').read_text()
        self.assertIn('no further input sent',log)

    def test_swap_refusal_keeps_pending_and_passes_unstripped_prompt_on_argv(self):
        pending=self.home/'session.jsonl.pending'; pending.write_text('retained pending bytes')
        stripper=self.home/'stripper.py'; stripper.write_text('raise SystemExit(1)\n')
        r=self.watcher(cli='claude',swap=str(pending),stripper=str(stripper))
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(pending.read_text(),'retained pending bytes')
        texts=[e['payload'] for e in self.events() if e['kind']=='text']
        self.assertEqual(len(texts),2)  # /exit and the full launch line
        self.assertIn('UNSTRIPPED',shlex.split(texts[-1])[-1])

    def test_swap_runs_only_after_cli_is_gone(self):
        pending=self.home/'session.jsonl.pending'; pending.write_text('pending')
        stripper=self.home/'stripper.py'
        stripper.write_text('import os,pathlib,sys\n'
                            'assert pathlib.Path(os.environ["STATE"]).read_text().strip()=="shell"\n'
                            'pathlib.Path(sys.argv[2]).write_text("applied after exit")\n')
        r=self.watcher(cli='claude',swap=str(pending),stripper=str(stripper))
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(pending.read_text(),'applied after exit')

    def test_shell_replaced_before_launch_enter_is_not_submitted(self):
        r=self.watcher(mode='switch-before-enter',state='shell')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.events()),1)
        self.assertEqual(self.events()[0]['kind'],'text')

    def test_failed_text_write_never_sends_enter(self):
        r=self.watcher(mode='write-fail',state='shell')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.events()),1)

    def test_iterm_missing_session_aborts(self):
        r=self.watcher(mode='missing-iterm',backend='iterm',cli='claude')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.events()),1)

    def test_forced_escalation_only_signals_captured_process(self):
        r=self.watcher(mode='escalate')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.trace.read_text().count('SIGNAL -TERM 111'),2)

    def test_dry_run_sends_no_input_or_signals(self):
        r=self.watcher(dry='dry')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.trace.read_text(),'')

    def test_fork_resume_checks_old_identity_then_verifies_new_identity(self):
        self.env['RESUME_SID']=OTHER
        r=self.watcher()
        self.assertEqual(r.returncode,0,r.stderr)
        launch=next(e['payload'] for e in self.events() if e['kind']=='text')
        self.assertIn(OTHER,launch)

    def test_scheduler_uses_shared_identity_even_with_stale_env(self):
        bundle=self.home/'bundle'
        scripts=bundle/'respawn/scripts'; scripts.mkdir(parents=True)
        acdir=bundle/'agent-chat/scripts'; acdir.mkdir(parents=True)
        (acdir/'agent-chat').write_text(FAKE_AC); (acdir/'agent-chat').chmod(0o755)
        scheduler=scripts/'respawn.sh'; scheduler.write_text(SCHEDULER_PATH.read_text())
        self.env.update(CODEX_THREAD_ID=TID, CLAUDE_CODE_SESSION_ID=OTHER,
                        TMUX='daemon',TMUX_PANE='%daemon')
        r=self.shell('/bin/bash '+shlex.quote(str(scheduler))+' --dry-run\n',library=False)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('verified codex session '+TID,r.stdout)
        self.assertNotIn('%daemon',r.stdout)
        self.assertEqual(self.events(),[])


if __name__ == '__main__':
    unittest.main()
