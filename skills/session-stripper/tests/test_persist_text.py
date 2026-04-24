"""TDD tests for the new persist-text command.

Contract: takes one or many text blocks out of a session, writes each to
<sessionId>/persisted/text/<msg_uuid>_<block_idx>.txt, replaces with a
<persisted-text> marker carrying path, size, summary, preview.

These tests fail until `lib/persist_text.py` exists with `persist_text` (single,
by chain pos) and `persist_text_bulk` (range).
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import (
    build_session,
    iter_persisted_markers,
    assert_chain_valid,
    get_block_at,
    resolve_persisted_path,
)


def _long_text(n_chars: int = 3000) -> str:
    return "lorem ipsum " * (n_chars // 12 + 1)


class TestPersistTextSingle(unittest.TestCase):

    def setUp(self):
        long = _long_text(3000)
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", long),                       # pos 1, target
            ("user", "ok"),
        ])
        self.target_text = long

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_single_persist_replaces_block_with_marker(self):
        from lib.persist_text import persist_text
        persist_text(self.session_path, chain_pos=1)

        # The replaced block is now a text block containing a <persisted-text>
        block = get_block_at(self.session_path, 1, 0)
        self.assertEqual(block.get("type"), "text")
        self.assertIn("<persisted-text", block.get("text", ""))

    def test_single_persist_writes_sidecar_with_original_content(self):
        from lib.persist_text import persist_text
        persist_text(self.session_path, chain_pos=1)

        markers = list(iter_persisted_markers(self.session_path))
        self.assertEqual(len(markers), 1)
        _, _, _, _, fields = markers[0]
        self.assertIsNotNone(fields["path"])
        sidecar = resolve_persisted_path(self.session_path, fields["path"])
        self.assertTrue(sidecar.is_file(), f"sidecar missing at {sidecar}")
        self.assertEqual(sidecar.read_text(), self.target_text)

    def test_single_persist_preserves_chain_integrity(self):
        from lib.persist_text import persist_text
        persist_text(self.session_path, chain_pos=1)
        assert_chain_valid(self.session_path)


class TestPersistTextBulk(unittest.TestCase):

    def setUp(self):
        long = _long_text(3000)
        small = "ok"
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", long),
            ("user", small),
            ("assistant", long),
            ("user", small),
            ("assistant", long),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_bulk_persists_only_above_min_chars(self):
        from lib.persist_text import persist_text_bulk
        persist_text_bulk(self.session_path, min_chars=1000)

        markers = list(iter_persisted_markers(self.session_path))
        # 3 long assistant blocks should be persisted; small "ok" turns spared
        self.assertEqual(len(markers), 3)

    def test_bulk_keep_recent_n(self):
        from lib.persist_text import persist_text_bulk
        persist_text_bulk(self.session_path, min_chars=1000, keep_recent=1)
        markers = list(iter_persisted_markers(self.session_path))
        self.assertEqual(len(markers), 2,
                         "keep_recent=1 should leave the last long block intact")

    def test_bulk_range_filter(self):
        from lib.persist_text import persist_text_bulk
        # Range 0-2 contains one long block at pos 1
        persist_text_bulk(self.session_path, min_chars=1000, from_pos=0, to_pos=2)
        markers = list(iter_persisted_markers(self.session_path))
        self.assertEqual(len(markers), 1)


class TestPersistTextIdempotency(unittest.TestCase):

    def setUp(self):
        long = _long_text(3000)
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", long),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_running_twice_is_a_no_op(self):
        from lib.persist_text import persist_text
        persist_text(self.session_path, chain_pos=1)
        size_after_first = self.session_path.stat().st_size
        persist_text(self.session_path, chain_pos=1)  # already persisted
        size_after_second = self.session_path.stat().st_size
        self.assertEqual(size_after_first, size_after_second,
                         "second persist mutated session — should be no-op")


if __name__ == "__main__":
    unittest.main()
