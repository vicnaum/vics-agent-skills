"""Replace image blocks with text transcripts in Claude Code JSONL sessions.

Images in CC JSONL are base64-inlined. This module identifies each image by
the SHA256 of its decoded bytes, so you can generate a text transcript for
each unique image once (e.g. via a vision model) and plug it back in.

Images live in two places, and both are handled:
  * top-level `image` blocks in a user message (pasted images), and
  * images NESTED in a `tool_result` block's `content` list: every image a
    tool returned — Read on a .png/.jpg, screenshots, MCP tools. In a working
    session these are usually the majority.

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
    session_model,
    walk_active_chain,
)
from .image_tokens import (image_block_dims, image_block_tokens,
                           iter_image_blocks, tier_for_model)
from .persist_layout import persist_dir, to_marker_path

_DEFAULT_SUMMARY = "[image transcript]"
_PREVIEW_CHARS = 512
# Text left in a message whose only block was a dropped image: the API
# rejects empty user content.
_DROPPED_PLACEHOLDER = "[image removed]"


def _content_of(obj):
    msg = obj.get("message", {})
    return msg.get("content") if isinstance(msg, dict) else obj.get("content")


def _decode(block):
    """(sha256_hex, decoded_size) of a base64 image block, or None."""
    src = block.get("source") or {}
    if not isinstance(src, dict) or src.get("type") != "base64":
        return None
    try:
        decoded = base64.b64decode(src.get("data", ""), validate=False)
    except Exception:
        return None
    return hashlib.sha256(decoded).hexdigest(), len(decoded)


def _tool_names(objects):
    names = {}
    for obj in objects:
        content = _content_of(obj)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    names[b["id"]] = b.get("name") or "?"
    return names


def _iter_image_blocks(objects, active_only=True):
    """Yield (obj, block, parent, sha256_hex, decoded_size) for each base64
    image block, top-level or nested in a tool_result. When active_only,
    restricts to the active chain (in chain order)."""
    if active_only:
        records = walk_active_chain(objects, build_uuid_index(objects))
    else:
        records = objects
    for obj in records:
        for block, parent in iter_image_blocks(_content_of(obj)):
            decoded = _decode(block)
            if decoded is None:
                continue
            yield (obj, block, parent) + decoded


def list_images(session_path):
    """Print a table of image blocks in the active chain: chain position,
    where the image sits, dimensions, token cost, decoded byte size, media
    type, SHA256.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)
    pos_by_uuid = {o.get("uuid"): p for p, o in enumerate(chain) if o.get("uuid")}
    model = session_model(chain)
    tier = tier_for_model(model)
    tool_names = _tool_names(objects)

    rows = []
    for obj, block, parent, h, dsize in _iter_image_blocks(objects):
        src = block.get("source", {}) or {}
        if parent is None:
            where = "message"
        else:
            name = tool_names.get(parent.get("tool_use_id"), "?")
            where = f"tool_result:{name}"
        dims = image_block_dims(block)
        rows.append({
            "pos": pos_by_uuid.get(obj.get("uuid"), -1),
            "where": where,
            "nested": parent is not None,
            "dims": dims,
            "tokens": image_block_tokens(block, tier),
            "media_type": src.get("media_type", "?"),
            "bytes": dsize,
            "sha256": h,
        })

    if not rows:
        print("No image blocks in active chain.")
        return rows

    unique = {r["sha256"] for r in rows}
    print(f"{'Pos':>5}  {'Where':<22} {'Dims':>11} {'Tokens':>7} {'Bytes':>10}  "
          f"{'Media':>10}  SHA256")
    print(f"{'─' * 5}  {'─' * 22} {'─' * 11} {'─' * 7} {'─' * 10}  {'─' * 10}  {'─' * 64}")
    for r in rows:
        dims = f"{r['dims'][0]}x{r['dims'][1]}" if r["dims"] else "?"
        print(f"{r['pos']:>5}  {r['where'][:22]:<22} {dims:>11} {r['tokens']:>7,} "
              f"{r['bytes']:>10,}  {r['media_type']:>10}  {r['sha256']}")
    nested = sum(1 for r in rows if r["nested"])
    total_tokens = sum(r["tokens"] for r in rows)
    print(f"\n{len(rows)} image blocks ({nested} inside tool results)  •  "
          f"{len(unique)} unique by sha256  •  ~{total_tokens:,} tokens "
          f"({tier.name} tier{', ' + model if model else ''}: "
          f"ceil(w/28)×ceil(h/28) after resize to ≤{tier.max_edge}px / "
          f"≤{tier.max_tokens:,} tokens each)")
    return rows


