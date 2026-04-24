"""Persist an entire message — all its blocks collapse to one `<persisted-message>`
marker, the original message JSON goes to a sidecar.

Two safety rules:
  1. Refuse to persist the leaf message in the active chain (would orphan
     the resume cursor). Raises LeafPersistRefused.
  2. If the target message contains any `tool_use`, also persist the matching
     `tool_result` user message that follows. Otherwise the resumed API call
     would have an orphaned tool_use, which the API rejects.
"""

from __future__ import annotations

import json
from pathlib import Path

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    save_session,
    walk_active_chain,
)
from .persist_layout import persist_dir, to_marker_path

PREVIEW_CHARS = 1024
DEFAULT_SUMMARY = "[no summary provided]"


class LeafPersistRefused(RuntimeError):
    """Raised when persist_message is called on the leaf message of the
    active chain. The leaf is the resume cursor; persisting it would orphan
    any subsequent resume."""


def _content_of(obj):
    msg = obj.get("message", {})
    if isinstance(msg, dict) and "content" in msg:
        return msg, msg["content"]
    return obj, obj.get("content")


def _preview_of_message(obj) -> str:
    """Cheap human-readable preview of a message's content for the marker."""
    _, content = _content_of(obj)
    parts = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "thinking":
                parts.append(f"[thinking: {block.get('thinking', '')[:200]}]")
            elif t == "tool_use":
                parts.append(f"[tool_use {block.get('name', '?')}]")
            elif t == "tool_result":
                parts.append("[tool_result]")
            elif t == "image":
                parts.append("[image]")
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(parts)[:PREVIEW_CHARS]


def _build_marker(rel_path: str, original_chars: int, summary: str | None,
                  preview: str) -> str:
    s = summary if summary is not None else DEFAULT_SUMMARY
    return (
        f"<persisted-message>\n"
        f"Saved to: {rel_path} ({original_chars} chars)\n"
        f"Summary: {s}\n"
        f"\n"
        f"Preview:\n"
        f"{preview}\n"
        f"</persisted-message>"
    )


def _collect_tool_use_ids(obj):
    """Return the set of tool_use ids inside a message's content (if any)."""
    _, content = _content_of(obj)
    if not isinstance(content, list):
        return set()
    return {
        block.get("id")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("id")
    }


def _has_tool_result_for(obj, ids):
    """Does this message carry a tool_result for any id in `ids`?"""
    if not ids:
        return False
    _, content = _content_of(obj)
    if not isinstance(content, list):
        return False
    for block in content:
        if (isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in ids):
            return True
    return False


def _persist_one_message(obj, summary: str | None, session_path):
    """Mutate `obj` in place: write its sidecar, replace content with marker."""
    msg_uuid = obj.get("uuid", "unknown")
    out_dir = persist_dir(session_path, "message")
    sidecar = out_dir / f"{msg_uuid}.json"
    rel = to_marker_path(sidecar, session_path)

    # Save full original message (everything except the metadata that shouldn't
    # round-trip — keep envelope keys for forensic value).
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    sidecar.write_text(payload, encoding="utf-8")

    preview = _preview_of_message(obj)
    marker = _build_marker(rel, len(payload), summary, preview)

    msg, _ = _content_of(obj)
    msg["content"] = [{"type": "text", "text": marker}]
    return len(payload), len(marker)


def persist_message(session_path, chain_pos: int, summary: str | None = None,
                    dry_run: bool = False, no_backup: bool = False):
    """Persist a single message at chain position `chain_pos`.

    If the message contains any tool_use, the next user message that carries
    a matching tool_result is also persisted (uses the same summary by default).
    """
    objects = load_session(session_path)
    chain = walk_active_chain(objects, build_uuid_index(objects))

    if chain_pos < 0 or chain_pos >= len(chain):
        raise IndexError(f"chain_pos {chain_pos} out of range (0-{len(chain)-1})")

    if chain_pos == len(chain) - 1:
        raise LeafPersistRefused(
            f"refusing to persist leaf message at pos {chain_pos} — "
            f"would orphan the resume cursor"
        )

    target = chain[chain_pos]
    target_uuid = target.get("uuid")
    tool_ids = _collect_tool_use_ids(target)

    # Find the matching tool_result message (if any) — must be among descendants
    # in the active chain, but in practice it's the next message.
    pair_uuid = None
    if tool_ids:
        for next_obj in chain[chain_pos + 1:]:
            if _has_tool_result_for(next_obj, tool_ids):
                # Don't pair if pairing would persist the leaf
                if next_obj.get("uuid") != chain[-1].get("uuid"):
                    pair_uuid = next_obj.get("uuid")
                break

    persisted = 0
    chars_saved = 0
    if not dry_run:
        # Iterate the on-disk objects (not the chain copy — same dicts though)
        for obj in objects:
            uid = obj.get("uuid")
            if uid == target_uuid or uid == pair_uuid:
                orig, new = _persist_one_message(obj, summary, session_path)
                persisted += 1
                chars_saved += max(0, orig - new)
        save_session(session_path, objects, create_backup=not no_backup)
    else:
        persisted = 1 + (1 if pair_uuid else 0)

    print(f"{'[DRY RUN] ' if dry_run else ''}Messages persisted: {persisted}")
    if pair_uuid:
        print(f"  (auto-paired tool_use/tool_result)")
    print(f"Characters saved:   {chars_saved:,}")
    print(f"Est. tokens saved:  {estimate_tokens(chars_saved):,}")

    return {
        "persisted_count": persisted,
        "auto_paired": pair_uuid is not None,
        "chars_saved": chars_saved,
        "est_tokens_saved": estimate_tokens(chars_saved),
    }
