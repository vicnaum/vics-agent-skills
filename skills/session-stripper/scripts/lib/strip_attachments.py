"""Drop superseded `attachment` entries from a Claude Code JSONL session.

Attachments are real API context — CC re-expands every one of them into a
`<system-reminder>` on every request, forever, and nothing dedupes them (see
lib/attachment_cost.py for the source trail). A long session ends up paying for
hundreds of superseded todo snapshots, hook echoes and token-count reminders.

THE CHAIN TRAP
--------------
Attachments are `parentUuid` chain participants (CC's `isChainParticipant()`
excludes only `progress`). Real messages chain *through* them:

    attachment(hook_success) -> attachment(skill_listing) -> user -> assistant

CC resumes by walking `parentUuid` back from the newest leaf
(`buildConversationChain()`); if a child points at a uuid that no longer
exists, the walk stops dead and **the entire earlier conversation is silently
dropped**. So attachment lines can NOT simply be deleted — every child must be
re-parented to its nearest surviving ancestor first. That is exactly what
`remove_objects_and_rewire()` does, and it mirrors how CC itself handled the
same problem when it removed `progress` from transcripts (its `progressBridge`).
"""

from collections import Counter, defaultdict

from .attachment_cost import (
    KEEP_ALL,
    DEFAULT_POLICY,
    attachment_type,
    envelope_rendered_chars,
    policy_for,
)
from .chain import (
    build_uuid_index,
    estimate_tokens,
    load_session,
    remove_objects_and_rewire,
    save_session,
    walk_active_chain,
)


def _chain_attachments(objects, uuid_index=None):
    """Attachment envelopes on the ACTIVE chain, in order.

    Only these cost tokens — attachments on abandoned branches are never sent,
    so we leave them alone rather than churn the chain for zero benefit.
    """
    chain = walk_active_chain(objects, uuid_index)
    return [o for o in chain if attachment_type(o) is not None]


def collect_stats(objects):
    """Per-type counts and rendered cost for the active chain's attachments."""
    rows = defaultdict(lambda: {"count": 0, "chars": 0, "free": 0})
    for obj in _chain_attachments(objects):
        t = attachment_type(obj)
        c = envelope_rendered_chars(obj)
        rows[t]["count"] += 1
        rows[t]["chars"] += c
        if c == 0:
            rows[t]["free"] += 1
    return dict(rows)


def list_attachments(session_path):
    """Print the per-type attachment table with the default policy's verdict."""
    objects = load_session(session_path)
    stats = collect_stats(objects)
    if not stats:
        print("No attachments on the active chain.")
        return stats

    total_chars = sum(r["chars"] for r in stats.values())
    total_n = sum(r["count"] for r in stats.values())

    print(f"  {'type':<26} {'n':>5} {'rendered':>10} {'≈tok':>8}  policy")
    print(f"  {'─' * 26} {'─' * 5} {'─' * 10} {'─' * 8}  {'─' * 30}")
    for t, r in sorted(stats.items(), key=lambda kv: -kv[1]["chars"]):
        pol = policy_for(t)
        if pol == KEEP_ALL:
            verdict = "keep all"
        elif r["chars"] == 0:
            verdict = f"keep last {pol} (renders to nothing — 0 tok)"
        else:
            verdict = f"keep last {pol}, drop {max(0, r['count'] - pol)}"
        print(f"  {t:<26} {r['count']:>5} {r['chars']:>10,} "
              f"{estimate_tokens(r['chars']):>8,}  {verdict}")
    print(f"  {'─' * 26} {'─' * 5} {'─' * 10} {'─' * 8}")
    print(f"  {'TOTAL':<26} {total_n:>5} {total_chars:>10,} "
          f"{estimate_tokens(total_chars):>8,}")
    print()
    print("  Rendered cost models CC's per-type renderers, NOT the size of the")
    print("  stored record — a task_reminder record carries task descriptions")
    print("  that the renderer never sends. Unknown types are kept and sized")
    print("  conservatively.")
    return stats


def strip_attachments(session_path, dry_run=False, no_backup=False,
                      types=None, keep_recent=None, drop_all=False,
                      include_free=False, policy=None):
    """Drop superseded attachments, re-parenting their children.

    Parameters:
        types:        restrict to these attachment types (default: all,
                      subject to policy).
        keep_recent:  override how many of each reducible type to keep.
        drop_all:     drop EVERY matching attachment, ignoring the keep-all
                      policy. Requires an explicit `types` list — it is an
                      escape hatch, not a default, because it can drop the
                      capability attachments that tell the model which tools
                      exist.
        include_free: also drop attachments that render to nothing. Saves no
                      context (they are never sent); shrinks the file only.

    Returns a stats dict.
    """
    if drop_all and not types:
        raise ValueError(
            "--drop-all requires --types: it ignores the keep-all policy and "
            "will happily drop the capability attachments (deferred_tools_delta, "
            "mcp_instructions_delta, ...) that tell the model which tools and "
            "skills exist. Name the types explicitly."
        )

    objects = load_session(session_path)
    uuid_index = build_uuid_index(objects)
    atts = _chain_attachments(objects, uuid_index)

    wanted = set(types) if types else None

    # Group by type, preserving chain order, so "keep last N" means the N most
    # recent of that type.
    by_type = defaultdict(list)
    for obj in atts:
        by_type[attachment_type(obj)].append(obj)

    to_drop = []
    kept_free = 0
    for t, objs in by_type.items():
        if wanted is not None and t not in wanted:
            continue

        if drop_all:
            candidates = list(objs)
        else:
            pol = keep_recent if keep_recent is not None else policy_for(t, policy)
            if pol == KEEP_ALL:
                continue
            keep_n = max(0, int(pol))
            candidates = objs[:-keep_n] if keep_n else list(objs)

        for obj in candidates:
            if not include_free and envelope_rendered_chars(obj) == 0:
                # Renders to nothing: dropping it buys no context, and every
                # deletion is one more chain rewire. Skip it.
                kept_free += 1
                continue
            to_drop.append(obj)

    chars_saved = sum(envelope_rendered_chars(o) for o in to_drop)
    dropped_by_type = Counter(attachment_type(o) for o in to_drop)
    uuids = {o.get("uuid") for o in to_drop if o.get("uuid")}

    messages_removed = 0
    parents_rewired = 0
    if uuids and not dry_run:
        objects, messages_removed, parents_rewired = remove_objects_and_rewire(objects, uuids)
        save_session(session_path, objects, create_backup=not no_backup)
    elif uuids:
        # Dry run: still compute the rewire count so the report is honest.
        _, messages_removed, parents_rewired = remove_objects_and_rewire(
            [dict(o) for o in objects], uuids
        )

    est = estimate_tokens(chars_saved)
    stats = {
        "attachments_dropped": len(to_drop),
        "dropped_by_type": dict(dropped_by_type),
        "parents_rewired": parents_rewired,
        "chars_saved": chars_saved,
        "est_tokens_saved": est,
        "free_attachments_skipped": kept_free,
    }

    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Attachments dropped: {len(to_drop)}")
    for t, n in dropped_by_type.most_common():
        print(f"{mode}    {t:<26} {n:>5}")
    if kept_free:
        print(f"{mode}Skipped {kept_free} zero-cost attachment(s) — they render to "
              f"nothing, so dropping them would save no context "
              f"(use --include-free to drop them anyway)")
    print(f"{mode}Descendants re-parented: {parents_rewired}")
    print(f"{mode}Characters saved: {chars_saved:,}")
    print(f"{mode}Estimated tokens saved: {est:,}")

    return stats
