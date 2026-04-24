"""TDD tests for `compact-range` — collapse N consecutive messages to one
survivor carrying a `<persisted-range>` summary marker.

Contract:
- N → 1: only the survivor (range start) remains in the chain; N-1 messages
  removed; child of range end is rewired to point at survivor.
- Originals saved one-per-message to <sessionId>/persisted/message/<uuid>.json
  (same convention as persist-message).
- Survivor carries: {type: text, text: "<persisted-range from=A to=B count=N>
  Saved to: <dir> (count files)\nSummary: ...\n\nPreview:\n[role: ...] excerpt
  </persisted-range>"}.
- Defaults summary to "[range collapsed]" if none provided.
- Refuses if range includes the leaf (would orphan resume cursor).
- Refuses if range contains a tool_use whose tool_result lives outside the range.
- Idempotent: a survivor that already wraps a <persisted-range> is skipped on re-run.

Tests fail until lib.compact_range exists.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import (
    build_session,
    iter_persisted_markers,
    assert_chain_valid,
    resolve_persisted_path,
)


def _envelopes(p: Path):
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _conversation(p: Path):
    return [e for e in _envelopes(p) if e.get("type") in ("user", "assistant")]


class TestCompactRangeBasic(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "first"),         # pos 0
            ("assistant", "reply 1"),  # pos 1
            ("user", "msg 2"),         # pos 2  ← range start
            ("assistant", "reply 2"),  # pos 3
            ("user", "msg 3"),         # pos 4
            ("assistant", "reply 3"),  # pos 5  ← range end
            ("user", "tail"),          # pos 6
            ("assistant", "tail-reply"),  # pos 7  ← leaf
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_collapses_range_to_one_survivor(self):
        from lib.compact_range import compact_range
        compact_range(str(self.session_path), from_pos=2, to_pos=5,
                      summary="middle three turns", no_backup=True)

        chain = _conversation(self.session_path)
        # 8 → 5 (4 collapsed into 1)
        self.assertEqual(len(chain), 5,
                         f"expected 5 messages after collapse, got {len(chain)}")

    def test_survivor_carries_persisted_range_marker(self):
        from lib.compact_range import compact_range
        compact_range(str(self.session_path), from_pos=2, to_pos=5,
                      summary="middle three turns", no_backup=True)

        markers = list(iter_persisted_markers(self.session_path))
        kinds = {f["kind"] for *_, f in markers}
        self.assertIn("range", kinds,
                      f"no <persisted-range> marker found; got kinds {kinds}")

    def test_marker_records_count_and_bounds(self):
        from lib.compact_range import compact_range
        compact_range(str(self.session_path), from_pos=2, to_pos=5,
                      summary="x", no_backup=True)

        # Inspect the survivor message's text content (after JSON parsing,
        # so we don't trip over `\"` escaping in the raw file).
        markers = list(iter_persisted_markers(self.session_path))
        rng_entry = next(t for t in markers if t[4]["kind"] == "range")
        marker_text = rng_entry[3]
        self.assertIn('from="2"', marker_text)
        self.assertIn('to="5"', marker_text)
        self.assertIn('count="4"', marker_text)

    def test_each_collapsed_message_saved_to_sidecar(self):
        from lib.compact_range import compact_range
        original_uuids = self.info["uuids"][2:6]
        compact_range(str(self.session_path), from_pos=2, to_pos=5,
                      summary="x", no_backup=True)

        markers = list(iter_persisted_markers(self.session_path))
        rng = next(m for *_, m in markers if m["kind"] == "range")
        # path should be the directory containing the sidecars
        sidecar_dir = resolve_persisted_path(self.session_path, rng["path"])
        self.assertTrue(sidecar_dir.is_dir(),
                        f"sidecar dir not at {sidecar_dir}")
        for uid in original_uuids:
            self.assertTrue((sidecar_dir / f"{uid}.json").is_file(),
                            f"sidecar missing for {uid}")

    def test_chain_remains_valid(self):
        from lib.compact_range import compact_range
        compact_range(str(self.session_path), from_pos=2, to_pos=5,
                      summary="x", no_backup=True)
        assert_chain_valid(self.session_path)

    def test_default_summary_when_none_provided(self):
        from lib.compact_range import compact_range
        compact_range(str(self.session_path), from_pos=2, to_pos=5, no_backup=True)
        markers = list(iter_persisted_markers(self.session_path))
        rng = next(m for *_, m in markers if m["kind"] == "range")
        self.assertIn("collapsed", rng["summary"].lower(),
                      f"default summary missing, got: {rng['summary']!r}")


class TestCompactRangeSafety(unittest.TestCase):

    def test_refuses_leaf_inclusion(self):
        from lib.compact_range import compact_range, RangeRefused
        path, _ = build_session([
            ("user", "a"),
            ("assistant", "b"),
            ("user", "c"),
        ])
        try:
            with self.assertRaises(RangeRefused):
                compact_range(str(path), from_pos=1, to_pos=2, no_backup=True)
        finally:
            path.unlink(missing_ok=True)

    def test_refuses_orphaned_tool_use_in_range(self):
        from lib.compact_range import compact_range, RangeRefused
        # Range covers an assistant turn with a tool_use whose tool_result
        # lives in the *next* user turn — outside the range.
        path, _ = build_session([
            ("user", "do thing"),
            ("assistant", [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "toolu_X", "name": "Bash",
                 "input": {"command": "ls"}},
            ]),
            ("user", [
                {"type": "tool_result", "tool_use_id": "toolu_X",
                 "content": "files"},
            ]),
            ("assistant", "done"),
            ("user", "tail"),
        ])
        try:
            # Range 0–1 includes the tool_use (pos 1) but not the tool_result (pos 2)
            with self.assertRaises(RangeRefused):
                compact_range(str(path), from_pos=0, to_pos=1, no_backup=True)
        finally:
            path.unlink(missing_ok=True)

    def test_allows_range_that_includes_both_tool_use_and_result(self):
        from lib.compact_range import compact_range
        path, _ = build_session([
            ("user", "do thing"),
            ("assistant", [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "toolu_Y", "name": "Bash",
                 "input": {"command": "ls"}},
            ]),
            ("user", [
                {"type": "tool_result", "tool_use_id": "toolu_Y",
                 "content": "files"},
            ]),
            ("assistant", "done"),
            ("user", "tail"),
        ])
        try:
            # Range 0–2 covers both the tool_use and the tool_result
            compact_range(str(path), from_pos=0, to_pos=2,
                          summary="full tool round-trip", no_backup=True)
            assert_chain_valid(path)
        finally:
            path.unlink(missing_ok=True)


class TestCompactRangeIdempotency(unittest.TestCase):

    def test_running_twice_is_no_op(self):
        from lib.compact_range import compact_range
        path, _ = build_session([
            ("user", "a"),
            ("assistant", "b"),
            ("user", "c"),
            ("assistant", "d"),
            ("user", "tail"),
        ])
        try:
            compact_range(str(path), from_pos=1, to_pos=3, summary="x",
                          no_backup=True)
            after_first = path.read_text()
            # Re-running on what is now a single survivor (pos 1) should be a no-op
            compact_range(str(path), from_pos=1, to_pos=1, summary="x",
                          no_backup=True)
            after_second = path.read_text()
            self.assertEqual(after_first, after_second,
                             "second compact-range mutated session")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
