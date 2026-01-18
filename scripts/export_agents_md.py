#!/usr/bin/env python3
"""
Export-only utility: copy AGENTS.md files from a subtree into a standalone folder.

- Copies ONLY files named 'AGENTS.md'
- Preserves relative paths from the source root
- Does NOT modify the source repo

Example:
  python3 export_agents_md.py --src reth --out agents-export/reth
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export AGENTS.md files from a subtree.")
    p.add_argument(
        "--src",
        type=Path,
        default=Path("reth"),
        help="Source directory to scan (default: reth).",
    )
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory to write exported AGENTS.md into.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow writing into an existing output directory.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    src = args.src.resolve()
    out = args.out.resolve()

    if not src.exists() or not src.is_dir():
        print(f"ERROR: --src must be an existing directory: {src}", file=sys.stderr)
        return 2

    if out.exists() and not args.overwrite:
        print(
            f"ERROR: --out already exists: {out}\n"
            f"       Choose a new path or pass --overwrite.",
            file=sys.stderr,
        )
        return 2

    out.mkdir(parents=True, exist_ok=True)

    agents = sorted(src.rglob("AGENTS.md"))
    if not agents:
        print(f"ERROR: no AGENTS.md files found under {src}", file=sys.stderr)
        return 1

    sizes: list[int] = []
    total_bytes = 0

    for path in agents:
        rel = path.relative_to(src)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

        size = dest.stat().st_size
        sizes.append(size)
        total_bytes += size

    sizes.sort()
    p50 = sizes[len(sizes) // 2]

    print(f"src={src}")
    print(f"out={out}")
    print(f"files_copied={len(agents)}")
    print(f"total_bytes={total_bytes}")
    print(f"min_size={sizes[0]}")
    print(f"p50_size={p50}")
    print(f"max_size={sizes[-1]}")

    empties = [p for p in agents if p.stat().st_size == 0]
    if empties:
        print("WARNING: empty AGENTS.md files in source:", file=sys.stderr)
        for p in empties:
            print(f" - {p}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

