"""Copy-first mutation layer: snapshot → mutate the copy → atomic `apply`.

Why this exists (incident 2026-08-05): mutating a session JSONL in place while
its CLI is running is a lost-update race. CC flushes appends from a write queue
every 100ms, takes no lock, and `--resume` walks parentUuid back from the
newest leaf, silently truncating at the first missing uuid — so a whole-file
rewrite that eats concurrently-appended lines collapses the reachable chain
(observed: 763 → 14 messages). Every stripper op used to be such a rewrite;
`strip-all` alone was four of them back to back.

The fix is architectural, not a guard: mutating commands NEVER write a real
session file. They materialize `<file>.jsonl.pending` (a newline-boundary
snapshot) and mutate only that. The original is replaced exactly once, by
`apply` — an atomic rename that runs when nothing is writing the file:
immediately for dead/stuck sessions, or from respawn's watcher (after the CLI
process is confirmed dead) for a session stripping itself.

Everything appended to the original AFTER the snapshot is discarded by apply,
by design: in the self-strip flow that tail is the stripping commands
themselves — noise that used to be baked into every post-strip session.

The `.pending` suffix keeps the copy out of CC's `*.jsonl` session-listing
glob, so it never shows up in `claude -r`'s picker.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

PENDING_SUFFIX = ".pending"
_HASH_CHUNK = 4 * 1024 * 1024


class ApplyRefused(RuntimeError):
    """Raised when apply detects it is unsafe or meaningless to swap."""


def is_pending(path) -> bool:
    return str(path).endswith(PENDING_SUFFIX)


def pending_path(session_path) -> Path:
    return Path(str(Path(session_path).expanduser()) + PENDING_SUFFIX)


def original_of(pending) -> Path:
    p = str(Path(pending).expanduser())
    if not p.endswith(PENDING_SUFFIX):
        raise ValueError(f"not a pending path: {pending}")
    return Path(p[: -len(PENDING_SUFFIX)])


def meta_path_for(pending) -> Path:
    return Path(str(pending) + ".meta.json")


def _sha256_prefix(path: Path, n_bytes: int) -> str:
    """SHA256 of the first n_bytes of path, chunked."""
    h = hashlib.sha256()
    remaining = n_bytes
    with open(path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(_HASH_CHUNK, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def create_snapshot(session_path, refresh: bool = False) -> Path:
    """Copy `session_path` to its `.pending` sibling at a newline boundary.

    A live CLI may be mid-append, so any torn final line (no trailing newline)
    is dropped from the snapshot — it stays in the original and is handled
    like the rest of the post-snapshot tail (discarded at apply).

    Records a meta sidecar with the snapshot's base size and prefix hash so
    apply can verify the original still descends from this snapshot.
    """
    src = Path(session_path).expanduser().absolute()
    if is_pending(src):
        raise ValueError(f"already a pending copy: {src}")
    if not src.is_file():
        raise FileNotFoundError(f"session not found: {src}")
    dst = pending_path(src)
    if dst.exists() and not refresh:
        raise FileExistsError(
            f"pending copy already exists: {dst}\n"
            f"(mutating commands reuse it; pass refresh to re-snapshot)"
        )

    data = src.read_bytes()
    cut = data.rfind(b"\n") + 1
    if cut == 0:
        raise ValueError(f"no complete line in source (torn or empty): {src}")
    dropped = len(data) - cut
    data = data[:cut]

    dst.write_bytes(data)
    meta = {
        "source": str(src),
        "base_size": cut,
        "base_sha256": hashlib.sha256(data).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "torn_tail_bytes_dropped": dropped,
    }
    meta_path_for(dst).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return dst


def ensure_pending(session_path):
    """Return (pending_path, created). Reuses an existing pending copy so
    multi-pass strips (analyze → compact-range → strip-tools → ...) all hit
    the same file."""
    dst = pending_path(Path(session_path).expanduser().absolute())
    if dst.exists():
        return dst, False
    return create_snapshot(session_path), True


def apply_pending(path, dry_run: bool = False, no_backup: bool = False,
                  settle_ms: int = 400) -> dict:
    """Atomically swap the pending copy over its original.

    `path` may be either the original session or the pending copy.

    Checks, in order:
      1. liveness — stat the original twice, `settle_ms` apart; if it grew, a
         CLI is appending RIGHT NOW and swapping would orphan its next append.
      2. lineage — the original must still have the snapshot as byte prefix.
         CC only ever appends, so a mismatch means the original was rewritten
         since the snapshot (another apply, a manual edit): the pending copy
         no longer descends from it. Re-snapshot and redo.
      3. health — the pending copy must pass the chain health check; a broken
         file is never swapped in.

    On success: enumerated .bak of the original (unless no_backup), atomic
    os.replace, meta sidecar removed. The original's post-snapshot tail is
    discarded — counted and reported, not merged.
    """
    p = Path(path).expanduser().absolute()
    if is_pending(p):
        pend, orig = p, original_of(p)
    else:
        pend, orig = pending_path(p), p

    if not pend.is_file():
        raise ApplyRefused(f"no pending copy: {pend}")
    if not orig.is_file():
        raise ApplyRefused(f"original missing: {orig}")

    meta = None
    mp = meta_path_for(pend)
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None

    # 1. liveness
    size_1 = orig.stat().st_size
    time.sleep(settle_ms / 1000.0)
    size_2 = orig.stat().st_size
    if size_2 != size_1:
        raise ApplyRefused(
            f"original is being written right now ({size_1:,} → {size_2:,} "
            f"bytes in {settle_ms}ms): {orig}\n"
            f"Exit that CLI first — or, if it is your own session, schedule "
            f"the swap for the restart window: respawn.sh --swap {pend}"
        )

    # 2. lineage
    tail_bytes = None
    tail_lines = None
    if meta:
        base = int(meta.get("base_size", -1))
        if base < 0 or size_2 < base:
            raise ApplyRefused(
                f"original ({size_2:,} bytes) is smaller than the snapshot "
                f"base ({base:,}) — it was rewritten since the snapshot. "
                f"Re-snapshot and redo the strip."
            )
        if _sha256_prefix(orig, base) != meta.get("base_sha256"):
            raise ApplyRefused(
                "original was rewritten since the snapshot (prefix hash "
                "mismatch) — the pending copy no longer descends from it. "
                "Re-snapshot and redo the strip."
            )
        tail_bytes = size_2 - base
        if tail_bytes:
            with open(orig, "rb") as f:
                f.seek(base)
                tail_lines = f.read().count(b"\n")
    else:
        print("WARNING: no meta sidecar — skipping lineage check "
              "(snapshot predates it, or sidecar was deleted).")

    # 3. health of what we're about to swap in
    from .analyze import health_check
    if not health_check(str(pend)):
        raise ApplyRefused(f"pending copy fails verify — not swapping: {pend}")

    stats = {
        "original": str(orig),
        "pending": str(pend),
        "tail_bytes_discarded": tail_bytes,
        "tail_lines_discarded": tail_lines,
    }

    if dry_run:
        print(f"[DRY RUN] would swap {pend.name} → {orig.name}")
        if tail_bytes:
            print(f"[DRY RUN] would discard post-snapshot tail: "
                  f"{tail_lines} line(s) / {tail_bytes:,} bytes")
        return stats

    if not no_backup:
        from .chain import _next_backup_path
        backup = _next_backup_path(orig)
        shutil.copy2(orig, backup)
        stats["backup"] = str(backup)
        print(f"Backup: {backup}")

    os.replace(pend, orig)
    mp.unlink(missing_ok=True)

    print(f"Applied: {pend.name} → {orig}")
    if tail_bytes:
        print(f"Discarded post-snapshot tail: {tail_lines} line(s) / "
              f"{tail_bytes:,} bytes (appends after the snapshot — in the "
              f"self-strip flow this is the stripping turn itself)")
    elif tail_bytes == 0:
        print("No post-snapshot tail — original had not grown.")
    return stats
