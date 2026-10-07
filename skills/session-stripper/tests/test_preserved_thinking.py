"""Preserved thinking: an edit invalidates every later thinking block.

On Claude Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5, a thinking block's
signature binds it to the history before it and to the previous thinking
block. Editing an earlier turn, or removing a thinking block from the middle,
makes the API drop every later block (Claude Code runs it in drop mode, so the
request still succeeds, unbilled, without that reasoning). Removing thinking
from the FRONT is fine.

The stripper reports this on every mutating command and, by default, removes
the dropped blocks from the result so the file matches what the model sees.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.chain import build_uuid_index, load_session, save_session, walk_active_chain
from lib.pending import pending_path
from lib.preserved_thinking import find_invalidated

import stripper

BIG = "output line\n" * 400


def _think(n):
    return {"type": "thinking", "thinking": "", "signature": f"SIG{n}-" + "x" * 2000}


def _session(model="claude-fable-5-1"):
    """pos0 user, pos1 think+tool_use, pos2 tool_result, pos3 think+tool_use,
    pos4 tool_result, pos5 think+text."""
    path, info = build_session([
        ("user", "hi"),
        ("assistant", [_think(1), {"type": "tool_use", "id": "t1", "name": "Bash",
                                   "input": {"command": "ls"}}]),
        ("user", [{"type": "tool_result", "tool_use_id": "t1", "content": BIG}]),
        ("assistant", [_think(2), {"type": "tool_use", "id": "t2", "name": "Bash",
                                   "input": {"command": "pwd"}}]),
        ("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
        ("assistant", [_think(3), {"type": "text", "text": "done"}]),
    ])
    objects = load_session(path)
    for o in objects:
        if o.get("type") == "assistant":
            o["message"]["model"] = model
    save_session(path, objects, create_backup=False)
    return path


def _chain(path):
    objects = load_session(path)
    return walk_active_chain(objects, build_uuid_index(objects))


def _signatures(path):
    sigs = []
    for o in _chain(path):
        for b in o["message"]["content"] if isinstance(o["message"]["content"], list) else []:
            if b.get("type") == "thinking":
                sigs.append(b["signature"][:4])
    return sigs


def _edit(path, fn):
    objects = load_session(path)
    fn(walk_active_chain(objects, build_uuid_index(objects)))
    out = Path(str(path) + ".edited.jsonl")
    save_session(out, objects, create_backup=False)
    return out


def _cleanup(*paths):
    for p in paths:
        p = Path(p)
        for f in p.parent.glob(p.name + "*"):
            f.unlink(missing_ok=True)


class TestFindInvalidated(unittest.TestCase):
    def setUp(self):
        self.path = _session()

    def tearDown(self):
        _cleanup(self.path)

    def _report(self, edited):
        return find_invalidated(_chain(self.path), _chain(edited))

    def test_unchanged_session_has_nothing_invalid(self):
        r = find_invalidated(_chain(self.path), _chain(self.path))
        self.assertTrue(r["bound"])
        self.assertEqual(r["invalid"], [])

    def test_mid_edit_invalidates_later_blocks_only(self):
        def clear_result(chain):
            chain[2]["message"]["content"][0]["content"] = "[cleared]"
        r = self._report(_edit(self.path, clear_result))
        self.assertEqual([p for p, _ in r["invalid"]], [3, 5])
        self.assertEqual(r["first_pos"], 3)
        self.assertGreater(r["chars"], 4000)

    def test_front_thinking_removal_is_safe(self):
        def drop_first(chain):
            chain[1]["message"]["content"].pop(0)
        r = self._report(_edit(self.path, drop_first))
        self.assertEqual(r["invalid"], [])

    def test_middle_thinking_removal_breaks_the_chain(self):
        def drop_second(chain):
            chain[3]["message"]["content"].pop(0)
        r = self._report(_edit(self.path, drop_second))
        self.assertEqual([p for p, _ in r["invalid"]], [5])

    def test_deleted_record_before_block_is_detected(self):
        """Prefix match alone is not enough: the block must sit after the
        same records as before. Deleting the tool_result at pos 2 leaves the
        new prefix of the pos-3 block equal to the start of the original."""
        original = _chain(self.path)
        new = [o for i, o in enumerate(original) if i != 2]
        r = find_invalidated(original, new)
        self.assertEqual([p for p, _ in r["invalid"]], [2, 4])

    def test_unbound_model_reports_nothing(self):
        path = _session(model="claude-opus-4-8")
        try:
            def clear_result(chain):
                chain[2]["message"]["content"][0]["content"] = "[cleared]"
            edited = _edit(path, clear_result)
            r = find_invalidated(_chain(path), _chain(edited))
            self.assertFalse(r["bound"])
            self.assertEqual(r["invalid"], [])
        finally:
            _cleanup(path)


def _strip_tools_args(session, **over):
    base = dict(session=str(session), dry_run=False, no_backup=False,
                from_pos=2, to_pos=2, fork=False, fork_title=None,
                no_usage_reset=True, only_inputs=False, only_results=True,
                tools=None, keep_last_lines=None, keep_dropped_thinking=False,
                func=stripper.cmd_strip_tools)
    base.update(over)
    return SimpleNamespace(**base)


def _run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        stripper.run_command(args)
    return buf.getvalue()


class TestCliStep(unittest.TestCase):
    def setUp(self):
        self.path = _session()
        self.original = self.path.read_bytes()

    def tearDown(self):
        _cleanup(self.path)

    def test_default_removes_dropped_blocks_from_result(self):
        out = _run(_strip_tools_args(self.path))
        self.assertIn("Preserved thinking (claude-fable-5-1)", out)
        self.assertIn("invalidates 2 later thinking block(s)", out)
        self.assertEqual(_signatures(pending_path(self.path)), ["SIG1"])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_keep_flag_leaves_blocks(self):
        out = _run(_strip_tools_args(self.path, keep_dropped_thinking=True))
        self.assertIn("Kept in the file", out)
        self.assertEqual(_signatures(pending_path(self.path)), ["SIG1", "SIG2", "SIG3"])

    def test_dry_run_reports_and_writes_nothing(self):
        out = _run(_strip_tools_args(self.path, dry_run=True))
        self.assertIn("Would remove", out)
        self.assertFalse(pending_path(self.path).exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_front_strip_thinking_prints_nothing(self):
        args = _strip_tools_args(self.path, func=stripper.cmd_strip_thinking,
                                 from_pos=0, to_pos=1)
        out = _run(args)
        self.assertNotIn("Preserved thinking", out)
        self.assertEqual(_signatures(pending_path(self.path)), ["SIG2", "SIG3"])

    def test_unbound_model_is_untouched(self):
        path = _session(model="claude-opus-4-8")
        try:
            out = _run(_strip_tools_args(path))
            self.assertNotIn("Preserved thinking", out)
            self.assertEqual(_signatures(pending_path(path)), ["SIG1", "SIG2", "SIG3"])
        finally:
            _cleanup(path)


if __name__ == "__main__":
    unittest.main()
