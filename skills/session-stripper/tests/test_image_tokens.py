"""Tests for the Anthropic-accurate image token estimator.

Verifies the formula, the dimension parsers (PNG/JPEG/WebP/GIF), and the
content-block helper. Uses hand-crafted minimal valid headers — no external
images, no Pillow.
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
    anthropic_image_tokens,
    image_dims_from_base64,
    image_dims_from_bytes,
    image_block_tokens,
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

    def test_zero_or_negative_returns_one(self):
        self.assertEqual(anthropic_image_tokens(0, 0), 1)
        self.assertEqual(anthropic_image_tokens(-5, 100), 1)

    def test_small_image(self):
        # 100x100 = 10000 px; 10000/750 ≈ 13.33 → 13
        self.assertEqual(anthropic_image_tokens(100, 100), 13)

    def test_typical_screenshot(self):
        # 1200x800 = 960000 px; 960000/750 = 1280 → exactly 1280
        self.assertEqual(anthropic_image_tokens(1200, 800), 1280)

    def test_capped_at_1600(self):
        # 2000x2000 = 4M px → would be 5333; cap at 1600
        self.assertEqual(anthropic_image_tokens(2000, 2000), 1600)
        self.assertEqual(anthropic_image_tokens(10000, 10000), 1600)

    def test_just_under_cap(self):
        # 1095x1095 = 1199025 / 750 = 1598.7 → 1599 (under cap)
        self.assertEqual(anthropic_image_tokens(1095, 1095), 1599)


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
        # 1200x800 = 1280 tokens
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(_png_bytes(1200, 800)).decode()},
        }
        self.assertEqual(image_block_tokens(block), 1280)

    def test_capped_for_large_image(self):
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(_png_bytes(4000, 4000)).decode()},
        }
        self.assertEqual(image_block_tokens(block), 1600)

    def test_returns_zero_for_non_image_block(self):
        self.assertEqual(image_block_tokens({"type": "text", "text": "hi"}), 0)
        self.assertEqual(image_block_tokens({}), 0)
        self.assertEqual(image_block_tokens(None), 0)

    def test_returns_zero_for_non_base64_source(self):
        block = {"type": "image", "source": {"type": "url", "url": "http://x"}}
        self.assertEqual(image_block_tokens(block), 0)

    def test_falls_back_to_cap_for_unparseable_image(self):
        # Bad data — parser returns None, function returns 1600 (conservative)
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(b"garbage" * 50).decode()},
        }
        self.assertEqual(image_block_tokens(block), 1600)


if __name__ == "__main__":
    unittest.main()
