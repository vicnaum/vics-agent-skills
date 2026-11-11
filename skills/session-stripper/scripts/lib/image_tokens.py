"""Anthropic-accurate image token estimation.

The bytes-divided-by-4 heuristic the rest of session-stripper uses is fine for
text/tool_use/tool_result/thinking but wildly wrong for images: an image's
token cost depends on its pixel dimensions, not on its base64 size. Sizing a
screenshot's base64 as text over-counts it 20-100x.

THE FORMULA (Anthropic vision docs, checked 2026-11)
----------------------------------------------------
Claude processes images in 28×28-pixel patches ("visual tokens"):

    tokens = ceil(width / 28) × ceil(height / 28)

after downscaling the image (aspect preserved) to the largest size that fits
BOTH of its model's limits:

    tier             models                    max long edge   max tokens
    high-resolution  Claude 4.7 and later      2,576 px        4,784
    standard         all other models          1,568 px        1,568

`resized_size()` is a straight port of the reference implementation in
platform.claude.com/docs/en/build-with-claude/vision-coordinates ("How Claude
resizes and pads images"), including Python's round-half-to-even.

The previous rule here, (w×h)/750 capped at 1,600, is the standard tier's
older approximation. On the high-resolution tier it under-counts every image
past ~1.2 MP by up to 3x. Measured on a claude-opus-5-5 session: across 69
turns that each added images, the usage delta was this formula's figure plus a
steady ~40-90 tokens of per-turn overhead (2325×1300 cost 4,040 tokens; the
formula says 3,948, the old rule said 1,600).

WHERE IMAGES LIVE IN A CLAUDE CODE TRANSCRIPT
---------------------------------------------
Pasted images are top-level `image` blocks in a user message. Images that a
tool returns (Read on a .png/.jpg, screenshots, MCP tools) are NESTED inside
the `tool_result` block's `content` list. Both are sent to the model.
`iter_image_blocks()` yields both kinds.

Pure stdlib. No external dependencies.
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
from collections import namedtuple

PATCH_PX = 28

ImageTier = namedtuple("ImageTier", "name max_edge max_tokens")

HIGH_RES = ImageTier("high-resolution", 2576, 4784)
STANDARD = ImageTier("standard", 1568, 1568)

# Claude Code's default models are all on the high-resolution tier, and an
# over-count is safer than an under-count for context budgeting.
DEFAULT_TIER = HIGH_RES


def tier_for_model(model) -> ImageTier:
    """Resolution tier of a model id ("Claude 4.7 and later" = high-res).

    Accepts bare ids (claude-opus-5-5), dated ids (claude-haiku-4-5-20251001),
    legacy ids (claude-3-7-sonnet-20250219) and platform-prefixed ids
    (us.anthropic.claude-sonnet-4-6). Unknown or missing ids get DEFAULT_TIER.
    """
    if not isinstance(model, str) or not model or model.startswith("<"):
        return DEFAULT_TIER
    m = model.lower()
    if "fable" in m or "mythos" in m:
        return HIGH_RES
    # claude-<family>-<major>[-<minor>]  (current naming)
    hit = re.search(r"claude-(?:opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?!\d)", m)
    if hit:
        major = int(hit.group(1))
        minor = int(hit.group(2)) if hit.group(2) else 0
        return HIGH_RES if (major, minor) >= (4, 7) else STANDARD
    # claude-<major>[-<minor>]-<family>  (Claude 3.x naming)
    if re.search(r"claude-\d", m):
        return STANDARD
    return DEFAULT_TIER


def count_image_tokens(width: int, height: int) -> int:
    """Visual tokens of an image as sent: one per 28×28 patch."""
    return math.ceil(width / PATCH_PX) * math.ceil(height / PATCH_PX)


def resized_size(width: int, height: int, tier: ImageTier = DEFAULT_TIER):
    """(width, height) Claude resizes an image to before padding.

    Port of the docs' reference implementation. Images that already fit both
    limits come back unchanged.
    """
    max_edge, max_tokens = tier.max_edge, tier.max_tokens

    def fits(w: int, h: int) -> bool:
        return (math.ceil(w / PATCH_PX) * PATCH_PX <= max_edge
                and math.ceil(h / PATCH_PX) * PATCH_PX <= max_edge
                and count_image_tokens(w, h) <= max_tokens)

    if fits(width, height):
        return (width, height)
    if height > width:
        resized_h, resized_w = resized_size(height, width, tier)
        return (resized_w, resized_h)

    # Binary search along the long edge for the largest aspect-preserving
    # size that fits.
    aspect_ratio = width / height
    lo, hi = 1, width  # lo always fits; hi never fits
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect_ratio), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect_ratio), 1))


def anthropic_image_tokens(width: int, height: int,
                           tier: ImageTier = DEFAULT_TIER) -> int:
    """Token cost of a width×height image on `tier`, after Claude's resize."""
    if width <= 0 or height <= 0:
        return 1
    return count_image_tokens(*resized_size(width, height, tier))


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
        # Image headers usually sit in the first few hundred bytes, but a JPEG
        # can carry EXIF/ICC segments before its SOF marker, so decode it all
        # (base64 is fast and we already paid the cost reading the JSONL).
        decoded = base64.b64decode(data_b64, validate=False)
    except Exception:
        return None
    return image_dims_from_bytes(decoded)


def image_block_dims(block):
    """(width, height) of a base64 image block, or None."""
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    src = block.get("source")
    if not isinstance(src, dict) or src.get("type") != "base64":
        return None
    return image_dims_from_base64(src.get("data", ""))


def image_block_tokens(block, tier: ImageTier = DEFAULT_TIER) -> int:
    """Anthropic token cost for an image content block in a CC envelope.

    Returns 0 if the block isn't a base64 image. Falls back to the tier's cap
    when dimensions can't be parsed — conservative for context budgeting.
    """
    if not isinstance(block, dict) or block.get("type") != "image":
        return 0
    src = block.get("source", {})
    if not isinstance(src, dict) or src.get("type") != "base64":
        return 0
    dims = image_dims_from_base64(src.get("data", ""))
    if dims is None:
        return tier.max_tokens
    return anthropic_image_tokens(*dims, tier=tier)


def iter_image_blocks(content, _parent=None):
    """Yield (image_block, parent_block) for every image in a content list.

    `parent_block` is None for a top-level image and the containing block
    (a `tool_result`, in practice) for a nested one. Recurses into any block
    whose `content` is a list, so images inside tool results are found.
    """
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            yield block, _parent
        elif isinstance(block.get("content"), list):
            yield from iter_image_blocks(block["content"], block)


def image_payload_chars(block) -> int:
    """Characters an image block occupies when serialized (what chars/4
    sizing would have charged for it)."""
    return len(json.dumps(block, ensure_ascii=False))


def content_image_stats(content, tier: ImageTier = DEFAULT_TIER) -> dict:
    """Images in a content list: count, nested count, serialized chars and
    token cost on `tier`."""
    stats = {"count": 0, "nested": 0, "chars": 0, "tokens": 0}
    for block, parent in iter_image_blocks(content):
        stats["count"] += 1
        if parent is not None:
            stats["nested"] += 1
        stats["chars"] += image_payload_chars(block)
        stats["tokens"] += image_block_tokens(block, tier)
    return stats
