#!/usr/bin/env python3
"""
Verify layered-summary invariants for an AGENTS.md subtree.

This script is read-only.

Checks:
- Every meaningful directory has an AGENTS.md
- Each meaningful directory's AGENTS.md lists all one-hop subdirectories (ledger matches filesystem)
- ASCII-only content (no non-ASCII characters)
- If --strict: no remaining [TBD] and no unchecked '- [ ]' entries

Meaningful directories are determined without language/framework heuristics:
they are derived from --include/--ignore scope plus filesystem structure.

Example:
  python3 scripts/agents_verify.py --root example-repo --strict
"""

from __future__ import annotations

import argparse
from collections import Counter
import re
import sys
from pathlib import Path

from agents_lib import (
    agents_status,
    classify_dirs,
    compile_globs_csv,
    expected_one_hop_subdirs,
    scan_tree,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify layered-summary AGENTS.md invariants.")
    p.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Target root directory (the subtree to verify).",
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
        "--strict",
        action="store_true",
        help="Fail if any AGENTS.md is incomplete ([TBD] or unchecked - [ ]).",
    )
    return p.parse_args()


def _non_ascii_counts(text: str) -> Counter[str]:
    c: Counter[str] = Counter()
    for ch in text:
        if ord(ch) > 127:
            c[ch] += 1
    return c


def _extract_subdir_entries(text: str) -> list[str] | None:
    """
    Return subdirectory names (with trailing '/') from the '### Subdirectories' section.
    If the section does not exist, return None.
    """
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^###\s+Subdirectories\s*$", line.strip()):
            header_idx = i
            break
    if header_idx is None:
        return None

    out: list[str] = []
    for line in lines[header_idx + 1 :]:
        if re.match(r"^#{1,3}\s+", line):
            break
        m = re.match(r"^- \[[ x]\]\s+[`']([^`']+/)[`']\s+-", line.strip())
        if not m:
            continue
        out.append(m.group(1))
    return out


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

    missing_agents: list[Path] = []
    ledger_mismatches: list[str] = []
    non_ascii_files: list[str] = []
    incomplete_files: list[str] = []

    for rel in meaningful_rels:
        d = nodes[rel].abs
        agents_path = d / "AGENTS.md"
        if not agents_path.exists():
            missing_agents.append(agents_path)
            continue

        try:
            text = agents_path.read_text(encoding="utf-8")
        except Exception as e:
            incomplete_files.append(f"{agents_path}: failed to read: {e}")
            continue

        # ASCII-only enforcement.
        non_ascii = _non_ascii_counts(text)
        if non_ascii:
            summary = ", ".join(f"U+{ord(ch):04X}x{n}" for ch, n in sorted(non_ascii.items(), key=lambda kv: ord(kv[0])))
            non_ascii_files.append(f"{agents_path}: {summary}")

        # Ledger must match filesystem.
        expected = set(expected_one_hop_subdirs(nodes, classes, rel, include=include, ignore=ignore))
        listed = _extract_subdir_entries(text)

        if expected and listed is None:
            ledger_mismatches.append(f"{agents_path}: missing '### Subdirectories' section (expected {len(expected)} entries)")
        else:
            listed_set = set(listed or [])
            missing = sorted(expected - listed_set)
            extra = sorted(listed_set - expected)
            if missing or extra:
                parts: list[str] = []
                if missing:
                    parts.append(f"missing={missing}")
                if extra:
                    parts.append(f"extra={extra}")
                ledger_mismatches.append(f"{agents_path}: " + " ".join(parts))

        if args.strict:
            st = agents_status(d)
            if st != "done":
                incomplete_files.append(f"{agents_path}: status={st}")

    ok = True
    if missing_agents:
        ok = False
    if ledger_mismatches:
        ok = False
    if non_ascii_files:
        ok = False
    if args.strict and incomplete_files:
        ok = False

    print(f"root={root}")
    print(f"meaningful_dirs={len(meaningful_rels)}")
    print(f"missing_agents={len(missing_agents)}")
    print(f"ledger_mismatches={len(ledger_mismatches)}")
    print(f"non_ascii_files={len(non_ascii_files)}")
    print(f"incomplete_files={len(incomplete_files) if args.strict else 0}")

    if missing_agents:
        print("\nmissing_agents_paths:")
        for p in missing_agents:
            print(f" - {p.relative_to(root)}")

    if ledger_mismatches:
        print("\nledger_mismatches:")
        for msg in ledger_mismatches:
            print(f" - {msg}")

    if non_ascii_files:
        print("\nnon_ascii_files:")
        for msg in non_ascii_files:
            print(f" - {msg}")

    if args.strict and incomplete_files:
        print("\nincomplete_files:")
        for msg in incomplete_files:
            print(f" - {msg}")

    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())

