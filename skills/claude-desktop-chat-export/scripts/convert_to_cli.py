#!/usr/bin/env python3
"""
Convert a claude.ai conversation.json export into a Claude Code CLI JSONL session
that can be resumed with `claude -r <sessionId>`.

Usage:
  python3 convert_to_cli.py <conversation.json> [--cwd PATH] [--files-dir DIR]
                                                [--slug SLUG] [--dry-run]

Key behaviors:
- Splits claude.ai's interleaved assistant messages (which mix tool_use and
  tool_result blocks in a single turn) into proper assistant/user alternation
  required by the Anthropic API / CC.
- Inlines attachment text (extracted_content) into the user message as text.
- Inlines downloaded images from --files-dir as base64 image blocks.
- Points to in-repo copies of blob files that weren't viewed in the chat.

Output location:
  ~/.claude/projects/<encoded-cwd>/<new-sessionId>.jsonl

  Where encoded-cwd replaces '/' with '-' in the absolute CWD path (CC convention).
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import uuid as _uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CC_VERSION_DEFAULT = "2.1.114"
USER_TYPE = "external"


def encode_cwd(cwd: str) -> str:
    """Mirror CC's projects/<encoded-cwd>/ convention: '/' → '-'."""
    abs_path = str(Path(cwd).expanduser().resolve())
    return abs_path.replace("/", "-")


def load_conversation(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def image_block_from_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    mt, _ = mimetypes.guess_type(str(path))
    if mt is None:
        # webp, which stdlib mimetypes misses on old Pythons
        ext = path.suffix.lower()
        mt = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(ext, "application/octet-stream")
    data = base64.b64encode(path.read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mt, "data": data},
    }


def build_user_content_from_chat_ai(msg: dict, files_dir: Path | None) -> list[dict]:
    """Convert a claude.ai 'human' message into CC user content blocks.

    Claude.ai 'human' messages have content blocks plus top-level 'attachments'
    and 'files' arrays. CC stores everything inline in the content array.
    """
    out: list[dict] = []

    # Text / thinking blocks straight through. Humans don't emit tool_use.
    for b in msg.get("content") or []:
        bt = b.get("type")
        if bt == "text":
            txt = b.get("text", "")
            if txt:  # API rejects empty text blocks
                out.append({"type": "text", "text": txt})
        elif bt == "thinking":
            # humans don't think; skip defensively
            continue
        # tool_result handled at the message level (humans in claude.ai never carry them)

    # Attachments: inline their extracted_content as text
    for a in msg.get("attachments") or []:
        name = a.get("file_name") or "(unnamed)"
        content = a.get("extracted_content") or ""
        text = f"<attachment name={name!r}>\n{content}\n</attachment>"
        out.append({"type": "text", "text": text})

    # Files: images as base64 blocks (if we have them), blobs as references
    for f in msg.get("files") or []:
        kind = f.get("file_kind")
        fuuid = f.get("file_uuid")
        fname = f.get("file_name") or "(unnamed)"
        if kind == "image" and files_dir is not None and fuuid:
            # Expect files_dir/<file_uuid>.webp (or any ext)
            hits = list(files_dir.glob(f"{fuuid}.*"))
            if hits:
                blk = image_block_from_file(hits[0])
                if blk:
                    out.append(blk)
                    continue
            out.append({"type": "text", "text": f"[image {fname} uuid={fuuid} — file not found locally]"})
        else:
            out.append({"type": "text", "text": f"[file uploaded: {fname} kind={kind} uuid={fuuid}]"})

    # User messages must have at least one non-empty block
    if not out:
        out.append({"type": "text", "text": "(empty message)"})
    return out


