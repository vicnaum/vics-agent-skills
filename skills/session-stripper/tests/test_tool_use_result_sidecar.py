"""REGRESSION: `strip-tools` must clear the `toolUseResult` sidecar too.

Claude Code records every tool result TWICE — the `tool_result` block inside
`message.content[]`, and a top-level `toolUseResult` field on the SAME record.
`strip_tools` only ever walked `message.content[]`, so the sidecar survived
untouched: measured on one stripped session, tool_result blocks were down to
119,427 chars while 349 sidecars still held 756,266.

The sidecar is local replay/display metadata — it does NOT enter the prompt — so
its saving is reported on its own line and deliberately kept OUT of the token
figure, which would otherwise overstate the context that was actually freed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.chain import load_session
from lib.strip_tools import TOOL_USE_RESULT_PLACEHOLDER, strip_tools

TOOL_ID = "toolu_01ABCDEFGHIJKLMNOPQRSTUV"
BIG_STDOUT = "x" * 5000


def _session_with_sidecar():
    """A Bash call whose result is recorded in both places, as CC writes it."""
    path, info = build_session([
        ("user", "run it"),
        ("assistant", [{"type": "tool_use", "id": TOOL_ID, "name": "Bash",
                        "input": {"command": "echo hi"}}]),
        ("user", [{"type": "tool_result", "tool_use_id": TOOL_ID,
                   "content": BIG_STDOUT}]),
        ("assistant", "done"),
    ])
    # Attach the sidecar to the record carrying the tool_result block (line 2).
    objects = load_session(path)
    objects[2]["toolUseResult"] = {
        "stdout": BIG_STDOUT, "stderr": "", "interrupted": False,
        "isImage": False, "noOutputExpected": False,
    }
    with open(path, "w", encoding="utf-8") as f:
        for o in objects:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return path, info


class TestToolUseResultSidecar(unittest.TestCase):
    def test_sidecar_is_stripped_with_its_sibling_block(self):
        path, _ = _session_with_sidecar()
        try:
            stats = strip_tools(str(path), no_backup=True)
            objects = load_session(path)

            self.assertEqual(TOOL_USE_RESULT_PLACEHOLDER, objects[2]["toolUseResult"])
            self.assertEqual(1, stats["tool_use_result_cleared"])
            self.assertNotIn(BIG_STDOUT, path.read_text(encoding="utf-8"),
                             "no copy of the payload may survive anywhere in the file")
        finally:
            path.unlink(missing_ok=True)

    def test_sidecar_saving_is_reported_separately_from_context(self):
        """On-disk bytes must not be folded into the token estimate.

        Strip the SAME session twice — once with the sidecar attached, once
        without — and require an identical context figure. The sidecar is never
        sent to the model, so it must not move `chars_saved` by a single byte.
        """
        with_sidecar, _ = _session_with_sidecar()
        without_sidecar, _ = _session_with_sidecar()
        try:
            objects = load_session(without_sidecar)
            del objects[2]["toolUseResult"]
            with open(without_sidecar, "w", encoding="utf-8") as f:
                for o in objects:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")

            a = strip_tools(str(with_sidecar), no_backup=True)
            b = strip_tools(str(without_sidecar), no_backup=True)

            self.assertEqual(b["chars_saved"], a["chars_saved"],
                             "the sidecar must not change the context saving")
            self.assertEqual(b["est_tokens_saved"], a["est_tokens_saved"])
            self.assertGreater(a["sidecar_chars_saved"], 4000)
            self.assertEqual(0, b["sidecar_chars_saved"])
        finally:
            with_sidecar.unlink(missing_ok=True)
            without_sidecar.unlink(missing_ok=True)

    def test_running_twice_is_a_no_op_on_the_sidecar(self):
        path, _ = _session_with_sidecar()
        try:
            strip_tools(str(path), no_backup=True)
            second = strip_tools(str(path), no_backup=True)
            self.assertEqual(0, second["tool_use_result_cleared"])
            self.assertEqual(0, second["sidecar_chars_saved"])
        finally:
            path.unlink(missing_ok=True)

    def test_only_inputs_leaves_the_sidecar_alone(self):
        """--only-inputs never touches results, so the sidecar must stay."""
        path, _ = _session_with_sidecar()
        try:
            stats = strip_tools(str(path), no_backup=True, only_inputs=True)
            objects = load_session(path)
            self.assertEqual(0, stats["tool_use_result_cleared"])
            self.assertEqual(BIG_STDOUT, objects[2]["toolUseResult"]["stdout"])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
