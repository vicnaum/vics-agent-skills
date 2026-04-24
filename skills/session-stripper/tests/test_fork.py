"""TDD tests for `session-stripper`'s fork mechanism.

Matches Claude Code's `/branch` convention exactly so forked sessions appear
as siblings in CC's listings and `claude -r <newId>` works out of the box.

Convention pinned by these tests (verified against
`/Users/vicnaum/github/claude-src/commands/branch/branch.ts`):

- New sessionId = randomUUID(); JSONL written to
  `<project-dir>/<newSessionId>.jsonl`.
- Every envelope carries `sessionId = <newSessionId>` AND
  `forkedFrom = {sessionId: <originalSessionId>, messageUuid: <envelope's
  original uuid>}`.
- Root envelope's `parentUuid` is null (matches CC).
- A `custom-title` entry is appended with " (Stripped)" suffix; collisions
  auto-increment as " (Stripped 2)", "(Stripped 3)", ...
- `--fork-title` overrides the default suffix.
- `strippedBy = {tool, operation, at}` field is stamped on every envelope —
  our addition (CC ignores unknown fields).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, assert_chain_valid


def _read_envelopes(p: Path):
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _conversation_envelopes(p: Path):
    """Drop meta entries (custom-title, content-replacement, etc.) — keep
    only the actual user/assistant message chain."""
    return [e for e in _read_envelopes(p) if e.get("type") in ("user", "assistant")]


class TestForkBasics(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "first"),
            ("assistant", "reply"),
            ("user", "second"),
            ("assistant", "another"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        # Clean up any forked siblings the tests may have produced.
        for sib in self.session_path.parent.glob("*.jsonl"):
            if sib != self.session_path:
                sib.unlink()

    def test_fork_creates_new_file_with_new_session_id(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path)
        self.assertTrue(forked.is_file())
        self.assertNotEqual(forked, self.session_path)
        envs = _conversation_envelopes(forked)
        self.assertGreater(len(envs), 0)
        new_sid = envs[0]["sessionId"]
        self.assertNotEqual(new_sid, self.info["session_id"])
        # Every envelope agrees on the new sessionId
        for e in envs:
            self.assertEqual(e["sessionId"], new_sid)

    def test_fork_stamps_forkedFrom_on_every_envelope(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path)
        envs = _conversation_envelopes(forked)
        original_uuids = self.info["uuids"]
        for env, original_uuid in zip(envs, original_uuids):
            self.assertIn("forkedFrom", env, "missing forkedFrom on envelope")
            self.assertEqual(env["forkedFrom"]["sessionId"], self.info["session_id"])
            self.assertEqual(env["forkedFrom"]["messageUuid"], original_uuid,
                             "messageUuid must point at the corresponding parent envelope")

    def test_fork_root_envelope_parentUuid_is_null(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path)
        envs = _conversation_envelopes(forked)
        self.assertIsNone(envs[0]["parentUuid"],
                          "root parentUuid must be null (CC convention)")

    def test_fork_preserves_chain_integrity(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path)
        assert_chain_valid(forked)

    def test_original_session_untouched(self):
        from lib.fork import fork_session
        before = self.session_path.read_text()
        fork_session(self.session_path)
        after = self.session_path.read_text()
        self.assertEqual(before, after, "original session was mutated")


class TestForkCustomTitle(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", "ok"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        for sib in self.session_path.parent.glob("*.jsonl"):
            if sib != self.session_path:
                sib.unlink()

    def test_default_custom_title_includes_stripped_suffix(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path)
        envs = _read_envelopes(forked)
        ct = next((e for e in envs if e.get("type") == "custom-title"), None)
        self.assertIsNotNone(ct, "no custom-title entry written")
        self.assertIn("Stripped", ct["customTitle"])

    def test_custom_title_override(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path, custom_title="Polymarket-only thread")
        envs = _read_envelopes(forked)
        ct = next((e for e in envs if e.get("type") == "custom-title"), None)
        self.assertIsNotNone(ct)
        self.assertEqual(ct["customTitle"], "Polymarket-only thread")

    def test_collision_auto_increments(self):
        from lib.fork import fork_session
        # First fork — suffix " (Stripped)"
        f1 = fork_session(self.session_path)
        # Second fork — should become " (Stripped 2)"
        f2 = fork_session(self.session_path)

        ct1 = next(e for e in _read_envelopes(f1) if e.get("type") == "custom-title")
        ct2 = next(e for e in _read_envelopes(f2) if e.get("type") == "custom-title")
        self.assertNotEqual(ct1["customTitle"], ct2["customTitle"],
                            "collision not resolved")
        # Either the second has " 2" / "(Stripped 2)" / something distinguishing
        self.assertTrue("2" in ct2["customTitle"],
                        f"expected disambiguator in {ct2['customTitle']!r}")


class TestStrippedBy(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "hi"),
            ("assistant", "ok"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        for sib in self.session_path.parent.glob("*.jsonl"):
            if sib != self.session_path:
                sib.unlink()

    def test_strippedBy_on_every_envelope(self):
        from lib.fork import fork_session
        forked = fork_session(self.session_path,
                              operation="persist-range --kinds text --from 0 --to 10")
        envs = _conversation_envelopes(forked)
        for e in envs:
            self.assertIn("strippedBy", e, "strippedBy missing on envelope")
            sb = e["strippedBy"]
            self.assertEqual(sb["tool"], "session-stripper")
            self.assertIn("operation", sb)
            self.assertIn("at", sb)
            self.assertIn("persist-range", sb["operation"])

    def test_strippedBy_optional_when_no_operation(self):
        """When called without an operation hint (e.g. directly via CLI fork
        sub-command not yet wired), strippedBy can be omitted."""
        from lib.fork import fork_session
        forked = fork_session(self.session_path, operation=None)
        envs = _conversation_envelopes(forked)
        for e in envs:
            # strippedBy is allowed but not required when operation=None
            if "strippedBy" in e:
                self.assertEqual(e["strippedBy"]["tool"], "session-stripper")


class TestForkEndToEnd(unittest.TestCase):
    """The killer test: fork + then apply a persist operation to the fork.
    Both files must exist and both must be chain-valid."""

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "first"),
            ("assistant", "x" * 3000),
            ("user", "ack"),
            ("assistant", "y" * 3000),
            ("user", "tail"),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        for sib in self.session_path.parent.glob("*.jsonl"):
            if sib != self.session_path:
                sib.unlink()

    def test_fork_then_persist_range_keeps_both_valid(self):
        from lib.fork import fork_session
        from lib.persist_range import persist_range

        forked = fork_session(self.session_path,
                              operation="persist-range --kinds text --min-chars 1000")
        persist_range(str(forked), from_pos=0, to_pos=4,
                      kinds=("text",), min_chars=1000, no_backup=True)

        # Original untouched
        orig_envs = _conversation_envelopes(self.session_path)
        self.assertEqual(orig_envs[1]["message"]["content"][0]["text"], "x" * 3000,
                         "original was mutated by persist on fork")

        # Forked is modified and chain-valid
        assert_chain_valid(forked)
        forked_envs = _conversation_envelopes(forked)
        forked_text = forked_envs[1]["message"]["content"][0]["text"]
        self.assertIn("<persisted-text>", forked_text,
                      "fork didn't get persisted as expected")


if __name__ == "__main__":
    unittest.main()
