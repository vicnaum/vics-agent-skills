"""REGRESSION: `strip-thinking` must not report off-chain blocks as handled.

The command only targets uuids on the active chain, which is correct — off-chain
thinking is unreachable from the leaf, costs no context, and deleting it would
only destroy history. But the report said "312 removed" while 71 blocks
(248,283 chars) sat untouched, which reads as "the session is now clean".

On-chain and off-chain are now counted and printed separately.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.chain import load_session, walk_active_chain
from lib.strip_thinking import strip_thinking

OFFCHAIN_THINKING = "abandoned " * 100


def _session_with_offchain_branch():
    """A linear chain plus one abandoned branch (the shape an edit/rewind
    leaves). The branch record is inserted mid-file so the leaf is unchanged."""
    path, info = build_session([
        ("user", "hi"),
        ("assistant", [{"type": "thinking", "thinking": "on-chain " * 100},
                       {"type": "text", "text": "hello"}]),
        ("user", "more"),
        ("assistant", "sure"),
    ])
    objects = load_session(path)
    branch = {
        "parentUuid": info["uuids"][0], "isSidechain": False,
        "userType": "external", "cwd": info["cwd"],
        "sessionId": info["session_id"], "version": "2.1.114",
        "gitBranch": "master", "slug": info["slug"], "type": "assistant",
        "message": {"role": "assistant", "model": "claude-opus-4-6",
                    "content": [{"type": "thinking", "thinking": OFFCHAIN_THINKING},
                                {"type": "text", "text": "discarded reply"}]},
        "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "timestamp": "2026-01-01T00:00:01.500Z",
    }
    objects.insert(2, branch)
    with open(path, "w", encoding="utf-8") as f:
        for o in objects:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return path, info, branch["uuid"]


class TestOffChainThinkingAccounting(unittest.TestCase):
    def test_offchain_blocks_are_counted_not_silently_skipped(self):
        path, _, branch_uuid = _session_with_offchain_branch()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                stats = strip_thinking(str(path), no_backup=True)
            out = buf.getvalue()

            self.assertEqual(1, stats["thinking_cleared"], "one on-chain block")
            self.assertEqual(1, stats["offchain_thinking_skipped"])
            self.assertEqual(len(OFFCHAIN_THINKING), stats["offchain_chars_skipped"])
            self.assertIn("on-chain", out)
            self.assertIn("left off-chain", out)
        finally:
            path.unlink(missing_ok=True)

    def test_offchain_content_is_left_intact(self):
        """Counting it must not turn into deleting it."""
        path, _, branch_uuid = _session_with_offchain_branch()
        try:
            strip_thinking(str(path), no_backup=True)
            objects = load_session(path)
            branch = next(o for o in objects if o.get("uuid") == branch_uuid)
            self.assertEqual(OFFCHAIN_THINKING,
                             branch["message"]["content"][0]["thinking"])
        finally:
            path.unlink(missing_ok=True)

    def test_onchain_thinking_still_removed(self):
        path, _, _ = _session_with_offchain_branch()
        try:
            strip_thinking(str(path), no_backup=True)
            objects = load_session(path)
            chain = walk_active_chain(objects)
            for obj in chain:
                content = obj.get("message", {}).get("content")
                if isinstance(content, list):
                    self.assertNotIn(
                        "thinking", [b.get("type") for b in content if isinstance(b, dict)])
        finally:
            path.unlink(missing_ok=True)

    def test_already_emptied_offchain_blocks_read_sensibly(self):
        """Real sessions carry signature-only thinking blocks whose text is
        already "". Reporting "N (0 chars)" reads like a miscount."""
        path, info = build_session([("user", "hi"), ("assistant", "hello")])
        try:
            objects = load_session(path)
            objects.insert(1, {
                "parentUuid": info["uuids"][0], "isSidechain": False,
                "userType": "external", "cwd": info["cwd"],
                "sessionId": info["session_id"], "version": "2.1.114",
                "gitBranch": "master", "slug": info["slug"], "type": "assistant",
                "message": {"role": "assistant", "model": "claude-opus-4-6",
                            "content": [{"type": "thinking", "thinking": "",
                                         "signature": "abc123"}]},
                "uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                "timestamp": "2026-01-01T00:00:01.500Z",
            })
            with open(path, "w", encoding="utf-8") as f:
                for o in objects:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                stats = strip_thinking(str(path), no_backup=True)
            out = buf.getvalue()

            self.assertEqual(1, stats["offchain_thinking_skipped"])
            self.assertEqual(0, stats["offchain_chars_skipped"])
            self.assertIn("already emptied", out)
            self.assertNotIn("(0 chars)", out)
        finally:
            path.unlink(missing_ok=True)

    def test_session_with_no_offchain_reports_nothing_extra(self):
        path, _ = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "thinking", "thinking": "x " * 50},
                           {"type": "text", "text": "hello"}]),
        ])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                stats = strip_thinking(str(path), no_backup=True)
            self.assertEqual(0, stats["offchain_thinking_skipped"])
            self.assertNotIn("left off-chain", buf.getvalue())
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
