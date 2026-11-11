"""Session analysis and token breakdown for Claude Code JSONL sessions.

Provides detailed reporting on message composition, token distribution by
content type and tool name, cut point candidates, and chain health checks.
"""

import json
from collections import Counter, defaultdict

from .chain import (load_session, build_uuid_index, walk_active_chain, count_content_chars,
                    estimate_tokens, compact_relink, effective_parent, obj_image_tokens,
                    session_model)
from .image_tokens import content_image_stats, tier_for_model
from .strip_attachments import collect_stats as collect_attachment_stats


def _accurate_image_tokens(obj, tier=None):
    """Anthropic-correct tokens for every image in obj's content — top-level
    and nested inside tool results. The chars/4 estimate over-counts image
    cost 20-100x; this corrects it."""
    return obj_image_tokens(obj, tier)


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
    relink = compact_relink(objects)
    chain = walk_active_chain(objects, uuid_index, relink=relink)
    model = session_model(chain)
    tier = tier_for_model(model)

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
    print(f"  Model:             {model or '(unknown)'} — {tier.name} image tier")
    print()
    print("  Message type breakdown:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:<20s} {count:>6d}")
    print()

    # ── 1b. Compaction: records a compact boundary keeps ────────────────
    stats["relink_status"] = relink["status"]
    stats["preserved_records"] = len(relink["preserved"])
    if relink["status"] != "none":
        print("-" * 72)
        print("COMPACTION (records the last compact boundary keeps)")
        print("-" * 72)
        print()
        if relink["status"] == "ok" and relink["preserved"]:
            on_chain = {o.get("uuid") for o in chain}
            kept = sum(1 for u in relink["preserved"] if u in on_chain)
            print(f"  The last compact boundary preserves {len(relink['preserved']):,} records "
                  f"from before it ({relink['source']}).")
            print(f"  Claude Code splices them back in after the boundary on every load, so")
            print(f"  they are sent on every request. {kept:,} of them are on the chain below,")
            print(f"  and every command here covers them.")
        elif relink["status"] == "ok":
            print("  The last compact boundary preserves nothing: the chain starts at it.")
        else:
            missing = len(relink["missing"])
            what = (f"{missing:,} of its {len(relink['preserved']):,} listed records are missing"
                    if missing else "its preserved segment no longer resolves")
            print(f"  BROKEN: the last compact boundary preserves records, but {what}.")
            print(f"  Claude Code then relinks nothing and drops every preserved record from")
            print(f"  the conversation. See CHAIN HEALTH.")
        print()

    # ── 2. Token breakdown by content type ──────────────────────────────

    totals = {"tool_use": 0, "tool_result": 0, "thinking": 0, "text": 0, "image": 0, "other": 0, "total": 0}
    image_tokens_real = 0  # Anthropic formula for images, not chars/4
    image_count = 0
    image_nested = 0

    for obj in chain:
        cc = count_content_chars(obj)
        for key in totals:
            totals[key] += cc[key]
        msg = obj.get("message")
        s = content_image_stats(msg.get("content") if isinstance(msg, dict) else obj.get("content"),
                                tier)
        image_tokens_real += s["tokens"]
        image_count += s["count"]
        image_nested += s["nested"]

    stats["char_totals"] = dict(totals)
    stats["image_count"] = image_count
    stats["image_nested_count"] = image_nested
    # Token totals: chars/4 for everything except images, which use Anthropic's
    # pixel formula (28px patches, tier-capped). The chars/4 estimate
    # over-counts images 20-100x because base64 size has nothing to do with
    # what the model is charged. Images nested in tool results are in the
    # "image" bucket too (count_content_chars moves them there).
    token_totals = {k: estimate_tokens(v) for k, v in totals.items()}
    image_tokens_naive = token_totals["image"]
    token_totals["image"] = image_tokens_real
    # Recompute total: subtract the naive image estimate, add the real one.
    token_totals["total"] = token_totals["total"] - image_tokens_naive + image_tokens_real
    stats["token_totals"] = token_totals
    stats["image_tokens_naive_chars_div_4"] = image_tokens_naive

    # Attachments (system-reminders) are re-expanded into EVERY request, so they
    # are part of the prompt even though they carry no message content. Omitting
    # them used to make this whole report read ~30-50% low on long sessions.
    attach_rows = collect_attachment_stats(objects)
    attach_chars = sum(r["chars"] for r in attach_rows.values())
    attach_tokens = estimate_tokens(attach_chars)
    stats["attachment_chars"] = attach_chars
    stats["attachment_tokens"] = attach_tokens
    stats["attachment_breakdown"] = attach_rows

    totals["attachment"] = attach_chars
    totals["total"] += attach_chars
    token_totals["attachment"] = attach_tokens
    token_totals["total"] += attach_tokens

    strippable_keys = {"tool_use", "tool_result", "thinking", "image"}
    strippable_tokens = sum(token_totals[k] for k in strippable_keys)
    # Attachments are reducible too, but only down to the policy floor (the
    # newest of each kind has to stay), so they get their own line rather than
    # being folded into STRIPPABLE.
    stats["strippable_tokens"] = strippable_tokens

    grand_total = token_totals["total"] or 1  # avoid division by zero

    print("-" * 72)
    print("TOKEN BREAKDOWN BY CONTENT TYPE")
    print("-" * 72)
    print()
    print(f"  {'Type':<16s} {'Chars':>12s} {'Est. Tokens':>12s} {'%':>7s}  {'Note'}")
    print(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 7}  {'─' * 12}")
    for key in ("text", "tool_use", "tool_result", "thinking", "image", "attachment", "other"):
        chars = totals[key]
        tokens = token_totals[key]
        pct = (tokens / grand_total) * 100
        if key == "image":
            note = f"strippable · 28px patches, ≤{tier.max_tokens:,}/img"
        elif key == "attachment":
            note = "reducible · strip-attachments"
        else:
            note = "strippable" if key in strippable_keys else ""
        print(f"  {key:<16s} {chars:>12,d} {tokens:>12,d} {pct:>6.1f}%  {note}")
    print(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 7}")
    print(f"  {'TOTAL':<16s} {totals['total']:>12,d} {token_totals['total']:>12,d} {'100.0%':>7s}")
    print(f"  {'STRIPPABLE':<16s} {'':>12s} {strippable_tokens:>12,d} {(strippable_tokens / grand_total) * 100:>6.1f}%")
    if image_count:
        print(f"  Images: {image_count:,} on the chain ({image_nested:,} inside tool results), "
              f"~{image_tokens_real:,} tokens on the {tier.name} tier")
        print(f"  (ceil(w/28)×ceil(h/28) after resize to ≤{tier.max_edge}px / "
              f"≤{tier.max_tokens:,} tokens; chars/4 on their base64 would say "
              f"{image_tokens_naive:,}) → list-images, replace-images, strip-tools")
    print()

    # ── 2b. Attachment breakdown ────────────────────────────────────────

    if attach_rows:
        from .attachment_cost import KEEP_ALL, policy_for

        print("-" * 72)
        print("ATTACHMENTS (system-reminders re-sent on EVERY request)")
        print("-" * 72)
        print()
        print(f"  {'Type':<26} {'Count':>6} {'Rendered':>10} {'Est. Tok':>9}  {'Policy'}")
        print(f"  {'─' * 26} {'─' * 6} {'─' * 10} {'─' * 9}  {'─' * 22}")
        reducible = 0
        for t, r in sorted(attach_rows.items(), key=lambda kv: -kv[1]["chars"]):
            pol = policy_for(t)
            if pol == KEEP_ALL:
                verdict = "keep all"
            else:
                drop_n = max(0, r["count"] - int(pol))
                verdict = f"keep last {pol}" + (f", drop {drop_n}" if drop_n else "")
                if r["count"] > int(pol) and r["chars"]:
                    reducible += r["chars"] - (r["chars"] // max(1, r["count"])) * int(pol)
            print(f"  {t:<26} {r['count']:>6,} {r['chars']:>10,} "
                  f"{estimate_tokens(r['chars']):>9,}  {verdict}")
        print(f"  {'─' * 26} {'─' * 6} {'─' * 10} {'─' * 9}")
        print(f"  {'TOTAL':<26} {sum(r['count'] for r in attach_rows.values()):>6,} "
              f"{attach_chars:>10,} {attach_tokens:>9,}")
        if reducible > 0:
            print(f"  {'REDUCIBLE':<26} {'':>6} {'':>10} {estimate_tokens(reducible):>9,}"
                  f"  → stripper strip-attachments")
        print()

    # ── 3. Token breakdown by tool name ─────────────────────────────────

    tool_result_index = _build_tool_result_index(chain)

    # Collect tool_use blocks with their IDs. Images a tool returned are
    # priced by the pixel formula (result_image_tokens), not as result chars.
    tool_stats = defaultdict(lambda: {"use_chars": 0, "result_chars": 0, "count": 0,
                                      "result_images": 0, "result_image_tokens": 0})

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
                            img = content_image_stats(result_content, tier)
                            tool_stats[name]["result_chars"] += len(
                                json.dumps(result_content, ensure_ascii=False)
                            ) - img["chars"]
                            tool_stats[name]["result_images"] += img["count"]
                            tool_stats[name]["result_image_tokens"] += img["tokens"]

    def _tok(s):
        use_tok = estimate_tokens(s["use_chars"])
        res_tok = estimate_tokens(s["result_chars"]) + s["result_image_tokens"]
        return use_tok, res_tok

    # Sort by total impact
    sorted_tools = sorted(tool_stats.items(), key=lambda x: sum(_tok(x[1])), reverse=True)

    stats["tool_breakdown"] = {
        name: {
            "use_chars": s["use_chars"],
            "result_chars": s["result_chars"],
            "result_images": s["result_images"],
            "result_image_tokens": s["result_image_tokens"],
            "use_tokens": _tok(s)[0],
            "result_tokens": _tok(s)[1],
            "total_tokens": sum(_tok(s)),
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

    tool_token_grand = sum(sum(_tok(s)) for _, s in sorted_tools) or 1

    for name, s in sorted_tools:
        use_tok, res_tok = _tok(s)
        total_tok = use_tok + res_tok
        pct = (total_tok / tool_token_grand) * 100
        imgs = (f"  incl. {s['result_images']} image(s), {s['result_image_tokens']:,} tok"
                if s["result_images"] else "")
        print(
            f"  {name:<28s} {s['count']:>6d} {use_tok:>10,d} {res_tok:>10,d} {total_tok:>10,d} {pct:>6.1f}%{imgs}"
        )

    print()

    # ── 3b. On-disk-only payload (toolUseResult sidecars) ───────────────
    # CC writes each tool result twice: the `tool_result` block (counted above,
    # and sent to the model) and a top-level `toolUseResult` field on the same
    # record. The sidecar is replay/display metadata — it does NOT enter the
    # prompt — but it is routinely the largest thing in the file, so leaving it
    # out of this report made `strip-tools`' savings look far smaller than the
    # on-disk reduction actually available.
    sidecar_count = 0
    sidecar_chars = 0
    for obj in objects:
        tur = obj.get("toolUseResult")
        if tur is None:
            continue
        sidecar_count += 1
        sidecar_chars += len(json.dumps(tur, ensure_ascii=False))
    stats["tool_use_result_count"] = sidecar_count
    stats["tool_use_result_chars"] = sidecar_chars

    if sidecar_count:
        print("-" * 72)
        print("ON-DISK ONLY (toolUseResult sidecars — never sent to the model)")
        print("-" * 72)
        print()
        print(f"  Records with toolUseResult: {sidecar_count:,}")
        print(f"  Chars on disk:              {sidecar_chars:,}")
        print(f"  Not counted in the token totals above (local replay metadata).")
        print(f"  → stripper strip-tools clears these alongside their tool_result block.")
        print()

    # ── 3c. Recorded system prompt and request journal (CC >= 2.1.267/2.1.300)
    # The latest prompt_snapshot is the system prompt + tools every request
    # sends (real context, not strippable). api-request* lines journal what was
    # sent, for sharing a transcript; resume rebuilds from the chain, so they
    # are file size only. Neither is ever dropped.
    from .attachment_cost import snapshot_chars
    snaps = [o for o in objects if o.get("type") == "attachment"
             and (o.get("attachment") or {}).get("type") == "prompt_snapshot"]
    journal = [o for o in objects if str(o.get("type", "")).startswith("api-request")]
    if snaps or journal:
        print("-" * 72)
        print("RECORDED BY CLAUDE CODE (kept by every command)")
        print("-" * 72)
        print()
        if snaps:
            snap_tok = estimate_tokens(snapshot_chars(snaps[-1]["attachment"]))
            stats["prompt_snapshot_tokens"] = snap_tok
            print(f"  System prompt + tools (latest of {len(snaps)} prompt_snapshot): ~{snap_tok:,} tokens,")
            print(f"  sent on every request — included in the context estimate, not strippable.")
        if journal:
            jchars = sum(len(json.dumps(o, ensure_ascii=False)) for o in journal)
            print(f"  Request journal (api-request*): {len(journal):,} lines, {jchars:,} chars on disk —")
            print(f"  never sent to the model; resume rebuilds the request from the chain.")
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
        cumulative_image_tokens = 0
        cut_points = []
        for i, obj in enumerate(chain):
            cc = count_content_chars(obj)
            cumulative_chars += cc["total"] - cc["image"]
            cumulative_image_tokens += _accurate_image_tokens(obj, tier)
            if obj.get("type") == "user":
                text = _extract_text_from_content(_get_content(obj))
                preview = text.replace("\n", " ").strip()
                if len(preview) > 100:
                    preview = preview[:100] + "..."
                cum_tokens = estimate_tokens(cumulative_chars) + cumulative_image_tokens
                cut_points.append({
                    "position": i,
                    "cumulative_tokens": cum_tokens,
                    "preview": preview,
                })
                print(f"  {i:>5d} {cum_tokens:>12,d}  {preview}")

        stats["cut_points"] = cut_points
        print()

    # ── 5. Chain health ─────────────────────────────────────────────────

    issues, warnings = _check_chain_health(objects, chain, uuid_index, relink)
    stats["health_issues"] = issues
    stats["health_warnings"] = warnings

    print("-" * 72)
    print("CHAIN HEALTH")
    print("-" * 72)
    print()
    if not issues and not warnings:
        print("  All checks passed.")
    for issue in issues:
        print(f"  [ISSUE] {issue}")
    for warning in warnings[:10]:
        print(f"  [WARN]  {warning}")
    if len(warnings) > 10:
        print(f"  [WARN]  ... and {len(warnings) - 10} more warning(s)")
    print()
    print("=" * 72)

    return stats


def _subtree_size(children, root_uuid):
    """Count records reachable downward from root_uuid (inclusive of its kids)."""
    total = 0
    stack = [root_uuid]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for kid in children.get(cur, []):
            total += 1
            ku = kid.get("uuid")
            if ku:
                stack.append(ku)
    return total


def _check_chain_health(objects, chain, uuid_index, relink=None,
                        allow_broken_relink=False):
    """Run health checks on the session chain.

    Returns (issues, warnings).

    `issues` are hard failures — they mean the file is structurally unusable and
    `apply` must refuse to swap it in. `warnings` are advisory: conditions that
    occur in healthy, untouched Claude Code sessions and therefore must not gate
    a strip.

    Parent links are checked as Claude Code sees them (`compact_relink`): a
    preserved record's on-disk parent is irrelevant, CC re-links it.
    """
    issues = []
    warnings = []
    if relink is None:
        relink = compact_relink(objects)

    # ── Hard: preserved records a compact boundary keeps must resolve ────
    # If one listed uuid is missing (or the segment walk breaks), CC relinks
    # nothing and drops every preserved record from the conversation — a
    # silent truncation like the ones below, just larger.
    if relink["status"] == "broken":
        if relink["missing"]:
            msg = (f"Compact boundary {relink['boundary']} preserves "
                   f"{len(relink['preserved']):,} records but {len(relink['missing']):,} "
                   f"are missing from the file (e.g. {relink['missing'][0]})")
        else:
            msg = (f"Compact boundary {relink['boundary']} preserves a segment that no "
                   f"longer resolves (the tail→head parentUuid walk breaks)")
        msg += " — Claude Code will relink nothing and drop every preserved record"
        (warnings if allow_broken_relink else issues).append(
            msg + (" (already broken in the original)" if allow_broken_relink else ""))

    # ── Hard: parentUuid integrity along the active chain ────────────────
    for i, obj in enumerate(chain):
        parent_uuid = effective_parent(obj, relink)
        if parent_uuid is None:
            continue
        if parent_uuid not in uuid_index:
            issues.append(
                f"Broken parent link at chain pos {i}: "
                f"uuid={obj.get('uuid', '?')} references missing parentUuid={parent_uuid}"
            )
        elif parent_uuid in relink["dropped"]:
            warnings.append(
                f"Chain starts at pos {i} on a parent Claude Code drops on load "
                f"(uuid={obj.get('uuid', '?')} → {parent_uuid}, before the last compact "
                f"boundary); CC falls back to a timestamp match there"
            )

    # ── Hard: slug consistency ───────────────────────────────────────────
    # Records a compact boundary preserved are exempt: they can predate a
    # `/branch` and carry the parent session's slug, and CC itself splices
    # them in (seen 2026-11-10: a branched session's chain held both slugs).
    preserved = set(relink["preserved"]) if relink["status"] == "ok" else set()
    slugs = set()
    for obj in chain:
        if obj.get("uuid") in preserved:
            continue
        s = obj.get("slug")
        if s is not None:
            slugs.add(s)
    if len(slugs) > 1:
        issues.append(f"Multiple slugs in active chain: {slugs}")

    # ── Warning: dangling parentUuid anywhere in the FILE ────────────────
    # The old check only walked the active chain, so a record whose parent had
    # been deleted simply fell OUT of that walk — and the shortened chain it
    # left behind was perfectly self-consistent. That is how `verify` printed
    # PASS on both sides of a break that orphaned 1,139 messages.
    #
    # Advisory, not fatal: 2 of 25 real sessions surveyed carry one benign
    # dangling parent (resume/fork leaves the parent in a previous session
    # file), so failing on this would block legitimate work. The orphan COUNT is
    # what distinguishes a harmless stub from a severed history.
    children = {}
    for obj in objects:
        parent = obj.get("parentUuid")
        if parent is not None:
            children.setdefault(parent, []).append(obj)
    chain_uuids = {o.get("uuid") for o in chain}
    for obj in objects:
        parent_uuid = obj.get("parentUuid")
        if parent_uuid is None or parent_uuid in uuid_index:
            continue
        if obj.get("uuid") in chain_uuids:
            continue  # already reported as a hard issue above
        orphaned = 1 + _subtree_size(children, obj.get("uuid"))
        warnings.append(
            f"Dangling parentUuid off the active chain: uuid={obj.get('uuid', '?')} "
            f"references missing parentUuid={parent_uuid} — {orphaned} record(s) "
            f"unreachable behind it"
        )

    # ── Warning: timestamp ordering ──────────────────────────────────────
    # Demoted from a hard failure: out-of-order timestamps are NORMAL in
    # untouched Claude Code sessions, so gating on absolute cleanliness blocked
    # any strip of an already-dirty file (observed: 6 violations in the stripped
    # copy vs 46 in the untouched original, and apply refused).
    #
    # The cause is not millisecond ties — the check uses strict `<`, so ties
    # already pass — and not chain order diverging from file order. Measured
    # across 25 real sessions: 418 violations, and in every one file order and
    # chain order AGREED. CC stamps a record when it is CONSTRUCTED but appends
    # it after the record it hangs off (71% are user -> attachment pairs where
    # the attachment is stamped a few ms early), and session resume/fork
    # re-links records that keep much older timestamps. Neither indicates
    # corruption, so neither should block a swap.
    #
    # Edges CC re-links on load (a compact boundary's preserved records) are
    # skipped: older preserved records following a newer boundary is the
    # design, not disorder.
    prev_ts = None
    relinked = relink["parents"]
    for i, obj in enumerate(chain):
        ts = obj.get("timestamp")
        if ts is not None:
            if (prev_ts is not None and ts < prev_ts
                    and obj.get("uuid") not in relinked):
                warnings.append(
                    f"Timestamp out of order at chain pos {i}: "
                    f"{ts} < previous {prev_ts}"
                )
            prev_ts = ts

    return issues, warnings


def health_check(session_path, allow_broken_relink=False):
    """Quick chain health verification.

    Args:
        session_path: Path to the .jsonl session file.
        allow_broken_relink: report a broken compact-boundary relink as a
            warning, not an issue (apply passes this when the ORIGINAL was
            already broken, so a pre-existing break cannot block every strip).

    Returns:
        True if all checks pass, False otherwise.
    """
    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    relink = compact_relink(objects)
    chain = walk_active_chain(objects, uuid_index, relink=relink)

    issues, warnings = _check_chain_health(objects, chain, uuid_index, relink,
                                           allow_broken_relink=allow_broken_relink)

    if not issues:
        print("HEALTH CHECK: PASS")
        print(f"  Chain length: {len(chain)} messages")
        print(f"  Total lines:  {len(objects)}")
    else:
        print("HEALTH CHECK: FAIL")
        for issue in issues:
            print(f"  [ISSUE] {issue}")

    # Warnings print on PASS too — a severed history shows up here, not in
    # `issues`, and printing only on failure is how the incident stayed silent.
    for warning in warnings[:10]:
        print(f"  [WARN]  {warning}")
    if len(warnings) > 10:
        print(f"  [WARN]  ... and {len(warnings) - 10} more warning(s)")

    return not issues
