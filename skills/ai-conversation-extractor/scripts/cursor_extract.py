#!/usr/bin/env python3
"""Extract Cursor IDE chat/composer conversations from its SQLite storage to Markdown.

Cursor stores conversations in a global SQLite DB (cursorDiskKV table):
  - composerData:<composerId>          -> conversation metadata (name, createdAt,
                                          fullConversationHeadersOnly: ordered bubble refs)
  - bubbleId:<composerId>:<bubbleId>   -> individual message (type 1=user, 2=assistant,
                                          text, thinking, toolFormerData)

Default DB location (macOS):
  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb

Usage:
    # List all conversations (newest first)
    python3 cursor_extract.py --list

    # List conversations whose content matches a regex (scans all bubbles; slow on big DBs)
    python3 cursor_extract.py --grep "AccessManager|rate.?limit"

    # Extract one or more conversations to Markdown
    python3 cursor_extract.py <composerId> [<composerId> ...] --out-dir ./converted/

    # Extract everything
    python3 cursor_extract.py --all --out-dir ./converted/

Output format matches extract.py (same Markdown conventions, YAML frontmatter,
file mtime set to the conversation's last-updated time).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse markdown conventions from the JSONL extractor
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import truncate, write_markdown  # noqa: E402

DEFAULT_DB_CANDIDATES = [
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb",  # macOS
    "~/.config/Cursor/User/globalStorage/state.vscdb",  # Linux
    "~/AppData/Roaming/Cursor/User/globalStorage/state.vscdb",  # Windows
]


def find_db(explicit: str | None) -> str:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            sys.exit(f"Error: DB not found: {p}")
        return str(p)
    for cand in DEFAULT_DB_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_file():
            return str(p)
    sys.exit("Error: Cursor state.vscdb not found in default locations; pass --db PATH")


def open_db(path: str) -> sqlite3.Connection:
    # Read-only so we never touch a live Cursor instance's DB
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def ts_iso(ms: int | float | None) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OSError, OverflowError, ValueError):
        return None


def load_composer_meta(con: sqlite3.Connection) -> dict[str, dict]:
    """Return {composerId: metadata dict} for all composers."""
    out: dict[str, dict] = {}
    cur = con.cursor()
    for key, val in cur.execute(
        "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
    ):
        cid = key.split(":", 1)[1]
        try:
            out[cid] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            continue
    return out


# ---------------------------------------------------------------------------
# Bubble -> markdown parts
# ---------------------------------------------------------------------------


def _bubble_parts(bd: dict) -> list[str]:
    parts: list[str] = []

    thinking = bd.get("thinking")
    if isinstance(thinking, dict):
        t = (thinking.get("text") or "").strip()
        if t:
            t = truncate(t, 3000, "thinking")
            parts.append(f"<details><summary>Thinking</summary>\n\n{t}\n\n</details>")

    text = (bd.get("text") or "").strip()
    if text:
        parts.append(text)

    if bd.get("images"):
        parts.append(f"*[{len(bd['images'])} image(s)]*")

    tfd = bd.get("toolFormerData")
    if isinstance(tfd, dict):
        name = tfd.get("name") or f"tool#{tfd.get('tool', '?')}"
        raw_args = tfd.get("rawArgs") or tfd.get("params") or ""
        args_str = ""
        if raw_args:
            try:
                args_str = json.dumps(json.loads(raw_args), indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                args_str = str(raw_args)
            args_str = truncate(args_str, 4000, "tool input")
        parts.append(f"**Tool: `{name}`**\n```json\n{args_str}\n```")

        result = tfd.get("result")
        if isinstance(result, str) and result.strip():
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
                    result = parsed["output"]
                else:
                    result = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
            result = truncate(result.strip(), 4000, "tool result")
            if result:
                parts.append(f"**Result**:\n```\n{result}\n```")

    return parts


def _legacy_item_parts(item: dict) -> list[str]:
    """Old Cursor format: composerData.conversation[] items (same shape as bubbles)."""
    return _bubble_parts(item)


# ---------------------------------------------------------------------------
# Conversation reconstruction
# ---------------------------------------------------------------------------

ROLE_BY_TYPE = {1: "user", 2: "assistant"}


def load_conversation(
    con: sqlite3.Connection, cid: str, meta: dict
) -> list[tuple[str, str, str | None]]:
    cur = con.cursor()
    conversation: list[tuple[str, str, str | None]] = []

    headers = meta.get("fullConversationHeadersOnly") or []
    inline = meta.get("conversation") or []

    if headers:
        for h in headers:
            bid = h.get("bubbleId")
            if not bid:
                continue
            row = cur.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"bubbleId:{cid}:{bid}",),
            ).fetchone()
            if not row:
                continue
            try:
                bd = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue
            role = ROLE_BY_TYPE.get(bd.get("type"), "assistant")
            parts = _bubble_parts(bd)
            if parts:
                conversation.append((role, "\n\n".join(parts), None))
    elif inline:
        for item in inline:
            if not isinstance(item, dict):
                continue
            role = ROLE_BY_TYPE.get(item.get("type"), "assistant")
            parts = _legacy_item_parts(item)
            if parts:
                conversation.append((role, "\n\n".join(parts), None))
    else:
        # Fallback: all bubbles for this composer in insertion order
        for (val,) in cur.execute(
            "SELECT value FROM cursorDiskKV WHERE key LIKE ? ORDER BY rowid",
            (f"bubbleId:{cid}:%",),
        ):
            try:
                bd = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                continue
            role = ROLE_BY_TYPE.get(bd.get("type"), "assistant")
            parts = _bubble_parts(bd)
            if parts:
                conversation.append((role, "\n\n".join(parts), None))

    return conversation


def slugify(name: str, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:max_len] or "untitled"


def extract_composer(
    con: sqlite3.Connection,
    cid: str,
    meta: dict,
    out_dir: Path,
) -> str | None:
    conversation = load_conversation(con, cid, meta)
    if not conversation:
        return None

    name = meta.get("name") or "untitled"
    last_ms = meta.get("lastUpdatedAt") or meta.get("createdAt")
    mtime = (last_ms / 1000) if isinstance(last_ms, (int, float)) and last_ms > 0 else None

    out_path = out_dir / f"{slugify(name)}-{cid[:8]}.md"
    write_markdown(
        conversation,
        str(out_path),
        f"{name} (Cursor)",
        source_filename=f"cursor:{cid}",
        source_mtime=mtime if mtime is not None else 0,
    )
    if mtime is not None:
        os.utime(out_path, (mtime, mtime))
    print(f"  {name!r} -> {out_path} ({len(conversation)} entries)")
    return str(out_path)


# ---------------------------------------------------------------------------
# Listing / searching
# ---------------------------------------------------------------------------


def cmd_list(con: sqlite3.Connection, limit: int | None):
    metas = load_composer_meta(con)
    rows = []
    for cid, m in metas.items():
        n_msgs = len(m.get("fullConversationHeadersOnly") or m.get("conversation") or [])
        if n_msgs == 0:
            continue
        rows.append(
            (
                m.get("lastUpdatedAt") or m.get("createdAt") or 0,
                cid,
                m.get("name") or "(unnamed)",
                n_msgs,
            )
        )
    rows.sort(reverse=True)
    if limit:
        rows = rows[:limit]
    for ts, cid, name, n in rows:
        print(f"{cid}  {ts_iso(ts) or '?':20}  {n:5} msgs  {name}")
    print(f"\n{len(rows)} conversations", file=sys.stderr)


def cmd_grep(con: sqlite3.Connection, pattern: str, limit: int | None):
    rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    metas = load_composer_meta(con)
    cur = con.cursor()
    hits: dict[str, int] = {}
    n = 0
    for key, val in cur.execute(
        "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
    ):
        n += 1
        if n % 50000 == 0:
            print(f"  ...scanned {n} messages", file=sys.stderr)
        try:
            text = val.decode("utf-8", "ignore") if isinstance(val, bytes) else val
        except Exception:
            continue
        if rx.search(text):
            cid = key.split(":")[1] if isinstance(key, str) else key.decode().split(":")[1]
            hits[cid] = hits.get(cid, 0) + 1

    rows = []
    for cid, count in hits.items():
        m = metas.get(cid, {})
        rows.append(
            (
                count,
                cid,
                m.get("name") or "(unnamed)",
                ts_iso(m.get("lastUpdatedAt") or m.get("createdAt")),
            )
        )
    rows.sort(reverse=True)
    if limit:
        rows = rows[:limit]
    for count, cid, name, ts in rows:
        print(f"{cid}  {ts or '?':20}  {count:5} matching msgs  {name}")
    print(f"\n{len(rows)} conversations matched", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Extract Cursor IDE conversations from SQLite storage to Markdown"
    )
    parser.add_argument("composer_ids", nargs="*", help="Composer IDs to extract")
    parser.add_argument("--db", help="Path to Cursor's global state.vscdb")
    parser.add_argument("--list", action="store_true", help="List conversations (newest first)")
    parser.add_argument("--grep", metavar="REGEX", help="List conversations whose messages match REGEX (case-insensitive; full DB scan)")
    parser.add_argument("--all", action="store_true", help="Extract all conversations")
    parser.add_argument("--out-dir", default=".", help="Output directory for extracted .md files")
    parser.add_argument("--limit", type=int, help="Limit rows for --list/--grep output")
    args = parser.parse_args()

    con = open_db(find_db(args.db))
    con.text_factory = lambda b: b.decode("utf-8", "ignore")

    if args.list:
        cmd_list(con, args.limit)
        return
    if args.grep:
        cmd_grep(con, args.grep, args.limit)
        return

    if not args.composer_ids and not args.all:
        parser.error("Provide composer IDs, or use --list / --grep / --all")

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    metas = load_composer_meta(con)

    targets = list(metas.keys()) if args.all else args.composer_ids
    extracted = 0
    for cid in targets:
        meta = metas.get(cid)
        if meta is None:
            print(f"  WARNING: composer {cid} not found", file=sys.stderr)
            continue
        if extract_composer(con, cid, meta, out_dir):
            extracted += 1
    print(f"Done: {extracted} conversation(s) extracted.")


if __name__ == "__main__":
    main()
