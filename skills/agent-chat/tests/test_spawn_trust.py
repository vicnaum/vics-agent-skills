"""spawn: folder pre-trust, trust-dialog answering and pane placement.

Run: python3 -m unittest discover -s skills/agent-chat/tests -v
Hermetic: isolated HOME, every terminal primitive is a stub that records into
TRACE. Nothing reaches iTerm, tmux or a real CLI.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CHAT = (ROOT / 'skills/agent-chat/scripts/agent-chat').read_text().split('# ---------- main ----------')[0]
SETUP = r'''
tmux() { echo 'UNEXPECTED tmux' >> "$TRACE"; return 97; }
osascript() { echo 'UNEXPECTED osascript' >> "$TRACE"; return 97; }
ps() { return 1; }
kill() { echo 'UNEXPECTED kill' >> "$TRACE"; return 97; }
sleep() { :; }
term_list() { return 1; }
'''
NEW = '11111111-2222-3333-4444-555555555555'
OLD = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
PEER = '99999999-8888-7777-6666-000000000000'
# A fake iTerm: snapshot lists OLD and PEER, a split returns NEW and logs its direction.
FAKE_ITERM = r'''
osascript() {
  local script; script=$(cat)
  case "$script" in
    *"split horizontally"*) echo "split $3 $2" >> "$TRACE"; echo "${FAKE_NEW:-%NEW%}";;
    *"set out to"*) printf '%s\n%s\n' "%OLD%" "%PEER%";;
    *) echo "UNEXPECTED osascript" >> "$TRACE"; return 97;;
  esac
}
agent_tty() { echo /dev/mock; }
tty_has_cli() { return 0; }
tty_fg_shell() { return 1; }
term_type() { echo "type $2 $3" >> "$TRACE"; }
term_enter() { echo "enter $2" >> "$TRACE"; }
term_key() { echo "key $2 $3" >> "$TRACE"; }
term_screen() { echo 'You are a fresh agent'; }
'''.replace('%NEW%', NEW).replace('%OLD%', OLD).replace('%PEER%', PEER)


class SpawnCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-chat-spawn-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.work = self.home / 'work'
        self.work.mkdir()
        self.env = os.environ.copy()
        for key in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'OPENCODE_SESSION_ID',
                    'TMUX', 'TMUX_PANE', 'ITERM_SESSION_ID', 'CODEX_HOME'):
            self.env.pop(key, None)
        self.trace = self.home / 'trace'
        self.trace.touch()
        self.env.update(HOME=str(self.home), AGENT_CHAT_HOME=str(self.home / 'chat'),
                        TRACE=str(self.trace), WORK=str(self.work))

    def shell(self, body):
        r = subprocess.run(['/bin/bash'], input=CHAT + SETUP + body, text=True,
                           capture_output=True, env=self.env, cwd=ROOT, timeout=15)
        self.assertNotIn('UNEXPECTED', self.trace.read_text(), r.stderr)
        return r

    def phys(self):
        return os.path.realpath(self.work)


class TrustClaude(SpawnCase):
    def cfg(self):
        return self.home / '.claude.json'

    def test_creates_missing_config(self):
        r = self.shell('trust_dir claude "$WORK"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.cfg().read_text())
        self.assertIs(data['projects'][self.phys()]['hasTrustDialogAccepted'], True)
        self.assertEqual(self.cfg().stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.home / '.claude.json.bak-agentchat').exists())

    def test_keeps_other_keys_and_backs_up_once(self):
        orig = {'numStartups': 7, 'projects': {self.phys(): {'allowedTools': ['x'], 'hasTrustDialogAccepted': False},
                                               '/other': {'hasTrustDialogAccepted': True}}}
        self.cfg().write_text(json.dumps(orig))
        r = self.shell('trust_dir claude "$WORK"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.cfg().read_text())
        self.assertEqual(data['numStartups'], 7)
        self.assertEqual(data['projects']['/other'], {'hasTrustDialogAccepted': True})
        self.assertEqual(data['projects'][self.phys()], {'allowedTools': ['x'], 'hasTrustDialogAccepted': True})
        bak = self.home / '.claude.json.bak-agentchat'
        self.assertEqual(json.loads(bak.read_text()), orig)
        # a second directory must not overwrite the one-time backup
        other = self.home / 'other'; other.mkdir()
        r = self.shell(f'trust_dir claude "{other}"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(bak.read_text()), orig)
        self.assertIs(json.loads(self.cfg().read_text())['projects'][os.path.realpath(other)]['hasTrustDialogAccepted'], True)

    def test_already_trusted_is_untouched(self):
        text = json.dumps({'projects': {self.phys(): {'hasTrustDialogAccepted': True}}}, indent=7)
        self.cfg().write_text(text)
        r = self.shell('trust_dir claude "$WORK"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cfg().read_text(), text)
        self.assertFalse((self.home / '.claude.json.bak-agentchat').exists())

    def test_invalid_json_is_left_alone(self):
        self.cfg().write_text('{not json')
        r = self.shell('trust_dir claude "$WORK"\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.cfg().read_text(), '{not json')


class TrustCodex(SpawnCase):
    def cfg(self):
        return self.home / '.codex/config.toml'

    def hdr(self, d=None):
        return f'[projects."{d or self.phys()}"]'

    def test_appends_table_when_absent_and_is_idempotent(self):
        self.cfg().parent.mkdir()
        self.cfg().write_text('model = "gpt-5"\n[projects."/x"]\ntrust_level = "trusted"')  # no trailing newline
        for _ in range(2):
            r = self.shell('trust_dir codex "$WORK"\n')
            self.assertEqual(r.returncode, 0, r.stderr)
        text = self.cfg().read_text()
        self.assertEqual(text.count(self.hdr()), 1)
        self.assertIn(f'trust_level = "trusted"\n\n{self.hdr()}\ntrust_level = "trusted"\n', text)
        self.assertTrue(text.startswith('model = "gpt-5"\n'))
        self.assertTrue((self.home / '.codex/config.toml.bak-agentchat').exists())

    def test_creates_missing_config(self):
        r = self.shell('trust_dir codex "$WORK"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cfg().read_text(), f'{self.hdr()}\ntrust_level = "trusted"\n')

    def test_adds_trust_level_to_existing_table(self):
        self.cfg().parent.mkdir()
        self.cfg().write_text(f'{self.hdr()}\nfoo = 1\n\n[other]\ntrust_level = "untrusted"\n')
        r = self.shell('trust_dir codex "$WORK"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cfg().read_text(),
                         f'{self.hdr()}\ntrust_level = "trusted"\nfoo = 1\n\n[other]\ntrust_level = "untrusted"\n')

    def test_explicit_untrusted_is_respected(self):
        self.cfg().parent.mkdir()
        text = f'{self.hdr()}\ntrust_level = "untrusted"\n'
        self.cfg().write_text(text)
        r = self.shell('trust_dir codex "$WORK"\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.cfg().read_text(), text)

    def test_git_root_is_trusted_too(self):
        sub = self.work / 'sub'; sub.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.work)], check=True)
        r = self.shell(f'trust_dir codex "{sub}"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.cfg().read_text()
        self.assertIn(self.hdr(os.path.realpath(sub)), text)
        self.assertIn(self.hdr(), text)


class TrustGate(SpawnCase):
    MENU = r'''
term_screen() {
  local c; c=$(cat "$HOME/cur" 2>/dev/null || echo 2)
  echo '❯ claude "You are a fresh agent"'
  echo ' Do you trust the files in this folder?'
  if [ "$c" = 1 ]; then echo ' ❯ 1. No, exit'; echo '   2. Yes, I trust this folder'
  else echo '   1. No, exit'; echo ' ❯ 2. Yes, I trust this folder'; fi
}
term_key() { echo "key $3" >> "$TRACE"; case "$3" in down) echo 2 > "$HOME/cur";; up) echo 1 > "$HOME/cur";; esac; }
term_enter() { echo enter >> "$TRACE"; }
'''

    def test_moves_cursor_to_yes_before_enter(self):
        r = self.shell(self.MENU + 'echo 1 > "$HOME/cur"; accept_trust_gate tmux %9\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.trace.read_text().split('\n')[:2], ['key down', 'enter'])

    def test_enter_only_when_yes_already_selected(self):
        r = self.shell(self.MENU + 'accept_trust_gate tmux %9\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.trace.read_text(), 'enter\n')

    def test_unreadable_menu_sends_nothing(self):
        r = self.shell('''term_screen() { echo 'Do you trust this folder?'; echo 'Press Enter'; }
term_key() { echo "key $3" >> "$TRACE"; }
term_enter() { echo enter >> "$TRACE"; }
accept_trust_gate tmux %9
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_spawn_never_types_at_a_shell_prompt(self):
        r = self.shell(r'''
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; *) echo "tmux $*" >> "$TRACE";; esac; }
term_type() { echo "type $3" >> "$TRACE"; }
term_enter() { echo enter >> "$TRACE"; }
term_key() { echo "key $3" >> "$TRACE"; }
term_screen() { echo 'Do you trust this folder? ❯ 1. No, exit'; echo 'vic@mac work %'; }
tty_has_cli() { return 1; }
tty_fg_shell() { return 0; }
cmd_spawn child --tmux --cmd claude --no-trust --dir "$WORK"
''')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not running', r.stderr)
        lines = self.trace.read_text().splitlines()
        self.assertEqual(len(lines), 2, lines)          # only the launch line and its Enter
        self.assertTrue(lines[0].startswith('type claude '))
        self.assertEqual(lines[1], 'enter')

    def test_spawn_answers_trust_gate_with_yes_not_default(self):
        r = self.shell(self.MENU + r'''
echo 1 > "$HOME/cur"
term_screen() {
  if [ -e "$HOME/accepted" ]; then echo 'You are a fresh agent'
  else local c; c=$(cat "$HOME/cur"); echo '❯ claude "You are a fresh agent"'
    # Claude Code 2.1 shape: indexes hidden, "No, exit" listed and focused first
    echo ' Quick safety check: Is this a project you created or one you trust?'
    if [ "$c" = 1 ]; then echo ' ❯ No, exit'; echo '   Yes, I trust this folder'
    else echo '   No, exit'; echo ' ❯ Yes, I trust this folder'; fi; fi
}
term_enter() { echo enter >> "$TRACE"; [ "$(cat "$HOME/cur" 2>/dev/null)" = 2 ] && touch "$HOME/accepted"; return 0; }
term_type() { echo type >> "$TRACE"; }
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; esac; }
tty_has_cli() { return 0; }
cmd_spawn child --tmux --cmd claude --no-trust --dir "$WORK"
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Kickoff delivered', r.stdout)
        # launch line + Enter, then Down onto "Yes" BEFORE the dialog's Enter
        self.assertEqual(self.trace.read_text().splitlines(), ['type', 'enter', 'key down', 'enter'])

    def test_prompt_mentioning_trust_is_not_a_dialog(self):
        r = self.shell(r'''
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; esac; }
term_type() { echo type >> "$TRACE"; }; term_enter() { echo enter >> "$TRACE"; }
term_key() { echo "key $3" >> "$TRACE"; }
term_screen() { echo 'You are a fresh agent. Your task: check "do you trust this folder" wording'; }
tty_has_cli() { return 0; }
cmd_spawn child --tmux --cmd claude --no-trust --dir "$WORK" --prompt 'check "do you trust this folder" wording'
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Kickoff delivered', r.stdout)
        self.assertEqual(self.trace.read_text().splitlines(), ['type', 'enter'])

    def test_unrecognised_launch_command_still_launches(self):
        r = self.shell(r'''
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; esac; }
term_type() { echo "type $3" >> "$TRACE"; }; term_enter() { :; }
term_screen() { echo 'You are a fresh agent'; }
tty_has_cli() { return 0; }
cmd_spawn child --tmux --cmd somewrapper --dir "$WORK"
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.trace.read_text().startswith('type somewrapper\n'))
        self.assertFalse((self.home / '.claude.json').exists())

    def test_spawn_pretrusts_before_launch(self):
        r = self.shell(r'''
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; esac; }
term_type() { "$JQ" -r --arg d "$(cd "$WORK" && pwd -P)" '.projects[$d].hasTrustDialogAccepted' "$HOME/.claude.json" >> "$TRACE"; }
term_enter() { :; }
term_screen() { echo 'You are a fresh agent'; }
tty_has_cli() { return 0; }
cmd_spawn child --tmux --cmd claude --dir "$WORK"
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.trace.read_text(), 'true\n')

    def test_no_trust_flag_skips_config(self):
        r = self.shell(r'''
tmux() { case "$1" in new-session) echo %5;; display) echo /dev/mock;; esac; }
term_type() { :; }; term_enter() { :; }
term_screen() { echo 'You are a fresh agent'; }
tty_has_cli() { return 0; }
cmd_spawn child --tmux --cmd claude --dir "$WORK" --no-trust
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / '.claude.json').exists())


class Placement(SpawnCase):
    TMUX = r'''
self_identity() { echo "claude sid"; }
detect_term() { echo 'tmux %caller title'; }
term_type() { :; }; term_enter() { :; }
term_screen() { echo 'You are a fresh agent'; }
tty_has_cli() { return 0; }
tmux() { echo "$*" >> "$TRACE"; case "$1" in split-window) echo %new;; display) echo /dev/mock;; esac; }
'''

    def test_tmux_pane_is_side_by_side(self):
        r = self.shell(self.TMUX + 'cmd_spawn child --tmux --pane --cmd codex --no-trust\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('split-window -h -P -F #{pane_id} -t %caller', self.trace.read_text())

    def test_tmux_below_stacks(self):
        r = self.shell(self.TMUX + 'cmd_spawn child --tmux --below --cmd codex --no-trust\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('split-window -v -P -F #{pane_id} -t %caller', self.trace.read_text())

    def test_tmux_of_pane_id_splits_that_pane(self):
        r = self.shell(self.TMUX + 'self_identity() { echo UNEXPECTED >> "$TRACE"; }\n'
                       'cmd_spawn child --tmux --below --of %42 --cmd codex --no-trust\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('split-window -v -P -F #{pane_id} -t %42', self.trace.read_text())

    def test_iterm_below_of_agent_splits_its_pane(self):
        r = self.shell(FAKE_ITERM + f'''
mkdir -p "$REG"; echo '{{"name":"peer","cli":"claude"}}' > "$REG/peer.json"
agent_term() {{ echo "iterm {PEER}"; }}
cmd_spawn child --below --of peer --cmd codex --no-trust
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.trace.read_text()
        self.assertIn(f'split horizontal {PEER}', t)
        self.assertIn(f'type {NEW} cd ', t)
        self.assertNotIn(f'type {PEER}', t)

    def test_iterm_raw_session_id_with_prefix(self):
        r = self.shell(FAKE_ITERM + f'cmd_spawn child --below --of w0t1p2:{PEER} --cmd codex --no-trust\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'split horizontal {PEER}', self.trace.read_text())

    def test_iterm_unknown_session_creates_nothing(self):
        r = self.shell(FAKE_ITERM + f'cmd_spawn child --below --of {NEW} --cmd codex --no-trust\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.trace.read_text(), '')

    def test_iterm_split_returning_existing_session_is_refused(self):
        r = self.shell(FAKE_ITERM + f'FAKE_NEW={OLD}\ncmd_spawn child --below --of {PEER} --cmd codex --no-trust\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SAFETY ABORT', r.stderr)
        self.assertNotIn('type', self.trace.read_text())

    def test_of_rejects_tab_and_bg(self):
        for flag in ('--tab', '--bg'):
            with self.subTest(flag=flag):
                r = self.shell(f'cmd_spawn child {flag} --of peer --cmd claude\n')
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(self.trace.read_text(), '')

    def test_of_background_agent_is_refused(self):
        r = self.shell('mkdir -p "$REG"; echo "{}" > "$REG/peer.json"\n'
                       'agent_term() { echo "bg abc123"; }\n'
                       'cmd_spawn child --of peer --cmd claude\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('background session', r.stderr)


if __name__ == '__main__':
    unittest.main()
