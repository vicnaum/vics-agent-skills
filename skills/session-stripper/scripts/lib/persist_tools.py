"""Persist and summarize tool results from Claude Code JSONL sessions."""

import json
from pathlib import Path

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


def _get_persist_dir(session_path):
    """Return the directory for persisted tool results, creating it if needed."""
    d = Path(session_path).expanduser().parent / '.tool-results'
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    """Count the total characters in a content field."""
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


def _content_to_text(content):
    """Convert content field to a text string for display/saving."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, indent=2))
        return "\n".join(parts)
    return str(content)


def show_tool(session_path, tool_use_id=None, chain_pos=None, context_lines=2):
    """Show a specific tool call with input, output, and surrounding context.

    If tool_use_id="list", list ALL tool calls in the active chain.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return None

    tool_id_to_name = _build_tool_use_id_to_name(objects)

    # --- LIST mode ---
    if tool_use_id == "list":
        rows = []
        for pos, obj in enumerate(chain):
            content = _get_content(obj)
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tid = block.get("id", "")
                    tname = block.get("name", "unknown")
                    input_chars = _content_char_count(block.get("input"))
                    # Find matching result
                    result_chars = 0
                    for robj in chain[pos:]:
                        rc = _get_content(robj)
                        if not isinstance(rc, list):
                            continue
                        for rb in rc:
                            if isinstance(rb, dict) and rb.get("type") == "tool_result" and rb.get("tool_use_id") == tid:
                                result_chars = _content_char_count(rb.get("content"))
                                break
                    rows.append((pos, tname, tid, input_chars, result_chars))

        if not rows:
            print("No tool calls found in active chain.")
            return []

        # Print table
        print(f"{'Pos':>5}  {'Tool Name':30s}  {'Tool Use ID':40s}  {'Input':>8}  {'Result':>8}")
        print("-" * 97)
        for pos, tname, tid, ic, rc in rows:
            print(f"{pos:>5}  {tname:30s}  {tid:40s}  {ic:>8,}  {rc:>8,}")
        print(f"\nTotal: {len(rows)} tool calls")
        return rows

    # --- SINGLE tool mode ---
    # Find the tool_use block
    found_tool_use = None
    found_pos = None

    if tool_use_id:
        for pos, obj in enumerate(chain):
            content = _get_content(obj)
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    found_tool_use = block
                    found_pos = pos
                    break
            if found_tool_use:
                break
    elif chain_pos is not None:
        if 0 <= chain_pos < len(chain):
            obj = chain[chain_pos]
            content = _get_content(obj)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        found_tool_use = block
                        found_pos = chain_pos
                        tool_use_id = block.get("id")
                        break
        if not found_tool_use:
            print(f"No tool_use block found at chain position {chain_pos}.")
            return None

    if not found_tool_use:
        print(f"Tool call not found: {tool_use_id or f'pos {chain_pos}'}")
        return None

    tool_name = found_tool_use.get("name", "unknown")
    tool_input = found_tool_use.get("input", {})
    input_chars = _content_char_count(tool_input)

    # Find matching tool_result
    result_content = None
    result_chars = 0
    for obj in chain[found_pos:]:
        content = _get_content(obj)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                result_content = block.get("content")
                result_chars = _content_char_count(result_content)
                break
        if result_content is not None:
            break

    # Extract context before/after
    context_before = []
    context_after = []

    # Gather text messages before
    for i in range(found_pos - 1, -1, -1):
        if len(context_before) >= context_lines:
            break
        obj = chain[i]
        obj_type = obj.get("type", "")
        content = _get_content(obj)
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", "").strip())
                elif isinstance(block, str):
                    text_parts.append(block.strip())
            text = " ".join(text_parts).strip()
        if text:
            context_before.insert(0, f"{obj_type.capitalize()}: {text[:200]}")

    # Gather text messages after
    for i in range(found_pos + 1, len(chain)):
        if len(context_after) >= context_lines:
            break
        obj = chain[i]
        obj_type = obj.get("type", "")
        content = _get_content(obj)
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", "").strip())
                elif isinstance(block, str):
                    text_parts.append(block.strip())
            text = " ".join(text_parts).strip()
        if text:
            context_after.append(f"{obj_type.capitalize()}: {text[:200]}")

    # Print formatted report
    print(f"=== Tool Call: {tool_name} (id: {tool_use_id}) at chain pos {found_pos} ===")
    print()

    if context_before:
        print("--- Context Before ---")
        for line in context_before:
            print(line)
        print()

    print("--- Tool Input ---")
    print(json.dumps(tool_input, indent=2, ensure_ascii=False))
    print()

    print("--- Tool Result ---")
    if result_content is not None:
        print(_content_to_text(result_content)[:2000])
        if result_chars > 2000:
            print(f"\n... ({result_chars:,} chars total, showing first 2000)")
    else:
        print("(no result found)")
    print()

    if context_after:
        print("--- Context After ---")
        for line in context_after:
            print(line)
        print()

    print(f"Input: {input_chars:,} chars | Result: {result_chars:,} chars | Total: {input_chars + result_chars:,} chars")

    return {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "chain_pos": found_pos,
        "input": tool_input,
        "result": result_content,
        "input_chars": input_chars,
        "result_chars": result_chars,
        "context_before": context_before,
        "context_after": context_after,
    }


