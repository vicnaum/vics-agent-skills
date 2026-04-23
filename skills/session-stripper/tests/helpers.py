"""Shared test helpers for session-stripper.

Pure stdlib (Python 3.8+). No external dependencies, mirroring the rule
session-stripper itself follows.

Provides:
- build_session(...) — produce a synthetic JSONL session with assigned
  block kinds, valid parentUuid chain, monotonic timestamps, consistent slug.
- copy_to_tmp(path) — copy a real session to /tmp before mutation.
- iter_persisted_markers(jsonl_path) — yield each marker text we find in any
  text-block in the session.
- MARKER_RE — the documented contract regex for <persisted-X> markers.
- assert_chain_valid(jsonl_path) — chain integrity invariant check
  (parentUuid unbroken, slug consistent, timestamps monotonic).
- get_block_at(jsonl_path, pos, block_idx=0) — fetch a content block by
  chain position + within-message index.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make scripts/ importable so tests can `from lib.X import Y`.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

CC_VERSION = "2.1.114"


# ───────────────────────────────────────────────────────────────────────────
# Marker contract — the regex any consumer (model, tool, future code) must
# follow to extract path/summary/preview from a <persisted-*> block.
# ───────────────────────────────────────────────────────────────────────────

# Kinds session-stripper emits. Extended over time. Tests should add to this
# tuple as new persist commands ship.
PERSIST_KINDS = ("output", "tool", "thinking", "text", "message", "image")

# Matches the wrapper. The inside is captured loosely; specific field
# extraction happens in marker_fields().
MARKER_RE = re.compile(
    r"<persisted-(?P<kind>" + "|".join(PERSIST_KINDS) + r")\b[^>]*>"
    r"(?P<body>.*?)"
    r"</persisted-(?P=kind)>",
    re.DOTALL,
)

# Inner-body field extraction. Permissive — matches both our new form
# ("Saved to: PATH (N chars)" on its own line) and CC's native form
# ("Output too large (N chars). Full output saved to: PATH" mid-line).
_PATH_RE = re.compile(
    r"(?:Full output\s+saved to|Saved to)\s*:\s*(?P<path>\S+)",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"\((?P<n>\d+)\s*chars?\)")
_SUMMARY_RE = re.compile(r"^\s*Summary\s*:\s*(?P<s>.+?)\s*$", re.MULTILINE)


def marker_fields(marker_text: str) -> dict:
    """Parse a single <persisted-*>...</persisted-*> blob into structured fields.

    Returns: {kind, path (str|None), size_chars (int|None), summary (str|None),
              preview (str|None), raw_body (str)}.
    Path is whatever appears after 'Saved to:' or 'Full output saved to:'.
    """
    m = MARKER_RE.search(marker_text)
    if not m:
        return {}
    body = m.group("body")
    fields: dict = {
        "kind": m.group("kind"),
        "path": None,
        "size_chars": None,
        "summary": None,
        "preview": None,
        "raw_body": body,
    }
    pm = _PATH_RE.search(body)
    if pm:
        fields["path"] = pm.group("path").rstrip(".,;)")
    sm = _SIZE_RE.search(body)
    if sm:
        fields["size_chars"] = int(sm.group("n"))
    smr = _SUMMARY_RE.search(body)
    if smr:
        fields["summary"] = smr.group("s")
    # Preview is whatever is in the body after a "Preview:" line.
    pv = re.search(r"^Preview:\s*\n(.*)$", body, re.DOTALL | re.MULTILINE)
    if pv:
        fields["preview"] = pv.group(1).rstrip()
    return fields


# ───────────────────────────────────────────────────────────────────────────
# Synthetic session builder
# ───────────────────────────────────────────────────────────────────────────


def _envelope(
    *,
    role: str,
    content,
    parent_uuid: str | None,
    session_id: str,
    slug: str,
    cwd: str,
    timestamp: datetime,
    msg_uuid: str | None = None,
):
    msg_uuid = msg_uuid or str(_uuid.uuid4())
    env = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": CC_VERSION,
        "gitBranch": "master",
        "slug": slug,
        "type": "user" if role == "user" else "assistant",
        "message": {"role": role, "content": content},
        "uuid": msg_uuid,
        "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    if role == "assistant":
        env["message"]["model"] = "claude-opus-4-6"
    return env


def build_session(turns, *, session_id: str | None = None,
                  slug: str = "test-session",
                  cwd: str = "/tmp/test-cwd") -> tuple[Path, dict]:
    """Build a synthetic JSONL session into a temp file.

    `turns` is a list where each item is one of:
      ("user", "text content")
      ("user", [block, block, ...])     # provide content blocks directly
      ("assistant", [block, block, ...])
      ("assistant", "text content")     # shorthand for [{"type":"text","text":...}]

    Returns (path, info_dict). info_dict has session_id, uuids (per chain pos),
    so tests can address messages by chain position.
    """
    session_id = session_id or str(_uuid.uuid4())
    tmp = Path(tempfile.mkstemp(suffix=".jsonl", prefix="ss-test-")[1])
    parent: str | None = None
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    uuids: list[str] = []
    with open(tmp, "w") as f:
        for role, content in turns:
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            ts = ts + timedelta(milliseconds=1)
            env = _envelope(
                role=role, content=content, parent_uuid=parent,
                session_id=session_id, slug=slug, cwd=cwd, timestamp=ts,
            )
            f.write(json.dumps(env, ensure_ascii=False) + "\n")
            parent = env["uuid"]
            uuids.append(env["uuid"])
    return tmp, {
        "session_id": session_id,
        "slug": slug,
        "cwd": cwd,
        "uuids": uuids,
        "n_turns": len(turns),
    }


def copy_to_tmp(path: str | Path) -> Path:
    """Copy a session JSONL to /tmp so tests don't mutate the original."""
    src = Path(path).expanduser()
    dst = Path(tempfile.mkstemp(suffix=".jsonl", prefix="ss-copy-")[1])
    shutil.copy2(src, dst)
    return dst


