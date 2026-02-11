#!/usr/bin/env python3
"""Convert Claude Code / ChatGPT conversation JSONL files to readable Markdown.

Strips binary data (base64 images, PDFs) and metadata noise while preserving
the full human-readable conversation: user messages, assistant text, thinking,
tool calls, and tool results.

Usage:
    python jsonl2md.py <file.jsonl>              # Convert one file
    python jsonl2md.py <dir>                     # Convert all .jsonl in dir
    python jsonl2md.py <dir> --recursive         # Recurse into subdirs
    python jsonl2md.py <file.jsonl> -o out.md    # Custom output path

Output: .md file alongside each .jsonl (or at -o path).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYSTEM_REMINDER_RE = re.compile(
    r"\n*<system-reminder>.*?</system-reminder>\n*", re.DOTALL
)
# Line-number prefixes that Claude Code adds to file reads: "     1→" or "  1234→"
LINE_NUM_RE = re.compile(r"^ {0,5}\d+→", re.MULTILINE)


def strip_system_reminders(text: str) -> str:
    return SYSTEM_REMINDER_RE.sub("", text)


def strip_line_numbers(text: str) -> str:
    """Remove '   123→' line-number prefixes from tool results."""
    return LINE_NUM_RE.sub("", text)


def looks_base64(s: str, threshold: int = 500) -> bool:
    """Heuristic: long string of base64-ish chars."""
    if len(s) < threshold:
        return False
    sample = s[:300].replace("\n", "")
    b64_chars = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    )
    return all(c in b64_chars for c in sample)


def truncate(text: str, max_len: int = 4000, label: str = "text") -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n\n[...truncated {label}, {len(text)} chars total]"


# ---------------------------------------------------------------------------
# Claude Code JSONL format
# ---------------------------------------------------------------------------


def process_claude_code(lines: list[str]) -> tuple[str | None, list[tuple[str, str]]]:
    """Parse Claude Code JSONL (envelope with type/message/toolUseResult).

    Returns (summary_title, conversation) where summary_title may be None.
    """
    conversation: list[tuple[str, str]] = []  # (role, markdown_text)
    summary_title: str | None = None

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        obj_type = obj.get("type", "")

        # Extract session title from summary record
        if obj_type == "summary":
            summary_title = obj.get("summary")
            continue

        # Skip non-conversation records
        if obj_type in ("file-history-snapshot", "progress", "system"):
            continue
        # Skip meta messages (like /init instructions injected by the tool)
        if obj.get("isMeta"):
            continue

        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        content = msg.get("content", "")

        parts: list[str] = []

        if role == "assistant":
            parts = _extract_assistant_parts(content)
        elif role == "user":
            parts = _extract_user_parts(content)

        if parts:
            conversation.append((role, "\n\n".join(parts)))

    return summary_title, conversation


def _extract_assistant_parts(content) -> list[str]:
    parts: list[str] = []
    if isinstance(content, str):
        t = strip_system_reminders(content).strip()
        if t:
            parts.append(t)
        return parts

    if not isinstance(content, list):
        return parts

    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type", "")

        if bt == "text":
            t = strip_system_reminders(block.get("text", "")).strip()
            if t:
                parts.append(t)

        elif bt == "thinking":
            t = block.get("thinking", "").strip()
            if t:
                t = truncate(t, 3000, "thinking")
                parts.append(
                    f"<details><summary>Thinking</summary>\n\n{t}\n\n</details>"
                )

        elif bt == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            inp_str = json.dumps(inp, indent=2, ensure_ascii=False)
            inp_str = truncate(inp_str, 4000, "tool input")
            parts.append(f"**Tool: `{name}`**\n```json\n{inp_str}\n```")

    return parts


def _extract_user_parts(content) -> list[str]:
    parts: list[str] = []
    if isinstance(content, str):
        t = strip_system_reminders(content).strip()
        if t:
            parts.append(t)
        return parts

    if not isinstance(content, list):
        return parts

    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type", "")

        if bt == "text":
            t = strip_system_reminders(block.get("text", "")).strip()
            if t:
                parts.append(t)

        elif bt == "image":
            media = block.get("source", {}).get("media_type", "image")
            parts.append(f"*[image: {media}]*")

        elif bt == "document":
            source = block.get("source", {})
            media = source.get("media_type", "?")
            if source.get("type") == "base64":
                parts.append(f"*[attached document: {media}]*")
            else:
                parts.append("*[document block]*")

        elif bt == "tool_result":
            tid = block.get("tool_use_id", "")[:12]
            sub = block.get("content", "")
            is_err = block.get("is_error", False)

            if isinstance(sub, str):
                text = strip_system_reminders(sub).strip()
                text = strip_line_numbers(text)
                if looks_base64(text):
                    parts.append(f"*[binary tool result, {len(text)} chars]*")
                elif text:
                    text = truncate(text, 6000, "tool result")
                    label = "Error" if is_err else "Result"
                    parts.append(f"**{label}** (`{tid}`):\n```\n{text}\n```")

            elif isinstance(sub, list):
                for sb in sub:
                    if not isinstance(sb, dict):
                        continue
                    if sb.get("type") == "text":
                        text = strip_system_reminders(sb.get("text", "")).strip()
                        text = strip_line_numbers(text)
                        if looks_base64(text):
                            parts.append(
                                f"*[binary tool result, {len(text)} chars]*"
                            )
                        elif text:
                            text = truncate(text, 6000, "tool result")
                            label = "Error" if is_err else "Result"
                            parts.append(
                                f"**{label}** (`{tid}`):\n```\n{text}\n```"
                            )
                    elif sb.get("type") == "image":
                        parts.append("*[tool result image]*")
                    elif sb.get("type") == "document":
                        parts.append("*[tool result document]*")

    return parts


# ---------------------------------------------------------------------------
# Simple JSONL format (ChatGPT / Gemini export)
# ---------------------------------------------------------------------------


def process_simple(lines: list[str]) -> tuple[None, list[tuple[str, str]]]:
    """Parse simple JSONL: {"role": "user/assistant", "message": {"content": [...]}}."""
    conversation: list[tuple[str, str]] = []

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        role = obj.get("role", "")
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue

        content = msg.get("content", "")
        parts: list[str] = []

        if isinstance(content, str):
            t = content.strip()
            if t:
                parts.append(t)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        parts.append(t)
                elif isinstance(block, dict) and block.get("type") == "image":
                    parts.append("*[image]*")

        if parts:
            conversation.append((role, "\n\n".join(parts)))

    return None, conversation


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_format(lines: list[str]) -> str:
    """Detect JSONL format from first few non-empty lines."""
    for raw in lines[:10]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Claude Code has "type" or "sessionId" in the envelope
        if "sessionId" in obj or "parentUuid" in obj or obj.get("type") in (
            "file-history-snapshot", "summary"
        ):
            return "claude_code"
        # Simple format has top-level "role" and "message"
        if "role" in obj and "message" in obj and "sessionId" not in obj:
            return "simple"
    return "unknown"


# ---------------------------------------------------------------------------
# Markdown writer
# ---------------------------------------------------------------------------


def write_markdown(conversation: list[tuple[str, str]], out_path: str, source: str):
    """Write conversation as Markdown, merging consecutive same-role blocks."""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# Conversation: {source}\n\n")
        f.write(
            "*Binary content (base64 images, PDFs) stripped. "
            "System reminders removed. "
            "Tool calls and results preserved.*\n\n"
        )
        f.write("---\n\n")

        prev_role = None
        for i, (role, text) in enumerate(conversation):
            if role != prev_role:
                if i > 0:
                    f.write("---\n\n")
                if role == "user":
                    f.write("## User\n\n")
                elif role == "assistant":
                    f.write("## Assistant\n\n")
                else:
                    f.write(f"## {role}\n\n")
            f.write(text + "\n\n")
            prev_role = role

        f.write("---\n*End of conversation*\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def convert_file(jsonl_path: str, out_path: str | None = None) -> str:
    """Convert a single JSONL file to Markdown. Returns output path."""
    p = Path(jsonl_path)
    if out_path is None:
        out_path = str(p.with_suffix(".md"))

    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    fmt = detect_format(lines)
    if fmt == "claude_code":
        title, conversation = process_claude_code(lines)
    elif fmt == "simple":
        title, conversation = process_simple(lines)
    else:
        print(f"  WARNING: Unknown format in {jsonl_path}, trying Claude Code parser")
        title, conversation = process_claude_code(lines)

    source = title or p.stem
    write_markdown(conversation, out_path, source)

    in_size = os.path.getsize(jsonl_path)
    out_size = os.path.getsize(out_path)
    ratio = out_size / in_size * 100 if in_size > 0 else 0
    print(
        f"  {p.name}: {in_size / 1024:.0f}KB -> {out_size / 1024:.0f}KB "
        f"({ratio:.1f}%), {len(conversation)} entries"
    )
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert Claude Code / ChatGPT conversation JSONL to Markdown"
    )
    parser.add_argument(
        "path",
        help="JSONL file or directory containing JSONL files",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output .md path (only for single-file mode)",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Recurse into subdirectories",
    )
    args = parser.parse_args()

    target = Path(args.path)

    if target.is_file():
        if not target.suffix == ".jsonl":
            print(f"Error: {target} is not a .jsonl file", file=sys.stderr)
            sys.exit(1)
        convert_file(str(target), args.output)

    elif target.is_dir():
        if args.output:
            print("Error: -o/--output not supported in directory mode", file=sys.stderr)
            sys.exit(1)
        pattern = "**/*.jsonl" if args.recursive else "*.jsonl"
        files = sorted(target.glob(pattern))
        if not files:
            print(f"No .jsonl files found in {target}")
            sys.exit(0)
        print(f"Converting {len(files)} files:")
        for f in files:
            convert_file(str(f))
        print("Done.")

    else:
        print(f"Error: {target} not found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
