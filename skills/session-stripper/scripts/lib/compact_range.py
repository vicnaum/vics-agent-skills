"""compact-range — collapse N consecutive messages into one survivor carrying
a `<persisted-range>` marker. Originals saved one-per-message to the same
sidecar dir persist-message uses.

Use cases:
  - Drop a topical chunk: pass the default summary (placeholder) — leaves
    one tiny marker in place of N noisy messages.
  - Compress a topical chunk: pass a hand-crafted summary covering the whole
    range — one summary subsumes many messages.

For per-message tailored summaries, use persist-message instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    remove_objects_and_rewire,
    save_session,
    walk_active_chain,
)
from .persist_layout import persist_dir, to_marker_path

PREVIEW_PER_MSG = 200  # chars from each message in the preview
PREVIEW_MAX_MSGS = 5   # cap how many messages contribute to the preview
DEFAULT_SUMMARY = "[range collapsed]"


class RangeRefused(RuntimeError):
    """Raised when compact-range refuses to collapse: leaf inclusion or
    orphaned tool_use/tool_result pair."""


def _content_of(obj):
    msg = obj.get("message", {})
    if isinstance(msg, dict) and "content" in msg:
        return msg, msg["content"]
    return obj, obj.get("content")


def _collect_tool_use_ids(obj):
    _, content = _content_of(obj)
    if not isinstance(content, list):
        return set()
    return {
        b.get("id") for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
    }


def _collect_tool_result_ids(obj):
    _, content = _content_of(obj)
    if not isinstance(content, list):
        return set()
    return {
        b.get("tool_use_id") for b in content
        if isinstance(b, dict) and b.get("type") == "tool_result"
        and b.get("tool_use_id")
    }


def _is_already_collapsed(obj) -> bool:
    """A survivor whose only block is a <persisted-range> text marker."""
    _, content = _content_of(obj)
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text":
        return False
    text = block.get("text", "")
    return isinstance(text, str) and text.lstrip().startswith("<persisted-range")


def _excerpt(obj) -> str:
    """Short human preview line: '[role] first text snippet'."""
    role = obj.get("type", "?")
    _, content = _content_of(obj)
    snippet = ""
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    snippet = b.get("text", "")
                    break
                if b.get("type") == "thinking":
                    snippet = "[thinking]"
                    break
                if b.get("type") == "tool_use":
                    snippet = f"[tool_use {b.get('name', '?')}]"
                    break
                if b.get("type") == "tool_result":
                    snippet = "[tool_result]"
                    break
                if b.get("type") == "image":
                    snippet = "[image]"
                    break
    elif isinstance(content, str):
        snippet = content
    snippet = snippet.replace("\n", " ").strip()[:PREVIEW_PER_MSG]
    return f"[{role}] {snippet}"


def _build_marker(from_pos: int, to_pos: int, count: int,
                  rel_dir: str, summary: str | None,
                  range_objs) -> str:
    s = summary if summary is not None else DEFAULT_SUMMARY
    head_excerpts = [_excerpt(o) for o in range_objs[:PREVIEW_MAX_MSGS]]
    if count > PREVIEW_MAX_MSGS:
        head_excerpts.append(f"[... {count - PREVIEW_MAX_MSGS} more]")
    preview = "\n".join(head_excerpts)
    return (
        f'<persisted-range from="{from_pos}" to="{to_pos}" count="{count}">\n'
        f'Saved to: {rel_dir} ({count} files)\n'
        f'Summary: {s}\n'
        f'\n'
        f'Preview:\n'
        f'{preview}\n'
        f'</persisted-range>'
    )


def compact_range(session_path, from_pos: int, to_pos: int,
                  summary: str | None = None,
                  dry_run: bool = False, no_backup: bool = False):
    """Collapse messages [from_pos..to_pos] (inclusive) to one survivor.

    Returns a stats dict.
    """
    if from_pos > to_pos:
        raise ValueError(f"from_pos ({from_pos}) > to_pos ({to_pos})")

    objects = load_session(session_path)
    chain = walk_active_chain(objects, build_uuid_index(objects))
    n = len(chain)
    if from_pos < 0 or to_pos >= n:
        raise IndexError(f"range {from_pos}..{to_pos} out of chain (0-{n-1})")

    if to_pos == n - 1:
        raise RangeRefused(
            f"refusing: range {from_pos}..{to_pos} includes the leaf message"
        )

    range_objs = chain[from_pos:to_pos + 1]

    # Idempotency: single-message range that's already a <persisted-range>
    if len(range_objs) == 1 and _is_already_collapsed(range_objs[0]):
        if not dry_run:
            print("Range already collapsed — no-op.")
        return {"collapsed_count": 0, "chars_saved": 0, "est_tokens_saved": 0}

    # tool_use / tool_result orphan check
    range_uuids = {o.get("uuid") for o in range_objs}
    inside_tool_uses: set = set()
    inside_tool_results: set = set()
    for o in range_objs:
        inside_tool_uses |= _collect_tool_use_ids(o)
        inside_tool_results |= _collect_tool_result_ids(o)

    # Any tool_use whose result lives outside?
    outside_results: set = set()
    outside_uses: set = set()
    for o in chain:
        if o.get("uuid") in range_uuids:
            continue
        outside_results |= _collect_tool_result_ids(o)
        outside_uses |= _collect_tool_use_ids(o)

    orphaned_uses = inside_tool_uses - inside_tool_results
    if orphaned_uses & outside_results:
        raise RangeRefused(
            f"refusing: range contains tool_use(s) {orphaned_uses & outside_results} "
            f"whose tool_result lives outside the range"
        )
    orphaned_results = inside_tool_results - inside_tool_uses
    if orphaned_results & outside_uses:
        raise RangeRefused(
            f"refusing: range contains tool_result(s) {orphaned_results & outside_uses} "
            f"whose tool_use lives outside the range"
        )

    # Save sidecars (one JSON per original message)
    out_dir = persist_dir(session_path, "message")
    rel_dir = to_marker_path(out_dir, session_path)
    sidecar_paths: list[Path] = []
    if not dry_run:
        for o in range_objs:
            uid = o.get("uuid", "unknown")
            p = out_dir / f"{uid}.json"
            p.write_text(json.dumps(o, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            sidecar_paths.append(p)

    # Build the marker
    marker = _build_marker(from_pos, to_pos, len(range_objs), rel_dir,
                           summary, range_objs)

    # Compute survivor: the FIRST message in the range. Replace its content
    # with the marker; leave its envelope metadata (uuid, parentUuid,
    # timestamp, role) intact so descendants chain through it.
    survivor_uuid = range_objs[0].get("uuid")

    # Mutate `objects` in place: rewrite survivor's content, mark others for removal.
    drop_uuids = {o.get("uuid") for o in range_objs[1:]}

    chars_before = 0
    for o in objects:
        uid = o.get("uuid")
        if uid == survivor_uuid:
            msg, content = _content_of(o)
            chars_before += len(json.dumps(content, ensure_ascii=False))
            msg["content"] = [{"type": "text", "text": marker}]
        elif uid in drop_uuids:
            chars_before += len(json.dumps(o, ensure_ascii=False))

    if not dry_run:
        survivors, _, rewired = remove_objects_and_rewire(objects, drop_uuids)
        save_session(session_path, survivors, create_backup=not no_backup)

    chars_after = len(marker)
    chars_saved = max(0, chars_before - chars_after)

    print(f"{'[DRY RUN] ' if dry_run else ''}Range {from_pos}..{to_pos} "
          f"({len(range_objs)} messages) collapsed to 1 survivor")
    print(f"Sidecars saved: {len(sidecar_paths) if not dry_run else len(range_objs)} "
          f"-> {rel_dir}/")
    print(f"Chars saved:    {chars_saved:,}")
    print(f"Est. tokens:    {estimate_tokens(chars_saved):,}")

    return {
        "collapsed_count": len(range_objs),
        "sidecars": [str(p) for p in sidecar_paths],
        "chars_saved": chars_saved,
        "est_tokens_saved": estimate_tokens(chars_saved),
    }
