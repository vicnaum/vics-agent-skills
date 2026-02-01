#!/usr/bin/env python3
"""
Build a bottom-up exploration plan ("waves") for layered-summary AGENTS.md generation.

This script is intentionally derived-state only:
- It reads the filesystem (and optionally git) to compute what to process.
- It can read existing AGENTS.md to compute status (missing/incomplete/done).
- It does NOT modify any files.
- It does NOT try to detect "code" vs "non-code" by language/framework; scope is controlled via --include/--ignore.

Examples:
  # Full plan for a subtree
  python3 scripts/agents_plan.py --root example-repo

  # Incremental plan based on git changes (base ref)
  python3 scripts/agents_plan.py --root example-repo --mode update --base-ref origin/main

  # Machine-readable output
  python3 scripts/agents_plan.py --root example-repo --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agents_lib import (
    agents_status,
    classify_dirs,
    compile_globs_csv,
    get_changed_files_for_update_mode,
    group_by_depth,
    scan_tree,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plan layered-summary exploration order (bottom-up waves).")
    p.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Target root directory (the subtree to plan).",
    )
    p.add_argument(
        "--mode",
        choices=("full", "update"),
        default="full",
        help="Planning mode: full subtree vs update-only based on git changes (default: full).",
    )
    p.add_argument(
        "--base-ref",
        default=None,
        help="Git base ref for update mode (example: origin/main). If omitted, uses working tree changes.",
    )
    p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
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
        "--include-done",
        action="store_true",
        help="Include done directories in per-wave listings (default: hide done but still count them).",
    )
    return p.parse_args()


def _rel_display(rel: str) -> str:
    if rel == ".":
        return "./"
    return f"{rel}/"


def _compute_update_dirs(root: Path, changed_files: list[Path], scanned_rels: set[str]) -> set[str]:
    root = root.resolve()
    out: set[str] = set()
    for f in changed_files:
        d = f.parent
        while True:
            try:
                rel = d.relative_to(root)
            except Exception:
                break
            rel_str = "." if not rel.parts else rel.as_posix()
            if rel_str in scanned_rels:
                out.add(rel_str)
            if d == root:
                break
            d = d.parent
    return out


def _emit_markdown(
    *,
    root: Path,
    mode: str,
    selected_rels: list[str],
    statuses: dict[str, str],
    kinds: dict[str, str],
    waves: dict[int, list[str]],
    include_done: bool,
) -> None:
    def _count(status: str) -> int:
        return sum(1 for s in statuses.values() if s == status)

    print(f"root={root.resolve()}")
    print(f"mode={mode}")
    print(f"dirs_planned={len(selected_rels)}")
    print(
        "status_counts:"
        f" missing={_count('missing')}"
        f" incomplete={_count('incomplete')}"
        f" done={_count('done')}"
    )
    print()

    depths = sorted(waves.keys(), reverse=True)
    for depth in depths:
        rels = waves[depth]
        rels_to_print = rels if include_done else [r for r in rels if statuses[r] != "done"]
        print(f"Wave depth={depth} dirs_total={len(rels)} dirs_listed={len(rels_to_print)}")
        for rel in rels_to_print:
            st = statuses[rel]
            kind = kinds.get(rel, "leaf")
            print(f"- [{st}] {_rel_display(rel)} ({kind})")
        print()


def _emit_json(
    *,
    root: Path,
    mode: str,
    selected_rels: list[str],
    statuses: dict[str, str],
    kinds: dict[str, str],
    waves: dict[int, list[str]],
) -> None:
    payload = {
        "root": str(root.resolve()),
        "mode": mode,
        "dirs_planned": len(selected_rels),
        "waves": [
            {
                "depth": depth,
                "dirs": [
                    {
                        "path": rel,
                        "display": _rel_display(rel),
                        "status": statuses[rel],
                        "kind": kinds.get(rel, "leaf"),
                    }
                    for rel in waves[depth]
                ],
            }
            for depth in sorted(waves.keys(), reverse=True)
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=False))


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
    meaningful_rels = sorted([rel for rel, c in classes.items() if c.meaningful], key=lambda r: nodes[r].depth)
    meaningful_set = set(meaningful_rels)

    selected_set: set[str]
    if args.mode == "full":
        selected_set = meaningful_set
    else:
        try:
            changed = get_changed_files_for_update_mode(root, args.base_ref, include=include, ignore=ignore)
        except Exception as e:
            print(f"ERROR: update mode failed: {e}", file=sys.stderr)
            return 3

        selected_set = _compute_update_dirs(root, changed, scanned_rels=set(nodes.keys()))
        selected_set = {r for r in selected_set if r in meaningful_set}

    selected_rels = sorted(selected_set, key=lambda r: (nodes[r].depth, r))

    statuses = {rel: agents_status(nodes[rel].abs) for rel in selected_rels}
    kinds = {rel: classes[rel].kind for rel in selected_rels}

    waves = group_by_depth(nodes, selected_rels)

    if args.format == "json":
        _emit_json(
            root=root,
            mode=args.mode,
            selected_rels=selected_rels,
            statuses=statuses,
            kinds=kinds,
            waves=waves,
        )
    else:
        _emit_markdown(
            root=root,
            mode=args.mode,
            selected_rels=selected_rels,
            statuses=statuses,
            kinds=kinds,
            waves=waves,
            include_done=args.include_done,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