# ───────────────────────────────────────────────────────────────────────────
# Marker walking + chain integrity
# ───────────────────────────────────────────────────────────────────────────


def iter_persisted_markers(jsonl_path: str | Path):
    """Yield (chain_pos, msg_uuid, block_index, marker_text, parsed_fields)
    for every <persisted-*> marker found in any text block of the session."""
    with open(jsonl_path) as f:
        for pos, line in enumerate(f):
            obj = json.loads(line)
            msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
            content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
            if not isinstance(content, list):
                continue
            for i, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and "<persisted-" in text:
                        for m in MARKER_RE.finditer(text):
                            yield (pos, obj.get("uuid"), i, m.group(0), marker_fields(m.group(0)))
                # Tool result content can also carry CC's native <persisted-output> markers.
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    raw = block.get("content")
                    if isinstance(raw, str) and "<persisted-" in raw:
                        for m in MARKER_RE.finditer(raw):
                            yield (pos, obj.get("uuid"), i, m.group(0), marker_fields(m.group(0)))


def assert_chain_valid(jsonl_path: str | Path):
    """Raise AssertionError if chain integrity invariants are violated:
    parentUuid unbroken, slug consistent, timestamps monotonic non-decreasing."""
    objects = []
    with open(jsonl_path) as f:
        for line in f:
            objects.append(json.loads(line))
    if not objects:
        return
    uuids = {o.get("uuid") for o in objects if o.get("uuid")}
    for o in objects:
        p = o.get("parentUuid")
        assert p is None or p in uuids, f"broken parentUuid: {p} not found"
    slugs = {o["slug"] for o in objects if "slug" in o}
    assert len(slugs) == 1, f"inconsistent slugs: {slugs}"
    timestamps = [o["timestamp"] for o in objects if "timestamp" in o]
    assert timestamps == sorted(timestamps), "timestamps not monotonic"


def get_block_at(jsonl_path: str | Path, pos: int, block_idx: int = 0) -> dict:
    """Return the content block at chain position `pos`, sub-index `block_idx`."""
    with open(jsonl_path) as f:
        for i, line in enumerate(f):
            if i == pos:
                obj = json.loads(line)
                msg = obj.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
                if isinstance(content, list) and block_idx < len(content):
                    return content[block_idx]
                raise IndexError(f"no block at pos={pos} block_idx={block_idx}")
    raise IndexError(f"pos {pos} out of range")


# ───────────────────────────────────────────────────────────────────────────
# Path resolution helper
# ───────────────────────────────────────────────────────────────────────────


def resolve_persisted_path(session_path: str | Path, advertised_path: str) -> Path:
    """Markers store paths relative to the project dir (sibling to the JSONL).
    Return an absolute Path WITHOUT following symlinks (so /tmp stays /tmp on
    macOS rather than expanding to /private/tmp)."""
    project_dir = Path(session_path).expanduser().absolute().parent
    return Path(str(project_dir / advertised_path))