def _flatten_tool_use_to_text(b: dict) -> str:
    name = b.get("name", "?")
    inp = b.get("input") or {}
    # Compact human-readable line
    try:
        inp_str = json.dumps(inp, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        inp_str = str(inp)
    if len(inp_str) > 500:
        inp_str = inp_str[:500] + "…"
    return f"[used tool `{name}` with input: {inp_str}]"


def _flatten_tool_result_to_text(b: dict) -> str:
    raw = b.get("content", "")
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append("[image in tool result]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False)[:200])
        body = "\n".join(parts)
    else:
        body = str(raw)
    err = " (error)" if b.get("is_error") else ""
    return f"[tool result{err}]\n{body}"


def split_assistant_blocks(
    blocks: list[dict],
    flatten_tools: bool = True,
    flatten_thinking: bool = False,
) -> list[tuple[str, list[dict]]]:
    """Split claude.ai interleaved assistant blocks into API-shape turns.

    Claude.ai packs [thinking, text, tool_use, tool_result, thinking, tool_use,
    tool_result, ...] into a single assistant message. The API requires:
      assistant: [thinking*, text*, tool_use+]
      user:      [tool_result+]
      assistant: ...

    When flatten_tools=True (default): tool_use and tool_result blocks are
    rendered as plain text and kept inside the single assistant turn. This is
    required for CC resume because CC's toolset doesn't include claude.ai's
    tools (view/web_fetch/web_search/bash_tool/...), and the API rejects
    tool_use blocks referencing undefined tools.

    Returns a list of (role, [blocks]) pairs.
    """
    turns: list[tuple[str, list[dict]]] = []
    buf: list[dict] = []
    role = "assistant"  # default role for non-tool_result blocks

    def flush():
        nonlocal buf
        if buf:
            turns.append((role, buf))
            buf = []

    for b in blocks:
        bt = b.get("type")
        if flatten_tools and bt in ("tool_use", "tool_result"):
            if role != "assistant":
                flush()
                role = "assistant"
            text = (_flatten_tool_use_to_text(b) if bt == "tool_use"
                    else _flatten_tool_result_to_text(b))
            if text:
                buf.append({"type": "text", "text": text})
            continue
        if bt == "tool_result":
            if role != "user":
                flush()
                role = "user"
            # Normalize content: API accepts a string or a list of blocks.
            # Claude.ai decorates each content item with extra fields (e.g. uuid,
            # citations) that the Anthropic API rejects — strip to API-valid keys.
            raw = b.get("content", "")
            if isinstance(raw, list):
                cleaned = []
                for item in raw:
                    if not isinstance(item, dict):
                        cleaned.append(item)
                        continue
                    it = item.get("type")
                    if it == "text":
                        cleaned.append({"type": "text", "text": item.get("text", "")})
                    elif it == "image":
                        src = item.get("source", {})
                        if src:
                            cleaned.append({"type": "image", "source": src})
                    else:
                        # unknown block: fall back to stringifying
                        cleaned.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
                content_value = cleaned
            else:
                content_value = raw
            new_b = {
                "type": "tool_result",
                "tool_use_id": b.get("tool_use_id"),
                "content": content_value,
            }
            if b.get("is_error"):
                new_b["is_error"] = True
            buf.append(new_b)
        else:
            if role != "assistant":
                flush()
                role = "assistant"
            if bt == "text":
                txt = b.get("text", "")
                if txt:  # API rejects empty text blocks
                    buf.append({"type": "text", "text": txt})
            elif bt == "thinking":
                # Thinking block signatures are cryptographic HMACs tied to the
                # original API context (claude.ai); they won't validate in a CC
                # resume. Two options:
                #   flatten_thinking=False (default): drop the block entirely.
                #   flatten_thinking=True: emit a plain text block wrapped in
                #     <thinking>...</thinking>. The text carries no signature,
                #     so the API accepts it; the model reads it as ordinary
                #     context. session-stripper recognizes whole-block wraps
                #     and can strip them later without re-converting.
                if flatten_thinking:
                    txt = b.get("thinking", "")
                    if txt:
                        buf.append({
                            "type": "text",
                            "text": f"<thinking>\n{txt}\n</thinking>",
                        })
                continue
            elif bt == "tool_use":
                buf.append({
                    "type": "tool_use",
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "input": b.get("input") or {},
                })
            else:
                # unknown block: preserve as-is
                buf.append(b)
    flush()

    # Drop turns that became empty (e.g. an assistant turn whose only block was
    # an empty text string) or that are "orphaned thinking" (assistant turn with
    # only thinking blocks — CC's formatTranscript strips these anyway, and the
    # API rejects messages without a non-thinking content block).
    def has_non_thinking(blks):
        return any(b.get("type") != "thinking" for b in blks if isinstance(b, dict))

    turns = [
        (r, blks) for (r, blks) in turns
        if blks and (r == "user" or has_non_thinking(blks))
    ]
    return turns


def build_envelope(
    *,
    role: str,
    content,
    parent_uuid: str | None,
    session_id: str,
    slug: str,
    cwd: str,
    version: str,
    git_branch: str,
    model: str | None,
    timestamp: datetime,
) -> dict:
    msg_uuid = str(_uuid.uuid4())
    env = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": USER_TYPE,
        "cwd": cwd,
        "sessionId": session_id,
        "version": version,
        "gitBranch": git_branch,
        "slug": slug,
        "type": "user" if role == "user" else "assistant",
        "message": {"role": role, "content": content},
        "uuid": msg_uuid,
        "timestamp": fmt_ts(timestamp),
    }
    if role == "assistant" and model:
        env["message"]["model"] = model
    return env


def convert(
    *,
    conv_path: Path,
    cwd: str,
    files_dir: Path | None,
    slug: str,
    version: str,
    git_branch: str,
    out_path: Path | None,
    dry_run: bool,
    session_id_override: str | None = None,
    flatten_tools: bool = True,
    flatten_thinking: bool = False,
) -> Path:
    data = load_conversation(conv_path)
    model = data.get("model") or "claude-opus-4-6"
    title = data.get("name", "")
    messages = data.get("chat_messages") or []

    session_id = session_id_override or str(_uuid.uuid4())
    encoded = encode_cwd(cwd)
    default_out = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    out_path = out_path or default_out

    # Timestamps: take claude.ai's created_at, but enforce monotonic increase at ms granularity.
    lines: list[dict] = []
    parent: str | None = None
    last_ts: datetime | None = None

    # One compact_boundary-free root: the first user message has parentUuid=null.
    for i, m in enumerate(messages):
        sender = m.get("sender")
        created = m.get("created_at") or m.get("updated_at")
        ts = parse_ts(created) if created else (last_ts or datetime.utcnow()) + timedelta(milliseconds=1)
        if last_ts is not None and ts <= last_ts:
            ts = last_ts + timedelta(milliseconds=1)

        if sender == "human":
            content = build_user_content_from_chat_ai(m, files_dir)
            env = build_envelope(
                role="user", content=content, parent_uuid=parent,
                session_id=session_id, slug=slug, cwd=cwd, version=version,
                git_branch=git_branch, model=None, timestamp=ts,
            )
            lines.append(env)
            parent = env["uuid"]
            last_ts = ts

        elif sender == "assistant":
            turns = split_assistant_blocks(
                m.get("content") or [],
                flatten_tools=flatten_tools,
                flatten_thinking=flatten_thinking,
            )
            for j, (role, blocks) in enumerate(turns):
                # each sub-turn gets its own ms-bumped timestamp to keep order
                sub_ts = ts + timedelta(milliseconds=j)
                if last_ts is not None and sub_ts <= last_ts:
                    sub_ts = last_ts + timedelta(milliseconds=1)
                env = build_envelope(
                    role=role, content=blocks, parent_uuid=parent,
                    session_id=session_id, slug=slug, cwd=cwd, version=version,
                    git_branch=git_branch,
                    model=(model if role == "assistant" else None),
                    timestamp=sub_ts,
                )
                lines.append(env)
                parent = env["uuid"]
                last_ts = sub_ts

        else:
            # Unknown sender — skip
            continue

    # Sanity checks
    uuids = {e["uuid"] for e in lines}
    for e in lines:
        p = e.get("parentUuid")
        assert p is None or p in uuids, f"broken parentUuid link at {e['uuid']}"

    ts_list = [e["timestamp"] for e in lines]
    assert ts_list == sorted(ts_list), "timestamps not monotonic"

    slugs = {e["slug"] for e in lines}
    assert len(slugs) == 1, f"inconsistent slugs: {slugs}"

    summary = {
        "source": str(conv_path),
        "title": title,
        "model": model,
        "chat_messages_in": len(messages),
        "jsonl_lines_out": len(lines),
        "session_id": session_id,
        "out_path": str(out_path),
        "resume_cmd": f"claude -r {session_id}",
    }

    if dry_run:
        print(json.dumps(summary, indent=2))
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for e in lines:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(json.dumps(summary, indent=2))
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("conversation", help="Path to claude.ai conversation.json")
    ap.add_argument("--cwd", default=os.getcwd(),
                    help="Working directory to associate with the CLI session "
                         "(determines which ~/.claude/projects/ folder it lands in). "
                         "Default: current directory.")
    ap.add_argument("--files-dir", default=None,
                    help="Directory containing downloaded images named <file_uuid>.<ext>. "
                         "If not provided, defaults to sibling 'files/' of the conversation.")
    ap.add_argument("--slug", default="imported-chat-ai",
                    help="Slug string (must be consistent across all messages). Default: imported-chat-ai.")
    ap.add_argument("--version", default=CC_VERSION_DEFAULT,
                    help=f"Claude Code version to stamp on messages. Default: {CC_VERSION_DEFAULT}.")
    ap.add_argument("--git-branch", default="master", help="gitBranch field. Default: master.")
    ap.add_argument("--out", default=None,
                    help="Override output path. Default: ~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl")
    ap.add_argument("--session-id", default=None,
                    help="Use this specific sessionId instead of generating a fresh UUID. "
                         "Useful for keeping claude.ai's conversation UUID as the CC sessionId.")
    ap.add_argument("--keep-tools", action="store_true",
                    help="Keep tool_use/tool_result blocks as-is. Default is to flatten "
                         "them into text blocks because claude.ai's tool names (view, "
                         "web_fetch, bash_tool, ...) are unknown to CC and cause the API "
                         "to reject the resumed conversation.")
    ap.add_argument("--flatten-thinking", action="store_true",
                    help="Preserve thinking blocks by flattening them into <thinking>...</thinking> "
                         "text blocks. Default drops thinking because the HMAC signatures tying "
                         "each block to its original API context won't validate in a CC resume. "
                         "Flattened thinking is signature-free and readable as context; "
                         "session-stripper recognizes the wrappers and can strip them later.")
    ap.add_argument("--dry-run", action="store_true", help="Print summary but don't write.")
    args = ap.parse_args()

    conv_path = Path(args.conversation).expanduser().resolve()
    if not conv_path.exists():
        print(f"error: {conv_path} not found", file=sys.stderr)
        return 2

    if args.files_dir is None:
        default_files = conv_path.parent / "files"
        files_dir = default_files if default_files.exists() else None
    else:
        files_dir = Path(args.files_dir).expanduser().resolve()

    out_path = Path(args.out).expanduser().resolve() if args.out else None

    convert(
        conv_path=conv_path,
        cwd=args.cwd,
        files_dir=files_dir,
        slug=args.slug,
        version=args.version,
        git_branch=args.git_branch,
        out_path=out_path,
        dry_run=args.dry_run,
        session_id_override=args.session_id,
        flatten_tools=not args.keep_tools,
        flatten_thinking=args.flatten_thinking,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
