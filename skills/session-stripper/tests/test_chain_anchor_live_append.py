"""REGRESSION (incident 2026-08-19): a strip must never delete a node that a
live Claude Code process is about to cite as `parentUuid`.

The headline flow is stripping your OWN current session, so the `claude` process
is still running and still appending to the same .jsonl. It holds the uuid of
the last record it wrote as its in-memory conversation head. If a strip deletes
that node, the process's next append is born dangling: `walk_active_chain` stops
at the missing uuid and every earlier message falls out of the conversation.

Observed: `strip-thinking` removed 311 thinking-only messages, one of them the
live head; the chain collapsed 1,139 -> 67 messages with no error, and `verify`
reported PASS on BOTH sides of the break because it only walks the (now short)
active chain.

`remove_objects_and_rewire`'s existing rewiring cannot fix this — the offending
record does not exist yet when the strip runs. So the node is kept as a chain
stepping-stone and only its payload is emptied.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.chain import (
    CHAIN_ANCHOR_PLACEHOLDER,
    build_uuid_index,
    chain_anchor_uuids,
    load_session,
    remove_objects_and_rewire,
    walk_active_chain,
)
from lib.strip_thinking import strip_thinking


def _thinking_only_tail_session():
    """6 messages; the LAST one is a thinking-only assistant turn — exactly the
    shape that made the live head deletable."""
    return build_session([
        ("user", "hi"),
        ("assistant", "sure"),
        ("user", "keep going"),
        ("assistant", "on it"),
        ("user", "and again"),
        ("assistant", [{"type": "thinking", "thinking": "pondering " * 200}]),
    ])


def _append_live_turn(path, parent_uuid, session_info):
    """Simulate the running CLI flushing one more record AFTER the strip,
    carrying the parentUuid it held in memory."""
    record = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": session_info["cwd"],
        "sessionId": session_info["session_id"],
        "version": "2.1.114",
        "gitBranch": "master",
        "slug": session_info["slug"],
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "next turn"}]},
        "uuid": "11111111-2222-3333-4444-555555555555",
        "timestamp": "2026-01-01T00:00:09.000Z",
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record["uuid"]


def _dangling_parents(path):
    objects = load_session(path)
    known = {o.get("uuid") for o in objects if o.get("uuid")}
    return [o.get("uuid") for o in objects
            if o.get("parentUuid") is not None and o["parentUuid"] not in known]


class TestLiveAppendAfterStrip(unittest.TestCase):
    def test_chain_survives_append_with_stale_parent_uuid(self):
        path, info = _thinking_only_tail_session()
        try:
            before = len(walk_active_chain(load_session(path)))
            live_head = info["uuids"][-1]

            strip_thinking(str(path), no_backup=True)
            _append_live_turn(path, live_head, info)

            objects = load_session(path)
            after = len(walk_active_chain(objects, build_uuid_index(objects)))

            self.assertEqual(
                after, before + 1,
                "the live append must extend the chain, not truncate it",
            )
            self.assertEqual([], _dangling_parents(path),
                             "no record may reference a missing parentUuid")
        finally:
            path.unlink(missing_ok=True)

    def test_anchor_node_is_kept_but_emptied(self):
        """The saving must be preserved: the node stays, the thinking does not."""
        path, info = _thinking_only_tail_session()
        try:
            stats = strip_thinking(str(path), no_backup=True)
            objects = load_session(path)
            by_uuid = {o["uuid"]: o for o in objects if "uuid" in o}
            anchor = by_uuid.get(info["uuids"][-1])

            self.assertIsNotNone(anchor, "the live head must survive as a node")
            self.assertEqual(
                [{"type": "text", "text": CHAIN_ANCHOR_PLACEHOLDER}],
                anchor["message"]["content"],
            )
            self.assertNotIn("pondering", json.dumps(anchor),
                             "the thinking payload must still be gone")
            self.assertEqual(1, stats["messages_anchored"])
            self.assertEqual(0, stats["messages_removed"])
        finally:
            path.unlink(missing_ok=True)

    def test_anchor_set_covers_file_tail_and_chain_leaf(self):
        path, info = _thinking_only_tail_session()
        try:
            anchors = chain_anchor_uuids(load_session(path))
            self.assertIn(info["uuids"][-1], anchors)
        finally:
            path.unlink(missing_ok=True)

    def test_without_the_anchor_the_collapse_reproduces(self):
        """Proves this file would actually catch a regression: with protection
        explicitly disabled, the exact 2026-08-19 failure comes back."""
        path, info = _thinking_only_tail_session()
        try:
            objects = load_session(path)
            live_head = info["uuids"][-1]
            survivors, removed, _, anchored = remove_objects_and_rewire(
                objects, {live_head}, protect=frozenset())
            self.assertEqual(1, removed)
            self.assertEqual(0, anchored)

            # Re-write the file without the head, then let the CLI append.
            with open(path, "w", encoding="utf-8") as f:
                for o in survivors:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")
            _append_live_turn(path, live_head, info)

            objects = load_session(path)
            chain = walk_active_chain(objects, build_uuid_index(objects))
            self.assertEqual(1, len(chain),
                             "unprotected: the append is dangling and the chain collapses")
            self.assertNotEqual([], _dangling_parents(path))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