def persist_tool_result(session_path, tool_use_id, summary=None, dry_run=False, no_backup=False):
    """Persist a single tool result to a file and replace in the session with a reference.

    Returns stats dict.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)
    tool_id_to_name = _build_tool_use_id_to_name(objects)
    tool_name = tool_id_to_name.get(tool_use_id, "unknown")

    # Find the tool_result block
    target_block = None
    for obj in objects:
        content = _get_content(obj)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                target_block = block
                break
        if target_block:
            break

    if not target_block:
        print(f"No tool_result found for tool_use_id: {tool_use_id}")
        return None

    original_content = target_block.get("content")
    original_chars = _content_char_count(original_content)

    # Determine persist path
    persist_dir = _get_persist_dir(session_path)
    if isinstance(original_content, list):
        persist_path = persist_dir / f"{tool_use_id}.json"
    else:
        persist_path = persist_dir / f"{tool_use_id}.txt"

    # Build replacement content
    if summary:
        replacement = (
            f"<persisted-output>\n"
            f"Summary: {summary}\n"
            f"\n"
            f"Full output saved to: {persist_path}\n"
            f"</persisted-output>"
        )
    else:
        replacement = (
            f"<persisted-output>\n"
            f"Full output saved to: {persist_path}\n"
            f"</persisted-output>"
        )

    new_chars = len(replacement)
    chars_saved = max(0, original_chars - new_chars)

    print(f"Tool: {tool_name} (id: {tool_use_id})")
    print(f"Original: {original_chars:,} chars -> New: {new_chars:,} chars (saving {chars_saved:,} chars)")
    print(f"Persist to: {persist_path}")
    if summary:
        print(f"Summary: {summary}")

    if dry_run:
        print("\n[dry run] No changes written.")
    else:
        # Save original content to file
        if isinstance(original_content, list):
            with open(persist_path, "w", encoding="utf-8") as f:
                json.dump(original_content, f, indent=2, ensure_ascii=False)
        else:
            with open(persist_path, "w", encoding="utf-8") as f:
                f.write(_content_to_text(original_content))

        # Replace in session
        target_block["content"] = replacement
        save_session(session_path, objects, create_backup=not no_backup)
        print(f"\nSession saved: {session_path}")

    return {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "original_chars": original_chars,
        "new_chars": new_chars,
        "chars_saved": chars_saved,
        "persist_path": str(persist_path),
        "summary_provided": summary is not None,
    }


def persist_tools_bulk(session_path, dry_run=False, no_backup=False,
                       from_pos=None, to_pos=None, tool_names=None,
                       keep_recent=3, summary_map=None):
    """Bulk persist tool results to files with optional summaries.

    Returns stats dict.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return {"persisted_count": 0, "skipped_count": 0, "chars_saved": 0,
                "est_tokens_saved": 0, "persisted_tools": []}

    start, end = resolve_range(chain, from_pos, to_pos)
    target_uuids = {chain[i]["uuid"] for i in range(start, end + 1)}

    tool_id_to_name = _build_tool_use_id_to_name(objects)
    tool_name_set = set(tool_names) if tool_names else None
    summary_map = summary_map or {}

    # Collect all tool_result blocks in the target range, with their chain position
    results = []
    for pos in range(start, end + 1):
        obj = chain[pos]
        if obj.get("uuid") not in target_uuids:
            continue
        content = _get_content(obj)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            tname = tool_id_to_name.get(tid, "unknown")

            # Filter by tool name
            if tool_name_set and tname not in tool_name_set:
                continue

            results.append({
                "pos": pos,
                "block": block,
                "tool_use_id": tid,
                "tool_name": tname,
            })

    # Exclude the last keep_recent results
    if keep_recent and keep_recent > 0 and len(results) > keep_recent:
        eligible = results[:-keep_recent]
    else:
        eligible = []

    persist_dir = _get_persist_dir(session_path)

    stats = {
        "persisted_count": 0,
        "skipped_count": 0,
        "chars_saved": 0,
        "est_tokens_saved": 0,
        "persisted_tools": [],
    }

    for item in eligible:
        block = item["block"]
        tid = item["tool_use_id"]
        tname = item["tool_name"]
        content = block.get("content")

        # Skip already persisted
        if isinstance(content, str) and "<persisted-output>" in content:
            stats["skipped_count"] += 1
            continue

        original_chars = _content_char_count(content)

        # Determine persist path
        if isinstance(content, list):
            persist_path = persist_dir / f"{tid}.json"
        else:
            persist_path = persist_dir / f"{tid}.txt"

        # Build replacement
        summary = summary_map.get(tid)
        if summary:
            replacement = (
                f"<persisted-output>\n"
                f"Summary: {summary}\n"
                f"\n"
                f"Full output saved to: {persist_path}\n"
                f"</persisted-output>"
            )
        else:
            replacement = (
                f"<persisted-output>\n"
                f"Full output saved to: {persist_path}\n"
                f"</persisted-output>"
            )

        new_chars = len(replacement)
        chars_saved = max(0, original_chars - new_chars)

        if not dry_run:
            # Save original to file
            if isinstance(content, list):
                with open(persist_path, "w", encoding="utf-8") as f:
                    json.dump(content, f, indent=2, ensure_ascii=False)
            else:
                with open(persist_path, "w", encoding="utf-8") as f:
                    f.write(_content_to_text(content))

            # Replace in session
            block["content"] = replacement

        stats["persisted_count"] += 1
        stats["chars_saved"] += chars_saved
        stats["persisted_tools"].append({
            "tool_use_id": tid,
            "tool_name": tname,
            "chars_saved": chars_saved,
        })

    stats["est_tokens_saved"] = estimate_tokens(stats["chars_saved"])

    # Print summary
    print(f"Tool results persisted:   {stats['persisted_count']}")
    print(f"Already persisted (skip): {stats['skipped_count']}")
    print(f"Kept recent (last {keep_recent}):   {len(results) - len(eligible) if results else 0}")
    print(f"Characters saved:         {stats['chars_saved']:,}")
    print(f"Est. tokens saved:        {stats['est_tokens_saved']:,}")

    if stats["persisted_tools"]:
        print("\nPersisted:")
        for t in stats["persisted_tools"]:
            print(f"  {t['tool_name']:30s}  {t['tool_use_id']:40s}  chars saved: {t['chars_saved']:>10,}")

    if dry_run:
        print("\n[dry run] No changes written.")
    else:
        if stats["persisted_count"] > 0:
            save_session(session_path, objects, create_backup=not no_backup)
            print(f"\nSession saved: {session_path}")
            print(f"Tool results saved to: {persist_dir}")
        else:
            print("\nNo tool results to persist.")

    return stats


