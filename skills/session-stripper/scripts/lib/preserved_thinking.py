"""Preserved thinking: which thinking blocks an edit invalidates.

On Claude Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5, every `thinking`
block's encrypted `signature` binds it to the conversation that produced it:

- the prefix before the block (system prompt, tools, and every earlier
  message, with earlier thinking blocks NOT counted as part of the prefix), and
- a chain to the previous thinking block.

When a transcript comes back with an earlier turn edited, every later thinking
block fails that check. Claude Code sends the `thinking-binding-controls` beta
in drop mode, so the API silently drops those blocks (unbilled) and the model
continues without that reasoning. Removing thinking blocks from the FRONT of the
history is fine; removing one from the middle breaks the chain for every later
block.

This module compares the live original session with the stripped result and
reports which surviving thinking blocks the API will drop. The stripper then
removes them from the file by default, so the file matches what the model sees.

Model list and rules: the claude-api reference, "Migrating to Claude Fable 5.1
from Claude Fable 5", breaking changes 2 and 3. Claude Mythos 5.1 does not run
the check, so it is not listed.
"""

from __future__ import annotations

import json

from .chain import build_uuid_index, estimate_tokens, load_session, walk_active_chain

# Models that run preserved thinking's history-editing check.
BOUND_MODEL_PREFIXES = (
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)

_THINKING_TYPES = ("thinking", "redacted_thinking")


def session_model(chain):
    """Model of the newest assistant record on the chain: the model the
    session will continue on, which is the one that runs the check."""
    for obj in reversed(chain):
        if obj.get("type") == "assistant":
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("model"):
                return msg["model"]
    return None


def is_bound_model(model):
    return bool(model) and any(model.startswith(p) for p in BOUND_MODEL_PREFIXES)


def _content(obj):
    msg = obj.get("message")
    if isinstance(msg, dict) and "content" in msg:
        return msg.get("content")
    return obj.get("content")


def _block_key(block):
    """Identity of a thinking block across the two files. The signature is
    unique per block; fall back to the payload for unsigned blocks."""
    return (block.get("type"), block.get("signature") or
            block.get("data") or block.get("thinking") or "")


def _fingerprint(obj):
    """What this record contributes to the prefix of later thinking blocks:
    its content with thinking blocks removed (earlier thinking is not part of
    the prefix). Returns None when nothing is left, e.g. a thinking-only
    record, so dropping such a record counts as removing thinking only."""
    content = _content(obj)
    if isinstance(content, list):
        content = [b for b in content
                   if not (isinstance(b, dict) and b.get("type") in _THINKING_TYPES)]
        if not content:
            content = None
    payload = {"type": obj.get("type"), "content": content}
    if obj.get("attachment") is not None:
        payload["attachment"] = obj.get("attachment")
    if payload["content"] is None and "attachment" not in payload:
        return None
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _thinking_sequence(chain):
    """[(key, chain_pos, prefix_len, chars)] in chain order, where prefix_len
    counts the non-empty records before the block's record."""
    out = []
    prefix_len = 0
    for pos, obj in enumerate(chain):
        content = _content(obj)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in _THINKING_TYPES:
                    chars = (len(b.get("thinking", "")) + len(b.get("data", ""))
                             + len(b.get("signature", "")))
                    out.append((_block_key(b), pos, prefix_len, chars))
        if _fingerprint(obj) is not None:
            prefix_len += 1
    return out


def _fingerprints(chain):
    return [fp for fp in (_fingerprint(o) for o in chain) if fp is not None]


def _common_prefix_len(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def find_invalidated(original_chain, new_chain):
    """Return a report of the thinking blocks in `new_chain` that the API will
    drop, given that `original_chain` is what produced them.

    Report keys: model, bound, invalid (list of (chain_pos, chars) in the new
    chain), first_pos (new-chain position of the first invalid block, or None),
    chars, est_tokens, edit_prefix (number of unchanged records before the
    first edit)."""
    model = session_model(new_chain) or session_model(original_chain)
    report = {"model": model, "bound": is_bound_model(model), "invalid": [],
              "first_pos": None, "chars": 0, "est_tokens": 0, "edit_prefix": None}
    if not report["bound"]:
        return report

    common = _common_prefix_len(_fingerprints(original_chain), _fingerprints(new_chain))
    report["edit_prefix"] = common

    new_seq = _thinking_sequence(new_chain)
    kept = {key for key, *_ in new_seq}
    new_by_key = {key: (pos, prefix_len, chars) for key, pos, prefix_len, chars in new_seq}

    broken = False          # once one block is invalid, every later one is
    seen_kept = False       # a kept block exists earlier in the chain
    gap = False             # a block was removed after a kept one
    for key, _pos, orig_prefix_len, _chars in _thinking_sequence(original_chain):
        if key not in kept:
            if seen_kept:
                gap = True
            continue
        pos, prefix_len, chars = new_by_key[key]
        # The block's prefix is intact only if it sits after the same records
        # as before AND all of them are unchanged.
        prefix_ok = prefix_len == orig_prefix_len and prefix_len <= common
        if broken or gap or not prefix_ok:
            broken = True
            report["invalid"].append((pos, chars))
        seen_kept = True

    if report["invalid"]:
        report["first_pos"] = min(p for p, _ in report["invalid"])
        report["chars"] = sum(c for _, c in report["invalid"])
        report["est_tokens"] = estimate_tokens(report["chars"])
    return report


def analyze_paths(original_path, new_path):
    def chain_of(path):
        objects = load_session(path)
        return walk_active_chain(objects, build_uuid_index(objects))
    return find_invalidated(chain_of(original_path), chain_of(new_path))


def format_report(report, will_remove, dry_run=False):
    """Human lines for a report with invalid blocks; [] when there is nothing
    to say."""
    if not report["bound"] or not report["invalid"]:
        return []
    n = len(report["invalid"])
    lines = [
        f"Preserved thinking ({report['model']}): this edit invalidates {n} later "
        f"thinking block(s), {report['chars']:,} chars, ~{report['est_tokens']:,} tokens, "
        f"from chain pos {report['first_pos']} on.",
        "  The API drops them unbilled, so the model continues without that reasoning.",
    ]
    if will_remove:
        verb = "Would remove" if dry_run else "Removing"
        lines.append(f"  {verb} them from the file so it matches what the model sees "
                     f"(keep them with --keep-dropped-thinking).")
    else:
        lines.append("  Kept in the file (--keep-dropped-thinking). They still cost no "
                     "tokens, but the context gauge will overstate the size.")
    return lines
