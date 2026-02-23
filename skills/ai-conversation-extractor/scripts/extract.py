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
from datetime import datetime, timezone
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


def process_claude_code(lines: list[str]) -> tuple[str | None, list[tuple[str, str, str | None]]]:
    """Parse Claude Code JSONL (envelope with type/message/toolUseResult).

    Returns (summary_title, conversation) where conversation is (role, text, timestamp).
    """
    conversation: list[tuple[str, str, str | None]] = []
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
        timestamp = obj.get("timestamp")

        parts: list[str] = []

        if role == "assistant":
            parts = _extract_assistant_parts(content)
        elif role == "user":
            parts = _extract_user_parts(content)

        if parts:
            conversation.append((role, "\n\n".join(parts), timestamp))

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


def process_simple(lines: list[str]) -> tuple[None, list[tuple[str, str, str | None]]]:
    """Parse simple JSONL: {"role": "user/assistant", "message": {"content": [...]}}."""
    conversation: list[tuple[str, str, str | None]] = []

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
            conversation.append((role, "\n\n".join(parts), None))

    return None, conversation


# ---------------------------------------------------------------------------
# Codex CLI JSONL format (OpenAI Codex CLI sessions)
# ---------------------------------------------------------------------------


def process_codex_cli(lines: list[str]) -> tuple[str | None, list[tuple[str, str, str | None]]]:
    """Parse Codex CLI session JSONL (event stream with timestamp/type/payload).

    We keep only human-readable conversation items:
    - records with type="response_item" and payload.type="message"
      where payload.role is "user" or "assistant"

    Content blocks usually look like:
      {"type":"input_text","text":"..."}  (user)
      {"type":"output_text","text":"..."} (assistant)
    """
    conversation: list[tuple[str, str, str | None]] = []
    summary_title: str | None = None

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        rec_type = obj.get("type")
        payload = obj.get("payload")
        ts = obj.get("timestamp")

        if rec_type == "session_meta" and isinstance(payload, dict):
            sid = payload.get("id")
            if isinstance(sid, str):
                summary_title = sid
            continue

        if rec_type != "response_item" or not isinstance(payload, dict):
            continue

        if payload.get("type") != "message":
            continue

        role = payload.get("role")
        if role not in ("user", "assistant"):
            continue

        blocks = payload.get("content")
        parts: list[str] = []

        if isinstance(blocks, str):
            t = strip_system_reminders(blocks).strip()
            if t:
                parts.append(t)
        elif isinstance(blocks, list):
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt in ("input_text", "output_text"):
                    t = strip_system_reminders(b.get("text", "")).strip()
                    if t:
                        parts.append(t)
                elif bt in ("image", "input_image"):
                    parts.append("*[image]*")
                elif bt in ("document", "input_document"):
                    parts.append("*[document]*")

        if parts:
            conversation.append((role, "\n\n".join(parts), ts))

    return summary_title, conversation


# ---------------------------------------------------------------------------
# Codex history.jsonl format (OpenAI Codex CLI prompt history)
# ---------------------------------------------------------------------------


def process_codex_history(lines: list[str]) -> tuple[str | None, list[tuple[str, str, str | None]]]:
    """Parse Codex CLI history.jsonl lines:
    {"session_id": "...", "ts": 1769204719, "text": "..."}

    This is typically user-only prompts (no assistant/tool output), but is still
    useful for keyword search and timeline reconstruction.
    """
    conversation: list[tuple[str, str, str | None]] = []
    sid: str | None = None

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if not isinstance(obj, dict):
            continue

        if "session_id" not in obj or "text" not in obj:
            continue

        if sid is None and isinstance(obj.get("session_id"), str):
            sid = obj.get("session_id")

        txt = obj.get("text")
        if not isinstance(txt, str):
            continue
        txt = strip_system_reminders(txt).strip()
        if not txt:
            continue

        ts = obj.get("ts")
        ts_iso: str | None = None
        if isinstance(ts, (int, float)):
            try:
                ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                ts_iso = None

        conversation.append(("user", txt, ts_iso))

    return sid, conversation


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
        # Codex CLI history.jsonl: {"session_id": "...", "ts": ..., "text": "..."}
        if (
            isinstance(obj, dict)
            and "session_id" in obj
            and "text" in obj
            and "ts" in obj
            and "payload" not in obj
        ):
            return "codex_history"
        # Codex CLI sessions have top-level timestamp/type/payload
        if (
            isinstance(obj, dict)
            and "timestamp" in obj
            and "payload" in obj
            and obj.get("type") in ("session_meta", "response_item", "event_msg")
        ):
            return "codex_cli"
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


