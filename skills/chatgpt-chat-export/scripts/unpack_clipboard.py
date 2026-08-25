#!/usr/bin/env python3
"""
Decode a clipboard bundle produced by the in-page attachment fetcher.

The browser side puts a single JSON object {"<filename>": "<base64>", ...}
on the clipboard (see SKILL.md). This reads it via `pbpaste` (macOS) or
stdin and writes each file into the target directory.

Usage:
    python3 unpack_clipboard.py <out-dir>            # macOS, reads pbpaste
    xclip -o | python3 unpack_clipboard.py <out-dir> --stdin   # Linux

Filenames are sanitized to a single path component. Stdlib only.
"""
import base64
import json
import re
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    if '--stdin' in sys.argv:
        data = sys.stdin.read()
    else:
        data = subprocess.run(['pbpaste'], capture_output=True).stdout.decode()
    files = json.loads(data)
    total = 0
    for name, b64 in files.items():
        safe = re.sub(r'[^A-Za-z0-9 ._()-]', '_', Path(name).name)[:150]
        raw = base64.b64decode(b64)
        (out_dir / safe).write_bytes(raw)
        total += len(raw)
    print(f'wrote {len(files)} files, {total} bytes -> {out_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
