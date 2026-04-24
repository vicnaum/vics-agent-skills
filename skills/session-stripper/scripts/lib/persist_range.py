"""persist_range — dispatcher that fans out to per-kind persist commands
across a chain-position range.

Supported kinds: tool, thinking, text, image, message.

Reads optional `summaries_file` JSON (`{"pos:N": "...", "toolu_X": "...",
"msg:UUID": "..."}`) so the calling layer (Claude Code main loop, a wrapper
script, etc.) can supply summaries without session-stripper invoking AI itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    walk_active_chain,
)


KNOWN_DISPATCH_KINDS = ("tool", "thinking", "text", "image", "message")


def _load_summaries(path) -> dict:
    if path is None:
        return {}
    p = Path(path).expanduser()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def persist_range(session_path, *, from_pos: int = 0,
                  to_pos: int | None = None,
                  kinds=("text", "thinking"),
                  min_chars: int = 0,
                  keep_recent: int = 0,
                  summaries_file=None,
                  dry_run: bool = False, no_backup: bool = False):
    """Dispatch persist operations across a range.

    Each kind has its own persist module, called with this range. Tool and
    image are id-keyed; text/thinking are pos-keyed; message is per-pos.
    """
    invalid = [k for k in kinds if k not in KNOWN_DISPATCH_KINDS]
    if invalid:
        raise ValueError(f"unknown kinds: {invalid} (known: {KNOWN_DISPATCH_KINDS})")

    summaries = _load_summaries(summaries_file)

    # Compute resolved range once (some downstream calls accept it).
    objects = load_session(session_path)
    chain = walk_active_chain(objects, build_uuid_index(objects))
    chain_len = len(chain)
    start = max(0, from_pos)
    end = (chain_len - 1) if to_pos is None else min(chain_len - 1, to_pos)

    total_chars_saved = 0
    summary: dict = {}

    for kind in kinds:
        if kind == "thinking":
            from .persist_tools import persist_thinking_bulk
            stats = persist_thinking_bulk(
                session_path,
                from_pos=start, to_pos=end,
                summary_map={
                    int(k.split(":", 1)[1]): v
                    for k, v in summaries.items()
                    if isinstance(k, str) and k.startswith("pos:") and k.split(":", 1)[1].isdigit()
                } or None,
                dry_run=dry_run, no_backup=no_backup,
            )
            total_chars_saved += (stats or {}).get("chars_saved", 0)
            summary["thinking"] = stats

        elif kind == "text":
            from .persist_text import persist_text_bulk
            stats = persist_text_bulk(
                session_path,
                from_pos=start, to_pos=end,
                min_chars=min_chars, keep_recent=keep_recent,
                summaries=summaries,
                dry_run=dry_run, no_backup=no_backup,
            )
            total_chars_saved += stats.get("chars_saved", 0)
            summary["text"] = stats

        elif kind == "tool":
            from .persist_tools import persist_tools_bulk
            stats = persist_tools_bulk(
                session_path,
                from_pos=start, to_pos=end,
                keep_recent=keep_recent,
                dry_run=dry_run, no_backup=no_backup,
            )
            total_chars_saved += (stats or {}).get("chars_saved", 0)
            summary["tool"] = stats

        elif kind == "image":
            # image needs a transcripts dir — skip if not provided. Caller
            # who wants images persisted should call replace-images directly
            # (it has --dir). We surface a hint here for clarity.
            summary["image"] = {"skipped": "use replace-images --dir <transcripts-dir> directly"}

        elif kind == "message":
            from .persist_message import persist_message, LeafPersistRefused
            persisted = 0
            for pos in range(start, min(end, chain_len - 2) + 1):  # never the leaf
                key = f"pos:{pos}"
                msg_summary = summaries.get(key)
                try:
                    s = persist_message(
                        session_path, chain_pos=pos, summary=msg_summary,
                        dry_run=dry_run, no_backup=no_backup,
                    )
                    persisted += s.get("persisted_count", 0)
                    total_chars_saved += s.get("chars_saved", 0)
                except LeafPersistRefused:
                    continue
            summary["message"] = {"persisted_count": persisted}

    print(f"\n=== persist-range summary ===")
    for kind, st in summary.items():
        print(f"  {kind:10s} {st}")
    print(f"  net chars saved: {total_chars_saved:,}  (~{estimate_tokens(total_chars_saved):,} tokens)")

    return summary
