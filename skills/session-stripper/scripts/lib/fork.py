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
import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

TOOL_NAME = "session-stripper"
DEFAULT_SUFFIX = "Stripped"


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
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


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
