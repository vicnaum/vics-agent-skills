"""Tests for `resolve_current_session` — the $CLAUDE_CODE_SESSION_ID-based
resolver that replaces mtime/`ls -t` guessing (which strips the wrong session
in multi-session project dirs)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from stripper import resolve_current_session


class TestResolveCurrentSession(unittest.TestCase):
    def setUp(self):
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.projects = self.home / ".claude" / "projects"
        self.sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    def tearDown(self):
        self._tmp.cleanup()

    def _mk(self, encoded_dir):
        d = self.projects / encoded_dir
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{self.sid}.jsonl"
        f.write_text("{}\n")
        return f

    def test_missing_env_var_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(Path, "home", return_value=self.home):
            with self.assertRaises(RuntimeError):
                resolve_current_session()

    def test_resolves_from_cwd_encoded_dir(self):
        want = self._mk("-Users-x-github-proj")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": self.sid}), \
             mock.patch.object(Path, "home", return_value=self.home):
            got = resolve_current_session(cwd="/Users/x/github/proj")
        self.assertEqual(got, want)
        self.assertTrue(got.exists())

    def test_dir_agnostic_fallback_when_cwd_differs(self):
        # session lives under a DIFFERENT encoded dir than the current cwd
        want = self._mk("-Users-x-where-it-started")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": self.sid}), \
             mock.patch.object(Path, "home", return_value=self.home):
            got = resolve_current_session(cwd="/Users/x/somewhere/else")
        # unique-UUID glob finds the one real file regardless of cwd
        self.assertEqual(got, want)

    def test_no_match_returns_cwd_guess_not_exists(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": self.sid}), \
             mock.patch.object(Path, "home", return_value=self.home):
            got = resolve_current_session(cwd="/Users/x/proj")
        self.assertFalse(got.exists())
        self.assertTrue(str(got).endswith(f"{self.sid}.jsonl"))


if __name__ == "__main__":
    unittest.main()
