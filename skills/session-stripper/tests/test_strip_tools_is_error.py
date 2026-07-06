"""Edge-case tests for strip-tools around `is_error` tool_results and
`--keep-last-lines` — the residual gaps surfaced by PRs #1 and #2.

API constraints being defended:
- `tool_result` content cannot be empty when `is_error: true`
  → our approach: drop the `is_error` flag once its message is gone (9b16eac)
- text content blocks must be non-empty
  → keep-last-lines must never leave blank text blocks behind
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, assert_chain_valid

from lib.strip_tools import strip_tools, _keep_last_lines


def _tool_turns(result_content, is_error=True):
    """Assistant tool_use + user tool_result pair."""
    tr = {"type": "tool_result", "tool_use_id": "toolu_01", "content": result_content}
    if is_error:
        tr["is_error"] = True
    return [
        ("user", "run the thing"),
        ("assistant", [{"type": "tool_use", "id": "toolu_01", "name": "Bash",
                        "input": {"command": "false"}}]),
        ("user", [tr]),
    ]


def _result_block(path):
    for line in Path(path).read_text().splitlines():
        obj = json.loads(line)
        for block in (obj.get("message", {}) or {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return block
    raise AssertionError("no tool_result block found")


class TestKeepLastLinesGuard(unittest.TestCase):
    def test_zero_keeps_nothing_not_everything(self):
        # lines[-0:] slices the whole list — the old code made n=0 a no-op
        self.assertEqual(_keep_last_lines("a\nb\nc", 0), "")

    def test_negative_keeps_nothing(self):
        self.assertEqual(_keep_last_lines("a\nb", -3), "")

    def test_positive_still_trims(self):
        self.assertEqual(_keep_last_lines("a\nb\nc", 2), "b\nc")


class TestIsErrorEmptyContent(unittest.TestCase):
    def test_plain_clear_drops_is_error(self):
        path, _ = build_session(_tool_turns("boom: command failed"))
        strip_tools(path, no_backup=True)
        block = _result_block(path)
        self.assertEqual(block["content"], "")
        self.assertNotIn("is_error", block)
        assert_chain_valid(path)

    def test_keep_last_lines_zero_string_drops_is_error(self):
        path, _ = build_session(_tool_turns("boom\nmore boom"))
        strip_tools(path, no_backup=True, keep_last_lines=0)
        block = _result_block(path)
        self.assertEqual(block["content"], "")
        self.assertNotIn("is_error", block)

    def test_all_blank_list_becomes_empty_and_drops_is_error(self):
        # list content whose text blocks all trim to blank must NOT survive as
        # a truthy-but-semantically-empty list (would sneak past the guard AND
        # violate the non-empty-text-block API rule)
        content = [{"type": "text", "text": "x\ny"}, {"type": "text", "text": ""}]
        path, _ = build_session(_tool_turns(content))
        strip_tools(path, no_backup=True, keep_last_lines=0)
        block = _result_block(path)
        self.assertEqual(block["content"], "")
        self.assertNotIn("is_error", block)

    def test_partially_blank_list_keeps_nonblank_and_is_error(self):
        content = [{"type": "text", "text": "tail line"}, {"type": "text", "text": ""}]
        path, _ = build_session(_tool_turns(content))
        strip_tools(path, no_backup=True, keep_last_lines=1)
        block = _result_block(path)
        self.assertEqual(block["content"], [{"type": "text", "text": "tail line"}])
        self.assertTrue(block.get("is_error"))  # content survives → flag stays

    def test_non_error_results_unaffected(self):
        path, _ = build_session(_tool_turns("normal output", is_error=False))
        strip_tools(path, no_backup=True)
        block = _result_block(path)
        self.assertEqual(block["content"], "")
        self.assertNotIn("is_error", block)


if __name__ == "__main__":
    unittest.main()
