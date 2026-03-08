"""Strip thinking and redacted_thinking blocks from Claude Code JSONL sessions."""

from .chain import load_session, build_uuid_index, walk_active_chain, resolve_range, save_session, estimate_tokens


def strip_thinking(session_path, dry_run=False, no_backup=False, from_pos=None, to_pos=None):
    """Remove thinking/redacted_thinking blocks from assistant messages in the active chain.

    Parameters:
        session_path: path to the JSONL session file.
        dry_run: if True, report what would change but don't write.
        no_backup: if True, skip .bak creation.
        from_pos: starting chain position (inclusive). None = beginning.
        to_pos: ending chain position (inclusive). None = end.

    Returns:
        dict with keys: thinking_cleared, messages_affected, chars_saved, est_tokens_saved.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)
    start, end = resolve_range(chain, from_pos, to_pos)

    # Build set of active-chain UUIDs within the target range
    target_uuids = set()
    for i in range(start, end + 1):
        obj = chain[i]
        uid = obj.get("uuid")
        if uid is not None:
            target_uuids.add(uid)

    thinking_cleared = 0
    messages_affected = 0
    chars_saved = 0

    for obj in objects:
        if obj.get("type") != "assistant":
            continue
        uid = obj.get("uuid")
        if uid not in target_uuids:
            continue

        content = obj.get("message", {}).get("content") if isinstance(obj.get("message"), dict) else None
        if not isinstance(content, list):
            continue

        new_content = []
        msg_modified = False

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")
            if block_type in ("thinking", "redacted_thinking"):
                # Count chars from the text payload
                if block_type == "thinking":
                    chars_saved += len(block.get("thinking", ""))
                elif block_type == "redacted_thinking":
                    chars_saved += len(block.get("data", ""))
                thinking_cleared += 1
                msg_modified = True
            else:
                new_content.append(block)

        if msg_modified:
            messages_affected += 1
            if not new_content:
                new_content = [{"type": "text", "text": ""}]
            obj["message"]["content"] = new_content

    if not dry_run:
        save_session(session_path, objects, create_backup=not no_backup)

    est_tokens_saved = estimate_tokens(chars_saved)

    stats = {
        "thinking_cleared": thinking_cleared,
        "messages_affected": messages_affected,
        "chars_saved": chars_saved,
        "est_tokens_saved": est_tokens_saved,
    }

    # Print summary
    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Thinking blocks removed: {thinking_cleared}")
    print(f"{mode}Messages affected: {messages_affected}")
    print(f"{mode}Characters saved: {chars_saved:,}")
    print(f"{mode}Estimated tokens saved: {est_tokens_saved:,}")

    return stats