def replace_images(session_path, descriptions_dir, dry_run=False,
                   no_backup=False, drop_missing=False):
    """Replace image blocks in the active chain with text transcripts.

    For each image block (top-level, or nested in a tool_result), compute
    SHA256 of the base64-decoded bytes, then look for
    `<descriptions_dir>/<sha256>.txt`. If found, replace the image block with
    a text block carrying a `<persisted-image>` marker.

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
    tier = tier_for_model(session_model(chain))

    stats = {
        "replaced": 0,
        "replaced_nested": 0,
        "missing": 0,
        "dropped": 0,
        "base64_chars_removed": 0,
        "transcript_chars_added": 0,
        "image_tokens_removed": 0,  # Anthropic pixel formula, not chars/4
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

    def _rewrite(content, nested):
        """Return (new_content, mutated) with images replaced/dropped, at this
        level and inside any block whose `content` is a list."""
        new_content = []
        mutated = False
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            if block.get("type") != "image":
                inner = block.get("content")
                if isinstance(inner, list):
                    new_inner, inner_mut = _rewrite(inner, True)
                    if inner_mut:
                        block = dict(block)
                        if new_inner:
                            block["content"] = new_inner
                        else:
                            # A tool result whose only content was a dropped
                            # image: empty content is accepted, but not
                            # together with is_error.
                            block["content"] = ""
                            if block.get("is_error") is True:
                                block.pop("is_error", None)
                        mutated = True
                new_content.append(block)
                continue

            decoded = _decode(block)
            if decoded is None:
                new_content.append(block)
                continue
            h, _size = decoded
            src = block["source"]
            data = src.get("data", "")
            transcript = _get_transcript(h)
            if transcript is not None:
                mt = src.get("media_type", "image")
                transcript_clean = transcript.rstrip()
                stats["image_tokens_removed"] += image_block_tokens(block, tier)

                # Copy the transcript into our session-scoped persisted/
                # dir so the marker can carry a stable relative path.
                out_dir = persist_dir(session_path, "image")
                sidecar = out_dir / f"{h}.txt"
                if not sidecar.exists() and not dry_run:
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
                if nested:
                    stats["replaced_nested"] += 1
                stats["base64_chars_removed"] += len(data)
                stats["transcript_chars_added"] += len(text)
                mutated = True
            else:
                stats["missing"] += 1
                if drop_missing:
                    stats["dropped"] += 1
                    stats["base64_chars_removed"] += len(data)
                    stats["image_tokens_removed"] += image_block_tokens(block, tier)
                    mutated = True
                    # skip appending the block
                else:
                    new_content.append(block)
        return new_content, mutated

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

        new_content, mutated = _rewrite(content, False)
        if mutated:
            if not new_content:
                new_content = [{"type": "text", "text": _DROPPED_PLACEHOLDER}]
            content_container["content"] = new_content

    nested_note = (f" ({stats['replaced_nested']} inside tool results)"
                   if stats["replaced_nested"] else "")
    print(f"Image blocks replaced:       {stats['replaced']}{nested_note}")
    print(f"Image blocks missing transcripts: {stats['missing']}")
    if drop_missing:
        print(f"Image blocks dropped:        {stats['dropped']}")
    print(f"Base64 chars removed:        {stats['base64_chars_removed']:,}")
    print(f"Transcript chars added:      {stats['transcript_chars_added']:,}")
    # Image tokens use Anthropic's pixel formula, not chars/4.
    image_tokens_freed = stats.get("image_tokens_removed", 0)
    transcript_tokens = estimate_tokens(stats["transcript_chars_added"])
    net_tokens = image_tokens_freed - transcript_tokens
    print(f"Image tokens removed:        {image_tokens_freed:,}  ({tier.name} tier formula)")
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
