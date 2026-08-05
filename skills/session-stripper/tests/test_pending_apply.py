"""Copy-first mutation layer (lib/pending.py + CLI wiring).

The invariant under test, born from the 2026-08-05 live-strip incident:
mutating commands NEVER write a real session file — they snapshot to
`<file>.pending`, mutate the copy, and `apply` swaps it in atomically only
when nothing is writing the original. Post-snapshot appends to the original
(the stripping turn itself, in the self-strip flow) are discarded by design.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, assert_chain_valid

from lib.pending import (
    ApplyRefused,
    apply_pending,
    create_snapshot,
    ensure_pending,
    is_pending,
    meta_path_for,
    original_of,
    pending_path,
)


def _cleanup(path: Path):
    """Remove a session file and every sibling artifact tests may create."""
    for suffix in ("", ".pending", ".pending.meta.json", ".bak", ".bak.1"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _strip_thinking_args(session, **over):
    """Namespace matching what argparse hands cmd_strip_thinking."""
    base = dict(session=str(session), dry_run=False, no_backup=False,
                from_pos=0, to_pos=None, fork=False, fork_title=None,
                no_usage_reset=True)
    base.update(over)
    return SimpleNamespace(**base)


class TestSnapshot(unittest.TestCase):
    def test_snapshot_creates_pending_and_meta(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            pend = create_snapshot(path)
            self.assertTrue(pend.is_file())
            self.assertTrue(is_pending(pend))
            self.assertEqual(original_of(pend), path.absolute())
            self.assertEqual(pend.read_bytes(), path.read_bytes())
            meta = json.loads(meta_path_for(pend).read_text())
            self.assertEqual(meta["base_size"], path.stat().st_size)
            self.assertEqual(meta["torn_tail_bytes_dropped"], 0)
        finally:
            _cleanup(path)

    def test_snapshot_drops_torn_final_line(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            whole = path.stat().st_size
            with open(path, "a") as f:
                f.write('{"type":"user","uuid":"torn')  # mid-append, no \n
            pend = create_snapshot(path)
            self.assertEqual(pend.stat().st_size, whole)
            # every snapshot line parses
            for line in pend.read_text().splitlines():
                json.loads(line)
            meta = json.loads(meta_path_for(pend).read_text())
            self.assertGreater(meta["torn_tail_bytes_dropped"], 0)
        finally:
            _cleanup(path)

    def test_snapshot_refuses_overwrite_unless_refresh(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            create_snapshot(path)
            with self.assertRaises(FileExistsError):
                create_snapshot(path)
            create_snapshot(path, refresh=True)  # no raise
        finally:
            _cleanup(path)

    def test_ensure_pending_reuses(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            p1, created1 = ensure_pending(path)
            p2, created2 = ensure_pending(path)
            self.assertTrue(created1)
            self.assertFalse(created2)
            self.assertEqual(p1, p2)
        finally:
            _cleanup(path)


class TestCliRedirect(unittest.TestCase):
    """Mutating CLI commands must leave the original byte-identical."""

    def test_strip_thinking_targets_pending_not_original(self):
        import stripper
        path, _ = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "thinking", "thinking": "deep " * 500},
                           {"type": "text", "text": "yo"}]),
            ("user", "ok"),
            ("assistant", "done"),
        ])
        try:
            before = path.read_bytes()
            stripper.cmd_strip_thinking(_strip_thinking_args(path))
            self.assertEqual(path.read_bytes(), before,
                             "mutating command wrote the original!")
            pend = pending_path(path)
            self.assertTrue(pend.is_file())
            self.assertNotIn("deep deep", pend.read_text())
            assert_chain_valid(pend)
        finally:
            _cleanup(path)

    def test_dry_run_does_not_materialize_snapshot(self):
        import stripper
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            stripper.cmd_strip_thinking(_strip_thinking_args(path, dry_run=True))
            self.assertFalse(pending_path(path).exists())
        finally:
            _cleanup(path)

    def test_fork_bypasses_pending(self):
        import stripper
        path, _ = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "thinking", "thinking": "deep " * 500},
                           {"type": "text", "text": "yo"}]),
        ])
        forked = []
        try:
            args = _strip_thinking_args(path, fork=True)
            stripper.cmd_strip_thinking(args)
            forked.append(Path(args.session))
            self.assertNotEqual(str(args.session), str(path))
            self.assertFalse(is_pending(args.session))
            self.assertFalse(pending_path(path).exists())
        finally:
            _cleanup(path)
            for f in forked:
                _cleanup(f)


class TestApply(unittest.TestCase):
    def _stripped_pair(self):
        """Session + pending copy that differs (thinking stripped)."""
        from lib.strip_thinking import strip_thinking
        path, _ = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "thinking", "thinking": "deep " * 500},
                           {"type": "text", "text": "yo"}]),
            ("user", "ok"),
            ("assistant", "done"),
        ])
        pend = create_snapshot(path)
        strip_thinking(str(pend), no_backup=True)
        return path, pend

    def test_apply_swaps_and_discards_tail(self):
        path, pend = self._stripped_pair()
        try:
            with open(path, "a") as f:  # post-snapshot appends (the tail)
                f.write('{"type":"user","uuid":"tail-1","parentUuid":null,'
                        '"message":{"role":"user","content":"tail"}}\n')
            stats = apply_pending(path, no_backup=False, settle_ms=50)
            self.assertNotIn("deep deep", path.read_text())      # strip landed
            self.assertNotIn("tail-1", path.read_text())         # tail gone
            self.assertEqual(stats["tail_lines_discarded"], 1)
            self.assertFalse(pend.exists())
            self.assertFalse(meta_path_for(pend).exists())
            bak = Path(str(path) + ".bak")
            self.assertTrue(bak.exists())
            self.assertIn("tail-1", bak.read_text())             # bak = pre-swap
            assert_chain_valid(path)
        finally:
            _cleanup(path)

    def test_apply_accepts_pending_path_too(self):
        path, pend = self._stripped_pair()
        try:
            apply_pending(pend, no_backup=True, settle_ms=50)
            self.assertNotIn("deep deep", path.read_text())
        finally:
            _cleanup(path)

    def test_apply_dry_run_swaps_nothing(self):
        path, pend = self._stripped_pair()
        try:
            before = path.read_bytes()
            apply_pending(path, dry_run=True, settle_ms=50)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(pend.exists())
        finally:
            _cleanup(path)

    def test_apply_refuses_rewritten_original(self):
        path, pend = self._stripped_pair()
        try:
            # Rewrite (not append) the original → prefix hash mismatch
            lines = path.read_text().splitlines(keepends=True)
            path.write_text("".join(lines[1:]))
            with self.assertRaises(ApplyRefused):
                apply_pending(path, settle_ms=50)
            self.assertTrue(pend.exists())  # nothing consumed on refusal
        finally:
            _cleanup(path)

    def test_apply_refuses_shrunk_original(self):
        path, pend = self._stripped_pair()
        try:
            path.write_text("")
            with self.assertRaises(ApplyRefused):
                apply_pending(path, settle_ms=50)
        finally:
            _cleanup(path)

    def test_apply_refuses_broken_pending(self):
        path, pend = self._stripped_pair()
        try:
            # Dangle the pending's leaf parent → health_check FAIL
            lines = [json.loads(x) for x in pend.read_text().splitlines()]
            lines[-1]["parentUuid"] = "00000000-dead-beef-0000-000000000000"
            pend.write_text("".join(json.dumps(x) + "\n" for x in lines))
            before = path.read_bytes()
            with self.assertRaises(ApplyRefused):
                apply_pending(path, settle_ms=50)
            self.assertEqual(path.read_bytes(), before)
        finally:
            _cleanup(path)

    def test_apply_refuses_while_original_grows(self):
        path, pend = self._stripped_pair()
        stop = threading.Event()

        def churn():
            while not stop.is_set():
                with open(path, "a") as f:
                    f.write('{"type":"progress","note":"live append"}\n')
                time.sleep(0.05)

        t = threading.Thread(target=churn)
        t.start()
        try:
            with self.assertRaises(ApplyRefused) as ctx:
                apply_pending(path, settle_ms=400)
            self.assertIn("being written right now", str(ctx.exception))
        finally:
            stop.set()
            t.join()
            _cleanup(path)

    def test_apply_refuses_missing_pending(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        try:
            with self.assertRaises(ApplyRefused):
                apply_pending(path, settle_ms=50)
        finally:
            _cleanup(path)


if __name__ == "__main__":
    unittest.main()
