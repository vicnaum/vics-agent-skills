"""Marker contract tests — the wire format every persist command must produce.

These tests establish the contract a downstream consumer (model, script, future
tool) needs to follow to extract structured fields from a `<persisted-X>` marker.
If these pass, anyone using `helpers.MARKER_RE` + `helpers.marker_fields` can
recover the path, size, summary, and preview reliably.

Tests in this file should NOT depend on any specific persist command working —
they exercise the contract only. They use synthetic markers built by hand.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from textwrap import dedent

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import MARKER_RE, marker_fields, resolve_persisted_path


# ── Canonical marker shape, extracted from CC's `<persisted-output>` and
# extended for the new kinds. Tests verify every persist command emits this. ──

CANONICAL_TOOL_RESULT = dedent("""\
    <persisted-output>
    Output too large (98765 chars). Full output saved to: b856d033-4f84-430b-a34c-f6776c4e0fcd/tool-results/toolu_abc.txt

    Preview (first 2KB):
    {"some": "json", "preview": "..."}
    [...]
    </persisted-output>""")

CANONICAL_THINKING = dedent("""\
    <persisted-thinking>
    Saved to: b856d033-4f84-430b-a34c-f6776c4e0fcd/persisted/thinking/0c3a-uuid.txt (4831 chars)
    Summary: Analyzed user's framing of the problem; pivoted to environment-blame.

    Preview:
    The user is sharing a private conversation between himself and Adam...
    </persisted-thinking>""")

CANONICAL_TEXT = (
    "<persisted-text>\n"
    "Saved to: b856d033-4f84-430b-a34c-f6776c4e0fcd/persisted/text/0c3a-uuid_2.txt (12500 chars)\n"
    "Summary: Long verbatim audit report draft.\n"
    "\n"
    "Preview:\n"
    "# Audit findings\n"
    "## M-02 ...\n"
    "</persisted-text>"
)

CANONICAL_MESSAGE = dedent("""\
    <persisted-message>
    Saved to: b856d033-4f84-430b-a34c-f6776c4e0fcd/persisted/message/0c3a-uuid.json (24000 chars)
    Summary: Whole-message persist (chain pos 47).

    Preview:
    [text, thinking, text — first 1KB]
    </persisted-message>""")

CANONICAL_IMAGE = dedent("""\
    <persisted-image sha256="abc123def">
    Saved to: b856d033-4f84-430b-a34c-f6776c4e0fcd/persisted/image/abc123def.txt (2030 chars)
    Summary: Telegram chat with Gus about CTO opportunity.

    Preview:
    Telegram chat with "Gus"...
    </persisted-image>""")


class TestMarkerRegexMatchesAllKinds(unittest.TestCase):
    """The MARKER_RE must recognise every kind in PERSIST_KINDS."""

    def test_matches_persisted_output(self):
        m = MARKER_RE.search(CANONICAL_TOOL_RESULT)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind"), "output")

    def test_matches_persisted_thinking(self):
        m = MARKER_RE.search(CANONICAL_THINKING)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind"), "thinking")

    def test_matches_persisted_text(self):
        m = MARKER_RE.search(CANONICAL_TEXT)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind"), "text")

    def test_matches_persisted_message(self):
        m = MARKER_RE.search(CANONICAL_MESSAGE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind"), "message")

    def test_matches_persisted_image_with_attribute(self):
        """Markers may carry attributes inside the opening tag (e.g. sha256)."""
        m = MARKER_RE.search(CANONICAL_IMAGE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind"), "image")


class TestMarkerFieldsExtraction(unittest.TestCase):
    """Given a marker, marker_fields() returns the documented structured shape."""

    def test_extracts_path_and_size_for_thinking(self):
        f = marker_fields(CANONICAL_THINKING)
        self.assertEqual(f["kind"], "thinking")
        self.assertIn("persisted/thinking/0c3a-uuid.txt", f["path"])
        self.assertEqual(f["size_chars"], 4831)
        self.assertIn("framing", f["summary"])

    def test_extracts_path_and_size_for_tool_result_full_form(self):
        f = marker_fields(CANONICAL_TOOL_RESULT)
        self.assertEqual(f["kind"], "output")
        self.assertIn("tool-results/toolu_abc.txt", f["path"])
        self.assertEqual(f["size_chars"], 98765)

    def test_extracts_path_for_image_marker(self):
        f = marker_fields(CANONICAL_IMAGE)
        self.assertEqual(f["kind"], "image")
        self.assertIn("persisted/image/abc123def.txt", f["path"])

    def test_extracts_summary_for_message(self):
        f = marker_fields(CANONICAL_MESSAGE)
        self.assertIn("Whole-message persist", f["summary"])

    def test_extracts_preview(self):
        f = marker_fields(CANONICAL_THINKING)
        self.assertIsNotNone(f["preview"])
        self.assertIn("private conversation", f["preview"])

    def test_unknown_kind_returns_empty(self):
        bad = "<persisted-foo>oops</persisted-foo>"
        self.assertEqual(marker_fields(bad), {})


class TestMarkerPathSafety(unittest.TestCase):
    """Persisted paths must be safe to pass to file-system tools without
    shell escaping concerns. Absolute paths are forbidden so the marker
    travels with a moved session tree."""

    def test_path_is_relative_in_canonical_markers(self):
        for marker in (CANONICAL_THINKING, CANONICAL_TEXT, CANONICAL_MESSAGE,
                       CANONICAL_IMAGE, CANONICAL_TOOL_RESULT):
            f = marker_fields(marker)
            self.assertFalse(f["path"].startswith("/"),
                             f"absolute path leaked: {f['path']}")

    def test_no_shell_metacharacters_in_path(self):
        unsafe = set('<>|;&$`"\'')
        for marker in (CANONICAL_THINKING, CANONICAL_TEXT, CANONICAL_MESSAGE,
                       CANONICAL_IMAGE, CANONICAL_TOOL_RESULT):
            f = marker_fields(marker)
            offenders = unsafe & set(f["path"])
            self.assertFalse(offenders, f"unsafe chars {offenders} in {f['path']}")


class TestPathResolution(unittest.TestCase):
    """Helpers.resolve_persisted_path(session, advertised) yields a path
    rooted at the session's project directory."""

    def test_resolves_relative_path_under_project_dir(self):
        # imagine session at /tmp/proj/<sid>.jsonl, advertised path 'sid/persisted/thinking/x.txt'
        session = Path("/tmp/proj/abc.jsonl")
        out = resolve_persisted_path(session, "abc/persisted/thinking/x.txt")
        self.assertEqual(out, Path("/tmp/proj/abc/persisted/thinking/x.txt"))


if __name__ == "__main__":
    unittest.main()
