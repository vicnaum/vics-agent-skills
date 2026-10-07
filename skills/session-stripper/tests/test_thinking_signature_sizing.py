"""REGRESSION: thinking is sized by text PLUS its encrypted `signature`.

Recent Claude Code sessions store thinking as near-empty `thinking` text and
a signature of several thousand chars. Sizing by text alone made
`strip-thinking`, `show-thinking` and `persist-thinking(s)` report ~0 for
blocks that are most of the thinking weight, while the commands really did
remove them. Measured on a real session: 801 readable chars, 104,624
signature chars across 30 blocks, 26 of them with empty text.

The persisted-thinking marker still advertises the READABLE chars, because
that is what the sidecar file holds. Only the savings count the signature.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, iter_persisted_markers

from lib.persist_tools import (persist_thinking, persist_thinking_bulk,
                               show_thinking)
from lib.strip_thinking import strip_thinking

SIG = "S" * 4000  # stand-in for an encrypted signature


def _session(thinking_text: str = "", sig: str = SIG):
    return build_session([
        ("user", "hi"),
        ("assistant", [{"type": "thinking", "thinking": thinking_text, "signature": sig},
                       {"type": "text", "text": "hello"}]),
        ("user", "more"),
        ("assistant", "sure"),
    ])


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self.path, self.info = _session()

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        side = self.path.with_suffix("")
        shutil.rmtree(side, ignore_errors=True)
        for bak in self.path.parent.glob(self.path.name + ".bak*"):
            bak.unlink(missing_ok=True)


class TestStripThinkingCountsSignature(_Base):
    def test_signature_only_block_counts_as_saved(self):
        stats, out = _quiet(strip_thinking, str(self.path), dry_run=True)
        self.assertGreaterEqual(stats["chars_saved"], len(SIG))
        self.assertIn(f"Characters saved: {len(SIG):,}", out)

    def test_redacted_thinking_signature_counted(self):
        path, _ = build_session([
            ("user", "hi"),
            ("assistant", [{"type": "redacted_thinking", "data": "D" * 100, "signature": SIG},
                           {"type": "text", "text": "hello"}]),
        ])
        try:
            stats, _ = _quiet(strip_thinking, str(path), dry_run=True)
            self.assertEqual(stats["chars_saved"], 100 + len(SIG))
        finally:
            path.unlink(missing_ok=True)


class TestShowThinkingCountsSignature(_Base):
    def test_list_mode_sizes_signature_and_labels_it(self):
        rows, _ = _quiet(show_thinking, str(self.path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thinking_chars"], len(SIG))
        self.assertIn("encrypted signature only", rows[0]["preview"])

    def test_detail_mode_reports_signature_share(self):
        res, out = _quiet(show_thinking, str(self.path), chain_pos=1)
        self.assertEqual(res["thinking_chars"], len(SIG))
        self.assertIn(f"of which {len(SIG):,} signature", out)
        self.assertIn("encrypted signature only", out)


class TestPersistThinkingCountsSignature(_Base):
    def test_single_savings_include_signature_marker_shows_readable(self):
        path, _ = _session(thinking_text="readable " * 10)
        try:
            res, _ = _quiet(persist_thinking, str(path), 1, no_backup=True)
            self.assertGreater(res["chars_saved"], len(SIG) - 1000)
            self.assertEqual(res["original_chars"], len("readable " * 10))
            markers = [m for m in iter_persisted_markers(path)
                       if "<persisted-thinking>" in json.dumps(m)]
            self.assertTrue(markers)
            self.assertIn(f"({len('readable ' * 10)} chars)", json.dumps(markers))
        finally:
            path.unlink(missing_ok=True)
            shutil.rmtree(path.with_suffix(""), ignore_errors=True)

    def test_bulk_savings_include_signature(self):
        stats, _ = _quiet(persist_thinking_bulk, str(self.path), dry_run=True)
        self.assertEqual(stats["persisted_count"], 1)
        self.assertGreater(stats["chars_saved"], len(SIG) - 1000)


if __name__ == "__main__":
    unittest.main()
