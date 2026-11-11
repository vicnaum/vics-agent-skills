"""Tests for the Anthropic-accurate image token estimator.

Verifies the formula against the published table in Anthropic's vision docs
(both resolution tiers), the resize rule, model → tier mapping, the dimension
parsers (PNG/JPEG/WebP/GIF), nested-image discovery, and the content-block
helper. Uses hand-crafted minimal valid headers — no external images, no
Pillow.
"""

from __future__ import annotations

import base64
import struct
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session  # noqa: F401 — sets up sys.path for lib imports

from lib.image_tokens import (
    HIGH_RES,
    STANDARD,
    anthropic_image_tokens,
    content_image_stats,
    count_image_tokens,
    image_dims_from_base64,
    image_dims_from_bytes,
    image_block_tokens,
    iter_image_blocks,
    resized_size,
    tier_for_model,
)


# ── Minimal valid image-header builders ────────────────────────────────────

def _png_bytes(w: int, h: int) -> bytes:
    """Smallest possible PNG header that carries width/height. Doesn't need
    to be a decodable image — only the IHDR is parsed."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return sig + ihdr + b"\x00" * 8  # padding so len > 24


def _gif_bytes(w: int, h: int, version: bytes = b"GIF89a") -> bytes:
    return version + struct.pack("<HH", w, h) + b"\x00" * 16


def _webp_vp8x_bytes(w: int, h: int) -> bytes:
    """Extended WebP container — w-1 and h-1 are encoded as 3-byte LE."""
    riff = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"
    chunk = b"VP8X" + b"\x00\x00\x00\x0a" + b"\x00" * 4
    wm1 = w - 1
    hm1 = h - 1
    canvas = bytes([wm1 & 0xFF, (wm1 >> 8) & 0xFF, (wm1 >> 16) & 0xFF,
                    hm1 & 0xFF, (hm1 >> 8) & 0xFF, (hm1 >> 16) & 0xFF])
    return riff + chunk + canvas + b"\x00" * 4


def _webp_vp8l_bytes(w: int, h: int) -> bytes:
    """Lossless WebP — packed (w-1)|(h-1)<<14 LE u32 after a 1-byte signature."""
    riff = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"
    chunk = b"VP8L" + b"\x00\x00\x00\x05"
    wm1 = w - 1
    hm1 = h - 1
    packed = (wm1 & 0x3FFF) | ((hm1 & 0x3FFF) << 14)
    payload = b"\x2f" + struct.pack("<I", packed)
    return riff + chunk + payload + b"\x00" * 4


def _jpeg_bytes(w: int, h: int) -> bytes:
    """Minimal JPEG header that lands an SOF0 marker carrying dimensions."""
    soi = b"\xff\xd8"
    # Skip a small APP0 segment so the parser has to walk
    app0_len = 2 + 14
    app0 = b"\xff\xe0" + struct.pack(">H", app0_len) + b"JFIF\x00" + b"\x00" * 9
    sof0_len = 2 + 1 + 2 + 2 + 1 + 3
    sof0 = b"\xff\xc0" + struct.pack(">H", sof0_len) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x00\x00\x00"
    eoi = b"\xff\xd9"
    return soi + app0 + sof0 + eoi


# ── Formula tests ──────────────────────────────────────────────────────────


class TestAnthropicFormula(unittest.TestCase):
    """The "Resolution and token cost" table in Anthropic's vision docs:
    tokens = ceil(w/28) × ceil(h/28) after resizing to the tier's limits."""

    # (width, height, standard-tier tokens, high-resolution-tier tokens)
    DOCS_TABLE = [
        (200, 200, 64, 64),
        (1000, 1000, 1296, 1296),
        (1092, 1092, 1521, 1521),
        (1920, 1080, 1560, 2691),
        (2000, 1500, 1564, 3888),
        (3840, 2160, 1560, 4784),
    ]

    def test_docs_table_both_tiers(self):
        for w, h, std, hi in self.DOCS_TABLE:
            with self.subTest(size=f"{w}x{h}"):
                self.assertEqual(anthropic_image_tokens(w, h, STANDARD), std)
                self.assertEqual(anthropic_image_tokens(w, h, HIGH_RES), hi)

    def test_default_tier_is_high_res(self):
        # Claude Code's current models are all high-res; under-counting their
        # images is what let a 913k-token session look like 174k.
        self.assertEqual(anthropic_image_tokens(3840, 2160), 4784)

    def test_patch_count(self):
        self.assertEqual(count_image_tokens(28, 28), 1)
        self.assertEqual(count_image_tokens(29, 28), 2)
        self.assertEqual(count_image_tokens(1260, 990), 45 * 36)

    def test_zero_or_negative_returns_one(self):
        self.assertEqual(anthropic_image_tokens(0, 0), 1)
        self.assertEqual(anthropic_image_tokens(-5, 100), 1)

    def test_tier_caps(self):
        self.assertEqual(anthropic_image_tokens(10000, 10000, HIGH_RES), 4761)  # 1932² → 69×69
        self.assertLessEqual(anthropic_image_tokens(10000, 10000, STANDARD), 1568)
        self.assertLessEqual(anthropic_image_tokens(8000, 300, HIGH_RES), 4784)


