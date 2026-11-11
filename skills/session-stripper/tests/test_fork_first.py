"""Mutating commands write a FORK by default.

Incident 2026-11-11: an in-place strip of a live session left a `.pending`
copy waiting for the CLI to quit so `apply` could swap it in. The user kept
working in the session meanwhile, so the copy went stale — swapping it in
would have discarded those turns — and the session itself never got smaller.
A fork has neither problem: it is a new session you resume, nothing is ever
swapped over the original, and nothing the original records later is lost.

Back-to-back passes on the same session must still land in ONE fork, so an
unresumed fork is reused (and a fork you target directly is edited in place)
until someone resumes it.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

import stripper
from lib.chain import load_session
from lib.fork import read_state, state_path
from lib.pending import pending_path


def _session(dirpath):
    """A session in its own directory (forks land next to it)."""
    path, _ = build_session([
        ("user", "hi"),
        ("assistant", [{"type": "thinking", "thinking": "deep " * 400, "signature": "S" * 50},
                       {"type": "text", "text": "yo"}]),
        ("user", "more"),
        ("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash",
                        "input": {"command": "ls " * 200}}]),
        ("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 3000}]),
        ("assistant", "done"),
    ])
    dst = Path(dirpath) / path.name
    shutil.move(str(path), dst)
    return dst


def _args(session, func, **over):
    base = dict(session=str(session), dry_run=False, no_backup=False,
                from_pos=0, to_pos=None, fork=False, fork_title=None, in_place=False,
                new_fork=False, no_usage_reset=True, keep_dropped_thinking=False,
                only_inputs=False, only_results=False, tools=None,
                keep_last_lines=None, func=func)
    base.update(over)
    return SimpleNamespace(**base)


def _run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        stripper.run_command(args)
    return buf.getvalue()


class TestForkFirst(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ss-forkfirst-"))
        self.path = _session(self.dir)
        self.original = self.path.read_bytes()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sessions(self):
        return sorted(p for p in self.dir.glob("*.jsonl") if p != self.path)

    def test_default_writes_a_fork_and_never_the_original(self):
        args = _args(self.path, stripper.cmd_strip_thinking)
        out = _run(args)
        forks = self._sessions()
        self.assertEqual(len(forks), 1)
        self.assertEqual(Path(args._fork_result), forks[0])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(pending_path(self.path).exists())
        self.assertNotIn("deep deep", forks[0].read_text())
        self.assertEqual(read_state(forks[0])["source"], str(self.path))
        self.assertIn(f"claude -r {forks[0].stem}", out)
        ids = {o.get("sessionId") for o in load_session(forks[0]) if o.get("sessionId")}
        self.assertEqual(ids, {forks[0].stem})

    def test_second_pass_builds_on_the_same_fork(self):
        first = _args(self.path, stripper.cmd_strip_thinking)
        _run(first)
        second = _args(self.path, stripper.cmd_strip_tools)
        out = _run(second)
        self.assertIn("Reusing stripper fork", out)
        self.assertEqual(second._fork_result, first._fork_result)
        self.assertEqual(len(self._sessions()), 1)
        text = Path(first._fork_result).read_text()
        self.assertNotIn("deep deep", text)    # pass 1 survived
        self.assertNotIn("x" * 3000, text)      # pass 2 applied
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_reuse_notes_what_the_source_recorded_since(self):
        _run(_args(self.path, stripper.cmd_strip_thinking))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "later"}) + "\n")
        out = _run(_args(self.path, stripper.cmd_strip_tools))
        self.assertIn("has grown by", out)

    def test_a_resumed_fork_is_never_edited(self):
        first = _args(self.path, stripper.cmd_strip_thinking)
        _run(first)
        fork = Path(first._fork_result)
        with open(fork, "a", encoding="utf-8") as f:  # what a resumed CLI does
            f.write(json.dumps({"type": "mode", "mode": "default"}) + "\n")
        resumed = fork.read_bytes()
        second = _args(self.path, stripper.cmd_strip_tools)
        _run(second)
        self.assertNotEqual(second._fork_result, first._fork_result)
        self.assertEqual(fork.read_bytes(), resumed)
        self.assertEqual(len(self._sessions()), 2)

    def test_targeting_an_unresumed_fork_edits_it_in_place(self):
        first = _args(self.path, stripper.cmd_strip_thinking)
        _run(first)
        fork = Path(first._fork_result)
        second = _args(fork, stripper.cmd_strip_tools)
        out = _run(second)
        self.assertIn("Editing stripper fork in place", out)
        self.assertEqual(second._fork_result, str(fork))
        self.assertEqual(len(self._sessions()), 1)
        self.assertEqual(second._pt_source, str(self.path))  # still judged against the original

    def test_new_fork_flag_takes_a_fresh_one(self):
        _run(_args(self.path, stripper.cmd_strip_thinking))
        _run(_args(self.path, stripper.cmd_strip_tools, new_fork=True))
        self.assertEqual(len(self._sessions()), 2)

    def test_dry_run_creates_nothing(self):
        args = _args(self.path, stripper.cmd_strip_thinking, dry_run=True)
        _run(args)
        self.assertEqual(self._sessions(), [])
        self.assertFalse(pending_path(self.path).exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_rewritten_source_gets_a_fresh_fork(self):
        first = _args(self.path, stripper.cmd_strip_thinking)
        _run(first)
        self.path.write_bytes(self.original[: len(self.original) // 2].rsplit(b"\n", 1)[0] + b"\n")
        second = _args(self.path, stripper.cmd_strip_tools)
        _run(second)
        self.assertNotEqual(second._fork_result, first._fork_result)

    def test_in_place_flag_keeps_the_pending_flow(self):
        args = _args(self.path, stripper.cmd_strip_thinking, in_place=True)
        out = _run(args)
        self.assertTrue(pending_path(self.path).exists())
        self.assertEqual(self._sessions(), [])
        self.assertIn("stripper.py apply", out)

    def test_migrate_persisted_stays_in_place(self):
        args = SimpleNamespace(session=str(self.path), dry_run=False, no_backup=False,
                               fork=False, fork_title=None, in_place=False, new_fork=False,
                               keep_dropped_thinking=False,
                               func=stripper.cmd_migrate_persisted)
        _run(args)
        self.assertEqual(self._sessions(), [])
        self.assertTrue(pending_path(self.path).exists())

    def test_stale_pending_is_reported_and_ignored(self):
        stripper.cmd_strip_thinking(_args(self.path, stripper.cmd_strip_thinking, in_place=True))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "later"}) + "\n")
        args = _args(self.path, stripper.cmd_strip_tools)
        out = _run(args)
        self.assertIn("pending copy exists", out)
        self.assertIn("would discard them", out)
        self.assertIn("later", Path(args._fork_result).read_text())  # forked from the original

    def test_state_sidecar_is_not_a_session(self):
        args = _args(self.path, stripper.cmd_strip_thinking)
        _run(args)
        self.assertTrue(state_path(args._fork_result).exists())
        self.assertFalse(state_path(args._fork_result).name.endswith(".jsonl"))


if __name__ == "__main__":
    unittest.main()
