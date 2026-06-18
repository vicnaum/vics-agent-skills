"""Tests for the context-gauge fix and enumerated backups.

Background: CC's "context left" meter reads the token counts stored on the last
assistant turn (input_tokens + cache_read_input_tokens + cache_creation_input_tokens),
NOT a live recount. So stripping content leaves the meter pinned at the pre-strip
size and CC keeps blocking input. `reset_usage_metadata` rewrites those stale
counts to the post-strip active-chain estimate.

Also pins the enumerated-backup contract: `save_session` never silently skips a
backup just because a `.bak` already exists.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, assert_chain_valid

from lib.chain import (
    load_session,
    save_session,
    reset_usage_metadata,
    compute_active_chain_tokens,
)


def _set_usage(env, *, inp=0, cache_r=0, cache_c=0, out=0):
    env["message"]["usage"] = {
        "input_tokens": inp,
        "cache_read_input_tokens": cache_r,
        "cache_creation_input_tokens": cache_c,
        "output_tokens": out,
    }


def _ctx(env):
    u = env["message"].get("usage") or {}
    return (u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0))


class ResetUsageTest(unittest.TestCase):
    def _build(self):
        # Big filler so the active-chain estimate is comfortably > a few thousand
        # tokens (otherwise a "small" recorded usage would itself exceed target).
        filler = "word " * 20_000  # ~25k tokens of content
        path, info = build_session([
            ("user", "hi"),
            ("assistant", "small early turn"),
            ("user", filler),
            ("assistant", "bloated middle turn"),
            ("user", "even more"),
            ("assistant", "bloated leaf turn"),
        ])
        return path, info

    def test_caps_bloated_and_pins_leaf(self):
        path, _ = self._build()
        objs = load_session(path)
        asst = [o for o in objs if o.get("type") == "assistant"]
        _set_usage(asst[0], inp=2_000)                 # small — must stay
        _set_usage(asst[1], cache_r=900_000, inp=5_000)  # bloated — must cap
        _set_usage(asst[2], cache_r=950_000, inp=5_000)  # bloated leaf — pin
        save_session(path, objs, create_backup=False)

        objs = load_session(path)
        target = compute_active_chain_tokens(objs)
        self.assertGreater(target, 0)
        n = reset_usage_metadata(objs, target)
        self.assertGreaterEqual(n, 2)

        asst = [o for o in objs if o.get("type") == "assistant"]
        # no turn exceeds the target anymore
        for a in asst:
            self.assertLessEqual(_ctx(a), target)
        # small early turn untouched
        self.assertEqual(asst[0]["message"]["usage"]["input_tokens"], 2_000)
        # leaf pinned exactly to target, caches zeroed
        leaf_u = asst[-1]["message"]["usage"]
        self.assertEqual(leaf_u["input_tokens"], target)
        self.assertEqual(leaf_u["cache_read_input_tokens"], 0)
        self.assertEqual(leaf_u["cache_creation_input_tokens"], 0)

    def test_leaf_with_zero_usage_gets_pinned(self):
        # Mirrors a blocked "Prompt is too long" leaf turn (usage recorded as 0).
        path, _ = self._build()
        objs = load_session(path)
        asst = [o for o in objs if o.get("type") == "assistant"]
        _set_usage(asst[1], cache_r=900_000)
        _set_usage(asst[2], inp=0)  # leaf: zero
        save_session(path, objs, create_backup=False)

        objs = load_session(path)
        target = compute_active_chain_tokens(objs)
        reset_usage_metadata(objs, target)
        asst = [o for o in objs if o.get("type") == "assistant"]
        self.assertEqual(asst[-1]["message"]["usage"]["input_tokens"], target)

    def test_idempotent(self):
        path, _ = self._build()
        objs = load_session(path)
        asst = [o for o in objs if o.get("type") == "assistant"]
        _set_usage(asst[2], cache_r=900_000)
        target = compute_active_chain_tokens(objs)
        reset_usage_metadata(objs, target)
        first = _ctx([o for o in objs if o.get("type") == "assistant"][-1])
        reset_usage_metadata(objs, target)  # second pass
        second = _ctx([o for o in objs if o.get("type") == "assistant"][-1])
        self.assertEqual(first, second, "re-running reset must be a no-op on the meter")

    def test_chain_still_valid(self):
        path, _ = self._build()
        objs = load_session(path)
        for a in (o for o in objs if o.get("type") == "assistant"):
            _set_usage(a, cache_r=900_000)
        reset_usage_metadata(objs, compute_active_chain_tokens(objs))
        save_session(path, objs, create_backup=False)
        assert_chain_valid(path)


class EnumeratedBackupTest(unittest.TestCase):
    def test_backup_never_skipped(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        objs = load_session(path)

        save_session(path, objs, create_backup=True)
        bak = Path(str(path) + ".bak")
        self.assertTrue(bak.exists(), "first run must create .bak")

        # second run must NOT skip — it enumerates to .bak.1
        save_session(path, objs, create_backup=True)
        bak1 = Path(str(path) + ".bak.1")
        self.assertTrue(bak1.exists(), "second run must create .bak.1, not skip")

        # third run -> .bak.2
        save_session(path, objs, create_backup=True)
        self.assertTrue(Path(str(path) + ".bak.2").exists())

    def test_no_backup_flag_skips(self):
        path, _ = build_session([("user", "hi"), ("assistant", "yo")])
        objs = load_session(path)
        save_session(path, objs, create_backup=False)
        self.assertFalse(Path(str(path) + ".bak").exists())


if __name__ == "__main__":
    unittest.main()
