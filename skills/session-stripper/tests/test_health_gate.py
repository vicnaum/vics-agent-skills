"""REGRESSION: the `verify` gate must fail on real breakage, not on conditions
that exist in healthy untouched sessions.

Two defects met here.

Bug 3 — the timestamp-ordering check was a HARD failure, so `apply` refused a
strip whose copy had 6 out-of-order timestamps while the untouched original had
46 of the same violations: a pre-existing condition blocked all work. The cause
is NOT millisecond ties (the check uses strict `<`, so ties already pass), and
NOT chain order diverging from file order — measured across 25 real sessions,
418 violations, file order and chain order agreed in every one. CC stamps a
record when it is CONSTRUCTED but appends it after the record it hangs off
(71% are user -> attachment pairs), and session resume/fork re-links records
that keep much older timestamps. Both are normal, so ordering is now a warning.

Bug 1 — `verify` walked only the active chain, so a record whose parent had been
deleted simply fell OUT of the walk and the shortened chain left behind was
self-consistent. That is how PASS was printed on both sides of a break that
orphaned 1,139 messages. A file-wide dangling-parent scan now reports the
orphan count. It is a WARNING, not a failure: 2 of 25 real sessions carry one
benign dangling parent (resume/fork leaves it in a previous session file).
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.analyze import health_check
from lib.chain import load_session


def _rewrite(path, objects):
    with open(path, "w", encoding="utf-8") as f:
        for o in objects:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def _scenario():
    return build_session([
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "more"),
        ("assistant", "sure"),
    ])


def _run(path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = health_check(str(path))
    return ok, buf.getvalue()


class TestTimestampOrderingIsNotFatal(unittest.TestCase):
    def test_out_of_order_timestamps_still_pass(self):
        path, _ = _scenario()
        try:
            objects = load_session(path)
            # Stamp a record two minutes BEFORE its parent — the resume/fork
            # shape, far larger than any millisecond tie.
            objects[2]["timestamp"] = "2025-12-31T23:58:00.000Z"
            _rewrite(path, objects)

            ok, out = _run(path)
            self.assertTrue(ok, "a pre-existing ordering violation must not gate a strip")
            self.assertIn("PASS", out)
            self.assertIn("Timestamp out of order", out)
            self.assertIn("[WARN]", out)
        finally:
            path.unlink(missing_ok=True)

    def test_equal_timestamps_still_pass(self):
        """Ties were never the cause; assert they stay clean, with no warning."""
        path, _ = _scenario()
        try:
            objects = load_session(path)
            objects[2]["timestamp"] = objects[1]["timestamp"]
            _rewrite(path, objects)

            ok, out = _run(path)
            self.assertTrue(ok)
            self.assertNotIn("Timestamp out of order", out)
        finally:
            path.unlink(missing_ok=True)


class TestBreakageStillFails(unittest.TestCase):
    def test_dangling_parent_on_active_chain_is_fatal(self):
        path, _ = _scenario()
        try:
            objects = load_session(path)
            objects[-1]["parentUuid"] = "00000000-dead-beef-0000-000000000000"
            _rewrite(path, objects)

            ok, out = _run(path)
            self.assertFalse(ok, "a severed active chain must still block apply")
            self.assertIn("FAIL", out)
        finally:
            path.unlink(missing_ok=True)

    def test_inconsistent_slug_is_fatal(self):
        path, _ = _scenario()
        try:
            objects = load_session(path)
            objects[2]["slug"] = "a-different-session"
            _rewrite(path, objects)

            ok, _ = _run(path)
            self.assertFalse(ok)
        finally:
            path.unlink(missing_ok=True)


class TestOrphansAreSurfaced(unittest.TestCase):
    def test_orphaned_history_is_reported_with_a_count(self):
        """The incident's signature: verify printed PASS while history hung off
        a dangling parent. It must now say so, and say how much."""
        path, info = _scenario()
        try:
            objects = load_session(path)
            # Sever the middle: keep 0 and 1 reachable, orphan 2 and 3 behind a
            # parent that no longer exists. The leaf walk still succeeds.
            objects[2]["parentUuid"] = "00000000-dead-beef-0000-000000000000"
            objects.append({
                "parentUuid": objects[1]["uuid"], "isSidechain": False,
                "userType": "external", "cwd": info["cwd"],
                "sessionId": info["session_id"], "version": "2.1.114",
                "gitBranch": "master", "slug": info["slug"], "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "new leaf"}]},
                "uuid": "99999999-8888-7777-6666-555555555555",
                "timestamp": "2026-01-01T00:00:09.000Z",
            })
            _rewrite(path, objects)

            ok, out = _run(path)
            self.assertTrue(ok, "off-chain orphans warn, they do not fail")
            self.assertIn("Dangling parentUuid off the active chain", out)
            self.assertIn("2 record(s) unreachable behind it", out)
        finally:
            path.unlink(missing_ok=True)

    def test_clean_session_says_nothing(self):
        path, _ = _scenario()
        try:
            ok, out = _run(path)
            self.assertTrue(ok)
            self.assertNotIn("[WARN]", out)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
