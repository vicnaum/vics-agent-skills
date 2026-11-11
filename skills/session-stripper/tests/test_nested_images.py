"""REGRESSION: images a tool returned sit INSIDE the tool_result block.

Read on a .png/.jpg, screenshots and MCP image tools put the image in the
`tool_result` block's `content` list, not at the top level of the message.
Every image path in session-stripper looked only at top-level blocks. On
2026-11-10 `list-images` found 1 of 139 images in a session, `analyze` priced
the other 138 as tool-result TEXT (their base64 at chars/4, ~2.4M "tokens"),
and `strip-tools --keep-last-lines` kept every one of them.

Images are priced by Anthropic's pixel formula on the session model's tier:
a 1260×990 frame is 45×36 = 1,620 tokens on claude-opus-5-5, whatever its
base64 size.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

from lib.analyze import analyze_session
from lib.chain import compute_active_chain_tokens, load_session, save_session
from lib.image_tokens import STANDARD, anthropic_image_tokens
from lib.replace_images import list_images, replace_images
from lib.strip_tools import strip_tools

READ_ID = "toolu_01READIMAGEAAAAAAAAAAAAA"
W, H = 1260, 990
HI_TOKENS = 45 * 36          # 1620, no resize on the high-resolution tier
PAD = 200_000                # makes the base64 ~267k chars: 66k "tokens" at chars/4


def _png_b64(w, h, pad=PAD):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return base64.b64encode(sig + ihdr + b"\x00" * (8 + pad)).decode()


def _image(data):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": data}}


def _session(model="claude-opus-5-5", with_text=True):
    """A Read of an image file, recorded the way Claude Code records it."""
    data = _png_b64(W, H)
    result = ([{"type": "text", "text": "line one\nline two"}] if with_text else []) + [_image(data)]
    path, info = build_session([
        ("user", "look at the frame"),
        ("assistant", [{"type": "tool_use", "id": READ_ID, "name": "Read",
                        "input": {"file_path": "/tmp/frame.png"}}]),
        ("user", [{"type": "tool_result", "tool_use_id": READ_ID, "content": result}]),
        ("assistant", "it is a window"),
    ])
    objects = load_session(path)
    for o in objects:
        if o.get("type") == "assistant":
            o["message"]["model"] = model
    save_session(path, objects, create_backup=False)
    return path, data


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _result_block(path):
    for o in load_session(path):
        content = (o.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return b
    raise AssertionError("no tool_result")


class _Base(unittest.TestCase):
    def tearDown(self):
        for p in getattr(self, "_paths", []):
            p.unlink(missing_ok=True)
            shutil.rmtree(p.with_suffix(""), ignore_errors=True)

    def session(self, **kw):
        path, data = _session(**kw)
        self._paths = getattr(self, "_paths", []) + [path]
        return path, data


class TestListImages(_Base):
    def test_finds_image_inside_tool_result(self):
        path, _ = self.session()
        rows = _quiet(list_images, str(path))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["nested"])
        self.assertEqual(rows[0]["where"], "tool_result:Read")
        self.assertEqual(rows[0]["dims"], (W, H))
        self.assertEqual(rows[0]["tokens"], HI_TOKENS)

    def test_standard_tier_model_prices_by_standard_limits(self):
        path, _ = self.session(model="claude-opus-4-6")
        rows = _quiet(list_images, str(path))
        self.assertEqual(rows[0]["tokens"], anthropic_image_tokens(W, H, STANDARD))
        self.assertLess(rows[0]["tokens"], HI_TOKENS)  # 1620 > 1568 cap → resized


class TestReplaceImages(_Base):
    def _transcripts(self, data):
        import hashlib
        d = Path(tempfile.mkdtemp(prefix="ss-nested-tx-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        sha = hashlib.sha256(base64.b64decode(data)).hexdigest()
        (d / f"{sha}.txt").write_text("a window, half open")
        return d

    def test_replaces_nested_image_with_marker_inside_the_result(self):
        path, data = self.session()
        stats = _quiet(replace_images, str(path), self._transcripts(data), no_backup=True)
        self.assertEqual(stats["replaced"], 1)
        self.assertEqual(stats["replaced_nested"], 1)
        self.assertEqual(stats["image_tokens_removed"], HI_TOKENS)
        block = _result_block(path)
        types = [b.get("type") for b in block["content"]]
        self.assertEqual(types, ["text", "text"])
        self.assertIn("<persisted-image", block["content"][1]["text"])
        self.assertNotIn(data[:200], path.read_text())

    def test_drop_missing_empties_an_image_only_result(self):
        path, data = self.session(with_text=False)
        empty = Path(tempfile.mkdtemp(prefix="ss-nested-empty-"))
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        stats = _quiet(replace_images, str(path), empty, no_backup=True, drop_missing=True)
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(_result_block(path)["content"], "")


class TestStripTools(_Base):
    def test_counts_and_prices_nested_images(self):
        path, _ = self.session()
        stats = _quiet(strip_tools, str(path), no_backup=True)
        self.assertEqual(stats["images_cleared"], 1)
        self.assertEqual(stats["nested_images_cleared"], 1)
        self.assertEqual(stats["image_tokens_saved"], HI_TOKENS)
        # The token figure prices the image by pixels, not its 267k-char base64.
        self.assertLess(stats["est_tokens_saved"], HI_TOKENS + 200)
        self.assertGreater(stats["chars_saved"], PAD)  # on-disk chars still reported

    def test_keep_last_lines_drops_nested_images(self):
        path, data = self.session()
        stats = _quiet(strip_tools, str(path), no_backup=True, keep_last_lines=1)
        block = _result_block(path)
        self.assertEqual(block["content"], [{"type": "text", "text": "line two"}])
        self.assertEqual(stats["images_cleared"], 1)
        self.assertNotIn(data[:200], path.read_text())


class TestAnalyze(_Base):
    def test_counts_nested_image_by_formula(self):
        path, _ = self.session()
        stats = _quiet(analyze_session, str(path), show_cut_points=False)
        self.assertEqual(stats["image_count"], 1)
        self.assertEqual(stats["image_nested_count"], 1)
        self.assertEqual(stats["token_totals"]["image"], HI_TOKENS)
        # The base64 is no longer charged to tool_result.
        self.assertLess(stats["token_totals"]["tool_result"], 200)
        read = stats["tool_breakdown"]["Read"]
        self.assertEqual(read["result_images"], 1)
        self.assertEqual(read["result_image_tokens"], HI_TOKENS)
        self.assertLess(read["result_tokens"], HI_TOKENS + 200)


class TestGaugeEstimate(_Base):
    def test_gauge_prices_nested_image_by_formula(self):
        """compute_active_chain_tokens feeds the context-gauge reset. Pricing
        base64 as text here would pin the gauge far past the real size."""
        path, _ = self.session()
        est = compute_active_chain_tokens(load_session(path))
        self.assertGreaterEqual(est, HI_TOKENS)
        self.assertLess(est, HI_TOKENS + 500)

    def test_gauge_prices_top_level_image_by_formula(self):
        data = _png_b64(W, H)
        path, _ = build_session([("user", [{"type": "text", "text": "pasted"}, _image(data)]),
                                 ("assistant", "ok")])
        self._paths = getattr(self, "_paths", []) + [path]
        objects = load_session(path)
        objects[-1]["message"]["model"] = "claude-opus-5-5"
        est = compute_active_chain_tokens(objects)
        self.assertLess(est, HI_TOKENS + 500)


if __name__ == "__main__":
    unittest.main()