def write_markdown(
    conversation: list[tuple[str, str, str | None]],
    out_path: str,
    source: str,
    *,
    source_filename: str | None = None,
    source_mtime: float | None = None,
    messages_only: bool = False,
):
    """Write conversation as Markdown, merging consecutive same-role blocks."""
    with open(out_path, "w", encoding="utf-8") as f:
        # YAML frontmatter for source metadata (sortable, parseable)
        if source_filename is not None and source_mtime is not None:
            modified_iso = datetime.fromtimestamp(
                source_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            f.write("---\n")
            f.write(f"source: {source_filename}\n")
            f.write(f"modified: {modified_iso}\n")
            f.write("---\n\n")

        f.write(f"# Conversation: {source}\n\n")
        if messages_only:
            f.write(
                "*Messages-only view: user messages and assistant final responses per turn. "
                "Binary content stripped; system reminders removed.*\n\n"
            )
        else:
            f.write(
                "*Binary content (base64 images, PDFs) stripped. "
                "System reminders removed. "
                "Tool calls and results preserved.*\n\n"
            )
        f.write("---\n\n")

        prev_role = None
        for i, (role, text, timestamp) in enumerate(conversation):
            if role != prev_role:
                if i > 0:
                    f.write("---\n\n")
                if role == "user":
                    f.write("## User\n\n")
                elif role == "assistant":
                    f.write("## Assistant\n\n")
                else:
                    f.write(f"## {role}\n\n")
                if timestamp:
                    f.write(f"*{timestamp}*\n\n")
            f.write(text + "\n\n")
            prev_role = role

        f.write("---\n*End of conversation*\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _filter_user_assistant_final_only(
    conversation: list[tuple[str, str, str | None]],
    *,
    drop_env_context: bool,
    drop_agent_boilerplate: bool,
) -> list[tuple[str, str, str | None]]:
    """Keep only: user message(s) and the final assistant message for that turn."""
    out: list[tuple[str, str, str | None]] = []

    pending_user: list[tuple[str, str, str | None]] = []
    last_assistant: tuple[str, str, str | None] | None = None

    def flush():
        nonlocal pending_user, last_assistant, out
        if not pending_user and last_assistant is None:
            return
        if pending_user:
            # Coalesce consecutive user messages into one block (common in Codex: env_context + prompt).
            merged_txt = "\n\n".join(t for (_r, t, _ts) in pending_user).strip()
            merged_ts = pending_user[-1][2]
            if merged_txt:
                out.append(("user", merged_txt, merged_ts))
        if last_assistant is not None:
            out.append(last_assistant)
        pending_user = []
        last_assistant = None

    for role, text, ts in conversation:
        if role == "user":
            t = text.strip()
            # New user turn after we've already seen an assistant response:
            # flush the previous (user..., assistant_final) pair now.
            if last_assistant is not None:
                flush()
            if drop_env_context and t.startswith("<environment_context>"):
                continue
            if drop_agent_boilerplate and (
                t.startswith("# AGENTS.md instructions")
                or t.startswith("AGENTS.md instructions")
                or "<INSTRUCTIONS>" in t and "Available skills" in t
            ):
                continue
            pending_user.append((role, t, ts))
            continue
        if role == "assistant":
            # Overwrite until we hit the next user turn; last one wins.
            last_assistant = (role, text.strip(), ts)
            continue
        # Unknown role: ignore in this filtered mode.

        # If needed, could flush here, but currently we just skip.

    flush()
    return out


def convert_file(
    jsonl_path: str,
    out_path: str | None = None,
    *,
    ua_final_only: bool = False,
    drop_env_context: bool = True,
    drop_agent_boilerplate: bool = True,
) -> str:
    """Convert a single JSONL file to Markdown. Returns output path."""
    p = Path(jsonl_path)
    if out_path is None:
        out_path = str(p.with_suffix(".md"))

    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    fmt = detect_format(lines)
    if fmt == "codex_cli":
        title, conversation = process_codex_cli(lines)
    elif fmt == "codex_history":
        title, conversation = process_codex_history(lines)
    elif fmt == "claude_code":
        title, conversation = process_claude_code(lines)
    elif fmt == "simple":
        title, conversation = process_simple(lines)
    else:
        print(f"  WARNING: Unknown format in {jsonl_path}, trying Claude Code parser")
        title, conversation = process_claude_code(lines)

    source = title or p.stem
    source_mtime = os.path.getmtime(jsonl_path)
    if ua_final_only:
        conversation = _filter_user_assistant_final_only(
            conversation,
            drop_env_context=drop_env_context,
            drop_agent_boilerplate=drop_agent_boilerplate,
        )
    write_markdown(
        conversation,
        out_path,
        source,
        source_filename=p.name,
        source_mtime=source_mtime,
        messages_only=ua_final_only,
    )
    os.utime(out_path, (source_mtime, source_mtime))

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
    parser.add_argument(
        "--ua-final-only",
        action="store_true",
        help="Write a messages-only view: user messages and assistant final response per turn (drops tools/thinking).",
    )
    parser.add_argument(
        "--out-dir",
        help="Output directory (directory mode only). Writes <stem>.md into this folder.",
    )
    parser.add_argument(
        "--keep-env-context",
        action="store_true",
        help="Keep Codex <environment_context> user messages when using --ua-final-only.",
    )
    parser.add_argument(
        "--keep-agent-boilerplate",
        action="store_true",
        help="Keep Codex AGENTS/skills boilerplate user messages when using --ua-final-only.",
    )
    args = parser.parse_args()

    target = Path(args.path)

    if target.is_file():
        if not target.suffix == ".jsonl":
            print(f"Error: {target} is not a .jsonl file", file=sys.stderr)
            sys.exit(1)
        convert_file(
            str(target),
            args.output,
            ua_final_only=args.ua_final_only,
            drop_env_context=not args.keep_env_context,
            drop_agent_boilerplate=not args.keep_agent_boilerplate,
        )

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
        out_dir: Path | None = None
        if args.out_dir:
            out_dir = Path(args.out_dir).expanduser().resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            out_path = None
            if out_dir is not None:
                out_path = str(out_dir / (f.stem + ".md"))
            convert_file(
                str(f),
                out_path,
                ua_final_only=args.ua_final_only,
                drop_env_context=not args.keep_env_context,
                drop_agent_boilerplate=not args.keep_agent_boilerplate,
            )
        print("Done.")

    else:
        print(f"Error: {target} not found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
