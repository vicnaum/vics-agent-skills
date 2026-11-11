"""Session forking — match Claude Code's `/branch` convention.

Creates a new `<newSessionId>.jsonl` next to the original. Every envelope is
copied with:
  - `sessionId` rewritten to the new id
  - `forkedFrom` = {sessionId: <originalSessionId>, messageUuid: <env's
    original uuid>} — same shape CC writes (verified against
    `~/github/claude-src/commands/branch/branch.ts`)
  - Optional `strippedBy` = {tool, operation, at} — session-stripper-specific
    metadata so future tooling can show strip lineage. CC ignores unknown fields.

A `custom-title` entry is appended after the conversation envelopes:
  - Default suffix " (Stripped)"; collisions auto-increment to " (Stripped 2)",
    " (Stripped 3)", ...
  - Override via `custom_title`.

Returns the absolute Path of the new JSONL.
"""

from __future__ import annotations

import json
import os
import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from .chain import dump_line

TOOL_NAME = "session-stripper"
DEFAULT_SUFFIX = "Stripped"

# Records that belong to the original session, not to its conversation. CC's
# own `--fork-session` does not carry them (checked on 2.1.302/2.1.310):
#   bridge-session  — its Remote Control link; two sessions on one bridge
#                     would receive each other's claude.ai messages
#   continued-in    — "this session went on as <id>": a fork that kept it
#                     would point resumes elsewhere
#   cost-state      — the original's spend and timers
#   api-request*    — the journal of requests the original sent (a fork
#                     starts its own; resume rebuilds from the chain anyway)
SESSION_BOUND_TYPES = frozenset({
    "bridge-session", "continued-in", "cost-state",
    "api-request", "api-request-blob", "api-request-shape",
})


