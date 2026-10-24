"""Real PTY/process tests on a private tmux server using a tiny fake Codex TUI.
No model is invoked and no existing tmux server or iTerm session is controlled.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
CHAT = ROOT/'skills/agent-chat/scripts/agent-chat'
RESPAWN = ROOT/'skills/respawn/scripts/respawn.sh'
TID = '01a16db1-81c3-7cd1-8f70-f5e8af3ee33a'
OTHER = '01a16db2-1111-7111-8111-111111111111'
C_SOURCE = r'''
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
static void title(const char *id) { printf("\033]0;Fixture | %s\007", id); fflush(stdout); }
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    struct termios raw;
    if (tcgetattr(0, &raw)) return 3;
    cfmakeraw(&raw); tcsetattr(0, TCSANOW, &raw);
    FILE *log = fopen(getenv("DUMMY_LOG"), "a"); if (!log) return 4;
    setvbuf(log, NULL, _IOLBF, 0);
    fprintf(log, "START %ld\n", (long)getpid());
    if (argc > 3) fprintf(log, "PROMPT %s\n", argv[3]);
    title(argv[2]);
    char line[2048]; size_t n=0; unsigned char c;
    while (read(0, &c, 1) == 1) {
        if (c == '\r' || c == '\n') {
            line[n] = 0; fprintf(log, "LINE %s\n", line);
            if (!strncmp(line, "SWITCH ", 7)) title(line+7);
            if (!strcmp(line, "EXIT")) { fclose(log); return 0; }
            n=0;
        } else if (c < 32) fprintf(log, "KEY %u\n", (unsigned)c);
        else if (n < sizeof(line)-1) line[n++]=(char)c;
    }
    fclose(log); return 0;
}
'''


@unittest.skipUnless(shutil.which('tmux') and shutil.which('cc'), 'requires tmux and a C compiler')
class PrivateTmuxTest(unittest.TestCase):
    def test_real_pty_routing_restart_and_dead_recovery(self):
        with tempfile.TemporaryDirectory(prefix='ac-private-tmux-') as td:
            home=Path(td); binary=home/'codex'; source=home/'dummy.c'
            source.write_text(C_SOURCE)
            subprocess.run(['cc','-o',str(binary),str(source)],check=True,capture_output=True)
            real_tmux=shutil.which('tmux'); socket='ac-test-'+str(os.getpid())+'-'+str(time.time_ns())
            env=os.environ.copy()
            for key in ('TMUX','TMUX_PANE','ITERM_SESSION_ID','CLAUDE_CODE_SESSION_ID','OPENCODE_SESSION_ID'):
                env.pop(key,None)
            bindir=home/'bin'; bindir.mkdir()
            # Select only this test's tmux socket. Hide iTerm from the inventory
            # query without changing the ps metadata used for process ownership.
            wrappers={
                'tmux':f'#!/usr/bin/env python3\nimport os,sys\nos.execv({real_tmux!r},[{real_tmux!r},"-L",{socket!r}]+sys.argv[1:])\n',
                'ps':'#!/usr/bin/env python3\nimport os,sys\nif sys.argv[1:]==["-axo","comm="]: sys.exit(0)\nos.execv("/bin/ps",["/bin/ps"]+sys.argv[1:])\n',
                'osascript':'#!/bin/sh\necho "unexpected iTerm access" >&2\nexit 97\n',
            }
            for name,body in wrappers.items():
                p=bindir/name; p.write_text(body); p.chmod(0o755)
            env.update(HOME=td,AGENT_CHAT_HOME=str(home/'chat'),CODEX_THREAD_ID=TID,
                       DUMMY_LOG=str(home/'dummy.log'),PATH=str(bindir)+':'+env['PATH'])
            def tmux(*args):
                return subprocess.run([real_tmux,'-L',socket,*args],env=env,text=True,
                                      capture_output=True,timeout=15,check=True).stdout.strip()
            def command(*args,expected=0):
                r=subprocess.run([str(CHAT),*args],env=env,cwd=home,text=True,capture_output=True,timeout=25)
                self.assertEqual(r.returncode,expected,r.stdout+r.stderr)
                return r.stdout
            def wait_for(predicate,seconds=20):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    if predicate(): return
                    time.sleep(.1)
                self.fail('timed out waiting for isolated dummy TUI')
            def logfile():
                p=home/'dummy.log'; return p.read_text() if p.exists() else ''
            pane=tmux('new-session','-d','-P','-F','#{pane_id}','-s','fixture','/bin/bash --noprofile --norc')
            try:
                launch=shlex.join([str(binary),'resume',TID])
                tmux('send-keys','-t',pane,'-l','--',launch); tmux('send-keys','-t',pane,'Enter')
                wait_for(lambda:'START ' in logfile() and TID in tmux('display','-p','-t',pane,'#{pane_title}'))
                command('register','fixture')
                command('type','fixture','probe direct'); command('key','fixture','enter')
                wait_for(lambda:'LINE probe direct' in logfile())
                # /new-like switch: process argv remains `resume OLD_ID`.
                tmux('send-keys','-t',pane,'-l','--','SWITCH '+OTHER); tmux('send-keys','-t',pane,'Enter')
                wait_for(lambda:OTHER in tmux('display','-p','-t',pane,'#{pane_title}'))
                before=logfile()
                command('key','fixture','ctrl-c',expected=1)
                self.assertEqual(logfile(),before)
                env['CODEX_THREAD_ID']=OTHER
                command('register','fixture')
                dry=subprocess.run([str(RESPAWN),'--dry-run','--cmd',str(binary)],env=env,cwd=home,
                                   text=True,capture_output=True,timeout=25)
                self.assertEqual(dry.returncode,0,dry.stderr)
                self.assertIn('verified codex session '+OTHER,dry.stdout)
                self.assertIn('DRY RUN',command('respawn','fixture','--dry-run','--ignore-cooldown'))
                # Exercise the actual detached watcher and actual TERM, scoped
                # to our dummy pid on the private server, then prove restart.
                restart=subprocess.run([str(RESPAWN),'--grace','0','--cmd',str(binary),
                                        '--prompt',"integration kickoff 'quoted' $literal"],env=env,cwd=home,
                                       text=True,capture_output=True,timeout=25)
                self.assertEqual(restart.returncode,0,restart.stderr)
                log=home/'.claude/respawn/respawn.log'
                wait_for(lambda:log.exists() and ('done: resumed session' in log.read_text() or 'ABORT:' in log.read_text()))
                self.assertIn('done: resumed session',log.read_text())
                self.assertIn("PROMPT integration kickoff 'quoted' $literal",logfile())
                command('register','fixture')
                replacement=command('respawn','fixture','--ignore-cooldown','--no-wait',
                                    '--cmd',shlex.join([str(binary),'resume',TID]))
                self.assertIn('Respawned',replacement)
                env['CODEX_THREAD_ID']=TID
                wait_for(lambda:TID in tmux('display','-p','-t',pane,'#{pane_title}'))
                command('register','fixture')
                tmux('send-keys','-t',pane,'-l','--','EXIT'); tmux('send-keys','-t',pane,'Enter')
                wait_for(lambda:'dead' in command('status','fixture'))
                self.assertIn('DRY RUN',command('respawn','fixture','--dry-run','--ignore-cooldown'))
            finally:
                subprocess.run([real_tmux,'-L',socket,'kill-server'],env=env,capture_output=True,timeout=10)


if __name__ == '__main__': unittest.main()
