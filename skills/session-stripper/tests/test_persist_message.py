"""TDD tests for persist-message — replaces ALL blocks of a single message
with one <persisted-message> marker; sidecar is the full original message
JSON.

Critical edge cases tested:
- A message containing tool_use must auto-persist its matching tool_result
  (next message), or the API rejects the chain on resume.
- Persisting the LAST (leaf) message must refuse — would orphan the resume
  cursor.
- Sidecar is JSON, not txt (whole message structure preserved).
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
    get_block_at,
    resolve_persisted_path,
)


class TestPersistMessageBasic(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", [
                {"type": "text", "text": "first text"},
                {"type": "text", "text": "second text"},
            ]),
            ("user", "ack"),
            ("assistant", "trailing"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_replaces_all_blocks_with_one_marker(self):
        from lib.persist_message import persist_message
        persist_message(self.session_path, chain_pos=1, summary="multi-block message")

        # The message at pos 1 now has exactly one block, a text containing the marker
        block_count = 0
        with open(self.session_path) as f:
            for i, line in enumerate(f):
                if i == 1:
                    obj = json.loads(line)
                    block_count = len(obj["message"]["content"])
        self.assertEqual(block_count, 1)
        marker_block = get_block_at(self.session_path, 1, 0)
        self.assertEqual(marker_block.get("type"), "text")
        self.assertIn("<persisted-message", marker_block.get("text", ""))

    def test_sidecar_is_json_with_full_original_content(self):
        from lib.persist_message import persist_message
        persist_message(self.session_path, chain_pos=1, summary="x")

        markers = list(iter_persisted_markers(self.session_path))
        self.assertEqual(len(markers), 1)
        _, _, _, _, fields = markers[0]
        sidecar = resolve_persisted_path(self.session_path, fields["path"])
        self.assertTrue(sidecar.is_file())
        # Sidecar must be valid JSON containing both original blocks
        data = json.loads(sidecar.read_text())
        self.assertIsInstance(data, dict)
        contents = data.get("message", {}).get("content", [])
        texts = [b.get("text") for b in contents if isinstance(b, dict)]
        self.assertIn("first text", texts)
        self.assertIn("second text", texts)

    def test_chain_remains_valid(self):
        from lib.persist_message import persist_message
        persist_message(self.session_path, chain_pos=1, summary="x")
        assert_chain_valid(self.session_path)


class TestPersistMessageLeafSafety(unittest.TestCase):
    """Persisting the last message in the active chain orphans the resume
    cursor. The command must refuse."""

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", "reply"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_refuses_persist_of_leaf(self):
        from lib.persist_message import persist_message, LeafPersistRefused
        with self.assertRaises(LeafPersistRefused):
            persist_message(self.session_path, chain_pos=1, summary="x")


class TestPersistMessageToolUsePair(unittest.TestCase):
    """A message containing tool_use must persist the matching tool_result
    too — otherwise the API rejects the chain (orphaned tool_use)."""

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "do thing"),
            ("assistant", [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "toolu_X", "name": "Bash",
                 "input": {"command": "ls"}},
            ]),
            ("user", [
                {"type": "tool_result", "tool_use_id": "toolu_X",
                 "content": "lots of files"},
            ]),
            ("assistant", "and another reply"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_persisting_tool_use_message_also_persists_matching_result(self):
        from lib.persist_message import persist_message
        persist_message(self.session_path, chain_pos=1, summary="tool turn")

        # Both pos 1 (assistant with tool_use) and pos 2 (user with tool_result)
        # should now contain a single <persisted-*> marker each.
        msgs = []
        with open(self.session_path) as f:
            for line in f:
                msgs.append(json.loads(line))
        for pos in (1, 2):
            blocks = msgs[pos]["message"]["content"]
            self.assertEqual(len(blocks), 1, f"pos {pos} not collapsed")
            self.assertIn("<persisted-", blocks[0].get("text", ""))

        assert_chain_valid(self.session_path)


if __name__ == "__main__":
    unittest.main()
