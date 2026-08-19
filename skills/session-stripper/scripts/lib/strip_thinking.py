"""Strip thinking and redacted_thinking blocks from Claude Code JSONL sessions."""

from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    remove_objects_and_rewire,
    resolve_range,
    save_session,
    walk_active_chain,
    wrapped_thinking_text,
)


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

    # Everything reachable from the leaf. Anything outside it is off-chain:
    # sidechain/subagent turns, and branches abandoned by an edit or rewind.
    chain_uuids = {o.get("uuid") for o in chain if o.get("uuid") is not None}

    thinking_cleared = 0
    messages_affected = 0
    chars_saved = 0
    uuids_to_drop = set()
    offchain_thinking = 0
    offchain_chars = 0

    for obj in objects:
        if obj.get("type") != "assistant":
            continue
        uid = obj.get("uuid")
        content = obj.get("message", {}).get("content") if isinstance(obj.get("message"), dict) else None
        if uid not in target_uuids:
            # Off-chain thinking is deliberately left alone — it is unreachable
            # from the leaf, so it costs no context and removing it would only
            # destroy history. But it must still be COUNTED: reporting a bare
            # "312 removed" while 71 blocks (248,283 chars) sat untouched read
            # as if the session had been fully cleaned.
            if uid not in chain_uuids and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    wrapped = wrapped_thinking_text(block)
                    if btype == "thinking":
                        offchain_thinking += 1
                        offchain_chars += len(block.get("thinking", ""))
                    elif btype == "redacted_thinking":
                        offchain_thinking += 1
                        offchain_chars += len(block.get("data", ""))
                    elif wrapped is not None:
                        offchain_thinking += 1
                        offchain_chars += len(block.get("text", ""))
            continue

        if not isinstance(content, list):
            continue

        new_content = []
        msg_modified = False

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")
            wrapped = wrapped_thinking_text(block)
            if block_type in ("thinking", "redacted_thinking"):
                # Count chars from the text payload
                if block_type == "thinking":
                    chars_saved += len(block.get("thinking", ""))
                elif block_type == "redacted_thinking":
                    chars_saved += len(block.get("data", ""))
                thinking_cleared += 1
                msg_modified = True
            elif wrapped is not None:
                # Whole-block <thinking>/<think> text wrap (from
                # convert_to_cli.py --flatten-thinking or open-source models
                # using <think>). Treat same as a real thinking block.
                chars_saved += len(block.get("text", ""))
                thinking_cleared += 1
                msg_modified = True
            else:
                new_content.append(block)

        if msg_modified:
            messages_affected += 1
            if not new_content:
                # Thinking-only message: drop it entirely and rewire parentUuid.
                # Leaving an empty {"type":"text","text":""} breaks the API
                # ("text content blocks must be non-empty").
                if uid is not None:
                    uuids_to_drop.add(uid)
            else:
                obj["message"]["content"] = new_content

    messages_removed = 0
    parents_rewired = 0
    messages_anchored = 0
    if uuids_to_drop:
        objects, messages_removed, parents_rewired, messages_anchored = \
            remove_objects_and_rewire(objects, uuids_to_drop)

    if not dry_run:
        save_session(session_path, objects, create_backup=not no_backup)

    est_tokens_saved = estimate_tokens(chars_saved)

    stats = {
        "thinking_cleared": thinking_cleared,
        "messages_affected": messages_affected,
        "messages_removed": messages_removed,
        "messages_anchored": messages_anchored,
        "parents_rewired": parents_rewired,
        "chars_saved": chars_saved,
        "est_tokens_saved": est_tokens_saved,
        "offchain_thinking_skipped": offchain_thinking,
        "offchain_chars_skipped": offchain_chars,
    }

    # Print summary
    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Thinking blocks removed (on-chain): {thinking_cleared}")
    print(f"{mode}Messages affected: {messages_affected}")
    if messages_removed:
        print(f"{mode}Thinking-only messages dropped: {messages_removed} (parentUuid rewired on {parents_rewired} descendants)")
    if messages_anchored:
        print(f"{mode}Thinking-only messages kept as chain anchors: {messages_anchored} "
              f"(payload emptied; a live CLI may still cite their uuid as parentUuid)")
    print(f"{mode}Characters saved: {chars_saved:,}")
    print(f"{mode}Estimated tokens saved: {est_tokens_saved:,}")
    if offchain_thinking:
        # Blocks whose `thinking` is already "" (an earlier strip, or CC's own
        # signature-only records) would otherwise print as "976 (0 chars)",
        # which reads like a miscount rather than "nothing left to reclaim".
        detail = (f"{offchain_chars:,} chars" if offchain_chars
                  else "no text left — already emptied")
        print(f"{mode}Thinking blocks left off-chain: {offchain_thinking} "
              f"({detail}) — unreachable from the leaf, so they cost no context "
              f"and are not touched")

    return stats