# ─── Thinking persistence ───────────────────────────────────────────────────


def _extract_thinking_blocks(content):
    """Extract thinking and redacted_thinking blocks from a content list.

    Returns a list of dicts: {type, text, index} for each thinking block found.
    """
    blocks = []
    if not isinstance(content, list):
        return blocks
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            blocks.append({
                "type": "thinking",
                "text": block.get("thinking", ""),
                "index": i,
            })
        elif block.get("type") == "redacted_thinking":
            blocks.append({
                "type": "redacted_thinking",
                "text": block.get("data", ""),
                "index": i,
            })
    return blocks


def _extract_message_text(content):
    """Extract the text portion of a message's content (non-thinking blocks)."""
    if not isinstance(content, list):
        return _content_to_text(content)
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") not in ("thinking", "redacted_thinking"):
                parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts)


def show_thinking(session_path, chain_pos=None, context_lines=2):
    """Show thinking blocks from the active chain.

    If chain_pos is None or "list", list all thinking blocks.
    If chain_pos is an integer, show the thinking block(s) at that position in detail.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return None

    # --- LIST mode ---
    if chain_pos is None or chain_pos == "list":
        rows = []
        for pos, obj in enumerate(chain):
            if obj.get("type") != "assistant":
                continue
            content = _get_content(obj)
            thinking_blocks = _extract_thinking_blocks(content)
            if not thinking_blocks:
                continue

            total_chars = sum(len(b["text"]) for b in thinking_blocks)
            preview = thinking_blocks[0]["text"].replace("\n", " ").strip()[:80]
            rows.append({
                "chain_pos": pos,
                "thinking_chars": total_chars,
                "preview": preview,
                "block_count": len(thinking_blocks),
            })

        if not rows:
            print("No thinking blocks found in active chain.")
            return []

        # Print table
        print(f"{'Pos':>5}  {'Thinking Chars':>14}  {'Text Preview'}")
        print(f"{'───':>5}  {'─────────────':>14}  {'────────────'}")
        for r in rows:
            print(f"{r['chain_pos']:>5}  {r['thinking_chars']:>14,}  {r['preview']}")
        print(f"\nTotal: {len(rows)} assistant messages with thinking blocks")
        return rows

    # --- DETAIL mode ---
    pos = int(chain_pos)
    if pos < 0 or pos >= len(chain):
        print(f"Chain position {pos} out of range (0-{len(chain)-1}).")
        return None

    obj = chain[pos]
    if obj.get("type") != "assistant":
        print(f"Chain position {pos} is not an assistant message (type: {obj.get('type')}).")
        return None

    content = _get_content(obj)
    thinking_blocks = _extract_thinking_blocks(content)
    if not thinking_blocks:
        print(f"No thinking blocks at chain position {pos}.")
        return None

    total_chars = sum(len(b["text"]) for b in thinking_blocks)
    est_tokens = estimate_tokens(total_chars)
    thinking_text = "\n---\n".join(b["text"] for b in thinking_blocks)
    message_text = _extract_message_text(content)

    # Context before
    context_before = []
    for i in range(pos - 1, -1, -1):
        if len(context_before) >= context_lines:
            break
        cobj = chain[i]
        ctype = cobj.get("type", "")
        ccontent = _get_content(cobj)
        text = ""
        if isinstance(ccontent, str):
            text = ccontent.strip()
        elif isinstance(ccontent, list):
            text_parts = []
            for block in ccontent:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", "").strip())
                elif isinstance(block, str):
                    text_parts.append(block.strip())
            text = " ".join(text_parts).strip()
        if text:
            context_before.insert(0, f"{ctype.capitalize()}: {text[:200]}")

    # Context after
    context_after = []
    for i in range(pos + 1, len(chain)):
        if len(context_after) >= context_lines:
            break
        cobj = chain[i]
        ctype = cobj.get("type", "")
        ccontent = _get_content(cobj)
        text = ""
        if isinstance(ccontent, str):
            text = ccontent.strip()
        elif isinstance(ccontent, list):
            text_parts = []
            for block in ccontent:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", "").strip())
                elif isinstance(block, str):
                    text_parts.append(block.strip())
            text = " ".join(text_parts).strip()
        if text:
            context_after.append(f"{ctype.capitalize()}: {text[:200]}")

    # Print formatted report
    print(f"=== Thinking at chain pos {pos} ({total_chars:,} chars, ~{est_tokens:,} tokens) ===")
    print()

    if context_before:
        print("--- Context Before ---")
        for line in context_before:
            print(line)
        print()

    print("--- Thinking Content ---")
    print(thinking_text)
    print()

    print("--- Message Text ---")
    if message_text.strip():
        print(message_text)
    else:
        print("(no text content)")
    print()

    if context_after:
        print("--- Context After ---")
        for line in context_after:
            print(line)
        print()

    return {
        "chain_pos": pos,
        "thinking_chars": total_chars,
        "thinking_text": thinking_text,
        "message_text": message_text,
        "context_before": context_before,
        "context_after": context_after,
    }


def persist_thinking(session_path, chain_pos, summary=None, dry_run=False, no_backup=False):
    """Persist thinking block(s) from a specific assistant message to a file and replace with summary.

    Returns stats dict.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return None

    pos = int(chain_pos)
    if pos < 0 or pos >= len(chain):
        print(f"Chain position {pos} out of range (0-{len(chain)-1}).")
        return None

    obj = chain[pos]
    if obj.get("type") != "assistant":
        print(f"Chain position {pos} is not an assistant message (type: {obj.get('type')}).")
        return None

    content = _get_content(obj)
    thinking_blocks = _extract_thinking_blocks(content)
    if not thinking_blocks:
        print(f"No thinking blocks at chain position {pos}.")
        return None

    msg_uuid = obj.get("uuid", "unknown")
    total_chars = sum(len(b["text"]) for b in thinking_blocks)
    thinking_text = "\n---\n".join(b["text"] for b in thinking_blocks)

    # Determine persist path
    persist_dir = _get_persist_dir(session_path)
    persist_path = persist_dir / f"{msg_uuid}_thinking.txt"

    print(f"Chain pos {pos} (uuid: {msg_uuid})")
    print(f"Thinking: {total_chars:,} chars ({len(thinking_blocks)} block(s))")
    print(f"Persist to: {persist_path}")
    if summary:
        print(f"Summary: {summary}")

    if dry_run:
        chars_saved = total_chars
        if summary:
            replacement_len = len(f"<persisted-thinking>\nSummary: {summary}\n\nFull thinking saved to: {persist_path}\n</persisted-thinking>")
            chars_saved = max(0, total_chars - replacement_len)
        print(f"Would save ~{chars_saved:,} chars")
        print("\n[dry run] No changes written.")
    else:
        # Save thinking to file
        with open(persist_path, "w", encoding="utf-8") as f:
            f.write(thinking_text)

        # Remove thinking blocks from content (in reverse order to preserve indices)
        if isinstance(content, list):
            for b in reversed(thinking_blocks):
                del content[b["index"]]

            # Insert summary block if provided
            if summary:
                summary_block = {
                    "type": "text",
                    "text": (
                        f"<persisted-thinking>\n"
                        f"Summary: {summary}\n"
                        f"\n"
                        f"Full thinking saved to: {persist_path}\n"
                        f"</persisted-thinking>"
                    ),
                }
                content.insert(0, summary_block)

            _set_content(obj, content)

        save_session(session_path, objects, create_backup=not no_backup)
        print(f"\nSession saved: {session_path}")

    summary_chars = 0
    if summary:
        summary_chars = len(f"<persisted-thinking>\nSummary: {summary}\n\nFull thinking saved to: {persist_path}\n</persisted-thinking>")

    return {
        "chain_pos": pos,
        "uuid": msg_uuid,
        "original_chars": total_chars,
        "summary_provided": summary is not None,
        "persist_path": str(persist_path),
        "chars_saved": max(0, total_chars - summary_chars),
    }


