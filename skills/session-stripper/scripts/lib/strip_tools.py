"""Strip tool call inputs and tool result outputs from Claude Code JSONL sessions."""

import json

from .chain import load_session, build_uuid_index, walk_active_chain, resolve_range, save_session, estimate_tokens


def _get_content(obj):
    """Get the content field from a message object, handling the message wrapper."""
    msg = obj.get("message", {})
    if isinstance(msg, dict):
        c = msg.get("content")
        if c is not None:
            return c
    return obj.get("content")


def _set_content(obj, content):
    """Set the content field on a message object, handling the message wrapper."""
    msg = obj.get("message", {})
    if isinstance(msg, dict) and "content" in msg:
        msg["content"] = content
    else:
        obj["content"] = content


def _build_tool_use_id_to_name(objects):
    """Scan all objects for tool_use blocks and build a map of tool_use_id -> tool_name."""
    mapping = {}
    for obj in objects:
        content = _get_content(obj) if isinstance(obj, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id")
                tool_name = block.get("name")
                if tool_id and tool_name:
                    mapping[tool_id] = tool_name
    return mapping


def _content_char_count(content):
    """Count the total characters in a content field (string, list of dicts, etc.)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, dict):
        return len(json.dumps(content, ensure_ascii=False))
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, str):
                total += len(item)
            elif isinstance(item, dict):
                total += len(json.dumps(item, ensure_ascii=False))
        return total
    return len(str(content))


def _keep_last_lines(text, n):
    """Keep only the last N lines of a text string."""
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])


def strip_tools(session_path, dry_run=False, no_backup=False,
                from_pos=None, to_pos=None,
                only_inputs=False, only_results=False,
                tool_names=None, keep_last_lines=None):
    """Strip tool call inputs and/or tool result outputs from a session.

    Returns a stats dict with counts and character savings.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return {"tool_use_cleared": 0, "tool_result_cleared": 0, "images_cleared": 0,
                "chars_saved": 0, "est_tokens_saved": 0, "by_tool": {}}

    start, end = resolve_range(chain, from_pos, to_pos)
    target_uuids = {chain[i]["uuid"] for i in range(start, end + 1)}

    # Build tool_use_id -> tool_name mapping from the entire session
    tool_id_to_name = _build_tool_use_id_to_name(objects)

    # Convert tool_names to a set for fast lookup
    tool_name_set = set(tool_names) if tool_names else None

    stats = {
        "tool_use_cleared": 0,
        "tool_result_cleared": 0,
        "images_cleared": 0,
        "chars_saved": 0,
        "est_tokens_saved": 0,
        "by_tool": {},
    }

    for obj in objects:
        obj_uuid = obj.get("uuid")
        if obj_uuid not in target_uuids:
            continue

        content = _get_content(obj)
        if not isinstance(content, list):
            continue

        new_content = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")

            # --- image blocks: always strip (unless scoped to only_inputs/only_results) ---
            if block_type == "image":
                if only_inputs or only_results:
                    # If user explicitly scoped to only inputs or only results,
                    # images aren't tools — pass them through
                    new_content.append(block)
                else:
                    chars = _content_char_count(block)
                    stats["images_cleared"] += 1
                    stats["chars_saved"] += chars
                continue

            # --- tool_use blocks ---
            if block_type == "tool_use":
                tool_name = block.get("name", "unknown")

                if tool_name_set and tool_name not in tool_name_set:
                    new_content.append(block)
                    continue

                if only_results:
                    new_content.append(block)
                    continue

                old_input = block.get("input")
                old_chars = _content_char_count(old_input)

                block["input"] = {}
                new_chars = _content_char_count(block["input"])
                saved = max(0, old_chars - new_chars)

                stats["tool_use_cleared"] += 1
                stats["chars_saved"] += saved

                by_tool = stats["by_tool"].setdefault(tool_name, {"cleared": 0, "chars_saved": 0})
                by_tool["cleared"] += 1
                by_tool["chars_saved"] += saved

                new_content.append(block)
                continue

            # --- tool_result blocks ---
            if block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                tool_name = tool_id_to_name.get(tool_use_id, "unknown")

                if tool_name_set and tool_name not in tool_name_set:
                    new_content.append(block)
                    continue

                if only_inputs:
                    new_content.append(block)
                    continue

                result_content = block.get("content")
                old_chars = _content_char_count(result_content)
                is_error = block.get("is_error", False)
                # API requires non-empty content when is_error=true
                empty_placeholder = "[stripped]" if is_error else ""

                if keep_last_lines is not None:
                    # Preserve last N lines
                    if isinstance(result_content, str):
                        block["content"] = _keep_last_lines(result_content, keep_last_lines) or empty_placeholder
                    elif isinstance(result_content, list):
                        new_items = []
                        for item in result_content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                item = dict(item)
                                item["text"] = _keep_last_lines(item.get("text", ""), keep_last_lines)
                            new_items.append(item)
                        block["content"] = new_items or empty_placeholder
                    else:
                        block["content"] = empty_placeholder
                else:
                    block["content"] = empty_placeholder

                # Drop is_error when content is empty — the API rejects
                # tool_result with is_error=true and empty content. Once we've
                # cleared the error message itself, the error flag is meaningless.
                if block.get("is_error") is True and not block.get("content"):
                    block.pop("is_error", None)

                new_chars = _content_char_count(block["content"])
                saved = max(0, old_chars - new_chars)

                stats["tool_result_cleared"] += 1
                stats["chars_saved"] += saved

                by_tool = stats["by_tool"].setdefault(tool_name, {"cleared": 0, "chars_saved": 0})
                by_tool["cleared"] += 1
                by_tool["chars_saved"] += saved

                new_content.append(block)
                continue

            # All other block types pass through
            new_content.append(block)

        _set_content(obj, new_content)

    stats["est_tokens_saved"] = estimate_tokens(stats["chars_saved"])

    # Print summary
    print(f"Tool use inputs cleared:  {stats['tool_use_cleared']}")
    print(f"Tool results cleared:     {stats['tool_result_cleared']}")
    print(f"Images removed:           {stats['images_cleared']}")
    print(f"Characters saved:         {stats['chars_saved']:,}")
    print(f"Est. tokens saved:        {stats['est_tokens_saved']:,}")
    if stats["by_tool"]:
        print("\nBy tool:")
        for name in sorted(stats["by_tool"]):
            t = stats["by_tool"][name]
            print(f"  {name:30s}  cleared: {t['cleared']:4d}  chars: {t['chars_saved']:>10,}")

    if dry_run:
        print("\n[dry run] No changes written.")
    else:
        save_session(session_path, objects, create_backup=not no_backup)
        print(f"\nSession saved: {session_path}")

    return stats
