"""REGRESSION: records a compact boundary PRESERVES are part of the conversation.

Since CC 2.1.3xx a `/compact` can keep a run of pre-boundary records verbatim.
The boundary (`parentUuid: null`) lists them in
`compactMetadata.preservedMessages` / `preservedSegment`, and Claude Code
splices them back in after the anchor on every load (minified `dWo`/`uWo` in
2.1.322). No on-disk parentUuid leads from the leaf to them.

Incident 2026-11-10: a session at 913k tokens looked like ~174k to `analyze`,
because the walk stopped at the boundary. 3,122 preserved records — thinking,
tool calls, 64 tool-result images — were invisible to every command, so
`strip-all` freed almost nothing and the gauge snapped back to 91% on the
next turn.

The flip side is just as dangerous: Claude Code refuses the whole relink if a
single listed uuid is missing, silently dropping every preserved record. So a
command that deletes a preserved record must prune it from the list.

Expected chains below are written out by hand from CC's algorithm, not
computed by the code under test.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shutil
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
import helpers  # noqa: F401 — puts scripts/ on sys.path

from lib.analyze import analyze_session, health_check
from lib.chain import (compact_relink, compute_active_chain_tokens, load_session,
                       remove_objects_and_rewire, save_session, walk_active_chain)
from lib.compact import compact_before
from lib.replace_images import list_images
from lib.strip_attachments import strip_attachments
from lib.strip_thinking import strip_thinking
from lib.strip_tools import strip_tools

SID = "11111111-2222-3333-4444-555555555555"
READ_ID = "toolu_01PRESERVEDREADAAAAAAAAAA"


def _png_b64(w=1260, h=990, pad=50_000):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return base64.b64encode(sig + ihdr + b"\x00" * (8 + pad)).decode()


class _Builder:
    def __init__(self):
        self.ts = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
        self.objects = []

    def _stamp(self):
        self.ts += timedelta(seconds=1)
        return self.ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def rec(self, uuid, parent, type_, content=None, **extra):
        o = {"parentUuid": parent, "isSidechain": False, "type": type_, "uuid": uuid,
             "timestamp": self._stamp(), "sessionId": SID, "cwd": "/tmp/relink",
             "version": "2.1.322", "slug": "relink-test"}
        if type_ in ("user", "assistant"):
            o["message"] = {"role": type_, "content": content}
            if type_ == "assistant":
                o["message"]["model"] = "claude-opus-5-5"
                o["message"]["id"] = f"msg_{uuid}"
        o.update(extra)
        self.objects.append(o)
        return o

    def att(self, uuid, parent, text):
        return self.rec(uuid, parent, "attachment",
                        attachment={"type": "total_tokens_reminder", "text": text})

    def boundary(self, uuid, logical_parent, preserved=None, segment=None):
        cm = {"trigger": "manual", "preTokens": 900_000}
        if preserved is not None:
            cm["preservedMessages"] = {"anchorUuid": preserved[0], "uuids": preserved[1]}
        if segment is not None:
            cm["preservedSegment"] = dict(zip(("headUuid", "anchorUuid", "tailUuid"), segment))
        return self.rec(uuid, None, "system", subtype="compact_boundary",
                        content="Conversation compacted", logicalParentUuid=logical_parent,
                        compactMetadata=cm)

    def write(self):
        path = Path(tempfile.mkstemp(suffix=".jsonl", prefix="ss-relink-")[1])
        save_session(path, self.objects, create_backup=False)
        return path


BIG_KEEP_TEXT = "keep me " * 5000  # 40k chars ≈ 10k tokens, only reachable via the relink
PRESERVED = ["u2", "a2", "a3", "t3", "u3", "a4"]
EXPECTED_CHAIN = ["B", "u2", "a2", "a3", "t3", "u3", "a4", "S", "t5", "u5", "a5"]


def _fixture(list_mode=True, segment=True):
    """u1/a1 summarized away; u2..a4 preserved; S = the new summary."""
    b = _Builder()
    b.rec("u1", None, "user", "old question")
    b.rec("a1", "u1", "assistant", [{"type": "text", "text": "old answer"}])
    b.rec("u2", "a1", "user", BIG_KEEP_TEXT)
    b.rec("a2", "u2", "assistant", [{"type": "thinking", "thinking": "hmm",
                                      "signature": "S" * 800}])
    b.rec("a3", "a2", "assistant", [{"type": "text", "text": "reading it"},
                                    {"type": "tool_use", "id": READ_ID, "name": "Read",
                                     "input": {"file_path": "/tmp/frame.png"}}])
    b.att("t3", "a3", "Token usage: 500000/1000000")
    b.rec("u3", "t3", "user", [{"type": "tool_result", "tool_use_id": READ_ID, "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": _png_b64()}}]}])
    b.rec("a4", "u3", "assistant", [{"type": "text", "text": "saw it"}])
    b.boundary("B", "a4",
               preserved=("B", list(PRESERVED)) if list_mode else None,
               segment=("u2", "B", "a4") if segment else None)
    b.rec("S", "B", "user", "This session is being continued... summary of u1/a1",
          isCompactSummary=True)
    b.att("t5", "S", "Token usage: 600000/1000000")
    b.rec("u5", "t5", "user", "new question")
    b.rec("a5", "u5", "assistant", [{"type": "text", "text": "new answer"}])
    return b


def _nested_fixture():
    """Window-cam shape: the newest boundary's preserved list starts with an
    OLDER boundary and its summary (a compaction of a compacted session)."""
    b = _Builder()
    b.rec("x1", None, "user", "ancient")
    b.boundary("B0", "x1", preserved=("S0", ["p0"]))  # moot: not the last boundary
    b.rec("S0", "B0", "user", "summary zero", isCompactSummary=True)
    b.rec("p1", "S0", "user", "p1")
    b.rec("p2", "p1", "assistant", [{"type": "text", "text": "p2"}])
    b.boundary("B1", "p2", preserved=("B1", ["B0", "S0", "p1", "p2"]))
    b.rec("S1", "B1", "user", "summary one", isCompactSummary=True)
    b.rec("q1", "S1", "user", "q1")
    b.rec("q2", "q1", "assistant", [{"type": "text", "text": "q2"}])
    return b


def _chain_ids(path_or_objects):
    objects = (load_session(path_or_objects) if isinstance(path_or_objects, (str, Path))
               else path_or_objects)
    return [o["uuid"] for o in walk_active_chain(objects)]


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _boundary(path, uuid="B"):
    return next(o for o in load_session(path) if o.get("uuid") == uuid)


class _Base(unittest.TestCase):
    def file(self, builder):
        path = builder.write()
        self.addCleanup(path.unlink, missing_ok=True)
        self.addCleanup(shutil.rmtree, path.with_suffix(""), ignore_errors=True)
        return path


class TestWalk(_Base):
    def test_walk_splices_preserved_records_after_the_anchor(self):
        self.assertEqual(_chain_ids(_fixture().objects), EXPECTED_CHAIN)

    def test_walk_mode_segment_only(self):
        self.assertEqual(_chain_ids(_fixture(list_mode=False).objects), EXPECTED_CHAIN)

    def test_list_order_wins_over_disk_parents(self):
        b = _fixture()
        # Scramble an on-disk parent inside the run: CC ignores it.
        next(o for o in b.objects if o["uuid"] == "a3")["parentUuid"] = "u1"
        self.assertEqual(_chain_ids(b.objects), EXPECTED_CHAIN)

    def test_summarized_records_are_dropped(self):
        relink = compact_relink(_fixture().objects)
        self.assertEqual(relink["status"], "ok")
        self.assertEqual(relink["dropped"], {"u1", "a1"})
        self.assertEqual(relink["preserved"], PRESERVED)

    def test_plain_boundary_still_starts_the_chain(self):
        b = _fixture(list_mode=False, segment=False)
        self.assertEqual(_chain_ids(b.objects), ["B", "S", "t5", "u5", "a5"])

    def test_nested_compaction(self):
        self.assertEqual(_chain_ids(_nested_fixture().objects),
                         ["B1", "B0", "S0", "p1", "p2", "S1", "q1", "q2"])

    def test_missing_listed_uuid_breaks_the_relink_like_cc(self):
        b = _fixture()
        b.objects = [o for o in b.objects if o["uuid"] != "a3"]
        relink = compact_relink(b.objects)
        self.assertEqual(relink["status"], "broken")
        self.assertEqual(relink["missing"], ["a3"])
        # CC relinks nothing: the walk stops at the boundary.
        self.assertEqual(_chain_ids(b.objects), ["B", "S", "t5", "u5", "a5"])

    def test_unresolvable_segment_breaks_the_relink(self):
        b = _fixture(list_mode=False)
        next(o for o in b.objects if o["uuid"] == "a3")["parentUuid"] = None
        self.assertEqual(compact_relink(b.objects)["status"], "broken")
        self.assertEqual(_chain_ids(b.objects), ["B", "S", "t5", "u5", "a5"])


class TestDeletionKeepsTheRelink(_Base):
    def test_strip_thinking_prunes_the_preserved_list(self):
        path = self.file(_fixture())
        _quiet(strip_thinking, str(path), no_backup=True)
        self.assertNotIn("a2", _boundary(path)["compactMetadata"]["preservedMessages"]["uuids"])
        self.assertEqual(compact_relink(load_session(path))["status"], "ok")
        self.assertEqual(_chain_ids(path), [u for u in EXPECTED_CHAIN if u != "a2"])
        self.assertTrue(_quiet(health_check, str(path)))

    def test_strip_attachments_prunes_the_preserved_list(self):
        path = self.file(_fixture())
        stats = _quiet(strip_attachments, str(path), no_backup=True)
        self.assertEqual(stats["attachments_dropped"], 1)  # t3: keep the newest only
        self.assertNotIn("t3", _boundary(path)["compactMetadata"]["preservedMessages"]["uuids"])
        self.assertEqual(_chain_ids(path), [u for u in EXPECTED_CHAIN if u != "t3"])

    def test_removing_the_segment_head_moves_it(self):
        b = _fixture(list_mode=False)
        survivors, removed, _, _ = remove_objects_and_rewire(b.objects, {"u2"}, protect=set())
        self.assertEqual(removed, 1)
        seg = next(o for o in survivors if o["uuid"] == "B")["compactMetadata"]["preservedSegment"]
        self.assertEqual(seg["headUuid"], "a2")
        self.assertEqual(_chain_ids(survivors), [u for u in EXPECTED_CHAIN if u != "u2"])

    def test_removing_the_tail_moves_tail_and_logical_parent(self):
        b = _fixture()
        survivors, _, _, _ = remove_objects_and_rewire(b.objects, {"a4"}, protect=set())
        bnd = next(o for o in survivors if o["uuid"] == "B")
        self.assertEqual(bnd["compactMetadata"]["preservedSegment"]["tailUuid"], "u3")
        self.assertEqual(bnd["logicalParentUuid"], "u3")
        self.assertEqual(bnd["compactMetadata"]["preservedMessages"]["uuids"],
                         ["u2", "a2", "a3", "t3", "u3"])
        # The summary now follows the new last preserved record.
        self.assertEqual(_chain_ids(survivors), [u for u in EXPECTED_CHAIN if u != "a4"])

    def test_dry_run_copies_do_not_leak_into_live_data(self):
        b = _fixture()
        before = json.dumps(b.objects, sort_keys=True)
        remove_objects_and_rewire([dict(o) for o in b.objects], {"a2", "t3"}, protect=set())
        self.assertEqual(json.dumps(b.objects, sort_keys=True), before)


class TestCommandsSeePreservedRecords(_Base):
    def test_analyze_reports_preserved_records_and_their_image(self):
        path = self.file(_fixture())
        stats = _quiet(analyze_session, str(path), show_cut_points=False)
        self.assertEqual(stats["active_chain_length"], len(EXPECTED_CHAIN))
        self.assertEqual(stats["relink_status"], "ok")
        self.assertEqual(stats["preserved_records"], len(PRESERVED))
        self.assertEqual(stats["image_count"], 1)
        self.assertEqual(stats["image_nested_count"], 1)
        self.assertEqual(stats["health_issues"], [])

    def test_list_images_sees_preserved_image(self):
        path = self.file(_fixture())
        rows = _quiet(list_images, str(path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["where"], "tool_result:Read")
        self.assertEqual(rows[0]["pos"], EXPECTED_CHAIN.index("u3"))

    def test_strip_tools_reaches_preserved_records(self):
        path = self.file(_fixture())
        stats = _quiet(strip_tools, str(path), no_backup=True)
        self.assertEqual(stats["images_cleared"], 1)
        self.assertEqual(stats["tool_use_cleared"], 1)
        self.assertEqual(compact_relink(load_session(path))["status"], "ok")

    def test_gauge_counts_preserved_records(self):
        est = compute_active_chain_tokens(_fixture().objects)
        self.assertGreater(est, len(BIG_KEEP_TEXT) // 4)


class TestHealth(_Base):
    def test_broken_relink_is_a_hard_issue(self):
        b = _fixture()
        b.objects = [o for o in b.objects if o["uuid"] != "a3"]
        path = self.file(b)
        self.assertFalse(_quiet(health_check, str(path)))
        self.assertTrue(_quiet(health_check, str(path), allow_broken_relink=True))

    def test_preserved_records_may_carry_a_pre_branch_slug(self):
        # A `/branch`ed session keeps the parent's slug on records it
        # preserved; CC splices them in, so this must not block `apply`.
        b = _fixture()
        for o in b.objects:
            if o["uuid"] in PRESERVED:
                o["slug"] = "parent-session-slug"
        self.assertTrue(_quiet(health_check, str(self.file(b))))

    def test_preserved_records_on_disk_parents_do_not_matter(self):
        b = _fixture()
        next(o for o in b.objects if o["uuid"] == "u2")["parentUuid"] = "not-in-this-file"
        self.assertTrue(_quiet(health_check, str(self.file(b))))


class TestCompact(_Base):
    def _compact(self, path, before):
        out = Path(tempfile.mkstemp(suffix=".jsonl", prefix="ss-relink-out-")[1])
        self.addCleanup(out.unlink, missing_ok=True)
        _quiet(compact_before, str(path), before_pos=before, output_path=str(out),
               no_backup=True)
        return load_session(out)

    def test_compact_chains_copies_in_chain_order(self):
        # Remapping ON-DISK parents (what compact used to do) breaks on a
        # relinked chain: S's on-disk parent is B, which is not copied, so S
        # and everything after it would dangle.
        objects = self._compact(self.file(_fixture()), before=3)
        uuids = {o["uuid"] for o in objects}
        for o in objects:
            self.assertTrue(o["parentUuid"] is None or o["parentUuid"] in uuids)
        chain = walk_active_chain(objects)
        self.assertEqual(len(chain), 2 + len(EXPECTED_CHAIN) - 3)
        self.assertEqual(chain[-1]["message"]["content"][0]["text"], "new answer")

    def test_compact_does_not_copy_older_boundaries(self):
        objects = self._compact(self.file(_nested_fixture()), before=1)
        boundaries = [o for o in objects if o.get("subtype") == "compact_boundary"]
        self.assertEqual(len(boundaries), 1)
        self.assertIs(objects[0], boundaries[0])
        texts = [o["message"]["content"] if isinstance(o["message"]["content"], str)
                 else o["message"]["content"][0]["text"]
                 for o in walk_active_chain(objects)[2:]]
        self.assertEqual(texts, ["summary zero", "p1", "p2", "summary one", "q1", "q2"])


class TestApply(_Base):
    def test_apply_refuses_a_copy_that_breaks_the_relink(self):
        from lib.pending import ApplyRefused, apply_pending, pending_path
        path = self.file(_fixture())
        pend = pending_path(path)
        self.addCleanup(pend.unlink, missing_ok=True)
        objects = [o for o in load_session(path) if o["uuid"] != "a3"]  # unpruned delete
        save_session(pend, objects, create_backup=False)
        with self.assertRaises(ApplyRefused):
            _quiet(apply_pending, str(path), no_backup=True, settle_ms=0)
        self.assertEqual(_chain_ids(path), EXPECTED_CHAIN)  # original untouched


if __name__ == "__main__":
    unittest.main()
