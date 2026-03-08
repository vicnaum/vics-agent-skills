"""Compact a session before a specific message -- extract dialogue from the early
portion into a summary and keep the later portion intact with fresh UUIDs."""

import json
import uuid
import copy
from datetime import datetime, timezone
from pathlib import Path

from .chain import load_session, build_uuid_index, walk_active_chain, save_session, estimate_tokens


def extract_dialogue(chain_slice):
    """Extract human-readable dialogue from a chain slice.

    Returns a single string with User/Assistant prefixed lines joined by double newlines.
    Skips tool-only messages, meta/commands, interrupts, thinking blocks, and tool_use blocks.
    """
    lines = []

    for msg in chain_slice:
        msg_type = msg.get("type")
        message = msg.get("message", {})
        role = message.get("role") if isinstance(message, dict) else None
        content = message.get("content") if isinstance(message, dict) else msg.get("content")

        if msg_type == "user" or role == "user":
            # Skip tool_result-only messages
            if isinstance(content, list):
                non_tool_result = [b for b in content
                                   if not (isinstance(b, dict) and b.get("type") == "tool_result")]
                if not non_tool_result:
                    continue

            # Skip meta/commands and interrupts
            content_str = ""
            if isinstance(content, str):
                content_str = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                content_str = " ".join(parts)

            if not content_str.strip():
                continue
            if "<command-name>" in content_str or "<local-command" in content_str:
                continue
            if "[Request interrupted" in content_str:
                continue

            lines.append(f"User: {content_str.strip()}")

        elif msg_type == "assistant" or role == "assistant":
            # Extract only text blocks
            text_parts = []
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            text_parts.append(text)
                    elif isinstance(block, str):
                        text_parts.append(block)

            if not text_parts:
                continue

            combined = " ".join(text_parts).strip()
            if combined:
                lines.append(f"Assistant: {combined}")

        # Skip all other types (system, progress, etc.)

    return "\n\n".join(lines)