def persist_thinking_bulk(session_path, dry_run=False, no_backup=False,
                          from_pos=None, to_pos=None, summary_map=None):
    """Bulk persist all thinking blocks in a range.

    Returns stats dict.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    if not chain:
        print("No active chain found.")
        return {"persisted_count": 0, "chars_saved": 0,
                "est_tokens_saved": 0, "persisted_positions": []}

    start, end = resolve_range(chain, from_pos, to_pos)
    summary_map = summary_map or {}
    persist_dir = _get_persist_dir(session_path)

    stats = {
        "persisted_count": 0,
        "chars_saved": 0,
        "est_tokens_saved": 0,
        "persisted_positions": [],
    }

    for pos in range(start, end + 1):
        obj = chain[pos]
        if obj.get("type") != "assistant":
            continue

        content = _get_content(obj)
        thinking_blocks = _extract_thinking_blocks(content)
        if not thinking_blocks:
            continue

        # Skip already persisted
        if isinstance(content, list):
            has_persisted = any(
                isinstance(b, dict) and b.get("type") == "text"
                and isinstance(b.get("text"), str) and "<persisted-thinking>" in b["text"]
                for b in content
            )
            if has_persisted:
                continue

        msg_uuid = obj.get("uuid", "unknown")
        total_chars = sum(len(b["text"]) for b in thinking_blocks)
        thinking_text = "\n---\n".join(b["text"] for b in thinking_blocks)
        persist_path = persist_dir / f"{msg_uuid}_thinking.txt"

        summary = summary_map.get(pos)

        if not dry_run:
            # Save thinking to file
            with open(persist_path, "w", encoding="utf-8") as f:
                f.write(thinking_text)

            # Remove thinking blocks from content (reverse order)
            if isinstance(content, list):
                for b in reversed(thinking_blocks):
                    del content[b["index"]]

                # Insert summary block if provided
                if summary:
                    summary_block = {
                        "type": "text",
                        "text": (
                            f"<persisted-thinking>\n"
                            f"Summary: {summary}\n"
                            f"\n"
                            f"Full thinking saved to: {persist_path}\n"
                            f"</persisted-thinking>"
                        ),
                    }
                    content.insert(0, summary_block)

                _set_content(obj, content)

        summary_chars = 0
        if summary:
            summary_chars = len(f"<persisted-thinking>\nSummary: {summary}\n\nFull thinking saved to: {persist_path}\n</persisted-thinking>")

        chars_saved = max(0, total_chars - summary_chars)
        stats["persisted_count"] += 1
        stats["chars_saved"] += chars_saved
        stats["persisted_positions"].append(pos)

    stats["est_tokens_saved"] = estimate_tokens(stats["chars_saved"])

    # Print summary
    print(f"Thinking blocks persisted: {stats['persisted_count']}")
    print(f"Characters saved:          {stats['chars_saved']:,}")
    print(f"Est. tokens saved:         {stats['est_tokens_saved']:,}")
    if stats["persisted_positions"]:
        print(f"Positions:                 {stats['persisted_positions']}")

    if dry_run:
        print("\n[dry run] No changes written.")
    else:
        if stats["persisted_count"] > 0:
            save_session(session_path, objects, create_backup=not no_backup)
            print(f"\nSession saved: {session_path}")
            print(f"Thinking saved to: {persist_dir}")
        else:
            print("\nNo thinking blocks to persist.")

    return stats
