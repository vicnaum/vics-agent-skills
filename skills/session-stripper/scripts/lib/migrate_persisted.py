"""One-shot migration of pre-persist-everything layouts.

Migrates two pre-PR shapes:

1. `<image sha256="...">...</image>` markers → `<persisted-image sha256="...">`
   markers (matching the new contract).

2. Sidecar files at `<project>/.tool-results/<id>.txt` (project-scoped, dot-
   prefix) → `<sessionId>/tool-results/<id>.txt`. Marker paths inside the
   JSONL are rewritten to point at the new location.

Idempotent — running twice is a no-op (we look for the OLD shapes and only
act if found).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .chain import load_session, save_session
from .persist_layout import session_id_of

# Old image marker: <image sha256="...">...</image>. Strict on tag name to
# avoid eating <persisted-image> by accident.
_OLD_IMAGE_RE = re.compile(
    r'<image\b([^>]*)>(.*?)</image>',
    re.DOTALL,
)

# Old tool-result path inside a marker: ".tool-results/<id>.txt".
_OLD_TOOL_PATH_RE = re.compile(r'\.tool-results/([^\s]+\.(?:txt|json))')


def _migrate_image_markers(jsonl_text: str) -> tuple[str, int]:
    """Return (new_text, count_rewritten)."""
    count = 0

    def _rewrite(m: re.Match) -> str:
        nonlocal count
        attrs = m.group(1)
        body = m.group(2).rstrip()
        # If the body already looks like the new shape (Saved to / Summary /
        # Preview), keep it; else preserve content as-is and wrap it.
        count += 1
        return f'<persisted-image{attrs}>\n{body}\n</persisted-image>'

    new_text = _OLD_IMAGE_RE.sub(_rewrite, jsonl_text)
    return new_text, count


def _migrate_tool_result_paths(jsonl_text: str, session_id: str) -> tuple[str, int]:
    """Rewrite `.tool-results/<id>.<ext>` → `<sessionId>/tool-results/<id>.<ext>`
    inside marker bodies."""
    count = 0

    def _rewrite(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{session_id}/tool-results/{m.group(1)}"

    new_text = _OLD_TOOL_PATH_RE.sub(_rewrite, jsonl_text)
    return new_text, count


def _migrate_sidecar_files(session_path: Path, session_id: str) -> int:
    """Move files from <project>/.tool-results/ → <project>/<sessionId>/tool-results/."""
    project_dir = session_path.parent
    old_dir = project_dir / ".tool-results"
    if not old_dir.is_dir():
        return 0
    new_dir = project_dir / session_id / "tool-results"
    new_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in list(old_dir.iterdir()):
        if not f.is_file():
            continue
        target = new_dir / f.name
        if target.exists():
            # Don't overwrite — assume already migrated (idempotent).
            continue
        shutil.move(str(f), str(target))
        moved += 1
    # If old_dir is now empty, remove it.
    try:
        if not any(old_dir.iterdir()):
            old_dir.rmdir()
    except OSError:
        pass
    return moved


def _walk_text_blocks(objects):
    """Yield each (block, get_text, set_text) tuple for every text/tool_result
    string body in any message, so callers can mutate in place."""
    for obj in objects:
        msg = obj.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                yield (block, lambda b=block: b.get("text", ""), lambda v, b=block: b.__setitem__("text", v))
            elif block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                yield (block, lambda b=block: b.get("content", ""), lambda v, b=block: b.__setitem__("content", v))


def migrate_persisted(session_path, dry_run: bool = False, no_backup: bool = False):
    """Migrate one session JSONL + its sibling pre-PR sidecar files in place.

    Returns a stats dict.
    """
    p = Path(session_path).expanduser().absolute()
    session_id = session_id_of(p)
    objects = load_session(p)

    image_rewrites = 0
    path_rewrites = 0

    for _block, get_text, set_text in _walk_text_blocks(objects):
        original = get_text()
        new_val, n_imgs = _migrate_image_markers(original)
        new_val, n_paths = _migrate_tool_result_paths(new_val, session_id)
        if n_imgs or n_paths:
            set_text(new_val)
        image_rewrites += n_imgs
        path_rewrites += n_paths

    stats = {
        "image_markers_rewritten": image_rewrites,
        "path_rewrites": path_rewrites,
        "sidecar_files_moved": 0,
    }

    if not dry_run:
        if image_rewrites or path_rewrites:
            save_session(p, objects, create_backup=not no_backup)
        stats["sidecar_files_moved"] = _migrate_sidecar_files(p, session_id)

    print(f"Image markers rewritten: {stats['image_markers_rewritten']}")
    print(f"Path rewrites:           {stats['path_rewrites']}")
    print(f"Sidecar files moved:     {stats['sidecar_files_moved']}")
    if dry_run:
        print("\n[dry run] No changes written.")
    return stats
