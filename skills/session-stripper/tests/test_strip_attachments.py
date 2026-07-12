"""Tests for strip-attachments.

The load-bearing test is `test_resume_walk_still_reaches_every_message`.
Attachments are `parentUuid` chain participants — real messages chain THROUGH
them. CC resumes by walking parentUuid back from the newest leaf and stopping
the instant a parent uuid is missing, so a naive delete doesn't corrupt the file
visibly: it silently truncates the conversation CC can see. That failure is
invisible to a "does the file still parse" check, so we simulate CC's own walk.
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import CC_VERSION, assert_chain_valid  # noqa: E402

from lib.attachment_cost import rendered_chars  # noqa: E402
from lib.chain import compute_active_chain_tokens, load_session  # noqa: E402
from lib.strip_attachments import collect_stats, strip_attachments  # noqa: E402


# ── builder: a session where attachments are chain participants ──────────

def build_session_with_attachments(items):
    """items: list of ("user"|"assistant", text) or ("attachment", {…attachment…}).

    Every entry is linked into a single parentUuid chain, exactly as CC does
    (isChainParticipant() excludes only `progress`).
    """
    session_id = str(_uuid.uuid4())
    tmp = Path(tempfile.mkstemp(suffix=".jsonl", prefix="ss-att-")[1])
    parent = None
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with open(tmp, "w") as f:
        for kind, payload in items:
            ts = ts + timedelta(milliseconds=1)
            u = str(_uuid.uuid4())
            base = {
                "parentUuid": parent,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp/test-cwd",
                "sessionId": session_id,
                "version": CC_VERSION,
                "gitBranch": "master",
                "slug": "test-session",
                "uuid": u,
                "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            }
            if kind == "attachment":
                base["type"] = "attachment"
                base["attachment"] = payload
            else:
                base["type"] = kind
                base["message"] = {"role": kind, "content": [{"type": "text", "text": payload}]}
            f.write(json.dumps(base, ensure_ascii=False) + "\n")
            parent = u
    return tmp


def cc_resume_walk(path):
    """Simulate CC's buildConversationChain(): start at the newest non-sidechain
    user/assistant leaf, follow parentUuid back, stop dead on a missing parent.
    Returns the user/assistant texts CC would actually see, oldest-first."""
    objects = load_session(path)
    index = {o["uuid"]: o for o in objects if "uuid" in o}
    leaf = None
    for o in reversed(objects):
        if o.get("type") in ("user", "assistant") and not o.get("isSidechain"):
            leaf = o
            break
    seen = []
    cur = leaf
    while cur is not None:
        if cur.get("type") in ("user", "assistant"):
            seen.append(cur["message"]["content"][0]["text"])
        p = cur.get("parentUuid")
        cur = index.get(p) if p else None  # missing parent -> walk ends
    return list(reversed(seen))


def task_reminder(n_tasks, tag=""):
    return {
        "type": "task_reminder",
        "content": [
            {
                "id": str(i),
                "status": "pending",
                "subject": f"task {i}{tag}",
                # The renderer NEVER sends description — it must not be costed.
                "description": "D" * 5000,
            }
            for i in range(n_tasks)
        ],
    }


# ── the load-bearing test ────────────────────────────────────────────────

def test_resume_walk_still_reaches_every_message():
    """After stripping, CC's parentUuid walk must still reach every message.

    This is the regression that a naive `del line` would cause: the file stays
    valid JSON, `verify` may pass, and CC silently loses everything older than
    the first deleted attachment.
    """
    items = [
        ("user", "m0"),
        ("attachment", task_reminder(3, "-a")),
        ("assistant", "m1"),
        ("attachment", task_reminder(3, "-b")),
        ("attachment", {"type": "total_tokens_reminder", "text": "<total_tokens>900</total_tokens>"}),
        ("user", "m2"),
        ("attachment", task_reminder(3, "-c")),
        ("attachment", task_reminder(3, "-d")),  # consecutive attachments
        ("assistant", "m3"),
        ("attachment", task_reminder(2, "-e")),
        ("user", "m4"),
    ]
    path = build_session_with_attachments(items)
    before = cc_resume_walk(path)
    assert before == ["m0", "m1", "m2", "m3", "m4"]

    strip_attachments(path, no_backup=True)

    after = cc_resume_walk(path)
    assert after == before, (
        f"CC's resume walk lost messages: {before} -> {after}. "
        "Children of dropped attachments were not re-parented."
    )
    assert_chain_valid(path)


def test_consecutive_dropped_attachments_rewire_to_surviving_ancestor():
    """Two attachments back-to-back: the survivor must skip BOTH, not point at
    a deleted uuid (the chain-resolve step in CC's own progressBridge)."""
    items = [
        ("user", "m0"),
        ("attachment", task_reminder(1, "-a")),
        ("attachment", task_reminder(1, "-b")),
        ("attachment", task_reminder(1, "-c")),
        ("assistant", "m1"),
        ("attachment", task_reminder(1, "-keep")),
        ("user", "m2"),
    ]
    path = build_session_with_attachments(items)
    strip_attachments(path, no_backup=True)
    assert cc_resume_walk(path) == ["m0", "m1", "m2"]
    assert_chain_valid(path)


# ── policy ───────────────────────────────────────────────────────────────

def test_keeps_last_task_reminder_only():
    items = [("user", "m0")] + [
        ("attachment", task_reminder(2, f"-{i}")) for i in range(5)
    ] + [("assistant", "m1")]
    path = build_session_with_attachments(items)
    strip_attachments(path, no_backup=True)
    stats = collect_stats(load_session(path))
    assert stats["task_reminder"]["count"] == 1, stats
    # ...and it's the NEWEST one that survived.
    objs = load_session(path)
    surviving = [o for o in objs if o.get("type") == "attachment"][0]
    assert "-4" in surviving["attachment"]["content"][0]["subject"]


def test_capability_attachments_are_never_dropped():
    """Deleting *_delta types makes CC re-emit a fresh full-size announcement —
    you get the tokens straight back. They must survive an unqualified strip."""
    items = [
        ("user", "m0"),
        ("attachment", {"type": "deferred_tools_delta", "addedNames": ["X"],
                        "addedLines": ["X: a tool"]}),
        ("attachment", {"type": "mcp_instructions_delta", "addedNames": ["srv"],
                        "addedBlocks": ["## srv instructions"]}),
        ("attachment", {"type": "nested_memory",
                        "content": {"path": "CLAUDE.md", "content": "rules"}}),
        ("attachment", task_reminder(1)),
        ("attachment", task_reminder(1)),
        ("assistant", "m1"),
    ]
    path = build_session_with_attachments(items)
    strip_attachments(path, no_backup=True)
    stats = collect_stats(load_session(path))
    assert stats["deferred_tools_delta"]["count"] == 1
    assert stats["mcp_instructions_delta"]["count"] == 1
    assert stats["nested_memory"]["count"] == 1


def test_drop_all_requires_explicit_types():
    path = build_session_with_attachments([("user", "m0"), ("assistant", "m1")])
    try:
        strip_attachments(path, drop_all=True, no_backup=True)
    except ValueError as e:
        assert "--types" in str(e)
    else:
        raise AssertionError("--drop-all without --types must raise")


def test_zero_cost_attachments_skipped_unless_requested():
    """A hook_success from PostToolUse renders to [] — dropping it saves nothing,
    so the default run leaves it alone rather than rewiring the chain for free."""
    free = {"type": "hook_success", "hookEvent": "PostToolUse",
            "hookName": "h", "content": "noise" * 100}
    items = [("user", "m0")] + [("attachment", dict(free)) for _ in range(5)] + [("assistant", "m1")]
    path = build_session_with_attachments(items)

    stats = strip_attachments(path, no_backup=True)
    assert stats["attachments_dropped"] == 0
    assert stats["free_attachments_skipped"] == 3  # 5 minus keep-last-2
    assert collect_stats(load_session(path))["hook_success"]["count"] == 5

    stats = strip_attachments(path, no_backup=True, include_free=True)
    assert stats["attachments_dropped"] == 3
    assert stats["est_tokens_saved"] == 0  # honest: shrinks the file, saves no context
    assert cc_resume_walk(path) == ["m0", "m1"]


def test_dry_run_changes_nothing_but_reports_rewires():
    items = [("user", "m0")] + [("attachment", task_reminder(2, f"-{i}")) for i in range(4)] + [("assistant", "m1")]
    path = build_session_with_attachments(items)
    before = path.read_text()
    stats = strip_attachments(path, dry_run=True, no_backup=True)
    assert path.read_text() == before
    assert stats["attachments_dropped"] == 3
    assert stats["parents_rewired"] > 0


# ── cost model ───────────────────────────────────────────────────────────

def test_task_reminder_cost_excludes_descriptions():
    """The renderer emits only `#id. [status] subject` — the 5 KB description
    field is never sent. Sizing the raw record over-counts ~4x, which is how the
    savings from this command got overstated in the first place."""
    att = task_reminder(3)
    raw = len(json.dumps(att))
    cost = rendered_chars(att)
    assert raw > 15000, "fixture should have fat descriptions"
    assert cost < 600, f"rendered cost must exclude descriptions, got {cost}"


def test_hook_success_costs_nothing_unless_sessionstart_or_prompt():
    assert rendered_chars({"type": "hook_success", "hookEvent": "PostToolUse",
                           "hookName": "h", "content": "x" * 500}) == 0
    assert rendered_chars({"type": "hook_success", "hookEvent": "SessionStart",
                           "hookName": "h", "content": "x" * 500}) > 500


def test_unknown_types_are_kept_and_costed_conservatively():
    """CC adds and renames attachment types between releases. An unknown type
    must never be silently dropped, and must never be silently costed at zero."""
    att = {"type": "some_future_type_2027", "content": "y" * 400}
    assert rendered_chars(att) >= 400

    items = [("user", "m0")] + [("attachment", dict(att)) for _ in range(4)] + [("assistant", "m1")]
    path = build_session_with_attachments(items)
    strip_attachments(path, no_backup=True)
    assert collect_stats(load_session(path))["some_future_type_2027"]["count"] == 4


def test_active_chain_estimate_counts_attachments():
    """The estimate feeds reset_usage_metadata(), which drives CC's context
    gauge. If attachments aren't counted, a barely-stripped session reports a
    reassuring number and then snaps back to ~100% on the next real turn."""
    plain = build_session_with_attachments([("user", "m0"), ("assistant", "m1")])
    heavy = build_session_with_attachments(
        [("user", "m0")] + [("attachment", task_reminder(10, f"-{i}")) for i in range(20)]
        + [("assistant", "m1")]
    )
    assert compute_active_chain_tokens(load_session(heavy)) > \
           compute_active_chain_tokens(load_session(plain)) + 1000


# ── unittest wrapper so `tests/run.sh` (unittest discover) collects these ──

import unittest  # noqa: E402


class TestStripAttachments(unittest.TestCase):
    pass


for _name, _fn in sorted(list(globals().items())):
    if _name.startswith("test_") and callable(_fn):
        setattr(TestStripAttachments, _name, staticmethod(_fn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
