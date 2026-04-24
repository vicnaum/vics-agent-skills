"""TDD tests for persist-range — the dispatcher that persists multiple
kinds across a chain-position range in one command.

Contract:
    persist_range(session, from_pos, to_pos, kinds, ...)
where kinds is a sequence drawn from {"tool", "thinking", "text", "image",
"message"}. Internally fans out to per-kind helpers; honors --keep-recent and
--min-chars; supports --summaries-file mapping ids/positions → summaries.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, iter_persisted_markers, assert_chain_valid


def _long(n: int = 3000) -> str:
    return "x" * n


class TestPersistRangeMixed(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "first"),
            ("assistant", [
                {"type": "text", "text": _long()},
                {"type": "thinking", "thinking": "deep thoughts " * 200},
            ]),
            ("user", "ack"),
            ("assistant", _long()),
            ("user", "tail"),
            ("assistant", "live tail msg"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_dispatches_to_text_and_thinking(self):
        from lib.persist_range import persist_range
        persist_range(self.session_path, from_pos=0, to_pos=4,
                      kinds=("text", "thinking"), min_chars=500)
        kinds_seen = {f["kind"] for *_, f in iter_persisted_markers(self.session_path)}
        self.assertIn("text", kinds_seen)
        self.assertIn("thinking", kinds_seen)

    def test_kinds_filter_excludes_others(self):
        from lib.persist_range import persist_range
        # Only thinking — text blocks should be untouched
        persist_range(self.session_path, from_pos=0, to_pos=4,
                      kinds=("thinking",), min_chars=500)
        kinds_seen = {f["kind"] for *_, f in iter_persisted_markers(self.session_path)}
        self.assertEqual(kinds_seen, {"thinking"})

    def test_range_filter_excludes_outside(self):
        from lib.persist_range import persist_range
        # Range 0-1 only — pos 3 (long text) should not be touched
        persist_range(self.session_path, from_pos=0, to_pos=1,
                      kinds=("text", "thinking"), min_chars=500)
        positions = {pos for pos, *_ in iter_persisted_markers(self.session_path)}
        self.assertTrue(all(p <= 1 for p in positions))

    def test_keep_recent_protects_tail(self):
        from lib.persist_range import persist_range
        persist_range(self.session_path, from_pos=0, to_pos=5,
                      kinds=("text",), min_chars=500, keep_recent=1)
        # With keep_recent=1, the last qualifying text block (pos 3) is
        # skipped — only pos 1 should be persisted. pos 5 is below
        # min_chars and never qualifies.
        marked_positions = [p for p, *_ in iter_persisted_markers(self.session_path)]
        self.assertEqual(marked_positions, [1],
                         f"keep_recent=1 should leave the last qualifying block "
                         f"intact; got {marked_positions}")

    def test_chain_remains_valid(self):
        from lib.persist_range import persist_range
        persist_range(self.session_path, from_pos=0, to_pos=4,
                      kinds=("text", "thinking"), min_chars=500)
        assert_chain_valid(self.session_path)


class TestPersistRangeSummariesFile(unittest.TestCase):
    """The --summaries-file JSON map allows the caller to provide per-block
    summaries without invoking AI inside session-stripper."""

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", _long()),
            ("user", "second"),
            ("assistant", _long()),
            ("user", "tail"),
        ])
        # A summary keyed by chain position.
        self.summaries_file = Path("/tmp") / "ss-test-summaries.json"
        self.summaries_file.write_text(json.dumps({
            "pos:1": "first long reply",
            "pos:3": "second long reply",
        }))

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        self.summaries_file.unlink(missing_ok=True)

    def test_summaries_inserted_into_markers(self):
        from lib.persist_range import persist_range
        persist_range(self.session_path, from_pos=0, to_pos=4,
                      kinds=("text",), min_chars=500,
                      summaries_file=self.summaries_file)
        markers = list(iter_persisted_markers(self.session_path))
        summaries = {f["summary"] for *_, f in markers if f.get("summary")}
        self.assertIn("first long reply", summaries)
        self.assertIn("second long reply", summaries)


if __name__ == "__main__":
    unittest.main()