def _read_envelopes(path: Path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _write_envelopes(path: Path, envelopes):
    with open(path, "w", encoding="utf-8") as f:
        for e in envelopes:
            f.write(dump_line(e))
    os.chmod(path, 0o600)


def _existing_titles(project_dir: Path) -> set[str]:
    """Collect custom-title strings already used by sibling sessions in the
    same project dir, so we can avoid collisions."""
    titles: set[str] = set()
    for jsonl in project_dir.glob("*.jsonl"):
        try:
            for e in _read_envelopes(jsonl):
                if e.get("type") == "custom-title" and isinstance(e.get("customTitle"), str):
                    titles.add(e["customTitle"])
        except (OSError, json.JSONDecodeError):
            continue
    return titles


def _derive_base_title(envelopes) -> str:
    """First user message's text becomes the base title (matching CC's pattern
    of slugged-from-content titles)."""
    for e in envelopes:
        if e.get("type") == "user":
            content = e.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            return text[:80]
            elif isinstance(content, str):
                return content.strip()[:80]
    return "Untitled session"


def _resolve_title(base: str, project_dir: Path,
                   custom_title: str | None) -> str:
    """Pick a non-colliding title.

    If `custom_title` is supplied, use it verbatim (still avoid collision by
    appending an integer if needed). Otherwise apply `base + ' (Stripped)'`
    and collide-resolve.
    """
    used = _existing_titles(project_dir)
    if custom_title:
        candidate = custom_title
        n = 2
        while candidate in used:
            candidate = f"{custom_title} {n}"
            n += 1
        return candidate

    candidate = f"{base} ({DEFAULT_SUFFIX})"
    n = 2
    while candidate in used:
        candidate = f"{base} ({DEFAULT_SUFFIX} {n})"
        n += 1
    return candidate


def fork_session(session_path, *,
                 new_session_id: str | None = None,
                 custom_title: str | None = None,
                 operation: str | None = None,
                 at: str | None = None) -> Path:
    """Fork `session_path` to a new sibling JSONL. Returns the new path.

    `new_session_id`: override; defaults to a fresh UUID.
    `custom_title`: override the auto-generated " (Stripped)" suffix.
    `operation`: short string describing the strip op (e.g. the CLI invocation)
                 — recorded in `strippedBy.operation`. If None, no `strippedBy`
                 field is stamped.
    `at`: ISO-8601 timestamp for `strippedBy.at`. Defaults to now (UTC).
    """
    src = Path(session_path).expanduser().absolute()
    if not src.is_file():
        raise FileNotFoundError(f"session not found: {src}")

    project_dir = src.parent
    new_sid = new_session_id or str(_uuid.uuid4())
    dst = project_dir / f"{new_sid}.jsonl"
    if dst.exists():
        raise FileExistsError(f"fork target already exists: {dst}")

    envelopes = _read_envelopes(src)
    if not envelopes:
        raise ValueError(f"source session is empty: {src}")

    original_sid = next(
        (e.get("sessionId") for e in envelopes if e.get("sessionId")),
        None,
    )

    stripped_by = None
    if operation:
        stripped_by = {
            "tool": TOOL_NAME,
            "operation": operation,
            "at": at or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }

    forked: list[dict] = []
    for env in envelopes:
        if env.get("type") in SESSION_BOUND_TYPES:
            continue
        # Pass through meta-only entries that aren't part of the conversation
        # chain unchanged in shape — but rewrite their sessionId so the entire
        # file is internally consistent.
        new_env = dict(env)
        if "sessionId" in new_env:
            new_env["sessionId"] = new_sid
        # Conversation envelopes get forkedFrom + (optionally) strippedBy.
        if env.get("type") in ("user", "assistant"):
            old_uuid = env.get("uuid")
            if old_uuid is not None:
                new_env["forkedFrom"] = {
                    "sessionId": original_sid,
                    "messageUuid": old_uuid,
                }
            if stripped_by is not None:
                new_env["strippedBy"] = stripped_by
        forked.append(new_env)

    # Append the custom-title entry (matches CC's pattern in branch.ts:252).
    base_title = _derive_base_title(envelopes)
    title = _resolve_title(base_title, project_dir, custom_title)
    forked.append({
        "type": "custom-title",
        "customTitle": title,
        "sessionId": new_sid,
    })

    _write_envelopes(dst, forked)
    return dst


# ── Fork-first bookkeeping ───────────────────────────────────────────────
#
# Mutating commands write a fork by default. A sidecar next to each fork,
# `<fork>.jsonl.stripper.json`, records where it came from and its size and
# mtime after the stripper's last write. While those still match, nobody has
# resumed the fork (a resumed CLI appends at once), so the next command on the
# same source — or on the fork itself — edits it in place instead of forking
# again: multi-pass strips (persist-range, then strip-all) land in ONE fork.
# The suffix keeps it out of CC's `*.jsonl` session glob.

STATE_SUFFIX = ".stripper.json"


def state_path(fork_path) -> Path:
    return Path(str(Path(fork_path).expanduser().absolute()) + STATE_SUFFIX)


def read_state(fork_path):
    try:
        return json.loads(state_path(fork_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(fork_path, source=None, source_size=None, calibration=None):
    """Record (or refresh after a write) a fork's state. Fields not given are
    kept from the existing record."""
    fork_path = Path(fork_path).expanduser().absolute()
    state = read_state(fork_path) or {}
    if source is not None:
        state["source"] = str(Path(source).expanduser().absolute())
    if source_size is not None:
        state["source_size"] = int(source_size)
    if calibration is not None:
        state["calibration"] = calibration
    st = fork_path.stat()
    state["size"] = st.st_size
    state["mtime_ns"] = st.st_mtime_ns
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    p = state_path(fork_path)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.chmod(p, 0o600)
    return state


def is_untouched(fork_path, state) -> bool:
    """True while the fork is byte-for-byte what the stripper last wrote."""
    try:
        st = Path(fork_path).stat()
    except OSError:
        return False
    return (isinstance(state, dict) and st.st_size == state.get("size")
            and st.st_mtime_ns == state.get("mtime_ns"))


def find_reusable_fork(source_path):
    """(fork_path, state) of the newest untouched fork of `source_path`, or
    None. A source rewritten since (smaller than when it was forked) has none:
    the fork no longer descends from it."""
    source = Path(source_path).expanduser().absolute()
    try:
        source_size = source.stat().st_size
    except OSError:
        return None
    best = None
    for sp in source.parent.glob("*.jsonl" + STATE_SUFFIX):
        fork_path = Path(str(sp)[: -len(STATE_SUFFIX)])
        state = read_state(fork_path)
        if not state or state.get("source") != str(source):
            continue
        if not is_untouched(fork_path, state):
            continue
        if source_size < state.get("source_size", 0):
            continue
        if best is None or state.get("updated_at", "") > best[1].get("updated_at", ""):
            best = (fork_path, state)
    return best


# CLI-level convenience: a wrapper that validates the source, prints
# user-facing details, and returns the new path.
def cli_fork(session_path, *, custom_title: str | None = None,
             operation: str | None = None) -> Path:
    dst = fork_session(session_path, custom_title=custom_title,
                       operation=operation)
    print(f"Forked: {dst}")
    new_sid = dst.stem
    print(f"New sessionId: {new_sid}")
    print(f"Resume original: claude -r {Path(session_path).expanduser().stem}")
    print(f"Resume fork:     claude -r {new_sid}")
    return dst
