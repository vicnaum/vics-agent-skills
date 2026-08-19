"""Guard rails on the chain-anchor protection itself.

Anchoring keeps a node alive that a strip asked to delete, so it must not edit
the caller's data while doing it. `strip_attachments`' dry-run path passes
`[dict(o) for o in objects]` — a SHALLOW copy that shares the inner `message`
dict with the real objects. Emptying a payload in place there rewrote the live
objects during a DRY RUN. Found by auditing the anchor change, not in the field.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.chain import load_session, remove_objects_and_rewire


class TestAnchorDoesNotMutateCallerData(unittest.TestCase):
    def test_shallow_copies_are_not_written_through(self):
        objects = [
            {"uuid": "a", "parentUuid": None, "type": "user",
             "message": {"role": "user", "content": [{"type": "text", "text": "keep me"}]}},
            {"uuid": "b", "parentUuid": "a", "type": "assistant",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "LIVE PAYLOAD"}]}},
        ]
        remove_objects_and_rewire([dict(o) for o in objects], {"b"})
        self.assertEqual(
            "LIVE PAYLOAD", objects[1]["message"]["content"][0]["text"],
            "a dry run must never rewrite the caller's objects")

    def test_real_path_still_empties_the_payload(self):
        objects = [
            {"uuid": "a", "parentUuid": None, "type": "user",
             "message": {"role": "user", "content": [{"type": "text", "text": "keep me"}]}},
            {"uuid": "b", "parentUuid": "a", "type": "assistant",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "LIVE PAYLOAD"}]}},
        ]
        _, removed, _, anchored = remove_objects_and_rewire(objects, {"b"})
        self.assertEqual((0, 1), (removed, anchored))
        self.assertNotEqual("LIVE PAYLOAD", objects[1]["message"]["content"][0]["text"])

    def test_anchor_keeps_the_rest_of_the_envelope(self):
        """Only the payload goes; uuid/parentUuid/timestamp must survive or the
        node is useless as a stepping-stone."""
        path, info = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "thinking", "thinking": "x " * 50}]),
        ])
        try:
            objects = load_session(path)
            leaf = info["uuids"][-1]
            remove_objects_and_rewire(objects, {leaf})
            kept = next(o for o in objects if o.get("uuid") == leaf)
            self.assertEqual(info["uuids"][0], kept["parentUuid"])
            self.assertIn("timestamp", kept)
            self.assertEqual("assistant", kept["message"]["role"])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
