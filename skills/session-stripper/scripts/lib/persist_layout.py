"""Persistence directory layout for session-stripper.

For a session at `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`:

    <encoded-cwd>/
    ├── <sessionId>.jsonl                        ← the session
    ├── <sessionId>/                             ← CC's per-session accessory dir
    │   ├── tool-results/<tool_use_id>.txt       ← persist_dir(s, "tool")
    │   ├── subagents/                           ← CC native (untouched)
    │   └── persisted/
    │       ├── thinking/<msg_uuid>.txt          ← persist_dir(s, "thinking")
    │       ├── text/<msg_uuid>_<block_idx>.txt  ← persist_dir(s, "text")
    │       ├── image/<sha256>.txt               ← persist_dir(s, "image")
    │       └── message/<msg_uuid>.json          ← persist_dir(s, "message")

`tool` lives in CC's existing `tool-results/` dir so we coexist with CC's
native large-tool-result spill (`utils/toolResultStorage.ts` writes there too,
keyed by tool_use_id — UUIDs, no collisions). Other kinds live under our own
`persisted/` namespace, per-kind, so different categories don't share a flat dir.

The dir is session-scoped (under `<sessionId>/`), never project-scoped, so two
sessions in the same `cwd` never collide on filenames.
"""

from __future__ import annotations

import json
from pathlib import Path

# Kinds session-stripper persists. Add to this when introducing new kinds.
KNOWN_KINDS = ("tool", "thinking", "text", "image", "message")


def session_id_of(session_path) -> str:
    """Extract the canonical sessionId from the JSONL.

    Prefers the `sessionId` field from the first envelope (definitive). Falls
    back to the file stem if no envelope carries one, so synthetic test
    fixtures with non-UUID stems still work.
    """
    p = Path(session_path).expanduser().absolute()
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                sid = obj.get("sessionId")
                if sid:
                    return str(sid)
    except (OSError, json.JSONDecodeError):
        pass
    return p.stem


def persist_dir(session_path, kind: str) -> Path:
    """Resolve and create the persist directory for a (session, kind) pair.

    Returns the absolute Path. Creates parents as needed.
    """
    if kind not in KNOWN_KINDS:
        raise ValueError(f"unknown persist kind: {kind!r} (known: {KNOWN_KINDS})")
    p = Path(session_path).expanduser().absolute()
    session_id = session_id_of(p)
    if kind == "tool":
        out = p.parent / session_id / "tool-results"
    else:
        out = p.parent / session_id / "persisted" / kind
    out.mkdir(parents=True, exist_ok=True)
    return out


def to_marker_path(sidecar_path, session_path) -> str:
    """Return `sidecar_path` expressed relative to the session's project dir
    (the dir containing the JSONL), as a forward-slash POSIX string suitable
    for marker emission. Markers store relative paths so the session survives
    if the `~/.claude/projects/<encoded-cwd>/` tree is moved.
    """
    project_dir = Path(session_path).expanduser().absolute().parent
    sidecar = Path(sidecar_path).absolute()
    return sidecar.relative_to(project_dir).as_posix()
