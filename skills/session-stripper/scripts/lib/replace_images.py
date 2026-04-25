"""Replace image blocks with text transcripts in Claude Code JSONL sessions.

Images in CC JSONL are base64-inlined. This module identifies each image by
the SHA256 of its decoded bytes, so you can generate a text transcript for
each unique image once (e.g. via a vision model) and plug it back in.

Layout the caller prepares:

    <descriptions_dir>/
    ├── <sha256-hex>.txt        ← one file per unique image
    └── ...

`list-images` shows what's in the session. `replace-images` walks the active
chain, hashes each image, loads the matching transcript (if any), and
replaces the image block with a text block wrapping the transcript.
"""

import base64
import hashlib
from pathlib import Path

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    save_session,
    walk_active_chain,
)
from .image_tokens import image_block_tokens
from .persist_layout import persist_dir, to_marker_path

_DEFAULT_SUMMARY = "[image transcript]"
_PREVIEW_CHARS = 512


def _iter_image_blocks(objects, active_only=True):
    """Yield (obj, block_index, block, sha256_hex, decoded_size) for each
    base64 image block. When active_only, restricts to the active chain."""
    target_uuids = None
    if active_only:
        uuid_index = build_uuid_index(objects)
        chain = walk_active_chain(objects, uuid_index)
        target_uuids = {o.get("uuid") for o in chain if o.get("uuid")}

    for obj in objects:
        if target_uuids is not None and obj.get("uuid") not in target_uuids:
            continue
        msg = obj.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "image":
                continue
            src = block.get("source", {}) or {}
            if src.get("type") != "base64":
                continue
            data = src.get("data", "")
            try:
                decoded = base64.b64decode(data, validate=False)
            except Exception:
                continue
            h = hashlib.sha256(decoded).hexdigest()
            yield obj, i, block, h, len(decoded)


