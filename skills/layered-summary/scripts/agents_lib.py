#!/usr/bin/env python3
"""
Shared helpers for layered-summary scripts.

Keep this module stdlib-only and stable: multiple scripts import it.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable


# Directories that are typically not part of the target content tree.
ALWAYS_IGNORE_DIR_NAMES: set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".cursor",
    ".claude",
    ".codex",
    "__pycache__",
}

# Directories that are typically huge and not useful to summarize.
# We still list them in the one-hop ledger, but we do not descend into them by default.
# To include them, pass an explicit `--include` that targets them.
#
# Keep this list minimal and conservative to preserve the scripts' generality.
DEFAULT_SKIP_DESCEND_DIR_NAMES: set[str] = {
    "node_modules",
    "bower_components",
    "jspm_packages",
}


@dataclass(frozen=True)
class DirNode:
    # "." for root, otherwise POSIX-style relative path without trailing slash.
    rel: str
    abs: Path
    depth: int

    # Immediate one-hop view (used for ledger verification + scaffolding).
    subdirs_one_hop: tuple[str, ...]  # names with trailing "/" (e.g. "src/")
    files_one_hop: tuple[str, ...]  # filenames (AGENTS.md excluded)

    # Traversal graph (used for meaningful closure + ordering).
    children: tuple[str, ...]  # rel paths (e.g. "src", "src/bin")


@dataclass(frozen=True)
class DirClass:
    meaningful: bool
    kind: str  # leaf | non-leaf | aggregator-only

    # True if directory is within the include/ignore scope.
    # Note: meaningful implies in_scope, but non-meaningful dirs can still be in-scope.
    in_scope: bool


# ---- Repomix-like include/ignore globs (comma-separated) ----

GlobList = tuple[re.Pattern[str], ...]


def _normalize_glob_pattern(p: str) -> str:
    p = p.strip()
    if p.startswith("./"):
        p = p[2:]
    while p.startswith("/"):
        p = p[1:]
    return p


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """
    Convert a glob pattern to a compiled regex.

    Supports a Repomix-style globstar:
    - `*` matches within a path segment (no '/')
    - `**` matches across path separators
    - `**/` matches zero or more directories (so `docs/**/*.md` matches `docs/README.md`)
    """
    pattern = _normalize_glob_pattern(pattern)

    # Special-case empty pattern: match nothing.
    if not pattern:
        return re.compile(r"a^")

    i = 0
    out = ["^"]
    while i < len(pattern):
        ch = pattern[i]

        # Globstar directory prefix: **/
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
            continue

        # Globstar: **
        if pattern.startswith("**", i):
            out.append(r".*")
            i += 2
            continue

        if ch == "*":
            out.append(r"[^/]*")
            i += 1
            continue

        if ch == "?":
            out.append(r"[^/]")
            i += 1
            continue

        if ch == "[":
            # Basic character class support.
            j = i + 1
            if j < len(pattern) and pattern[j] in "!^":
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            if j >= len(pattern):
                out.append(re.escape(ch))
                i += 1
                continue
            stuff = pattern[i + 1 : j]
            if stuff and stuff[0] in "!^":
                stuff = "^" + stuff[1:]
            out.append("[" + stuff + "]")
            i = j + 1
            continue

        out.append(re.escape(ch))
        i += 1

    out.append("$")
    return re.compile("".join(out))


def compile_globs_csv(value: str | None) -> GlobList:
    """
    Compile a comma-separated list of glob patterns into regexes.

    Mirrors Repomix CLI UX: `--include "docs/**/*.md,*.txt"`.
    """
    if not value:
        return ()
    parts = [_normalize_glob_pattern(p) for p in value.split(",")]
    parts = [p for p in parts if p]
    return tuple(_glob_to_regex(p) for p in parts)


def _matches_any(path_posix: str, globs: GlobList) -> bool:
    return any(rx.match(path_posix) for rx in globs)


def _dir_match_path(rel_dir: str) -> str:
    if rel_dir == ".":
        return "./"
    return f"{rel_dir}/"


def _file_match_path(rel_dir: str, filename: str) -> str:
    return filename if rel_dir == "." else f"{rel_dir}/{filename}"


def matches_any(path_posix: str, globs: GlobList) -> bool:
    """Public wrapper around internal glob matching."""
    return _matches_any(path_posix, globs)


def _include_mentions_dir_name(include: GlobList, dir_name: str) -> bool:
    """
    Best-effort check used to decide whether to descend into a default-skipped directory.

    We intentionally keep this coarse: if the include set mentions the directory name,
    allow descent. The precise filtering still happens later via include/ignore matching.
    """
    needle = dir_name
    return any(needle in rx.pattern for rx in include)


def expected_one_hop_subdirs(
    nodes: dict[str, DirNode],
    classes: dict[str, DirClass],
    parent_rel: str,
    *,
    include: GlobList = (),
    ignore: GlobList = (),
) -> tuple[str, ...]:
    """
    Expected `child/` entries for `parent_rel`'s `### Subdirectories` section under include/ignore.

    NOTE:
    - The one-hop ledger is derived during `scan_tree()` and intentionally does NOT apply
      additional filtering here.
    - `scan_tree()` omits ALWAYS_IGNORE directories, and also omits DEFAULT_SKIP_DESCEND
      directories unless explicitly included (to avoid huge autogenerated trees).
    """
    _ = classes, include, ignore
    return tuple(nodes[parent_rel].subdirs_one_hop)


def selected_one_hop_files(
    nodes: dict[str, DirNode],
    parent_rel: str,
    *,
    include: GlobList = (),
    ignore: GlobList = (),
) -> tuple[str, ...]:
    """
    Selected one-hop files for `parent_rel` under include/ignore.
    """
    out: list[str] = []
    parent = nodes[parent_rel]
    for f in parent.files_one_hop:
        fp = _file_match_path(parent_rel, f)
        if ignore and _matches_any(fp, ignore):
            continue
        if include and not _matches_any(fp, include):
            continue
        out.append(f)
    out.sort()
    return tuple(out)


def _rel_dir(root: Path, d: Path) -> str:
    rel = d.relative_to(root)
    if not rel.parts:
        return "."
    return rel.as_posix()


def scan_tree(root: Path, *, include: GlobList = (), ignore: GlobList = ()) -> dict[str, DirNode]:
    """
    Scan a directory subtree into DirNode records.

    - Descends into all directories except ALWAYS_IGNORE_DIR_NAMES.
    - Still records ignored directories as one-hop children for ledger purposes.
    """
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"root must be an existing directory: {root}")

    nodes: dict[str, DirNode] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        d_abs = Path(dirpath)
        rel = _rel_dir(root, d_abs)
        depth = 0 if rel == "." else len(Path(rel).parts)

        # Preserve a one-hop view for ledger purposes.
        #
        # - ALWAYS_IGNORE dirs are never listed.
        # - DEFAULT_SKIP_DESCEND dirs are also omitted from the ledger by default (these can be huge).
        #   They appear only when explicitly included.
        one_hop_dirs: list[str] = []
        for d in dirnames:
            if d in ALWAYS_IGNORE_DIR_NAMES:
                continue

            if d in DEFAULT_SKIP_DESCEND_DIR_NAMES:
                child_rel = _rel_dir(root, d_abs / d)
                child_dir_path = _dir_match_path(child_rel)
                if ignore and _matches_any(child_dir_path, ignore):
                    continue
                if include and (_matches_any(child_dir_path, include) or _include_mentions_dir_name(include, d)):
                    one_hop_dirs.append(d)
                continue

            one_hop_dirs.append(d)

        one_hop_dirs.sort()
        subdirs_one_hop = tuple(f"{d}/" for d in one_hop_dirs)

        # Control descent:
        # - always ignore ALWAYS_IGNORE
        # - skip user-ignored directories early (they may still appear in ledgers)
        # - skip default-pruned directories (e.g., node_modules) unless explicitly included
        next_dirnames: list[str] = []
        for d in dirnames:
            if d in ALWAYS_IGNORE_DIR_NAMES:
                continue

            child_rel = _rel_dir(root, d_abs / d)
            child_dir_path = _dir_match_path(child_rel)

            if ignore and _matches_any(child_dir_path, ignore):
                continue

            if d in DEFAULT_SKIP_DESCEND_DIR_NAMES:
                if include and (_matches_any(child_dir_path, include) or _include_mentions_dir_name(include, d)):
                    next_dirnames.append(d)
                else:
                    continue
            else:
                next_dirnames.append(d)

        next_dirnames.sort()
        dirnames[:] = next_dirnames

        # One-hop files (exclude AGENTS.md).
        files = [f for f in filenames if f != "AGENTS.md"]
        files.sort()
        files_one_hop = tuple(files)

        # Children are only those we descend into.
        children = tuple(_rel_dir(root, d_abs / child) for child in dirnames)

        nodes[rel] = DirNode(
            rel=rel,
            abs=d_abs,
            depth=depth,
            subdirs_one_hop=subdirs_one_hop,
            files_one_hop=files_one_hop,
            children=children,
        )

    return nodes


def classify_dirs(
    nodes: dict[str, DirNode],
    *,
    include: GlobList = (),
    ignore: GlobList = (),
) -> dict[str, DirClass]:
    """
    Compute which directories are meaningful (and their kind) using a bottom-up pass.

    Repomix-like semantics:
    - include = positive filter (if provided, only matching paths are candidates)
    - ignore = negative filter (removes candidates)
    - ignore wins if a path matches both
    """
    by_depth = sorted(nodes.values(), key=lambda n: n.depth, reverse=True)

    in_scope: dict[str, bool] = {n.rel: False for n in by_depth}
    meaningful: dict[str, bool] = {n.rel: False for n in by_depth}
    kinds: dict[str, str] = {n.rel: "leaf" for n in by_depth}

    for n in by_depth:
        dir_path = _dir_match_path(n.rel)
        dir_ignored = _matches_any(dir_path, ignore) if ignore else False

        # Determine which local files are selected under include/ignore.
        selected_files: list[str] = []
        for f in n.files_one_hop:
            fp = _file_match_path(n.rel, f)
            if ignore and _matches_any(fp, ignore):
                continue
            if include and not _matches_any(fp, include):
                continue
            selected_files.append(f)

        # Do not attempt to infer "code" vs "non-code" by file type. Any selected file counts.
        local_signal = bool(selected_files)

        child_in_scope = any(in_scope.get(ch, False) for ch in n.children)

        # Scope is a closure over include matches: a directory is in-scope if it matches include
        # directly, has any selected local file, or has an in-scope child. Ignore wins.
        if include:
            self_include = _matches_any(dir_path, include) or bool(selected_files) or child_in_scope
        else:
            self_include = True

        in_scope[n.rel] = bool(self_include and not dir_ignored)

        has_meaningful_child = any(meaningful.get(ch, False) for ch in n.children if in_scope.get(ch, False))
        is_meaningful = bool(in_scope[n.rel] and (local_signal or has_meaningful_child))
        meaningful[n.rel] = is_meaningful

        if not is_meaningful:
            kinds[n.rel] = "leaf"
        elif has_meaningful_child:
            kinds[n.rel] = "non-leaf" if local_signal else "aggregator-only"
        else:
            kinds[n.rel] = "leaf"

    return {rel: DirClass(meaningful=meaningful[rel], kind=kinds[rel], in_scope=in_scope[rel]) for rel in meaningful}


def agents_status(dir_abs: Path) -> str:
    """
    Derive status for DIR/AGENTS.md.

    - missing: no AGENTS.md
    - incomplete: empty OR contains '[TBD]' OR contains unchecked '- [ ]' anywhere
    - done: otherwise
    """
    path = dir_abs / "AGENTS.md"
    if not path.exists():
        return "missing"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return "incomplete"

    if not text.strip():
        return "incomplete"
    if "[TBD]" in text:
        return "incomplete"
    if re.search(r"^- \[ \]", text, flags=re.M):
        return "incomplete"
    return "done"


def group_by_depth(nodes: dict[str, DirNode], rels: Iterable[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for rel in rels:
        depth = nodes[rel].depth
        out.setdefault(depth, []).append(rel)
    for depth in out:
        out[depth].sort()
    return out


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def detect_repo_root(start: Path) -> Path | None:
    """
    Return the git repo root for `start`, or None if not in a git worktree.
    """
    res = _run_git(start, ["rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        return None
    p = Path(res.stdout.strip())
    return p if p.exists() else None


def get_changed_files_for_update_mode(
    root: Path,
    base_ref: str | None,
    *,
    include: GlobList = (),
    ignore: GlobList = (),
) -> list[Path]:
    """
    Return absolute paths to changed files under `root` using git.
    """
    root = root.resolve()
    repo_root = detect_repo_root(root)
    if repo_root is None:
        raise RuntimeError(f"not a git worktree: {root}")

    changed_rel: set[str] = set()

    if base_ref:
        res = _run_git(repo_root, ["diff", "--name-only", f"{base_ref}...HEAD"])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "git diff failed")
        changed_rel.update([ln.strip() for ln in res.stdout.splitlines() if ln.strip()])
    else:
        cmds = [
            ["diff", "--name-only", "--cached"],
            ["diff", "--name-only"],
            ["ls-files", "--others", "--exclude-standard"],
        ]
        for c in cmds:
            res = _run_git(repo_root, c)
            if res.returncode != 0:
                # If any command fails, treat update mode as unavailable.
                raise RuntimeError(res.stderr.strip() or f"git {' '.join(c)} failed")
            changed_rel.update([ln.strip() for ln in res.stdout.splitlines() if ln.strip()])

    changed_abs: list[Path] = []
    for rel in sorted(changed_rel):
        p = (repo_root / rel).resolve()

        # Ignore AGENTS.md changes; update mode is for input-tree changes (not summaries).
        if p.name == "AGENTS.md":
            continue

        # Filter to within target root.
        try:
            rel_to_root = p.relative_to(root)
        except Exception:
            continue

        # Ignore changes in always-ignored directories.
        if any(part in ALWAYS_IGNORE_DIR_NAMES for part in rel_to_root.parts):
            continue

        # Apply include/ignore globs relative to target root (Repomix semantics: ignore wins).
        fp = rel_to_root.as_posix()
        if ignore and _matches_any(fp, ignore):
            continue

        # Default-pruned directories: ignore unless explicitly included.
        #
        # We require an explicit include that targets the directory name (e.g. "node_modules/**"),
        # so broad includes like "**/*.js" don't accidentally pull in dependency trees.
        skip_parts = {part for part in rel_to_root.parts if part in DEFAULT_SKIP_DESCEND_DIR_NAMES}
        if skip_parts:
            if not include:
                continue
            if not all(_include_mentions_dir_name(include, name) for name in skip_parts):
                continue

        if include and not _matches_any(fp, include):
            continue

        changed_abs.append(p)

    return changed_abs

