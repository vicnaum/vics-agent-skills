"""Claude Code background sessions (`claude --bg`, `←`): hermetic regressions.

A background session's worker runs on a daemon-owned pty, so there is no pane:
identity, state and the inbox come from Claude Code's own records under
~/.claude/sessions/<pid>.json (pid + procStart proven) and ~/.claude/jobs/<id>/.
These tests build those records in a temporary HOME around a real, harmless
process (this test's own `sleep`), so pid/start-time proofs run for real.

Run: python3 -m unittest discover -s skills/agent-chat/tests -v
"""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

from test_terminal_ownership import FixtureCase, TID, OTHER

JOB = 'f7e9d242'


def proc_start_utc(pid):
    out = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True,
                         env={**os.environ, 'LC_ALL': 'C'}).stdout.strip()
    t = time.mktime(time.strptime(out, '%a %b %d %H:%M:%S %Y'))
    return time.strftime('%a %b %d %H:%M:%S %Y', time.gmtime(t))


class BackgroundCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.proc = subprocess.Popen(['sleep', '60'])
        self.addCleanup(self.proc.kill)
        self.pid = self.proc.pid
        (self.home/'.claude/sessions').mkdir(parents=True)
        (self.home/'.claude/jobs'/JOB).mkdir(parents=True)
        (self.home/'.claude/jobs'/JOB/'state.json').write_text(json.dumps(
            {'state': 'working', 'sessionId': TID,
             'respawnFlags': ['--permission-mode', 'bypassPermissions', '--allowed-tools', 'Bash(git *)']}))
        self.record()

    def shell(self, body, library=True):
        # the process proofs are the point here: use the real ps (terminal
        # primitives stay stubbed and fail loudly if anything reaches them)
        return super().shell('unset -f ps\n' + body, library)

    def record(self, **over):
        rec = {'pid': self.pid, 'sessionId': TID, 'kind': 'bg', 'jobId': JOB, 'status': 'idle',
               'messagingSocketPath': f'/tmp/cc-socks/{self.pid}.sock', 'cwd': '/tmp/x y',
               'procStart': proc_start_utc(self.pid)}
        rec.update(over)
        (self.home/'.claude/sessions'/f'{self.pid}.json').write_text(json.dumps(rec))

    def reg(self, backend='none', addr='-'):
        return ('"$JQ" -n --arg sid "$TID" \'{name:"peer",session_id:$sid,cli:"claude",'
                f'term_backend:"{backend}",term_addr:"{addr}",registered_at:"x",rooms:["general"]}}\''
                ' > "$REG/peer.json"\n')


