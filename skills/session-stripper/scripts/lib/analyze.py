"""Session analysis and token breakdown for Claude Code JSONL sessions.

Provides detailed reporting on message composition, token distribution by
content type and tool name, cut point candidates, and chain health checks.
"""

import json
from collections import Counter, defaultdict

from .chain import load_session, build_uuid_index, walk_active_chain, count_content_chars, estimate_tokens
from .image_tokens import image_block_tokens


def _accurate_image_tokens(obj):
    """Sum Anthropic-correct image tokens for all image blocks in obj's content.
    The chars/4 estimate over-counts image cost ~50-100x; this corrects it."""
    msg = obj.get("message", {}) if isinstance(obj, dict) else {}
    content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            total += image_block_tokens(block)
    return total


def _extract_text_from_content(content):
    """Extract plain text from a message's content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return ""


def _get_content(obj):
    """Get the content field from a message object, handling the message wrapper."""
    msg = obj.get("message", {})
    if isinstance(msg, dict):
        return msg.get("content")
    return obj.get("content")


def _build_tool_result_index(chain):
    """Build a mapping from tool_use_id -> list of tool_result blocks across the chain."""
    index = {}
    for obj in chain:
        content = _get_content(obj)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tuid = block.get("tool_use_id")
                if tuid:
                    index.setdefault(tuid, []).append(block)
    return index


def analyze_session(session_path, show_cut_points=True):
    """Analyze a Claude Code JSONL session and print a formatted report.

    Args:
        session_path: Path to the .jsonl session file.
        show_cut_points: Whether to show cut point candidates.

    Returns:
        dict with all computed stats.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    stats = {}

    # ── 1. Basic stats ──────────────────────────────────────────────────

    stats["total_lines"] = len(objects)

    type_counts = Counter()
    for obj in objects:
        msg_type = obj.get("type", "unknown")
        type_counts[msg_type] += 1
    stats["type_breakdown"] = dict(type_counts)

    stats["active_chain_length"] = len(chain)

    compact_boundaries = [obj for obj in objects if obj.get("subtype") == "compact_boundary"]
    stats["compact_boundary_count"] = len(compact_boundaries)

    session_id = None
    current_slug = None
    for obj in objects:
        if "sessionId" in obj:
            session_id = obj["sessionId"]
        if "slug" in obj:
            current_slug = obj["slug"]
    stats["session_id"] = session_id
    stats["current_slug"] = current_slug

    print("=" * 72)
    print("SESSION ANALYSIS")
    print("=" * 72)
    print()
    print(f"  File:              {session_path}")
    print(f"  Session ID:        {session_id or '(none)'}")
    print(f"  Current slug:      {current_slug or '(none)'}")
    print(f"  Total lines:       {stats['total_lines']}")
    print(f"  Active chain:      {stats['active_chain_length']} messages")
    print(f"  Compact boundaries:{stats['compact_boundary_count']}")
    print()
    print("  Message type breakdown:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:<20s} {count:>6d}")
    print()

    # ── 2. Token breakdown by content type ──────────────────────────────

    totals = {"tool_use": 0, "tool_result": 0, "thinking": 0, "text": 0, "image": 0, "other": 0, "total": 0}
    image_tokens_real = 0  # Anthropic formula for images, not chars/4

    for obj in chain:
        cc = count_content_chars(obj)
        for key in totals:
            totals[key] += cc[key]
        image_tokens_real += _accurate_image_tokens(obj)

    stats["char_totals"] = dict(totals)
    # Token totals: chars/4 for everything except images, which use Anthropic's
    # (w*h)/750 formula. The chars/4 estimate over-counts images by 50-100x
    # because base64 expansion has nothing to do with the actual model cost.
    token_totals = {k: estimate_tokens(v) for k, v in totals.items()}
    image_tokens_naive = token_totals["image"]
    token_totals["image"] = image_tokens_real
    # Recompute total: subtract the naive image estimate, add the real one.
    token_totals["total"] = token_totals["total"] - image_tokens_naive + image_tokens_real
    stats["token_totals"] = token_totals
    stats["image_tokens_naive_chars_div_4"] = image_tokens_naive

    strippable_keys = {"tool_use", "tool_result", "thinking", "image"}
    strippable_tokens = sum(token_totals[k] for k in strippable_keys)
    stats["strippable_tokens"] = strippable_tokens

    grand_total = token_totals["total"] or 1  # avoid division by zero

    print("-" * 72)
    print("TOKEN BREAKDOWN BY CONTENT TYPE")
    print("-" * 72)
    print()
    print(f"  {'Type':<16s} {'Chars':>12s} {'Est. Tokens':>12s} {'%':>7s}  {'Note'}")
    print(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 7}  {'─' * 12}")
    for key in ("text", "tool_use", "tool_result", "thinking", "image", "other"):
        chars = totals[key]
        tokens = token_totals[key]
        pct = (tokens / grand_total) * 100
        if key == "image":
            note = "strippable · (w×h)/750 capped 1600"
        else:
            note = "strippable" if key in strippable_keys else ""
        print(f"  {key:<16s} {chars:>12,d} {tokens:>12,d} {pct:>6.1f}%  {note}")
    print(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 7}")
    print(f"  {'TOTAL':<16s} {totals['total']:>12,d} {token_totals['total']:>12,d} {'100.0%':>7s}")
    print(f"  {'STRIPPABLE':<16s} {'':>12s} {strippable_tokens:>12,d} {(strippable_tokens / grand_total) * 100:>6.1f}%")
    if image_tokens_naive != image_tokens_real:
        delta = image_tokens_naive - image_tokens_real
        print(f"  (image col uses Anthropic formula; chars/4 would say {image_tokens_naive:,} — "
              f"over by {delta:,} tokens)")
    print()

    # ── 3. Token breakdown by tool name ─────────────────────────────────

    tool_result_index = _build_tool_result_index(chain)

    # Collect tool_use blocks with their IDs
    tool_stats = defaultdict(lambda: {"use_chars": 0, "result_chars": 0, "count": 0})

    for obj in chain:
        content = _get_content(obj)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                input_chars = len(json.dumps(block.get("input", {}), ensure_ascii=False))
                tool_stats[name]["use_chars"] += input_chars
                tool_stats[name]["count"] += 1

                # Find matching tool_result
                block_id = block.get("id")
                if block_id and block_id in tool_result_index:
                    for result_block in tool_result_index[block_id]:
                        result_content = result_block.get("content", "")
                        if isinstance(result_content, str):
                            tool_stats[name]["result_chars"] += len(result_content)
                        elif isinstance(result_content, list):
                            tool_stats[name]["result_chars"] += len(
                                json.dumps(result_content, ensure_ascii=False)
                            )

    # Sort by total impact
    sorted_tools = sorted(
        tool_stats.items(),
        key=lambda x: x[1]["use_chars"] + x[1]["result_chars"],
        reverse=True,
    )

    stats["tool_breakdown"] = {
        name: {
            "use_chars": s["use_chars"],
            "result_chars": s["result_chars"],
            "use_tokens": estimate_tokens(s["use_chars"]),
            "result_tokens": estimate_tokens(s["result_chars"]),
            "total_tokens": estimate_tokens(s["use_chars"] + s["result_chars"]),
            "count": s["count"],
        }
        for name, s in sorted_tools
    }

    print("-" * 72)
    print("TOKEN BREAKDOWN BY TOOL NAME")
    print("-" * 72)
    print()
    print(
        f"  {'Tool':<28s} {'Calls':>6s} {'Use Tok':>10s} {'Result Tok':>10s} {'Total Tok':>10s} {'%':>7s}"
    )
    print(f"  {'─' * 28} {'─' * 6} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 7}")

    tool_token_grand = sum(
        estimate_tokens(s["use_chars"] + s["result_chars"]) for _, s in sorted_tools
    ) or 1

    for name, s in sorted_tools:
        use_tok = estimate_tokens(s["use_chars"])
        res_tok = estimate_tokens(s["result_chars"])
        total_tok = use_tok + res_tok
        pct = (total_tok / tool_token_grand) * 100
        print(
            f"  {name:<28s} {s['count']:>6d} {use_tok:>10,d} {res_tok:>10,d} {total_tok:>10,d} {pct:>6.1f}%"
        )

    print()

    # ── 4. Cut point candidates ─────────────────────────────────────────

    if show_cut_points:
        print("-" * 72)
        print("CUT POINT CANDIDATES (human messages)")
        print("-" * 72)
        print()
        print(f"  {'Pos':>5s} {'Cum. Tokens':>12s}  {'Message preview'}")
        print(f"  {'─' * 5} {'─' * 12}  {'─' * 50}")

        cumulative_chars = 0
        cut_points = []
        for i, obj in enumerate(chain):
            cc = count_content_chars(obj)
            cumulative_chars += cc["total"]
            if obj.get("type") == "user":
                text = _extract_text_from_content(_get_content(obj))
                preview = text.replace("\n", " ").strip()
                if len(preview) > 100:
                    preview = preview[:100] + "..."
                cum_tokens = estimate_tokens(cumulative_chars)
                cut_points.append({
                    "position": i,
                    "cumulative_tokens": cum_tokens,
                    "preview": preview,
                })
                print(f"  {i:>5d} {cum_tokens:>12,d}  {preview}")

        stats["cut_points"] = cut_points
        print()

    # ── 5. Chain health ─────────────────────────────────────────────────

    issues = _check_chain_health(objects, chain, uuid_index)
    stats["health_issues"] = issues

    print("-" * 72)
    print("CHAIN HEALTH")
    print("-" * 72)
    print()
    if not issues:
        print("  All checks passed.")
    else:
        for issue in issues:
            print(f"  [ISSUE] {issue}")
    print()
    print("=" * 72)

    return stats


