"""Core functions for parsing and manipulating Claude Code JSONL session files."""

import json
import os
import re
import shutil
from pathlib import Path

from .image_tokens import (DEFAULT_TIER, content_image_stats, image_payload_chars,
                           iter_image_blocks, tier_for_model)


# Matches a text block whose entire content (modulo surrounding whitespace) is a
# single <thinking>...</thinking> or <think>...</think> span. Used to detect
# "flattened" thinking blocks emitted by convert_to_cli.py --flatten-thinking
# and by open-source models that use <think> tags.
#
# Intentionally conservative: only whole-block wraps match. Embedded wrapped
# thinking inside a larger text block is not auto-detected (it would require
# in-place span edits which complicate semantics).
WRAPPED_THINKING_RE = re.compile(
    r"^\s*<(thinking|think)\b[^>]*>(.*?)</\1>\s*$",
    re.DOTALL | re.IGNORECASE,
)


def wrapped_thinking_text(block):
    """If `block` is a text block wrapping thinking in <thinking>/<think> tags,
    return the inner text. Otherwise return None.
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") != "text":
        return None
    text = block.get("text", "")
    if not isinstance(text, str):
        return None
    m = WRAPPED_THINKING_RE.match(text)
    if not m:
        return None
    return m.group(2)


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


# ── Compact boundaries that preserve messages (CC >= 2.1.3xx) ─────────────
#
# A `/compact` no longer always throws the old conversation away. The boundary
# record (`type: system, subtype: compact_boundary, parentUuid: null`) can carry
# `compactMetadata.preservedMessages = {anchorUuid, uuids: [...]}` and/or
# `compactMetadata.preservedSegment = {headUuid, anchorUuid, tailUuid}`: a run
# of records from BEFORE the boundary that stays in the conversation verbatim.
# On every load Claude Code splices them back in after the anchor (see
# `compact_relink`), so they are sent to the model on every request, even
# though no `parentUuid` on disk leads from the leaf to them.
#
# A walk that stops at the boundary's null parent misses all of them. Incident
# 2026-11-10 (CC 2.1.322): a session at 913k tokens showed ~174k to `analyze`;
# 3,122 preserved records (thinking, tool calls, 64 tool-result images) were
# invisible, so `strip-all` freed almost nothing and the gauge snapped back to
# 91% on the next turn.

def is_compact_boundary(obj):
    return (isinstance(obj, dict) and obj.get("type") == "system"
            and obj.get("subtype") == "compact_boundary")


def _resolve_preserved(meta, entries):
    """The preserved list a boundary's metadata names: (uuids, anchor, source),
    or None when CC cannot resolve it (mirrors CC's `uWo`).

    `preservedMessages` wins when present: an explicit list in conversation
    order. Otherwise `preservedSegment` is resolved by walking on-disk
    parentUuid from tailUuid back to headUuid."""
    pm = meta.get("preservedMessages")
    if pm:
        if not isinstance(pm, dict):
            return None
        return list(pm.get("uuids") or []), pm.get("anchorUuid"), "list"
    seg = meta.get("preservedSegment")
    if not isinstance(seg, dict):
        return None
    head = seg.get("headUuid")
    seen = set()
    out = []
    cur = entries.get(seg.get("tailUuid"))
    while cur is not None and cur.get("uuid") not in seen:
        seen.add(cur["uuid"])
        out.append(cur["uuid"])
        if cur["uuid"] == head:
            out.reverse()
            return out, seg.get("anchorUuid"), "walk"
        p = cur.get("parentUuid")
        cur = entries.get(p) if p else None
    return None


def compact_relink(objects):
    """How Claude Code relinks this transcript on load.

    A faithful port of CC 2.1.322's transcript loader (minified `dWo`/`uWo`):

    1. Only the LAST compact boundary in file order matters. If it carries
       preserved records, they are chained in list order after the anchor:
       uuids[0].parent = anchor, uuids[i].parent = uuids[i-1].
    2. Every other record whose parent is the anchor (the new compact summary,
       usually) is re-parented to the last preserved record.
    3. Every record before the boundary that is not preserved is dropped, and
       user/assistant records whose parent was dropped are re-parented to the
       last preserved record.

    CC gives up (relinks nothing, drops nothing) when the preserved list
    cannot be resolved or names a uuid that is not in the file — and then the
    walk from the leaf stops at the boundary, so EVERY preserved record
    silently leaves the conversation. Mutations must therefore keep the lists
    in sync with deletions (see `remove_objects_and_rewire`).

    Returns a dict:
        status     "none" (nothing to relink), "ok", or "broken"
        boundary   uuid of the last compact boundary, or None
        anchor     anchorUuid of the preserved run, or None
        preserved  preserved uuids in conversation order ([] if none)
        source     "list" (preservedMessages) or "walk" (preservedSegment)
        parents    {uuid: effective parentUuid} overrides
        dropped    uuids CC drops on load
        missing    listed uuids absent from the file (status "broken")
    """
    entries = {}
    for obj in objects:
        u = obj.get("uuid") if isinstance(obj, dict) else None
        if u:
            entries[u] = obj  # first position, last value wins (a JS Map)

    result = {"status": "none", "boundary": None, "anchor": None,
              "preserved": [], "source": None, "parents": {},
              "dropped": set(), "missing": []}

    last_b = -1
    pres_b = -1
    meta = None
    order = {}
    for i, (u, obj) in enumerate(entries.items()):
        order[u] = i
        if is_compact_boundary(obj):
            last_b = i
            result["boundary"] = u
            cm = obj.get("compactMetadata")
            if isinstance(cm, dict) and (cm.get("preservedMessages")
                                         or cm.get("preservedSegment")):
                meta = cm
                pres_b = i
    if meta is None:
        return result

    preserved, anchor, source = [], None, None
    if pres_b == last_b:
        resolved = _resolve_preserved(meta, entries)
        if resolved is None:
            result["status"] = "broken"
            return result
        preserved, anchor, source = resolved
    # (An OLDER boundary's preserved info is moot: CC drops everything before
    # the last boundary, which has none.)

    missing = [u for u in preserved if u not in entries]
    if missing:
        result.update(status="broken", anchor=anchor, preserved=preserved,
                      source=source, missing=missing)
        return result

    parents = {}
    if preserved:
        prev = anchor
        for u in preserved:
            parents[u] = prev
            prev = u
        first, last = preserved[0], preserved[-1]
        if anchor is not None:
            for u, obj in entries.items():
                if u != first and parents.get(u, obj.get("parentUuid")) == anchor:
                    parents[u] = last

    keep = set(preserved)
    dropped = {u for u, i in order.items() if i < last_b and u not in keep}
    if preserved and dropped:
        last = preserved[-1]
        for u, obj in entries.items():
            if u in dropped or obj.get("type") not in ("user", "assistant"):
                continue
            p = parents.get(u, obj.get("parentUuid"))
            if p is not None and p in dropped:
                parents[u] = last

    result.update(status="ok", anchor=anchor, preserved=preserved,
                  source=source, parents=parents, dropped=dropped)
    return result


def effective_parent(obj, relink):
    """parentUuid of `obj` as Claude Code sees it after `compact_relink`."""
    u = obj.get("uuid")
    if u in relink["parents"]:
        return relink["parents"][u]
    return obj.get("parentUuid")


def walk_active_chain(objects, uuid_index=None, relink=None):
    """Walk from the last non-sidechain leaf back to root, return in
    chronological order — the conversation Claude Code loads and sends.

    Follows parentUuid as CC sees it: records a compact boundary preserved are
    spliced back in (`compact_relink`), and records CC drops on load are not
    part of the chain.
    """
    if uuid_index is None:
        uuid_index = build_uuid_index(objects)
    if relink is None:
        relink = compact_relink(objects)
    dropped = relink["dropped"]

    # The leaf: the last user/assistant message in the file that CC keeps.
    leaf = None
    for obj in reversed(objects):
        if (obj.get("type") in ("user", "assistant") and not obj.get("isSidechain")
                and obj.get("uuid") not in dropped):
            leaf = obj
            break

    if leaf is None:
        return []

    # Walk backwards from leaf to root
    chain = []
    seen = set()
    current = leaf
    while current is not None:
        u = current.get("uuid")
        if u in seen:  # a cycle; CC stops and keeps the partial chain too
            break
        seen.add(u)
        chain.append(current)
        parent_uuid = effective_parent(current, relink)
        if parent_uuid is None or parent_uuid in dropped:
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


# Payload we leave behind on a node that must survive as a chain stepping-stone
# (see chain_anchor_uuids). Must be NON-EMPTY: the API rejects empty text blocks
# ("text content blocks must be non-empty").
CHAIN_ANCHOR_PLACEHOLDER = "[stripped]"


def chain_anchor_uuids(objects):
    """Return uuids that must NEVER be deleted, only emptied.

    A running `claude` process holds the uuid of the last record it wrote as its
    in-memory conversation head, and cites that uuid as `parentUuid` on its next
    append — an append this code cannot see or rewire, because it does not exist
    yet. Delete that node and the append is born dangling: `walk_active_chain`
    stops dead there and every earlier message is silently orphaned.

    Incident 2026-08-19: `strip-thinking` deleted 311 thinking-only messages,
    one of which was the live process's head. The next append pointed at the
    deleted uuid and the chain collapsed from 1,139 messages to 67 — no error,
    and `verify` reported PASS on both sides of the break because it only walks
    the (now short) active chain.

    Rewiring cannot fix this, so we prevent it: the node stays as a stepping
    stone and only its payload is emptied. The saving is unaffected (the bytes
    are in the payload, not the envelope); all we keep is an addressable uuid.

    Returns the last record in file order plus the active chain's leaf — usually
    the same record, both cheap to keep.
    """
    anchors = set()
    for obj in reversed(objects):
        if isinstance(obj, dict) and obj.get("uuid"):
            anchors.add(obj["uuid"])
            break
    chain = walk_active_chain(objects)
    if chain and chain[-1].get("uuid"):
        anchors.add(chain[-1]["uuid"])
    return anchors


def _empty_payload(obj, placeholder=CHAIN_ANCHOR_PLACEHOLDER):
    """Strip an anchored record's payload in place, keeping its envelope.

    Mirrors the "keep the node, empty the payload" trick compact_range already
    uses for its range survivor. Returns True if a payload was replaced.
    """
    # Rebind rather than mutate in place: callers pass shallow copies
    # (`[dict(o) for o in objects]`, e.g. strip_attachments' dry run), which
    # share the inner `message` dict with the real objects. Mutating it there
    # would edit the caller's live data during a DRY RUN.
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("content") is not None:
        obj["message"] = {**msg, "content": [{"type": "text", "text": placeholder}]}
        return True
    if obj.get("content") is not None:
        obj["content"] = [{"type": "text", "text": placeholder}]
        return True
    return False


def remove_objects_and_rewire(objects, uuids_to_remove, protect=None):
    """Remove objects by uuid and rewire parentUuid on descendants to skip them.

    When a message is dropped, any child pointing to it is re-parented to the
    nearest surviving ancestor (walking up parentUuid as Claude Code sees it,
    i.e. after `compact_relink`). This keeps the active chain unbroken so the
    API doesn't see dangling references.

    Removed records are also pruned from every compact boundary's preserved
    list, and a preserved segment's head/tail/anchor are moved to surviving
    records: CC refuses the whole relink if one listed uuid is missing, and
    then silently drops every preserved record from the conversation.

    `protect` is the set of uuids that must survive as chain stepping-stones
    even when asked to remove them — their payload is emptied instead. Defaults
    to `chain_anchor_uuids(objects)`; pass an empty set to opt out.

    Returns:
        (survivors, removed_count, rewired_count, anchored_count)
    """
    uuids_to_remove = set(u for u in uuids_to_remove if u is not None)
    if not uuids_to_remove:
        return objects, 0, 0, 0

    if protect is None:
        protect = chain_anchor_uuids(objects)
    # An anchored node is kept (emptied), so it must not be treated as removed.
    anchored = uuids_to_remove & set(protect)
    uuids_to_remove -= anchored
    anchored_count = 0
    if anchored:
        for obj in objects:
            if obj.get("uuid") in anchored and _empty_payload(obj):
                anchored_count += 1
    if not uuids_to_remove:
        return objects, 0, 0, anchored_count

    uuid_to_obj = {obj.get("uuid"): obj for obj in objects if obj.get("uuid")}
    relink = compact_relink(objects)
    # Segment orders must be walked before the loop below rewires parents.
    seg_orders = {}
    for obj in objects:
        if not is_compact_boundary(obj):
            continue
        seg = (obj.get("compactMetadata") or {}).get("preservedSegment")
        if isinstance(seg, dict) and any(seg.get(k) in uuids_to_remove
                                         for k in ("headUuid", "tailUuid")):
            seg_orders[obj.get("uuid")] = _segment_order(seg, uuid_to_obj)

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
            cur = effective_parent(obj, relink)
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
        # CC resumes from the leaf a `last-prompt` record names (`summary`
        # records point at leaves the same way): keep those pointing at a node
        # that still exists, like any other child
        if obj.get("type") in ("last-prompt", "summary") and obj.get("leafUuid") in uuids_to_remove:
            obj["leafUuid"] = find_surviving_ancestor(obj["leafUuid"])
        if is_compact_boundary(obj):
            _prune_preserved(obj, uuids_to_remove, find_surviving_ancestor,
                             seg_orders.get(obj.get("uuid"), []))
        survivors.append(obj)

    return survivors, len(uuids_to_remove), rewired, anchored_count


def _segment_order(seg, uuid_to_obj):
    """uuids of a preservedSegment in conversation order (head → tail), walked
    on the ORIGINAL objects; [] if the walk does not reach the head."""
    resolved = _resolve_preserved({"preservedSegment": seg}, uuid_to_obj)
    return resolved[0] if resolved else []


def _prune_preserved(boundary, removed, surviving_ancestor, seg_order):
    """Keep a compact boundary's preserved-record references valid after
    `removed` uuids are deleted. `seg_order` is its preservedSegment in
    conversation order, walked before any rewiring. Rebinds (never mutates)
    nested dicts, because dry runs hand us shallow copies that share them with
    live data."""
    cm = boundary.get("compactMetadata")
    if not isinstance(cm, dict):
        lp = boundary.get("logicalParentUuid")
        if lp in removed:
            boundary["logicalParentUuid"] = surviving_ancestor(lp)
        return
    new_cm = dict(cm)
    changed = False

    pm = cm.get("preservedMessages")
    if isinstance(pm, dict):
        new_pm = dict(pm)
        uuids = pm.get("uuids")
        if isinstance(uuids, list) and any(u in removed for u in uuids):
            new_pm["uuids"] = [u for u in uuids if u not in removed]
        if pm.get("anchorUuid") in removed:
            new_pm["anchorUuid"] = surviving_ancestor(pm["anchorUuid"])
        if new_pm != pm:
            new_cm["preservedMessages"] = new_pm
            changed = True

    seg = cm.get("preservedSegment")
    if isinstance(seg, dict) and any(seg.get(k) in removed
                                     for k in ("headUuid", "tailUuid", "anchorUuid")):
        new_seg = dict(seg)
        order = [u for u in seg_order if u not in removed]
        if seg.get("headUuid") in removed or seg.get("tailUuid") in removed:
            if order:
                new_seg["headUuid"], new_seg["tailUuid"] = order[0], order[-1]
            else:
                new_seg = None  # nothing of the segment survives
        if new_seg is not None and seg.get("anchorUuid") in removed:
            new_seg["anchorUuid"] = surviving_ancestor(seg["anchorUuid"])
        if new_seg is None:
            new_cm.pop("preservedSegment", None)
        else:
            new_cm["preservedSegment"] = new_seg
        changed = True

    if changed:
        boundary["compactMetadata"] = new_cm
    lp = boundary.get("logicalParentUuid")
    if lp in removed:
        boundary["logicalParentUuid"] = surviving_ancestor(lp)


def _next_backup_path(path):
    """Return a backup path that does not yet exist: ``<path>.bak``, then
    ``<path>.bak.1``, ``<path>.bak.2``, ... — so a backup is NEVER skipped just
    because a prior one exists."""
    base = Path(str(path) + ".bak")
    if not base.exists():
        return base
    i = 1
    while True:
        cand = Path(str(path) + f".bak.{i}")
        if not cand.exists():
            return cand
        i += 1


def save_session(path, objects, create_backup=True):
    """Write all objects as JSONL. Optionally create a fresh backup first.

    The backup is *enumerated*: every mutating run that asks for a backup gets
    its own recoverable copy (``.bak``, then ``.bak.1``, ``.bak.2``, ...). This
    fixes a real footgun in the old single-``.bak`` scheme — if a stale ``.bak``
    already existed, the backup was silently skipped and the run mutated the
    session with no fresh recovery point. Pass ``create_backup=False`` (the
    ``--no-backup`` flag) to opt out.
    """
    path = Path(path).expanduser()
    if create_backup and path.exists():
        shutil.copy2(path, _next_backup_path(path))
    with open(path, "w", encoding="utf-8") as f:
        for obj in objects:
            f.write(dump_line(obj))
    os.chmod(path, 0o600)  # transcripts are private; CC creates them 0600


# Claude Code writes compact JSON, and its fast transcript scanner matches raw
# byte patterns (`{"parentUuid":`, `"type":"last-prompt"`, ...). The default
# `json.dumps` separators (", " / ": ") hide every record from that scanner:
# the background-session dispatcher then decides there is no conversation to
# restore, starts a fresh one under the same id and moves the file aside as
# `<id>.orphaned-<ts>-<rand>.jsonl` (seen 2026-10-25, CC 2.1.310). Always write
# what CC writes.
def dump_line(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def compute_active_chain_tokens(objects, uuid_index=None, factor=None):
    """Estimate the token weight of the active chain — roughly what CC re-sends
    to the model on the next turn.

    Counts BOTH message content and `attachment` entries. Attachments are not
    decoration: CC re-expands each one into a `<system-reminder>` on every
    request (see lib/attachment_cost.py), and in a long session they can be a
    six-figure token block. Leaving them out made this estimate — which feeds
    `reset_usage_metadata()` and therefore CC's context gauge — read far below
    the real prompt size, so a barely-stripped session would show a reassuring
    number and then snap back to ~100% on the very next turn.

    The system prompt and tool schemas count too when the session recorded
    them (a `prompt_snapshot` attachment, CC >= 2.1.267): the latest snapshot
    on the chain is what every request sends. Older sessions have none, and
    the estimate then remains a floor, not the exact size.

    Images are priced by Anthropic's pixel formula for the session model's
    tier, never as base64 text: chars/4 on a screenshot's base64 reads 20-100x
    too high, enough to pin the gauge past the context limit.

    `factor` (from `calibration()`) scales the text part to what this
    session's content really costs; without it the text is sized at chars/4.
    """
    chain = walk_active_chain(objects, uuid_index)
    tier = tier_for_model(session_model(chain))
    text_chars, image_tokens = _prompt_parts(chain, tier)
    return round(estimate_tokens(text_chars) * (factor or 1.0)) + image_tokens


def _prompt_parts(records, tier):
    """(text_chars, image_tokens) of a request built from `records`: content,
    rendered attachments, and the latest recorded system prompt + tools."""
    from .attachment_cost import envelope_rendered_chars, snapshot_chars
    text_chars, image_tokens = chain_token_parts(records, tier)
    text_chars += sum(envelope_rendered_chars(obj) for obj in records)
    snaps = [o for o in records if o.get("type") == "attachment"
             and (o.get("attachment") or {}).get("type") == "prompt_snapshot"]
    if snaps:
        text_chars += snapshot_chars(snaps[-1]["attachment"])
    return text_chars, image_tokens


# chars/4 is a rule of thumb for English prose. Measured on real sessions it
# runs 1.16x low on an English coding session and 1.5x low on a Russian
# genealogy one (archive text tokenized at ~2.35 chars/token). Calibrating
# against the session's own recorded usage fixes the scale per session.
CALIBRATION_RANGE = (0.5, 3.0)


def _measured_usage(obj):
    """Real context size of the API call behind an assistant record, or None
    when the record carries no measurement: CC's "<synthetic>" records, and
    usage a previous strip rewrote (`reset_usage_metadata` zeroes the cache
    fields; a real Claude Code call always reads or writes the prompt cache)."""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or str(msg.get("model") or "").startswith("<"):
        return None
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    parts = [u] + [it for it in (u.get("iterations") or []) if isinstance(it, dict)]
    cached = sum((p.get("cache_read_input_tokens") or 0)
                 + (p.get("cache_creation_input_tokens") or 0) for p in parts)
    if cached <= 0:
        return None
    return _usage_context_total(u)


def calibration(objects, uuid_index=None):
    """Scale factor from this session's chars/4 estimate to what Claude Code
    actually measured, or None when nothing was measured.

    Ground truth is the newest assistant turn with real usage: its context
    size is exactly the prompt of that API call — every chain record before
    the turn, its attachments, and the system prompt + tools. The factor is
    (real − images) / (text estimate of that same prompt), since images are
    already priced exactly. Clamped to CALIBRATION_RANGE.

    Only valid on a file whose history has not been edited since that call:
    take it from the live original, before stripping. Only the NEWEST real
    turn counts: once a strip has rewritten it (the gauge reset always pins
    the last turn), older turns measured a history that no longer exists, so
    a stripped file has no calibration until its next real turn.

    Returns {"factor", "real", "estimate", "pos"} (pos: chain position of the
    measured turn).
    """
    chain = walk_active_chain(objects, uuid_index)
    tier = tier_for_model(session_model(chain))
    for i in range(len(chain) - 1, -1, -1):
        obj = chain[i]
        msg = obj.get("message") if obj.get("type") == "assistant" else None
        if (not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict)
                or str(msg.get("model") or "").startswith("<")):
            continue  # not a turn, no usage, or a local "<synthetic>" record
        real = _measured_usage(obj)
        if real is None:
            return None  # the newest turn's usage was rewritten by a strip
        # One API response is written as several assistant records sharing a
        # message id; the prompt is everything before the first of them.
        mid = chain[i]["message"].get("id")
        start = i
        while (mid and start > 0 and chain[start - 1].get("type") == "assistant"
               and (chain[start - 1].get("message") or {}).get("id") == mid):
            start -= 1
        text_chars, image_tokens = _prompt_parts(chain[:start], tier)
        text_est = estimate_tokens(text_chars)
        if text_est <= 0:
            return None
        lo, hi = CALIBRATION_RANGE
        factor = min(hi, max(lo, (real - image_tokens) / text_est))
        return {"factor": factor, "real": real, "estimate": text_est + image_tokens,
                "pos": start}
    return None


def session_model(chain):
    """Model of the newest assistant record on the chain: the model the
    session will continue on.

    Claude Code writes local assistant records (API errors, "Prompt is too
    long", interrupts) with the placeholder model "<synthetic>". They are not
    a real model, so they are skipped."""
    for obj in reversed(chain):
        if obj.get("type") == "assistant":
            msg = obj.get("message")
            model = msg.get("model") if isinstance(msg, dict) else None
            if model and not model.startswith("<"):
                return model
    return None


def _obj_content(obj):
    msg = obj.get("message") if isinstance(obj, dict) else None
    return msg.get("content") if isinstance(msg, dict) else (
        obj.get("content") if isinstance(obj, dict) else None)


def obj_image_tokens(obj, tier=None):
    """Token cost of every image in a record's content, top-level and nested
    inside tool results, on `tier` (default: high-resolution)."""
    return content_image_stats(_obj_content(obj), tier or DEFAULT_TIER)["tokens"]


def chain_token_parts(objs, tier=None):
    """(text_chars, image_tokens) for a run of records: text-like content to
    be sized at chars/4, and images priced by the pixel formula."""
    text_chars = 0
    image_tokens = 0
    for obj in objs:
        cc = count_content_chars(obj)
        text_chars += cc["total"] - cc["image"]
        image_tokens += obj_image_tokens(obj, tier)
    return text_chars, image_tokens


def _context_fields_total(u):
    return ((u.get("input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0))


def _usage_context_total(u):
    """Largest context size this usage record claims, top-level or nested.

    CC 2.1.27x reads the gauge from ``usage.iterations[]`` (per-API-call
    counts), so a record whose top-level fields look small can still pin the
    meter at the pre-strip size.
    """
    total = _context_fields_total(u)
    for it in u.get("iterations") or []:
        if isinstance(it, dict):
            total = max(total, _context_fields_total(it))
    return total


def _pin_usage(u, target):
    u["input_tokens"] = target
    u["cache_read_input_tokens"] = 0
    u["cache_creation_input_tokens"] = 0
    if "iterations" in u:
        u["iterations"] = None
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        for k in cc:
            if isinstance(cc[k], (int, float)):
                cc[k] = 0


def reset_usage_metadata(objects, target_tokens):
    """Rewrite assistant ``usage`` so CC's context gauge reflects reality.

    CC computes "context left" from the token counts recorded on the most recent
    assistant turn (``input_tokens`` + ``cache_read_input_tokens`` +
    ``cache_creation_input_tokens``) — a STORED number, not a live recount of the
    conversation. After stripping, those numbers still describe the *pre-strip*
    size, so the meter stays pinned near 100% and CC blocks new input
    client-side ("Context limit reached"), even though the on-disk conversation
    is now tiny.

    This caps every assistant turn whose recorded context exceeds
    ``target_tokens`` down to ``target_tokens`` (it never inflates a smaller
    turn), and pins the active chain's final assistant turn to exactly
    ``target_tokens`` — even if that turn recorded 0 (e.g. a blocked
    "Prompt is too long" turn). Cache fields are zeroed and the nested
    ``iterations`` list is nulled on touched turns (newer CC builds read the
    gauge from it); the next real turn re-establishes accurate counts.

    Returns the number of usage records modified.
    """
    target = max(0, int(target_tokens))
    chain = walk_active_chain(objects)
    chain_uuids = {o.get("uuid") for o in chain}

    touched = set()
    leaf_obj = None
    for obj in objects:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        if obj.get("uuid") in chain_uuids:
            leaf_obj = obj  # last in file order → active-chain leaf assistant
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        if _usage_context_total(u) > target:
            _pin_usage(u, target)
            touched.add(id(obj))

    if leaf_obj is not None:
        msg = leaf_obj["message"]
        u = msg.get("usage")
        if not isinstance(u, dict):
            u = {}
            msg["usage"] = u
        _pin_usage(u, target)
        u.setdefault("output_tokens", 0)
        touched.add(id(leaf_obj))

    return len(touched)


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
    """Count characters by block type in a message object's content field.

    Images nested inside a tool_result count under "image", not
    "tool_result": callers price the "image" bucket by the pixel formula, and
    sizing a Read screenshot's base64 as tool output inflates it 20-100x.
    """
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
                if block_type == "text" and wrapped_thinking_text(block) is not None:
                    # Attribute wrapped <thinking>/<think> text blocks to the
                    # thinking bucket so they show up in analyze() output.
                    counts["thinking"] += char_count
                elif block_type != "image" and isinstance(block.get("content"), list):
                    nested = sum(image_payload_chars(b)
                                 for b, _ in iter_image_blocks(block["content"]))
                    bucket = block_type if block_type in counts else "other"
                    counts[bucket] += char_count - nested
                    counts["image"] += nested
                elif block_type in counts:
                    counts[block_type] += char_count
                else:
                    counts["other"] += char_count
                counts["total"] += char_count
    return counts