class TestResizedSize(unittest.TestCase):
    """Reference examples from "How Claude resizes and pads images"."""

    def test_a4_scan_resized_on_standard_tier(self):
        # Both sides under 1568 px, but 39×55 = 2145 tokens > 1568.
        self.assertEqual(resized_size(1075, 1520, STANDARD), (924, 1307))

    def test_a4_scan_untouched_on_high_res_tier(self):
        self.assertEqual(resized_size(1075, 1520, HIGH_RES), (1075, 1520))

    def test_1080p_screenshot(self):
        self.assertEqual(resized_size(1920, 1080, STANDARD), (1456, 819))
        self.assertEqual(resized_size(1920, 1080, HIGH_RES), (1920, 1080))

    def test_4k_on_high_res(self):
        self.assertEqual(resized_size(3840, 2160, HIGH_RES), (2576, 1449))

    def test_tall_image_uses_edge_limit(self):
        w, h = resized_size(500, 6000, HIGH_RES)
        self.assertLessEqual(-(-h // 28) * 28, HIGH_RES.max_edge)
        self.assertGreater(h, w)


class TestTierForModel(unittest.TestCase):

    def test_high_res_models(self):
        for m in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                  "claude-opus-4-7", "claude-sonnet-5-5", "claude-sonnet-5",
                  "claude-haiku-5-5", "claude-fable-5-2", "claude-mythos-5-1",
                  "us.anthropic.claude-opus-4-7", "anthropic.claude-sonnet-5-5"):
            with self.subTest(model=m):
                self.assertIs(tier_for_model(m), HIGH_RES)

    def test_standard_models(self):
        for m in ("claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                  "claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929",
                  "claude-sonnet-4-20250514", "claude-opus-4-1-20250805",
                  "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022"):
            with self.subTest(model=m):
                self.assertIs(tier_for_model(m), STANDARD)

    def test_unknown_defaults_to_high_res(self):
        for m in (None, "", "<synthetic>", "some-other-model"):
            with self.subTest(model=m):
                self.assertIs(tier_for_model(m), HIGH_RES)


# ── Dimension parser tests ─────────────────────────────────────────────────


class TestPngParser(unittest.TestCase):

    def test_parses_png_dimensions(self):
        self.assertEqual(image_dims_from_bytes(_png_bytes(640, 480)), (640, 480))

    def test_rejects_truncated_png(self):
        self.assertIsNone(image_dims_from_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4))


class TestGifParser(unittest.TestCase):

    def test_parses_gif89a(self):
        self.assertEqual(image_dims_from_bytes(_gif_bytes(320, 240)), (320, 240))

    def test_parses_gif87a(self):
        self.assertEqual(image_dims_from_bytes(_gif_bytes(100, 100, b"GIF87a")), (100, 100))


class TestWebPParser(unittest.TestCase):

    def test_parses_vp8x(self):
        self.assertEqual(image_dims_from_bytes(_webp_vp8x_bytes(1024, 768)), (1024, 768))

    def test_parses_vp8l(self):
        self.assertEqual(image_dims_from_bytes(_webp_vp8l_bytes(800, 600)), (800, 600))


