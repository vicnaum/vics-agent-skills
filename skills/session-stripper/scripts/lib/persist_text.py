"""Persist text blocks to sidecar files, replacing each with a `<persisted-text>`
marker. Single-position and bulk variants.

Marker shape (matches the contract pinned by tests/test_marker_contract.py):

    <persisted-text>
    Saved to: <relative-path> (N chars)
    Summary: ...

    Preview:
    <first ~1KB of original>
    </persisted-text>

The original text is written to:
    <sessionId>/persisted/text/<msg_uuid>_<block_idx>.txt
relative to the project dir (sibling of the JSONL).
"""

from __future__ import annotations

from pathlib import Path

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    resolve_range,
    save_session,
    walk_active_chain,
    wrapped_thinking_text,
)
from .persist_layout import persist_dir, to_marker_path

PREVIEW_CHARS = 1024
DEFAULT_SUMMARY = "[no summary provided]"


def _is_persisted_marker(text: str) -> bool:
    """Detect text blocks that are already a <persisted-*> marker so we don't
    persist a marker (idempotency)."""
    if not isinstance(text, str):
        return False
    head = text.lstrip()[:64]
    return head.startswith("<persisted-")


def _build_marker(rel_path: str, original_text: str, summary: str | None) -> str:
    s = summary if summary is not None else DEFAULT_SUMMARY
    preview = original_text[:PREVIEW_CHARS]
    return (
        f"<persisted-text>\n"
        f"Saved to: {rel_path} ({len(original_text)} chars)\n"
        f"Summary: {s}\n"
        f"\n"
        f"Preview:\n"
        f"{preview}\n"
        f"</persisted-text>"
    )


def _content_of(obj):
    msg = obj.get("message", {})
    if isinstance(msg, dict) and "content" in msg:
        return msg, msg["content"]
    return obj, obj.get("content")


def persist_text(session_path, chain_pos: int, summary: str | None = None,
                 dry_run: bool = False, no_backup: bool = False):
    """Persist all qualifying text blocks at a single chain position.

    Persists every text block in the message at chain_pos that:
      - is type=text with non-empty string text
      - is not already a <persisted-*> marker
      - is not a wrapped-thinking text block (handled by persist_thinking)
    """
    return _persist(
        session_path,
        positions=[chain_pos],
        summary_for=lambda pos, idx: summary,
        dry_run=dry_run, no_backup=no_backup,
        min_chars=0, keep_recent=0,
    )


def persist_text_bulk(session_path, *, min_chars: int = 0,
                      keep_recent: int = 0,
                      from_pos: int | None = None, to_pos: int | None = None,
                      summaries: dict | None = None,
                      dry_run: bool = False, no_backup: bool = False):
    """Bulk persist text blocks across a range.

    summaries: optional mapping from `f"pos:{N}"` (chain pos) → summary string.
    """
    objects = load_session(session_path)
    chain = walk_active_chain(objects, build_uuid_index(objects))
    start, end = resolve_range(chain, from_pos, to_pos)
    positions = list(range(start, end + 1))

    def _sum_for(pos, idx):
        if not summaries:
            return None
        return summaries.get(f"pos:{pos}")

    return _persist(
        session_path,
        positions=positions,
        summary_for=_sum_for,
        dry_run=dry_run, no_backup=no_backup,
        min_chars=min_chars, keep_recent=keep_recent,
    )


def _persist(session_path, *, positions, summary_for,
             dry_run: bool, no_backup: bool,
             min_chars: int, keep_recent: int):
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    target_uuids = set()
    for pos in positions:
        if 0 <= pos < len(chain):
            uid = chain[pos].get("uuid")
            if uid is not None:
                target_uuids.add((pos, uid))

    out_dir = persist_dir(session_path, "text")

    # First pass: collect all candidate (pos, msg_obj, block_idx, text) so we
    # can apply --keep-recent (skip the last N).
    candidates = []
    pos_by_uuid = {o.get("uuid"): p for p, o in enumerate(chain)}
    for obj in objects:
        uid = obj.get("uuid")
        if uid is None:
            continue
        pos = pos_by_uuid.get(uid)
        if pos is None or not any(pp == pos for pp, _ in target_uuids):
            continue
        _, content = _content_of(obj)
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not isinstance(text, str) or not text:
                continue
            if _is_persisted_marker(text):
                continue
            if wrapped_thinking_text(block) is not None:
                continue
            if len(text) < min_chars:
                continue
            candidates.append((pos, obj, i, text))

    # Apply keep_recent: skip the last N candidates (preserves tail).
    if keep_recent and keep_recent > 0:
        candidates = candidates[: max(0, len(candidates) - keep_recent)]

    persisted = 0
    chars_saved = 0

    for pos, obj, block_idx, original in candidates:
        msg_uuid = obj.get("uuid", "unknown")
        sidecar = out_dir / f"{msg_uuid}_{block_idx}.txt"
        rel = to_marker_path(sidecar, session_path)
        marker = _build_marker(rel, original, summary_for(pos, block_idx))

        if not dry_run:
            sidecar.write_text(original, encoding="utf-8")
            _, content = _content_of(obj)
            content[block_idx] = {"type": "text", "text": marker}

        persisted += 1
        chars_saved += max(0, len(original) - len(marker))

    if not dry_run and persisted:
        save_session(session_path, objects, create_backup=not no_backup)

    stats = {
        "persisted_count": persisted,
        "chars_saved": chars_saved,
        "est_tokens_saved": estimate_tokens(chars_saved),
    }
    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Text blocks persisted: {persisted}")
    print(f"{mode}Characters saved:      {chars_saved:,}")
    print(f"{mode}Est. tokens saved:     {stats['est_tokens_saved']:,}")
    return stats
