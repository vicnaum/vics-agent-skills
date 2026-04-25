"""Anthropic-accurate image token estimation.

The bytes-divided-by-4 heuristic the rest of session-stripper uses is fine for
text/tool_use/tool_result/thinking but wildly wrong for images — base64 expands
~33% on top of the raw bytes, and a screenshot's actual Anthropic token cost is
based on pixel dimensions, not byte size. The error can be 50-100×.

Anthropic's documented formula:

    tokens ≈ (width_px × height_px) / 750

…clamped to a minimum of 1 and a maximum of 1600 (roughly the cost of a 1092×1092
image; larger images are downscaled to fit). This module:

1. Parses width/height from raw image bytes for PNG, JPEG, WebP (VP8/VP8L/VP8X),
   and GIF — the formats that actually appear in claude.ai / CC sessions.
2. Applies the formula.
3. Falls back to the cap (1600) when dimensions can't be parsed — over-counting
   beats under-counting for context budgeting.

Pure stdlib. No external dependencies.
"""

from __future__ import annotations

import base64
import struct

# Anthropic constants — see https://docs.anthropic.com/ vision pricing.
_PIXELS_PER_TOKEN = 750
_MAX_TOKENS_PER_IMAGE = 1600


def anthropic_image_tokens(width: int, height: int) -> int:
    """Anthropic's image token cost: ceil((w × h) / 750), clamped to [1, 1600]."""
    if width <= 0 or height <= 0:
        return 1
    raw = (width * height) / _PIXELS_PER_TOKEN
    return max(1, min(_MAX_TOKENS_PER_IMAGE, round(raw)))


def image_dims_from_bytes(data: bytes) -> tuple[int, int] | None:
    """Parse (width, height) from raw image bytes by sniffing magic bytes.

    Supports PNG, JPEG, WebP (VP8 / VP8L / VP8X), GIF87a/89a — the formats
    claude.ai and CC actually emit. Returns None on unrecognized format or
    malformed header.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 16:
        return None

    # ── PNG ────────────────────────────────────────────────────────────────
    # 89 50 4E 47 0D 0A 1A 0A | (4 chunk len) | "IHDR" | width(4 BE) | height(4 BE)
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        try:
            w, h = struct.unpack(">II", data[16:24])
            return (w, h)
        except struct.error:
            return None

    # ── GIF ────────────────────────────────────────────────────────────────
    # GIF87a / GIF89a, then logical-screen width/height (LE u16) at offset 6
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        try:
            w, h = struct.unpack("<HH", data[6:10])
            return (w, h)
        except struct.error:
            return None

    # ── WebP ───────────────────────────────────────────────────────────────
    # "RIFF" | size(4) | "WEBP" | chunk-fourcc(4) | …
    if data[:4] == b"RIFF" and len(data) >= 16 and data[8:12] == b"WEBP":
        chunk = data[12:16]
        try:
            if chunk == b"VP8 " and len(data) >= 30:
                # Lossy. After 26-byte preamble: width(2 LE, low 14 bits),
                # height(2 LE, low 14 bits).
                w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                return (w, h)
            if chunk == b"VP8L" and len(data) >= 25:
                # Lossless. 1-byte signature 0x2F at offset 20, then a packed
                # 28-bit pair: (w-1) low 14 bits, (h-1) next 14 bits, LE.
                packed = struct.unpack("<I", data[21:25])[0]
                w = (packed & 0x3FFF) + 1
                h = ((packed >> 14) & 0x3FFF) + 1
                return (w, h)
            if chunk == b"VP8X" and len(data) >= 30:
                # Extended. At offset 24..29: 3 bytes (w-1) LE, then 3 bytes (h-1) LE.
                w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
                h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
                return (w, h)
        except struct.error:
            return None

    # ── JPEG ───────────────────────────────────────────────────────────────
    # FFD8 then walk segments looking for an SOFn marker carrying dimensions.
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i < n - 9:
            # Skip any 0xFF padding
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                return None
            marker = data[i]
            i += 1
            # SOFn markers carry dimensions (excluding SOF4=DHT, SOF8=JPG,
            # SOF12=DAC). All variants below have the same SOF layout.
            if marker in (0xC0, 0xC1, 0xC2, 0xC3,
                          0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB,
                          0xCD, 0xCE, 0xCF):
                # Layout: length(2 BE) | precision(1) | height(2 BE) | width(2 BE) | …
                if i + 7 > n:
                    return None
                try:
                    h, w = struct.unpack(">HH", data[i+3:i+7])
                    return (w, h)
                except struct.error:
                    return None
            # SOI/EOI/RSTn carry no payload.
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            # Everything else: 2-byte big-endian length then payload.
            if i + 2 > n:
                return None
            try:
                seg_len = struct.unpack(">H", data[i:i+2])[0]
            except struct.error:
                return None
            i += seg_len
        return None

    return None


def image_dims_from_base64(data_b64: str) -> tuple[int, int] | None:
    """Decode base64 + parse image dimensions. Returns None on any failure."""
    if not isinstance(data_b64, str) or not data_b64:
        return None
    try:
        # Image headers live in the first ~64 bytes; decoding the whole payload
        # is fine (base64 is fast and we already paid the cost reading the JSONL).
        decoded = base64.b64decode(data_b64, validate=False)
    except Exception:
        return None
    return image_dims_from_bytes(decoded)


def image_block_tokens(block) -> int:
    """Anthropic token cost for an image content block in a CC envelope.

    Returns 0 if the block isn't an image. Falls back to the cap (1600) when
    dimensions can't be parsed — conservative for context budgeting.
    """
    if not isinstance(block, dict) or block.get("type") != "image":
        return 0
    src = block.get("source", {})
    if not isinstance(src, dict) or src.get("type") != "base64":
        return 0
    dims = image_dims_from_base64(src.get("data", ""))
    if dims is None:
        return _MAX_TOKENS_PER_IMAGE
    return anthropic_image_tokens(*dims)
