"""Directory layout tests — where persisted files end up on disk.

After this PR's implementation lands, the layout must be:

    ~/.claude/projects/<encoded-cwd>/<sessionId>/
    ├── tool-results/<tool_use_id>.txt    ← shared with CC native (kind=tool)
    └── persisted/<kind>/...               ← session-stripper namespace
        ├── thinking/<msg_uuid>.txt
        ├── text/<msg_uuid>_<idx>.txt
        ├── image/<sha256>.txt
        └── message/<msg_uuid>.json

The OLD layout used `.tool-results/` SIBLING to the JSONL, project-scoped, dot-
prefixed. Legacy markers pointing to the old layout must still resolve via a
`migrate-persisted` command.

These tests assume the implementation lives at `lib/persist_layout.py` with a
`persist_dir(session_path, kind)` helper. They will fail (ImportError or
AssertionError) until that helper exists with the documented contract.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session


class TestPersistDirLayout(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([("user", "hi")])
        self.session_id = self.info["session_id"]

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_tool_kind_uses_cc_native_dir(self):
        """kind='tool' must write into <sessionId>/tool-results/ (CC's dir)."""
        from lib.persist_layout import persist_dir
        out = persist_dir(self.session_path, "tool")
        expected_suffix = Path(self.session_id) / "tool-results"
        self.assertTrue(str(out).endswith(str(expected_suffix)),
                        f"got {out}, expected to end with {expected_suffix}")

    def test_other_kinds_use_persisted_subdir(self):
        from lib.persist_layout import persist_dir
        for kind in ("thinking", "text", "image", "message"):
            out = persist_dir(self.session_path, kind)
            expected_suffix = Path(self.session_id) / "persisted" / kind
            self.assertTrue(str(out).endswith(str(expected_suffix)),
                            f"kind={kind}: got {out}")

    def test_dirs_are_created(self):
        """persist_dir() must mkdir parents=True."""
        from lib.persist_layout import persist_dir
        out = persist_dir(self.session_path, "thinking")
        self.assertTrue(out.is_dir(), f"{out} not created")

    def test_dir_is_session_scoped_not_project_scoped(self):
        """Two sessions in the same project must not collide."""
        from lib.persist_layout import persist_dir
        s2, info2 = build_session([("user", "hi")], cwd=self.info["cwd"])
        try:
            d1 = persist_dir(self.session_path, "thinking")
            d2 = persist_dir(s2, "thinking")
            self.assertNotEqual(d1, d2,
                                "dirs collided across sessions in same cwd")
        finally:
            s2.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