def list_images(session_path):
    """Print a table of image blocks in the active chain: chain position,
    decoded byte size, media type, SHA256.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)
    pos_by_uuid = {o.get("uuid"): p for p, o in enumerate(chain) if o.get("uuid")}

    rows = []
    for obj, _idx, block, h, dsize in _iter_image_blocks(objects):
        src = block.get("source", {}) or {}
        rows.append({
            "pos": pos_by_uuid.get(obj.get("uuid"), -1),
            "media_type": src.get("media_type", "?"),
            "bytes": dsize,
            "sha256": h,
        })

    if not rows:
        print("No image blocks in active chain.")
        return rows

    unique = {r["sha256"] for r in rows}
    print(f"{'Pos':>5}  {'Bytes':>10}  {'Media':>12}  SHA256")
    print(f"{'─' * 5}  {'─' * 10}  {'─' * 12}  {'─' * 64}")
    for r in rows:
        print(f"{r['pos']:>5}  {r['bytes']:>10,}  {r['media_type']:>12}  {r['sha256']}")
    print(f"\n{len(rows)} image blocks  •  {len(unique)} unique by sha256")
    return rows


def replace_images(session_path, descriptions_dir, dry_run=False,
                   no_backup=False, drop_missing=False):
    """Replace image blocks in the active chain with text transcripts.

    For each image block, compute SHA256 of the base64-decoded bytes, then
    look for `<descriptions_dir>/<sha256>.txt`. If found, replace the image
    block with a text block:

        {"type": "text",
         "text": "<image sha256=\"...\" media_type=\"image/webp\">\n{text}\n</image>"}

    If not found and drop_missing=True, drop the image block entirely. Else
    leave the original image block in place.

    Returns a stats dict.
    """
    descriptions_dir = Path(descriptions_dir).expanduser().resolve()
    if not descriptions_dir.is_dir():
        print(f"error: descriptions dir does not exist: {descriptions_dir}")
        return None

    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)
    target_uuids = {o.get("uuid") for o in chain if o.get("uuid")}

    stats = {
        "replaced": 0,
        "missing": 0,
        "dropped": 0,
        "base64_chars_removed": 0,
        "transcript_chars_added": 0,
        "image_tokens_removed": 0,  # Anthropic formula, not chars/4
    }

    # Cache loaded transcripts so repeated identical images hit disk once.
    transcript_cache: dict[str, str | None] = {}

    def _get_transcript(sha256_hex: str):
        if sha256_hex in transcript_cache:
            return transcript_cache[sha256_hex]
        path = descriptions_dir / f"{sha256_hex}.txt"
        if path.is_file():
            transcript_cache[sha256_hex] = path.read_text(encoding="utf-8")
        else:
            transcript_cache[sha256_hex] = None
        return transcript_cache[sha256_hex]

    for obj in objects:
        if obj.get("uuid") not in target_uuids:
            continue
        msg = obj.get("message", {})
        if isinstance(msg, dict) and "content" in msg:
            content_container = msg
            content = msg["content"]
        else:
            content_container = obj
            content = obj.get("content")
        if not isinstance(content, list):
            continue

        new_content = []
        mutated = False
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "image"
                and isinstance(block.get("source"), dict)
                and block["source"].get("type") == "base64"
            ):
                src = block["source"]
                data = src.get("data", "")
                try:
                    decoded = base64.b64decode(data, validate=False)
                except Exception:
                    new_content.append(block)
                    continue
                h = hashlib.sha256(decoded).hexdigest()
                transcript = _get_transcript(h)
                if transcript is not None:
                    mt = src.get("media_type", "image")
                    transcript_clean = transcript.rstrip()
                    # Account for what we're freeing in real Anthropic tokens
                    stats["image_tokens_removed"] += image_block_tokens(block)

                    # Copy the transcript into our session-scoped persisted/
                    # dir so the marker can carry a stable relative path.
                    out_dir = persist_dir(session_path, "image")
                    sidecar = out_dir / f"{h}.txt"
                    if not sidecar.exists():
                        sidecar.write_text(transcript_clean, encoding="utf-8")
                    rel = to_marker_path(sidecar, session_path)

                    text = (
                        f'<persisted-image sha256="{h}" media_type="{mt}">\n'
                        f'Saved to: {rel} ({len(transcript_clean)} chars)\n'
                        f'Summary: {_DEFAULT_SUMMARY}\n'
                        f'\n'
                        f'Preview:\n'
                        f'{transcript_clean[:_PREVIEW_CHARS]}\n'
                        f'</persisted-image>'
                    )
                    new_content.append({"type": "text", "text": text})
                    stats["replaced"] += 1
                    stats["base64_chars_removed"] += len(data)
                    stats["transcript_chars_added"] += len(text)
                    mutated = True
                else:
                    stats["missing"] += 1
                    if drop_missing:
                        stats["dropped"] += 1
                        stats["base64_chars_removed"] += len(data)
                        stats["image_tokens_removed"] += image_block_tokens(block)
                        mutated = True
                        # skip appending the block
                    else:
                        new_content.append(block)
            else:
                new_content.append(block)

        if mutated:
            content_container["content"] = new_content

    print(f"Image blocks replaced:       {stats['replaced']}")
    print(f"Image blocks missing transcripts: {stats['missing']}")
    if drop_missing:
        print(f"Image blocks dropped:        {stats['dropped']}")
    print(f"Base64 chars removed:        {stats['base64_chars_removed']:,}")
    print(f"Transcript chars added:      {stats['transcript_chars_added']:,}")
    # Image tokens use Anthropic's (w*h)/750 formula, not chars/4.
    image_tokens_freed = stats.get("image_tokens_removed", 0)
    transcript_tokens = estimate_tokens(stats["transcript_chars_added"])
    net_tokens = image_tokens_freed - transcript_tokens
    print(f"Image tokens removed:        {image_tokens_freed:,}  (Anthropic formula)")
    print(f"Transcript tokens added:     {transcript_tokens:,}  (chars/4)")
    print(f"Net tokens saved:            {net_tokens:,}")

    if dry_run:
        print("\n[dry run] No changes written.")
    elif stats["replaced"] or (drop_missing and stats["dropped"]):
        save_session(session_path, objects, create_backup=not no_backup)
        print(f"\nSession saved: {session_path}")
    else:
        print("\nNo changes to write.")

    return stats