class TestJpegParser(unittest.TestCase):

    def test_parses_jpeg_with_app0_then_sof0(self):
        self.assertEqual(image_dims_from_bytes(_jpeg_bytes(1920, 1080)), (1920, 1080))


class TestUnknownFormat(unittest.TestCase):

    def test_returns_none_for_random_bytes(self):
        self.assertIsNone(image_dims_from_bytes(b"\x00\x01\x02\x03" * 100))

    def test_returns_none_for_too_short(self):
        self.assertIsNone(image_dims_from_bytes(b"hi"))

    def test_returns_none_for_non_bytes(self):
        self.assertIsNone(image_dims_from_bytes("not bytes"))


# ── End-to-end via base64 + content block ──────────────────────────────────


class TestBase64Roundtrip(unittest.TestCase):

    def test_base64_png(self):
        b64 = base64.b64encode(_png_bytes(2048, 1024)).decode()
        self.assertEqual(image_dims_from_base64(b64), (2048, 1024))

    def test_invalid_base64_returns_none(self):
        self.assertIsNone(image_dims_from_base64("not-valid-base64-!!!"))


class TestImageBlockTokens(unittest.TestCase):

    def test_png_block_returns_correct_tokens(self):
        # 1200x800: ceil(1200/28)=43, ceil(800/28)=29 → 1247 tokens, no resize
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(_png_bytes(1200, 800)).decode()},
        }
        self.assertEqual(image_block_tokens(block), 1247)
        self.assertEqual(image_block_tokens(block, STANDARD), 1247)

    def test_capped_for_large_image(self):
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(_png_bytes(4000, 4000)).decode()},
        }
        self.assertEqual(image_block_tokens(block), 4761)
        self.assertLessEqual(image_block_tokens(block, STANDARD), 1568)

    def test_returns_zero_for_non_image_block(self):
        self.assertEqual(image_block_tokens({"type": "text", "text": "hi"}), 0)
        self.assertEqual(image_block_tokens({}), 0)
        self.assertEqual(image_block_tokens(None), 0)

    def test_returns_zero_for_non_base64_source(self):
        block = {"type": "image", "source": {"type": "url", "url": "http://x"}}
        self.assertEqual(image_block_tokens(block), 0)

    def test_falls_back_to_cap_for_unparseable_image(self):
        # Bad data — parser returns None, function returns the tier's cap
        # (conservative)
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(b"garbage" * 50).decode()},
        }
        self.assertEqual(image_block_tokens(block), 4784)
        self.assertEqual(image_block_tokens(block, STANDARD), 1568)


def _img(w, h):
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/png",
        "data": base64.b64encode(_png_bytes(w, h)).decode()}}


class TestNestedImages(unittest.TestCase):
    """Images a tool returns (Read on a .png, screenshots) sit INSIDE the
    tool_result block's content list. They used to be invisible."""

    def test_iter_finds_top_level_and_nested(self):
        top = _img(100, 100)
        nested = _img(1260, 990)
        result = {"type": "tool_result", "tool_use_id": "t1",
                  "content": [{"type": "text", "text": "frame"}, nested]}
        found = list(iter_image_blocks([{"type": "text", "text": "hi"}, top, result]))
        self.assertEqual([b for b, _ in found], [top, nested])
        self.assertIsNone(found[0][1])
        self.assertIs(found[1][1], result)

    def test_string_tool_result_has_no_images(self):
        self.assertEqual(list(iter_image_blocks(
            [{"type": "tool_result", "tool_use_id": "t", "content": "text"}])), [])

    def test_content_image_stats(self):
        content = [_img(100, 100),
                   {"type": "tool_result", "tool_use_id": "t1", "content": [_img(1260, 990)]}]
        s = content_image_stats(content)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["nested"], 1)
        self.assertEqual(s["tokens"], 16 + 45 * 36)
        self.assertGreater(s["chars"], 0)


if __name__ == "__main__":
    unittest.main()
