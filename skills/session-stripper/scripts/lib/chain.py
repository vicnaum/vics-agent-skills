"""Core functions for parsing and manipulating Claude Code JSONL session files."""

import json
import shutil
from pathlib import Path


def load_session(path):
    """Read a JSONL file, return list of parsed JSON objects."""
    path = Path(path).expanduser()
    objects = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                objects.append(json.loads(line))
    return objects


def build_uuid_index(objects):
    """Build a mapping from uuid -> object for all objects that have a uuid field."""
    return {obj["uuid"]: obj for obj in objects if "uuid" in obj}


def walk_active_chain(objects, uuid_index=None):
    """Walk parentUuid from the last non-sidechain leaf back to root, return in chronological order."""
    if uuid_index is None:
        uuid_index = build_uuid_index(objects)

    # Find children for each uuid
    children = {}
    for obj in objects:
        parent = obj.get("parentUuid")
        if parent is not None:
            children.setdefault(parent, []).append(obj)

    # Find the leaf: start from the last object and follow the non-sidechain path
    # The last message in the file that is a user or assistant message is typically the leaf
    leaf = None
    for obj in reversed(objects):
        if obj.get("type") in ("user", "assistant") and not obj.get("isSidechain"):
            leaf = obj
            break

    if leaf is None:
        return []

    # Walk backwards from leaf to root
    chain = []
    current = leaf
    while current is not None:
        chain.append(current)
        parent_uuid = current.get("parentUuid")
        if parent_uuid is None:
            break
        current = uuid_index.get(parent_uuid)

    chain.reverse()
    return chain


def resolve_range(chain, from_pos=None, to_pos=None):
    """Resolve --from/--to into valid chain indices (inclusive). Clamp to valid range."""
    start = from_pos if from_pos is not None else 0
    end = to_pos if to_pos is not None else len(chain) - 1
    start = max(0, min(start, len(chain) - 1))
    end = max(0, min(end, len(chain) - 1))
    return (start, end)


def remove_objects_and_rewire(objects, uuids_to_remove):
    """Remove objects by uuid and rewire parentUuid on descendants to skip them.

    When a message is dropped, any child pointing to it is re-parented to the
    nearest surviving ancestor (walking up parentUuid). This keeps the active
    chain unbroken so the API doesn't see dangling references.

    Returns:
        (survivors, removed_count, rewired_count)
    """
    uuids_to_remove = set(u for u in uuids_to_remove if u is not None)
    if not uuids_to_remove:
        return objects, 0, 0

    uuid_to_obj = {obj.get("uuid"): obj for obj in objects if obj.get("uuid")}

    def find_surviving_ancestor(uuid):
        seen = set()
        cur = uuid
        while cur is not None and cur in uuids_to_remove:
            if cur in seen:
                return None
            seen.add(cur)
            obj = uuid_to_obj.get(cur)
            if obj is None:
                return None
            cur = obj.get("parentUuid")
        return cur

    survivors = []
    rewired = 0
    for obj in objects:
        if obj.get("uuid") in uuids_to_remove:
            continue
        p = obj.get("parentUuid")
        if p in uuids_to_remove:
            obj["parentUuid"] = find_surviving_ancestor(p)
            rewired += 1
        survivors.append(obj)

    return survivors, len(uuids_to_remove), rewired


def save_session(path, objects, create_backup=True):
    """Write all objects as JSONL. Optionally create a .bak backup first."""
    path = Path(path).expanduser()
    if create_backup:
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists() and path.exists():
            shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        for obj in objects:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def format_content_preview(content, max_len=100):
    """Return a short human-readable preview of a message's content field."""
    if content is None:
        return ""
    if isinstance(content, str):
        preview = content.replace("\n", " ").strip()
        if len(preview) > max_len:
            return preview[:max_len] + "..."
        return preview
    if isinstance(content, list):
        types = [block.get("type", "?") if isinstance(block, dict) else "str" for block in content]
        summary = ", ".join(types)
        # Also try to grab the first text block for context
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                text = text.replace("\n", " ").strip()
                if text:
                    snippet = text[:max_len] + "..." if len(text) > max_len else text
                    return f"[{summary}] {snippet}"
        return f"[{summary}]"
    return str(content)[:max_len]


def estimate_tokens(chars):
    """Rough token estimate from character count."""
    return chars // 4


def count_content_chars(obj):
    """Count characters by block type in a message object's content field."""
    counts = {"tool_use": 0, "tool_result": 0, "thinking": 0, "text": 0, "image": 0, "other": 0, "total": 0}
    msg = obj.get("message", {}) if isinstance(obj, dict) else {}
    content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
    if content is None:
        return counts
    if isinstance(content, str):
        counts["text"] = len(content)
        counts["total"] = len(content)
        return counts
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                counts["text"] += len(block)
                counts["total"] += len(block)
            elif isinstance(block, dict):
                block_type = block.get("type", "other")
                block_text = json.dumps(block, ensure_ascii=False)
                char_count = len(block_text)
                if block_type in counts:
                    counts[block_type] += char_count
                else:
                    counts["other"] += char_count
                counts["total"] += char_count
    return counts
