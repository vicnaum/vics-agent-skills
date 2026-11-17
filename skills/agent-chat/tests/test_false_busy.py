"""False busy: a Claude Code session whose record says "shell" (or busy) only
because a BACKGROUND shell (run_in_background) runs, while it sits idle at its
prompt. Hermetic: fake session records around a real `sleep`, fake screens
through a stubbed term_screen; nothing reaches a real terminal or agent.

Run: python3 -m unittest discover -s skills/agent-chat/tests -v
"""
import time
import unittest

from test_background_sessions import BackgroundCase

# real Claude Code 2.1 screens (trimmed): idle prompt + '· 1 shell' footer
IDLE_BG_SHELL = '''  Either way, the timings stand: about 6:00 for 58 g, ±3 s per gram.

✻ Baked for 1m 4s · done 9:34 PM · 1 shell still running

──────────────────────────────────────────────────────────── Perfect Eggs ─
❯
───────────────────────────────────────────────────────────────────────────
  Opus 5.5 (medium) [9% ×1.02]     56%      2a17e27f-5444  ~/github/eggs/work
  ⏵⏵ bypass permissions on · ← 4 agents · 1 shell · ⧉ 2
'''
# working: spinner above the prompt, 'esc to interrupt' in the footer
WORKING = '''  ⎿  $ uv run -q --with pillow python -I -c "..."

· Percolating… (8m 40s · ↓ 18.7k tokens)

───────────────────────────────────────────────────────────────────────────
❯
───────────────────────────────────────────────────────────────────────────
  Opus 5.5 (medium) [42% ×1.14]     38%      2676bee0-e66f-49ba-b0ad-da3f79c…
  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · 1 shell · ← 4 age…
'''
# idle, no background shell in the footer (only the transcript mentions one)
IDLE_NO_FOOTER = IDLE_BG_SHELL.replace(' · 1 shell · ⧉ 2', ' · ⧉ 2')


class FalseBusyCase(BackgroundCase):
    def setUp(self):
        super().setUp()
        self.record(kind='interactive', jobId=None, status='shell')

    def peer(self, screen):
        return (self.reg('tmux', '%5') +
                "term_screen() { cat <<'SCREEN'\n" + screen + "SCREEN\n}\n")


class ClassifyTest(FalseBusyCase):
    def state(self, screen, **rec):
        if rec:
            self.record(kind='interactive', jobId=None, **rec)
        r = self.shell(self.peer(screen) + 'agent_state peer\n')
        return r.stdout.split('\t')

    def test_busy_with_spinner_stays_busy(self):
        self.assertEqual(self.state(WORKING)[0], 'busy')
        self.assertEqual(self.state(WORKING, status='busy')[0], 'busy')

    def test_shell_status_at_idle_prompt_with_shell_footer_is_idle_bg_shell(self):
        st, why = self.state(IDLE_BG_SHELL)
        self.assertEqual(st, 'idle')
        self.assertTrue(why.startswith('bg shell'), why)
        self.assertIn('1 shell', why)
        r = self.shell(self.peer(IDLE_BG_SHELL) + 'cmd_status peer\n')
        self.assertRegex(r.stdout, r'^peer +idle  \(bg shell')

    def test_without_shell_footer_or_prompt_it_stays_busy(self):
        self.assertEqual(self.state(IDLE_NO_FOOTER)[0], 'busy')      # '1 shell' above the prompt only
        self.assertEqual(self.state('')[0], 'busy')                  # no screen at all
        self.assertEqual(self.state(IDLE_BG_SHELL.replace('❯', '>'))[0], 'busy')

    def test_background_session_is_never_reclassified(self):
        self.record(status='shell')   # kind bg: no screen to look at
        r = self.shell(self.peer(IDLE_BG_SHELL) + 'agent_state peer\n')
        self.assertEqual(r.stdout.split('\t')[0], 'busy')


class WatchTest(FalseBusyCase):
    def watch(self, screen, args, pre=''):
        return self.shell(self.peer(screen) + 'warm_terms() { return 0; }\nmkdir -p "$AGENT_CHAT_HOME/watch" "$AGENT_CHAT_HOME/status"\n'
                          + pre + f'cmd_watch peer --interval 0 {args}\n')

    def test_idle_alert_fires_from_the_stop_stamp(self):
        old = int(time.time()) - 3600
        r = self.watch(IDLE_BG_SHELL, '--idle-alert 30', f'echo "idle {old}" > "$AGENT_CHAT_HOME/status/peer"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('IDLE for 60m (background shell running', r.stdout)
        self.assertIn('Re-arm', r.stdout)

    def test_idle_alert_fires_from_first_seen_without_a_stamp(self):
        old = int(time.time()) - 1800
        r = self.watch(IDLE_BG_SHELL, '--idle-alert 10',
                       f'echo "busy {old}" > "$AGENT_CHAT_HOME/status/peer"\n'
                       f'echo {old} > "$AGENT_CHAT_HOME/watch/peer.bgshell-since"\n')
        self.assertIn('IDLE for 30m (background shell running', r.stdout)

    def test_busy_alert_reports_a_long_busy_spell(self):
        old = int(time.time()) - 1200
        r = self.watch(WORKING, '--busy-alert 15', f'echo {old} > "$AGENT_CHAT_HOME/watch/peer.busy-since"\n')
        self.assertIn('peer: BUSY for 20m', r.stdout)


class SendTest(FalseBusyCase):
    def send(self, screen):
        body = (self.peer(screen) +
                '"$JQ" -n \'{name:"me",session_id:"x",cli:"claude",home_room:"general",rooms:["general"]}\' > "$REG/me.json"\n'
                'AS_NAME=me\nnudge_request() { echo "WAKE $1 $2" >> "$TRACE"; }\n'
                'cmd_send "hello" --to peer\n')
        return self.shell(body)

    def test_send_wakes_an_idle_prompt_with_a_background_shell(self):
        r = self.send(IDLE_BG_SHELL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('idle at its prompt (background shell running)', r.stdout)
        self.assertEqual(self.trace.read_text().strip(), 'WAKE peer me')

    def test_send_still_defers_to_a_working_agent(self):
        r = self.send(WORKING)
        self.assertIn('peer is busy', r.stdout)
        self.assertEqual(self.trace.read_text(), '')


if __name__ == '__main__':
    unittest.main()