def _check_chain_health(objects, chain, uuid_index):
    """Run health checks on the session chain. Return list of issue strings."""
    issues = []

    # Check parentUuid integrity
    for i, obj in enumerate(chain):
        parent_uuid = obj.get("parentUuid")
        if parent_uuid is not None and parent_uuid not in uuid_index:
            issues.append(
                f"Broken parent link at chain pos {i}: "
                f"uuid={obj.get('uuid', '?')} references missing parentUuid={parent_uuid}"
            )

    # Check slug consistency
    slugs = set()
    for obj in chain:
        s = obj.get("slug")
        if s is not None:
            slugs.add(s)
    if len(slugs) > 1:
        issues.append(f"Multiple slugs in active chain: {slugs}")

    # Check timestamp ordering
    prev_ts = None
    for i, obj in enumerate(chain):
        ts = obj.get("timestamp")
        if ts is not None:
            if prev_ts is not None and ts < prev_ts:
                issues.append(
                    f"Timestamp out of order at chain pos {i}: "
                    f"{ts} < previous {prev_ts}"
                )
            prev_ts = ts

    return issues


def health_check(session_path):
    """Quick chain health verification.

    Args:
        session_path: Path to the .jsonl session file.

    Returns:
        True if all checks pass, False otherwise.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    chain = walk_active_chain(objects, uuid_index)

    issues = _check_chain_health(objects, chain, uuid_index)

    if not issues:
        print("HEALTH CHECK: PASS")
        print(f"  Chain length: {len(chain)} messages")
        print(f"  Total lines:  {len(objects)}")
        return True
    else:
        print("HEALTH CHECK: FAIL")
        for issue in issues:
            print(f"  [ISSUE] {issue}")
        return False
