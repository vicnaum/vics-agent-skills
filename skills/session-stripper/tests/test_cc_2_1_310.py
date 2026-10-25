"""Regressions for transcript features of Claude Code 2.1.27x–2.1.310.

- CC's fast transcript scanner matches raw bytes (`{"parentUuid":`), so a
  file written with spaced JSON looked empty to the background-session
  dispatcher: it started a fresh conversation and moved the stripped file
  aside as `<id>.orphaned-*.jsonl`. Every writer must emit compact JSON.
- Attachments store what CC sent (`rendered`); that is the cost.
- Hook-injected context (agent-chat peer mail) must survive strip-attachments.
- prompt_snapshot is the system prompt + tools: not a message, counted once.
- last-prompt.leafUuid is the resume leaf pointer: never left dangling.
- Forks must not carry the original's session-bound records.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import build_session  # noqa: E402
from test_strip_attachments import build_session_with_attachments  # noqa: E402

from lib.attachment_cost import envelope_rendered_chars, policy_for, KEEP_ALL  # noqa: E402
from lib.chain import (compute_active_chain_tokens, dump_line, load_session,  # noqa: E402
                       remove_objects_and_rewire, save_session)
from lib.fork import fork_session  # noqa: E402
from lib.strip_attachments import strip_attachments  # noqa: E402


def _rewrite(path, mutate):
    objs = load_session(path)
    mutate(objs)
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


class CompactJsonTest(unittest.TestCase):
    def test_saved_lines_match_cc_byte_patterns_and_are_private(self):
        path, _ = build_session([("user", "a: b, c"), ("assistant", "ok")])
        save_session(path, load_session(path), create_backup=False)
        for line in path.read_text().splitlines():
            self.assertTrue(line.startswith('{"parentUuid":'), line[:40])
            self.assertIn('"type":"', line)
            self.assertNotIn('", "', line.replace('a: b, c', ''))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.loads(dump_line({"k": "é"})), {"k": "é"})

    def test_fork_writes_compact_json(self):
        path, _ = build_session([("user", "hi"), ("assistant", "ok")])
        dst = fork_session(path)
        self.addCleanup(dst.unlink)
        self.assertNotIn('": "', dst.read_text())


class RenderedCostTest(unittest.TestCase):
    def test_stored_rendered_is_the_cost(self):
        env = {"type": "attachment",
               "attachment": {"type": "hook_additional_context", "hookEvent": "PostToolUse",
                              "content": ["x"]},
               "rendered": [{"content": "<system-reminder>\nmail\n</system-reminder>"}]}
        self.assertEqual(envelope_rendered_chars(env), len(env["rendered"][0]["content"]))
        env["rendered"] = [{"content": [{"type": "text", "text": "abc"}]}]
        self.assertEqual(envelope_rendered_chars(env), 3)

    def test_prompt_snapshot_is_not_a_message_but_counts_once(self):
        snap = {"type": "prompt_snapshot", "systemPrompt": ["S" * 4000], "tools": []}
        old = {"type": "prompt_snapshot", "systemPrompt": ["O" * 40000], "tools": []}
        base, _ = build_session([("user", "hi"), ("assistant", "ok")])
        with_snap = build_session_with_attachments(
            [("attachment", old), ("user", "hi"), ("attachment", snap), ("assistant", "ok")])
        self.assertEqual(envelope_rendered_chars({"type": "attachment", "attachment": snap}), 0)
        extra = (compute_active_chain_tokens(load_session(with_snap))
                 - compute_active_chain_tokens(load_session(base)))
        self.assertAlmostEqual(extra, 1000, delta=20)   # the latest snapshot only


class HookMailPolicyTest(unittest.TestCase):
    def test_peer_mail_survives_default_strip(self):
        mail = {"type": "hook_additional_context", "hookEvent": "PostToolUse",
                "content": ["📨 agent-chat: message"]}
        items = []
        for i in range(5):
            items += [("user", f"q{i}"), ("attachment", dict(mail)), ("assistant", f"a{i}")]
        path = build_session_with_attachments(items)
        _rewrite(path, lambda objs: [o.update(rendered=[{"content": "<system-reminder>mail</system-reminder>"}])
                                     for o in objs if o.get("type") == "attachment"])
        for t in ("hook_additional_context", "hook_blocking_error",
                  "prompt_snapshot", "deferred_tools_record"):
            self.assertEqual(policy_for(t), KEEP_ALL, t)
        strip_attachments(path, no_backup=True, include_free=True)
        kept = [o for o in load_session(path) if o.get("type") == "attachment"]
        self.assertEqual(len(kept), 5)


class LeafPointerTest(unittest.TestCase):
    def test_last_prompt_follows_removed_leaf_to_surviving_ancestor(self):
        path = build_session_with_attachments(
            [("user", "q"), ("assistant", "a"), ("attachment", {"type": "hook_success"})])
        objs = load_session(path)
        att, ans = objs[-1]["uuid"], objs[-2]["uuid"]
        objs.append({"type": "last-prompt", "leafUuid": att, "sessionId": "s"})
        survivors, _, _, _ = remove_objects_and_rewire(objs, {att}, protect=set())
        lp = [o for o in survivors if o.get("type") == "last-prompt"][0]
        self.assertEqual(lp["leafUuid"], ans)


class ForkSessionBoundTest(unittest.TestCase):
    def test_fork_drops_records_bound_to_the_original(self):
        path, _ = build_session([("user", "hi"), ("assistant", "ok")])
        with open(path, "a") as f:
            for t in ("bridge-session", "continued-in", "cost-state", "api-request",
                      "api-request-blob", "api-request-shape", "ai-title", "file-history-snapshot"):
                f.write(json.dumps({"type": t, "sessionId": "orig"}) + "\n")
        dst = fork_session(path)
        self.addCleanup(dst.unlink)
        types = [json.loads(line)["type"] for line in dst.read_text().splitlines()]
        for gone in ("bridge-session", "continued-in", "cost-state", "api-request",
                     "api-request-blob", "api-request-shape"):
            self.assertNotIn(gone, types)
        for kept in ("user", "assistant", "ai-title", "file-history-snapshot", "custom-title"):
            self.assertIn(kept, types)


if __name__ == "__main__":
    unittest.main()
