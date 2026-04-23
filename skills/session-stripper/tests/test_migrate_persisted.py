"""TDD tests for migrate-persisted — one-shot migration of pre-PR layout.

Old layout: `<project-dir>/.tool-results/<id>.txt` (project-scoped, dot-prefix)
            and `<image sha256="...">...</image>` markers.

New layout: `<sessionId>/tool-results/<id>.txt` and `<sessionId>/persisted/<kind>/`
            with `<persisted-X>` markers.

The migration must:
  1. Move sidecar files to the new location.
  2. Rewrite markers in the JSONL to point to the new path.
  3. Switch <image ...> → <persisted-image ...>.
  4. Leave the chain valid.
  5. Be idempotent (running twice = no-op).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, iter_persisted_markers, assert_chain_valid


class TestMigrationOldImageMarker(unittest.TestCase):
    """Old <image sha256="..."> markers should migrate to <persisted-image>."""

    def setUp(self):
        # Build a session whose assistant turn already contains an old-shape marker
        self.session_path, self.info = build_session([
            ("user", "look"),
            ("assistant", [
                {"type": "text", "text":
                    '<image sha256="abc123">\n[transcript content here]\n</image>'},
            ]),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_old_marker_rewritten_to_persisted_form(self):
        from lib.migrate_persisted import migrate_persisted
        migrate_persisted(self.session_path)

        with open(self.session_path) as f:
            text = f.read()
        self.assertNotIn("<image sha256=", text,
                         "old <image ...> marker survived migration")
        self.assertIn("<persisted-image", text,
                      "no <persisted-image> in migrated session")

    def test_chain_still_valid(self):
        from lib.migrate_persisted import migrate_persisted
        migrate_persisted(self.session_path)
        assert_chain_valid(self.session_path)


class TestMigrationOldDotToolResults(unittest.TestCase):
    """Sidecar files in `.tool-results/` (project-scoped, dot-prefix) should
    move into `<sessionId>/tool-results/`."""

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "do thing"),
            ("assistant", [
                {"type": "tool_use", "id": "toolu_X", "name": "Bash",
                 "input": {"command": "ls"}},
            ]),
            ("user", [
                {"type": "tool_result", "tool_use_id": "toolu_X",
                 "content": (
                     "<persisted-output>\n"
                     "Output too large (50000 chars). Full output saved to: "
                     ".tool-results/toolu_X.txt\n"
                     "\n"
                     "Preview (first 2KB):\nfoo\n"
                     "</persisted-output>"
                 )},
            ]),
            ("assistant", "noted"),
        ])
        # Materialize the old sidecar
        self.old_sidecar = self.session_path.parent / ".tool-results" / "toolu_X.txt"
        self.old_sidecar.parent.mkdir(exist_ok=True)
        self.old_sidecar.write_text("the original tool output content")

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        # Clean up old + new locations
        if self.old_sidecar.exists():
            self.old_sidecar.unlink()
        if self.old_sidecar.parent.exists() and not any(self.old_sidecar.parent.iterdir()):
            self.old_sidecar.parent.rmdir()
        new_dir = self.session_path.parent / self.info["session_id"]
        if new_dir.exists():
            import shutil
            shutil.rmtree(new_dir)

    def test_sidecar_moved_to_new_location(self):
        from lib.migrate_persisted import migrate_persisted
        migrate_persisted(self.session_path)
        new_sidecar = (self.session_path.parent /
                       self.info["session_id"] / "tool-results" / "toolu_X.txt")
        self.assertTrue(new_sidecar.is_file(),
                        f"sidecar not moved to {new_sidecar}")
        self.assertEqual(new_sidecar.read_text(),
                         "the original tool output content")

    def test_marker_path_rewritten(self):
        from lib.migrate_persisted import migrate_persisted
        migrate_persisted(self.session_path)
        with open(self.session_path) as f:
            text = f.read()
        self.assertNotIn(".tool-results/toolu_X.txt", text,
                         "old path string survived in JSONL")
        # Either form acceptable as long as path points to new location
        self.assertIn("/tool-results/toolu_X.txt", text)


class TestMigrationIdempotency(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", "look"),
            ("assistant", [
                {"type": "text", "text":
                    '<image sha256="abc">\nx\n</image>'},
            ]),
        ])

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)

    def test_second_run_no_changes(self):
        from lib.migrate_persisted import migrate_persisted
        migrate_persisted(self.session_path)
        first = self.session_path.read_text()
        migrate_persisted(self.session_path)
        second = self.session_path.read_text()
        self.assertEqual(first, second, "second migration mutated session")


if __name__ == "__main__":
    unittest.main()
