#!/usr/bin/env python3
"""
Create-only scaffolder for layered-summary.

- Creates missing DIR/AGENTS.md for meaningful directories only (meaningful = in-scope and has content and/or in-scope descendants).
- Does NOT modify existing AGENTS.md files.
- Default is dry-run. Use --write to actually create files.

Example:
  python3 scripts/agents_scaffold.py --root example-repo --write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agents_lib import (
    classify_dirs,
    compile_globs_csv,
    expected_one_hop_subdirs,
    scan_tree,
    selected_one_hop_files,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create missing AGENTS.md stubs (create-only).")
    p.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Target root directory (the subtree to scaffold).",
    )
    p.add_argument(
        "--include",
        default=None,
        help='Include only paths matching these glob patterns (comma-separated, e.g., "docs/**/*.md,chapters/**").',
    )
    p.add_argument(
        "-i",
        "--ignore",
        default=None,
        help='Additional patterns to exclude (comma-separated, e.g., "**/build/**,**/*.png").',
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="Actually create files (default: dry-run).",
    )
    return p.parse_args()


def _title_for_dir(root: Path, rel: str) -> str:
    if rel == ".":
        return root.name or "root"
    return Path(rel).name


def _render_stub(*, root: Path, rel: str, subdirs: tuple[str, ...], files: tuple[str, ...]) -> str:
    title = _title_for_dir(root, rel)

    sub_lines = []
    for d in subdirs:
        sub_lines.append(f"- [ ] `{d}` - [TBD]")

    file_lines = []
    for f in files:
        file_lines.append(f"- `{f}` - [TBD]")

    # Keep this stub obviously incomplete so nothing can mistakenly "trust" it as done.
    return "\n".join(
        [
            f"# {title}",
            "",
            "## Purpose",
            "[TBD]",
            "",
            "## Contents (one hop)",
            "### Subdirectories",
            *(sub_lines if sub_lines else ["- (none)"]),
            "",
            "### Files",
            *(file_lines if file_lines else ["- (none)"]),
            "",
            "## Key APIs (no snippets)",
            "- [TBD]",
            "",
            "## Relationships",
            "- [TBD]",
            "",
            "## Notes",
            "- [TBD stub created by agents_scaffold.py]",
            "",
        ]
    )


def main() -> int:
    args = _parse_args()
    root = args.root.resolve()

    include = compile_globs_csv(args.include)
    ignore = compile_globs_csv(args.ignore)

    try:
        nodes = scan_tree(root, include=include, ignore=ignore)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    classes = classify_dirs(nodes, include=include, ignore=ignore)
    meaningful_rels = sorted([rel for rel, c in classes.items() if c.meaningful], key=lambda r: (nodes[r].depth, r))

    would_create: list[Path] = []
    created: list[Path] = []

    for rel in meaningful_rels:
        d = nodes[rel].abs
        path = d / "AGENTS.md"
        if path.exists():
            continue
        would_create.append(path)
        if args.write:
            subdirs = expected_one_hop_subdirs(nodes, classes, rel, include=include, ignore=ignore)
            files = selected_one_hop_files(nodes, rel, include=include, ignore=ignore)
            stub = _render_stub(
                root=root,
                rel=rel,
                subdirs=subdirs,
                files=files,
            )
            try:
                path.write_text(stub, encoding="utf-8")
                created.append(path)
            except Exception as e:
                print(f"ERROR: failed to write {path}: {e}", file=sys.stderr)
                return 3

    print(f"root={root}")
    print(f"mode={'write' if args.write else 'dry-run'}")
    print(f"meaningful_dirs={len(meaningful_rels)}")
    print(f"files_created={len(created) if args.write else 0}")
    print(f"files_would_create={len(would_create)}")

    if would_create:
        print("paths:")
        for p in would_create:
            print(f" - {p.relative_to(root)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