def compact_before(session_path, before_pos, dry_run=False, no_backup=False,
                   output_path=None, slug=None):
    """Compact a session by summarizing everything before a given chain position.

    Parameters:
        session_path: source JSONL file path
        before_pos: chain position -- everything BEFORE this becomes the summary,
                    everything from this position onward is kept intact
        dry_run: report only, do not write
        no_backup: skip .bak creation
        output_path: where to write; if None, generate a UUID-based path in same dir
        slug: custom slug; if None, generate one like "compact-{first8}"

    Returns a stats dict with counts, token estimates, and output path.
    """
    session_path = Path(session_path).expanduser()
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return {}

    if before_pos < 1 or before_pos >= len(chain):
        print(f"Invalid before_pos={before_pos}. Chain has {len(chain)} messages (valid range: 1..{len(chain) - 1}).")
        return {}

    # Split chain
    pre_cut = chain[:before_pos]
    post_cut = chain[before_pos:]

    # Extract dialogue from pre-cut
    dialogue_text = extract_dialogue(pre_cut)

    # Extract cwd and version from the first message that has them
    cwd = None
    version = None
    for msg in chain:
        if cwd is None and msg.get("cwd"):
            cwd = msg["cwd"]
        if version is None and msg.get("version"):
            version = msg["version"]
        if cwd and version:
            break

    # Generate IDs
    new_session_id = str(uuid.uuid4())
    boundary_uuid = str(uuid.uuid4())
    summary_uuid = str(uuid.uuid4())

    if slug is None:
        slug = f"compact-{new_session_id[:8]}"

    if output_path is None:
        output_path = session_path.parent / f"{new_session_id}.jsonl"
    else:
        output_path = Path(output_path).expanduser()

    # Base timestamp
    base_ts = datetime.now(timezone.utc).isoformat()

    # Estimate pre-cut tokens
    pre_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in pre_cut)
    pre_tokens = estimate_tokens(pre_chars)

    # --- Build new JSONL ---
    new_objects = []

    # Line 0: compact_boundary
    boundary = {
        "parentUuid": None,
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": boundary_uuid,
        "sessionId": new_session_id,
        "slug": slug,
        "timestamp": base_ts,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "version": version,
        "level": "info",
        "isMeta": False,
        "content": "Conversation compacted",
        "compactMetadata": {
            "trigger": "manual",
            "preTokens": pre_tokens,
        },
    }
    new_objects.append(boundary)

    # Line 1: compact summary
    summary = {
        "parentUuid": boundary_uuid,
        "type": "user",
        "uuid": summary_uuid,
        "sessionId": new_session_id,
        "slug": slug,
        "timestamp": _offset_ts(base_ts, milliseconds=1),
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "version": version,
        "message": {
            "role": "user",
            "content": f"[Prior context summary]:\n\n{dialogue_text}",
        },
        "isCompactSummary": True,
        "isVisibleInTranscriptOnly": True,
    }
    new_objects.append(summary)

    # Lines 2+: intact messages from post-cut with new UUIDs
    old_to_new = {}
    for i, msg in enumerate(post_cut):
        new_uuid = str(uuid.uuid4())
        old_uuid = msg.get("uuid")
        if old_uuid:
            old_to_new[old_uuid] = new_uuid

    for i, msg in enumerate(post_cut):
        new_msg = copy.deepcopy(msg)

        # Assign new UUID
        old_uuid = msg.get("uuid")
        new_msg["uuid"] = old_to_new.get(old_uuid, str(uuid.uuid4()))

        # Remap parentUuid
        if i == 0:
            new_msg["parentUuid"] = summary_uuid
        else:
            old_parent = msg.get("parentUuid")
            new_msg["parentUuid"] = old_to_new.get(old_parent, old_parent)

        # Set consistent sessionId and slug
        new_msg["sessionId"] = new_session_id
        new_msg["slug"] = slug

        # Shift timestamp
        new_msg["timestamp"] = _offset_ts(base_ts, seconds=2, milliseconds=500 * i)

        # Remove forkedFrom if present
        new_msg.pop("forkedFrom", None)

        new_objects.append(new_msg)

    # Verify chain: walk from last to root, check null-terminated
    chain_ok = _verify_chain(new_objects)

    # Stats
    summary_chars = len(dialogue_text)
    stats = {
        "pre_cut_messages": len(pre_cut),
        "post_cut_messages": len(post_cut),
        "summary_chars": summary_chars,
        "summary_est_tokens": summary_chars // 4,
        "output_path": str(output_path),
        "session_id": new_session_id,
    }

    # Print report
    print(f"Pre-cut messages:    {stats['pre_cut_messages']}")
    print(f"Post-cut messages:   {stats['post_cut_messages']}")
    print(f"Summary chars:       {stats['summary_chars']:,}")
    print(f"Summary est tokens:  {stats['summary_est_tokens']:,}")
    print(f"Chain verified:      {'OK' if chain_ok else 'FAILED'}")
    print(f"Output path:         {stats['output_path']}")
    print(f"Session ID:          {stats['session_id']}")
    print(f"\nResume with:  claude -r {new_session_id}")

    if dry_run:
        print("\n[dry run] No changes written.")
    else:
        save_session(output_path, new_objects, create_backup=not no_backup)
        print(f"\nSession saved: {output_path}")

    return stats


def _offset_ts(base_iso, seconds=0, milliseconds=0):
    """Offset an ISO timestamp by the given seconds and milliseconds."""
    from datetime import timedelta
    dt = datetime.fromisoformat(base_iso)
    dt += timedelta(seconds=seconds, milliseconds=milliseconds)
    return dt.isoformat()


def _verify_chain(objects):
    """Verify the chain walks from last message to root (null parentUuid)."""
    uuid_index = build_uuid_index(objects)

    # Find last message
    last = objects[-1] if objects else None
    if last is None:
        return False

    current = last
    visited = set()
    while current is not None:
        cur_uuid = current.get("uuid")
        if cur_uuid in visited:
            print(f"  [chain verify] cycle detected at {cur_uuid}")
            return False
        visited.add(cur_uuid)

        parent_uuid = current.get("parentUuid")
        if parent_uuid is None:
            return True
        current = uuid_index.get(parent_uuid)
        if current is None:
            print(f"  [chain verify] broken link: parentUuid {parent_uuid} not found")
            return False

    return False