class HolderTest(BackgroundCase):
    def test_live_record_is_the_holder(self):
        r = self.shell('cc_holder_line "$TID"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        pid, birth, kind, job, status, cwd = r.stdout.strip().split(' ', 5)
        self.assertEqual((pid, kind, job, status, cwd), (str(self.pid), 'bg', JOB, 'idle', '/tmp/x y'))

    def test_reused_pid_is_not_the_holder(self):
        self.record(procStart='Thu Jan 01 00:00:00 2026')
        self.assertNotEqual(self.shell('cc_holder "$TID"\n').returncode, 0)

    def test_two_claimants_are_ambiguous(self):
        other = subprocess.Popen(['sleep', '60']); self.addCleanup(other.kill)
        (self.home/'.claude/sessions'/f'{other.pid}.json').write_text(json.dumps(
            {'pid': other.pid, 'sessionId': TID, 'kind': 'interactive', 'procStart': proc_start_utc(other.pid)}))
        self.assertNotEqual(self.shell('cc_holder "$TID"\n').returncode, 0)


class StateTest(BackgroundCase):
    def test_native_status_maps_to_agent_states(self):
        for st, wf, want in (('busy', '', 'busy'), ('shell', '', 'busy'),
                             ('waiting', 'permission', 'waiting'), ('idle', '', 'idle')):
            with self.subTest(status=st):
                self.record(status=st, waitingFor=wf)
                r = self.shell(self.reg() + 'agent_state peer\n')
                self.assertEqual(r.stdout.split('\t')[0], want, r.stdout + r.stderr)
                self.assertIn(f'background job {JOB}', r.stdout)

    def test_background_registration_without_a_worker_is_stopped_not_dead(self):
        self.proc.kill(); self.proc.wait()
        r = self.shell(self.reg('bg', JOB) + 'agent_state peer\n')
        self.assertTrue(r.stdout.startswith('stopped\t'), r.stdout)
        self.assertIn(f'claude attach {JOB}', r.stdout)

    def test_live_background_session_overrides_a_stale_pane(self):
        r = self.shell(self.reg('iterm', 'OLD-PANE') + 'resolve_term peer\n')
        self.assertEqual(r.stdout.strip(), f'bg {JOB}')


class NoTerminalTest(BackgroundCase):
    def test_type_and_key_refuse_without_touching_a_terminal(self):
        for cmd in ('cmd_type peer hello', 'cmd_key peer escape'):
            with self.subTest(cmd=cmd):
                r = self.shell(self.reg() + cmd + '\n')
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('has no terminal to type into', r.stderr)
                self.assertEqual(self.trace.read_text(), '')

    def test_inbox_comes_from_the_holder_not_a_pane(self):
        sock = self.home/'s'; sock.mkdir()
        import socket
        path = str(sock/f'{self.pid}.sock')
        srv = socket.socket(socket.AF_UNIX); srv.bind(path); self.addCleanup(srv.close)
        self.record(messagingSocketPath=path)
        r = self.shell(self.reg() + 'cc_inbox peer\n')
        self.assertEqual(r.stdout.strip(), path, r.stderr)
        self.record(messagingSocketPath=str(sock/'999.sock'))   # not named after the holder
        self.assertNotEqual(self.shell(self.reg() + 'cc_inbox peer\n').returncode, 0)

    def test_whereami_reports_the_job_for_a_background_caller(self):
        r = self.shell(f'ancestry() {{ printf "{self.pid}\\t??\\tclaude bg-spare\\n"; }}\n'
                       'export CLAUDE_CODE_SESSION_ID="$TID"\ncmd_whereami --porcelain\n')
        self.assertEqual(r.stdout.split()[:4], ['claude', TID, 'bg', JOB], r.stdout + r.stderr)

    def test_watch_reports_stopped_and_never_respawns_it(self):
        self.proc.kill(); self.proc.wait()
        r = self.shell(self.reg('bg', JOB) + 'warm_terms() { return 0; }\n'
                       'cmd_respawn() { echo respawned >> "$TRACE"; }\n'
                       'cmd_watch peer --interval 0 --respawn\n')
        self.assertIn('STOPPED', r.stdout)
        self.assertEqual(self.trace.read_text(), '')


class LaunchTest(BackgroundCase):
    def test_launch_cmd_drops_selectors_and_the_kickoff_prompt(self):
        argv = ['/x/versions/2.1.310', '--dangerously-skip-permissions', '-n', 'lead one', '--resume', TID,
                '--allowed-tools', 'Bash(git *)', 'Edit', '--model=opus', "You are (a) FRESH session", '--x']
        r = self.shell(f"launch_cmd 'json:{json.dumps(argv)}' resume\n")
        self.assertEqual(r.stdout.strip(),
                         "claude --dangerously-skip-permissions -n 'lead one' --allowed-tools 'Bash(git *)' Edit --model=opus")
        r = self.shell(f"launch_cmd 'json:{json.dumps(argv)}' spawn\n")
        self.assertNotIn('lead one', r.stdout)

    def test_job_flags_are_shell_quoted(self):
        r = self.shell(f'job_flags {JOB}\n')
        self.assertEqual(r.stdout.strip(), "--permission-mode bypassPermissions --allowed-tools 'Bash(git *)'")

    def test_spawn_bg_passes_kickoff_on_argv_and_reports_job(self):
        r = self.shell('bg_launch() { echo "$2" >> "$TRACE"; echo abcd1234; }\n'
                       'cmd_spawn kid --bg --cmd claude\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('background session abcd1234', r.stdout)
        launched = self.trace.read_text()
        self.assertIn("claude --bg -n 'kid' 'You are a fresh agent.", launched)

    def test_background_respawn_stops_only_the_proven_job_then_starts_fresh(self):
        r = self.shell(self.reg('bg', JOB) + 'cc_bin() { echo fakeclaude; }\n'
                       'fakeclaude() { echo "$*" >> "$TRACE"; }\n'
                       'bg_launch() { echo "LAUNCH $2" >> "$TRACE"; echo newjob1; }\n'
                       'cmd_respawn peer --no-wait --ignore-cooldown --dry-run\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'stop background job {JOB}', r.stdout)
        self.assertIn("--permission-mode bypassPermissions --allowed-tools 'Bash(git *)' --bg -n 'peer'", r.stdout)
        self.assertEqual(self.trace.read_text(), '')      # dry run: nothing stopped or launched
        self.record(status='busy')
        r = self.shell(self.reg('bg', JOB) + 'cmd_respawn peer --no-wait --ignore-cooldown\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('BUSY', r.stderr)



# ---------- respawn watcher: background path and Claude Code's exit dialog ----------

import shlex  # noqa: E402
from test_terminal_ownership import WATCHER_PATH  # noqa: E402

FAKE_BG_AC = r'''#!/usr/bin/env python3
import os, pathlib, sys
sp = pathlib.Path(os.environ['STATE']); state = sp.read_text().strip()
cmd, *args = sys.argv[1:]
def note(s):
    with open(os.environ['TRACE'], 'a') as f: f.write(s + '\n')
if cmd == '_process-token':
    if args[0] == '111' and state in ('busy', 'idle'): print('100'); sys.exit(0)
    if args[0] == '333' and state == 'new': print('200'); sys.exit(0)
    sys.exit(1)
if cmd == '_cc-holder':
    if state == 'busy':
        note('holder busy'); sp.write_text('idle'); print('111 100 bg f7e9d242 busy /tmp/w d'); sys.exit(0)
    if state == 'idle': print('111 100 bg f7e9d242 idle /tmp/w d'); sys.exit(0)
    if state == 'other': print('999 900 bg f7e9d242 idle /tmp/w d'); sys.exit(0)
    if state == 'new': print('333 200 bg f7e9d242 idle /tmp/w d'); sys.exit(0)
    sys.exit(1)
if cmd == '_screen':
    if os.environ.get('MODE') == 'dialog' and state == 'old':
        print('Background work is running\n  shell · sleep 900\n  ❯ 1. Exit and stop tasks\n    2. Move to background and exit'); sys.exit(0)
    sys.exit(0)
sys.exit(1)
'''
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import os, pathlib, sys
sp = pathlib.Path(os.environ['STATE'])
with open(os.environ['TRACE'], 'a') as f: f.write('claude ' + ' '.join(sys.argv[1:]) + '\n')
if sys.argv[1:2] == ['stop']:
    sp.write_text('gone')
    with open(os.path.join(os.environ['HOME'], '.claude/daemon.log'), 'a') as f:
        f.write('[t] [bg] bg settled f7e9d242 (killed)\n')
elif '--bg' in sys.argv:
    sp.write_text(os.environ.get('AFTER_LAUNCH', 'new'))
    print('backgrounded · f7e9d242 · x')
'''


class BgWatcherCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.state = self.home/'state'
        self.bin = self.home/'bin'; self.bin.mkdir()
        (self.home/'.claude').mkdir(); (self.home/'.claude/daemon.log').write_text('')
        self.ac = self.bin/'agent-chat'; self.ac.write_text(FAKE_BG_AC); self.ac.chmod(0o755)
        c = self.bin/'claude'; c.write_text(FAKE_CLAUDE); c.chmod(0o755)
        self.env.update(STATE=str(self.state), PATH=str(self.bin)+':'+self.env['PATH'])

    def watcher(self, state='busy', swap='', stripper='', dry=''):
        self.state.write_text(state)
        args = ['bg', 'f7e9d242', TID, f"cd '{self.home}' && claude --bg --resume {TID}", 'KICK',
                '0', '900', dry, 'claude', swap, stripper, TID, '-', '111', '100', '-', '-', str(self.ac)]
        body = ('sleep() { SECONDS=$((SECONDS+1)); }\nkill() { echo "SIGNAL $*" >> "$TRACE"; }\n'
                'set -- ' + ' '.join(shlex.quote(a) for a in args) + '\nsource ' + shlex.quote(str(WATCHER_PATH)) + '\n')
        return self.shell(body, library=False)

    def lines(self):
        return self.trace.read_text().splitlines()

    def test_waits_for_turn_end_then_stops_swaps_and_resumes_same_job(self):
        pending = self.home/'s.jsonl.pending'; pending.write_text('p')
        stripper = self.home/'stripper.py'
        stripper.write_text('import os,pathlib,sys\n'
                            'assert pathlib.Path(os.environ["STATE"]).read_text().strip()=="gone"\n'
                            'open(os.environ["TRACE"],"a").write("APPLY\\n")\n')
        r = self.watcher(swap=str(pending), stripper=str(stripper))
        self.assertEqual(r.returncode, 0, r.stderr)
        seq = [l for l in self.lines() if not l.startswith('SIGNAL')]
        self.assertEqual(seq[0], 'holder busy')                     # waited while busy
        self.assertEqual(seq[1], 'claude stop f7e9d242')
        self.assertEqual(seq[2], 'APPLY')                           # swap only after the stop
        self.assertEqual(seq[3], f'claude --bg --resume {TID} KICK')
        self.assertNotIn('SIGNAL', self.trace.read_text())
        log = (self.home/'.claude/respawn/respawn.log').read_text()
        self.assertIn('done: resumed session', log)

    def test_changed_holder_is_never_stopped(self):
        r = self.watcher(state='other')
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('claude stop', self.trace.read_text())

    def test_a_copy_instead_of_a_resume_is_reported(self):
        (self.bin/'claude').write_text(FAKE_CLAUDE.replace("print('backgrounded · f7e9d242 · x')",
                                       "print('note: session x is already running in the background, so this started a copy as abcd')"))
        r = self.watcher(state='idle')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('COPY', (self.home/'.claude/respawn/respawn.log').read_text())

    def test_dry_run_touches_nothing(self):
        r = self.watcher(state='idle', dry='dry')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('claude ', self.trace.read_text())


class ExitDialogTest(unittest.TestCase):
    """The interactive watcher confirms "Background work is running" only while
    its selection sits on "Exit and stop tasks"."""

    def test_dialog_detector(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ac = Path(d)/'ac'
            for screen, expect in (('Background work is running\n  ❯ 1. Exit and stop tasks\n', 0),
                                   ('Background work is running\n    1. Exit and stop tasks\n  ❯ 3. Stay\n', 1),
                                   ('nothing here\n', 1)):
                ac.write_text('#!/bin/sh\nprintf %s ' + shlex.quote(screen) + '\n'); ac.chmod(0o755)
                src = WATCHER_PATH.read_text()
                fn = src[src.index('exit_dialog_up() {'):src.index('\n}\n', src.index('exit_dialog_up() {'))+3]
                r = subprocess.run(['/bin/bash', '-c', f'CLI=claude TB=tmux TA=%1 AC={ac}\n{fn}\nexit_dialog_up'])
                self.assertEqual(r.returncode, expect, screen)


if __name__ == '__main__':
    unittest.main()
