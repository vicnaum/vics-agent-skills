#!/usr/bin/env python3
"""
Normalize non-ASCII characters in AGENTS.md files to ASCII equivalents.

Default behavior is a dry-run. Use --write to modify files in-place.

Example:
  python3 scripts/normalize_agents_ascii.py --root reth --write
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from agents_lib import compile_globs_csv, matches_any


REPLACEMENTS: dict[str, str] = {
    "\u2014": "-",    # EM DASH
    "\u2013": "-",    # EN DASH
    "\u2019": "'",    # RIGHT SINGLE QUOTATION MARK
    "\u201C": '"',    # LEFT DOUBLE QUOTATION MARK
    "\u201D": '"',    # RIGHT DOUBLE QUOTATION MARK
    "\u2192": "->",   # RIGHTWARDS ARROW
    "\u2194": "<->",  # LEFT RIGHT ARROW
    "\u00E7": "c",    # LATIN SMALL LETTER C WITH CEDILLA
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normalize non-ASCII chars in AGENTS.md to ASCII.")
    p.add_argument(
        "--root",
        type=Path,
        default=Path("reth"),
        help="Root directory to scan (default: reth).",
    )
    p.add_argument(
        "--include",
        default=None,
        help='Include only paths matching these glob patterns (comma-separated, e.g., "src/**/AGENTS.md,AGENTS.md").',
    )
    p.add_argument(
        "-i",
        "--ignore",
        default=None,
        help='Additional patterns to exclude (comma-separated, e.g., "**/target/**,**/node_modules/**").',
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="Write changes in-place (default: dry-run).",
    )
    p.add_argument(
        "--fail-on-remaining",
        action="store_true",
        help="Exit non-zero if any non-ASCII characters remain after replacement.",
    )
    return p.parse_args()


def _summarize_non_ascii(text: str) -> Counter[str]:
    c: Counter[str] = Counter()
    for ch in text:
        if ord(ch) > 127:
            c[ch] += 1
    return c


def main() -> int:
    args = _parse_args()
    root = args.root.resolve()

    include = compile_globs_csv(args.include)
    ignore = compile_globs_csv(args.ignore)

    if not root.exists() or not root.is_dir():
        print(f"ERROR: --root must be an existing directory: {root}", file=sys.stderr)
        return 2

    all_agents = sorted(root.rglob("AGENTS.md"))
    agents: list[Path] = []
    for path in all_agents:
        rel_posix = path.relative_to(root).as_posix()
        if ignore and matches_any(rel_posix, ignore):
            continue
        if include and not matches_any(rel_posix, include):
            continue
        agents.append(path)
    if not agents:
        print(f"ERROR: no AGENTS.md files found under {root}", file=sys.stderr)
        return 1

    total_files = 0
    would_change: list[Path] = []
    changed: list[Path] = []

    repl_counts: Counter[str] = Counter()
    remaining_non_ascii: Counter[str] = Counter()

    for path in agents:
        total_files += 1
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARNING: failed to read {path}: {e}", file=sys.stderr)
            continue

        # Count planned replacements
        for src_ch in REPLACEMENTS:
            repl_counts[src_ch] += text.count(src_ch)

        new = text
        for src_ch, dst in REPLACEMENTS.items():
            new = new.replace(src_ch, dst)

        if new != text:
            would_change.append(path)
            if args.write:
                path.write_text(new, encoding="utf-8")
                changed.append(path)

        # Track remaining non-ascii after replacement (or after simulated replacement in dry-run)
        remaining_non_ascii.update(_summarize_non_ascii(new))

    print(f"root={root}")
    print(f"files_scanned={total_files}")
    print(f"mode={'write' if args.write else 'dry-run'}")
    print(f"files_changed={len(changed) if args.write else 0}")
    print(f"files_would_change={len(would_change)}")

    # Report replacement counts (only those that occur)
    print("replacement_counts:")
    for ch in sorted(REPLACEMENTS.keys(), key=lambda c: ord(c)):
        count = repl_counts[ch]
        if count == 0:
            continue
        code = f"U+{ord(ch):04X}"
        name = unicodedata.name(ch, "UNKNOWN")
        print(f"  - {code} {name}: {count}")

    # Report remaining non-ASCII after replacement
    remaining_items = [(ch, n) for ch, n in remaining_non_ascii.items() if ord(ch) > 127 and n > 0]
    remaining_items.sort(key=lambda kv: ord(kv[0]))
    print(f"remaining_non_ascii_unique={len(remaining_items)}")
    if remaining_items:
        for ch, count in remaining_items:
            code = f"U+{ord(ch):04X}"
            name = unicodedata.name(ch, "UNKNOWN")
            print(f"  - {code} {name}: {count}")

    if args.fail_on_remaining and remaining_items:
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

