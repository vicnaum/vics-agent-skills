#!/usr/bin/env python3
"""Session Stripper — CLI tool for trimming Claude Code sessions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import argparse

from lib.analyze import analyze_session, health_check
from lib.strip_tools import strip_tools
from lib.strip_thinking import strip_thinking
from lib.compact import compact_before
from lib.persist_tools import show_tool, persist_tool_result, persist_tools_bulk, show_thinking, persist_thinking, persist_thinking_bulk
from lib.persist_text import persist_text, persist_text_bulk
from lib.persist_message import persist_message
from lib.persist_range import persist_range
from lib.migrate_persisted import migrate_persisted
from lib.replace_images import list_images, replace_images
from lib.fork import fork_session, cli_fork
from lib.compact_range import compact_range


def _reset_usage_after_strip(session_path, dry_run, enabled=True):
    """After a strip, rewrite stale assistant `usage` counts so CC's context
    gauge reflects the post-strip size. Without this, the meter keeps reading
    the pre-strip token count and CC blocks new input even though the
    conversation is now tiny. Off when --dry-run or --no-usage-reset."""
    if dry_run or not enabled:
        return
    from lib.chain import (load_session, save_session,
                           compute_active_chain_tokens, reset_usage_metadata)
    objects = load_session(session_path)
    est = compute_active_chain_tokens(objects)
    n = reset_usage_metadata(objects, est)
    save_session(session_path, objects, create_backup=False)
    print(f"Context gauge reset: {n} usage record(s) capped to ~{est:,} tokens "
          f"(active-chain content estimate) so CC's meter reflects the stripped size.")


def resolve_current_session(cwd=None):
    """Return the absolute path to the CURRENT session's JSONL, resolved from
    $CLAUDE_CODE_SESSION_ID — the only reliable source. Never guess by mtime
    (`ls -t`): in a project dir with concurrent sessions the newest file is
    often a *different* session, and stripping it corrupts someone else's work.

    CC stores sessions at ~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl,
    where <encoded-cwd> is the absolute cwd with every '/' and '.' replaced by
    '-'. Returns a Path (may not exist if run outside a CC session) or raises
    RuntimeError if the env var is missing.
    """
    import os
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        raise RuntimeError(
            "$CLAUDE_CODE_SESSION_ID is not set — run this inside the Claude "
            "Code session you want to strip (it is exported into Bash tool "
            "calls). Do NOT fall back to `ls -t`; that races with other "
            "concurrent sessions."
        )
    projects = Path.home() / ".claude" / "projects"
    # Preferred: the project dir encoded from the current cwd.
    base = Path(cwd or os.getcwd()).resolve()
    encoded = str(base).replace("/", "-").replace(".", "-")
    guess = projects / encoded / f"{sid}.jsonl"
    if guess.exists():
        return guess
    # Fallback: the session may have STARTED in a different cwd (the agent
    # cd'd since). The sessionId is a unique UUID, so a dir-agnostic search is
    # still unambiguous — and still not an mtime guess. Use it only when it
    # resolves to exactly one file.
    matches = sorted(projects.glob(f"*/{sid}.jsonl"))
    if len(matches) == 1:
        return matches[0]
    # 0 or >1 matches: return the cwd-based guess so the caller sees a clear
    # "exists: NO" rather than silently targeting the wrong file.
    return guess


def cmd_current(args):
    """Print the current session's JSONL path (resolved from the env var)."""
    try:
        path = resolve_current_session()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    exists = path.exists()
    if args.quiet:
        print(path)
    else:
        print(f"session id: {path.stem}")
        print(f"path:       {path}")
        print(f"exists:     {'yes' if exists else 'NO — cwd may differ from where the session started'}")
    sys.exit(0 if exists else 1)


def _maybe_fork(args, operation: str):
    """If `--fork` is set, fork the session in place and rewrite args.session
    to point at the fork. Subsequent command logic then mutates the fork
    instead of the original. Returns the (possibly-new) session path.
    """
    if not getattr(args, "fork", False):
        return args.session
    forked = fork_session(
        args.session,
        custom_title=getattr(args, "fork_title", None),
        operation=operation,
    )
    print(f"Forked: {forked}")
    print(f"New sessionId: {forked.stem}\n")
    args.session = str(forked)
    return args.session


def cmd_analyze(args):
    """Run full session analysis with token breakdown and health check."""
    analyze_session(args.session)


def cmd_strip_tools(args):
    """Strip tool call content from a session."""
    _maybe_fork(args, f"strip-tools --from {args.from_pos} --to {args.to_pos}")
    tools = None
    if args.tools:
        tools = [t.strip() for t in args.tools.split(",")]

    strip_tools(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
        only_inputs=args.only_inputs,
        only_results=args.only_results,
        tool_names=tools,
        keep_last_lines=args.keep_last_lines,
    )
    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False))


def cmd_strip_thinking(args):
    """Strip thinking blocks from a session."""
    _maybe_fork(args, f"strip-thinking --from {args.from_pos} --to {args.to_pos}")
    strip_thinking(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )
    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False))


def cmd_strip_all(args):
    """Strip both tool content and thinking blocks."""
    _maybe_fork(args, f"strip-all --from {args.from_pos} --to {args.to_pos}")
    tools = None
    if args.tools:
        tools = [t.strip() for t in args.tools.split(",")]

    strip_tools(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
        only_inputs=args.only_inputs,
        only_results=args.only_results,
        tool_names=tools,
        keep_last_lines=args.keep_last_lines,
    )

    strip_thinking(
        args.session,
        dry_run=args.dry_run,
        no_backup=True,  # already backed up by strip_tools (or skipped)
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )
    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False))


def cmd_reset_usage(args):
    """Rewrite stale assistant usage counts so CC's context gauge matches the
    on-disk (post-strip) conversation size. Standalone: use on a session that
    was already stripped but whose meter is still pinned at the old size."""
    from lib.chain import (load_session, save_session,
                           compute_active_chain_tokens, reset_usage_metadata)
    _maybe_fork(args, "reset-usage")
    objects = load_session(args.session)
    est = compute_active_chain_tokens(objects)
    if args.dry_run:
        print(f"[dry run] would cap stale assistant usage to ~{est:,} tokens "
              f"(active-chain content estimate).")
        return
    n = reset_usage_metadata(objects, est)
    save_session(args.session, objects, create_backup=not args.no_backup)
    print(f"Reset {n} usage record(s) to ~{est:,} tokens. "
          f"CC's context gauge will now reflect the on-disk size.")


def cmd_compact(args):
    """Compact messages before a given chain position."""
    _maybe_fork(args, f"compact --before {args.before}")
    compact_before(
        args.session,
        before_pos=args.before,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        output_path=args.output,
        slug=args.slug,
    )


def cmd_verify(args):
    """Verify chain integrity of a session file."""
    ok = health_check(args.session)
    sys.exit(0 if ok else 1)


def cmd_show_tool(args):
    """Show a specific tool call or list all tool calls."""
    if args.list:
        show_tool(args.session, tool_use_id="list")
    elif args.id:
        show_tool(args.session, tool_use_id=args.id, context_lines=args.context)
    elif args.pos is not None:
        show_tool(args.session, chain_pos=args.pos, context_lines=args.context)
    else:
        print("Error: specify --id, --pos, or --list")
        sys.exit(1)


def cmd_persist_tool(args):
    """Persist a single tool result to file with optional summary."""
    _maybe_fork(args, f"persist-tool --id {args.id}")
    persist_tool_result(
        args.session,
        tool_use_id=args.id,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_tools(args):
    """Bulk persist tool results to files."""
    _maybe_fork(args, f"persist-tools --from {args.from_pos} --to {args.to_pos}")
    tools = None
    if args.tools:
        tools = [t.strip() for t in args.tools.split(",")]

    persist_tools_bulk(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
        tool_names=tools,
        keep_recent=args.keep_recent,
    )


def cmd_show_thinking(args):
    """Show or list thinking blocks in the active chain."""
    if args.pos is not None:
        show_thinking(args.session, chain_pos=args.pos, context_lines=args.context)
    else:
        show_thinking(args.session, chain_pos=None, context_lines=args.context)


def cmd_persist_thinking(args):
    """Persist a single thinking block to file with optional summary."""
    _maybe_fork(args, f"persist-thinking --pos {args.pos}")
    persist_thinking(
        args.session,
        chain_pos=args.pos,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_thinkings(args):
    """Bulk persist all thinking blocks to files."""
    _maybe_fork(args, f"persist-thinkings --from {args.from_pos} --to {args.to_pos}")
    persist_thinking_bulk(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )


def cmd_persist_text(args):
    """Persist a single text block at a chain position."""
    _maybe_fork(args, f"persist-text --pos {args.pos}")
    persist_text(args.session, chain_pos=args.pos, summary=args.summary,
                 dry_run=args.dry_run, no_backup=args.no_backup)


def cmd_persist_texts(args):
    """Bulk persist text blocks across a chain range."""
    _maybe_fork(args, f"persist-texts --from {args.from_pos} --to {args.to_pos} "
                       f"--min-chars {args.min_chars} --keep-recent {args.keep_recent}")
    persist_text_bulk(
        args.session,
        from_pos=args.from_pos, to_pos=args.to_pos,
        min_chars=args.min_chars or 0,
        keep_recent=args.keep_recent or 0,
        dry_run=args.dry_run, no_backup=args.no_backup,
    )


def cmd_persist_message(args):
    """Persist an entire message — all blocks collapse to one marker."""
    from lib.persist_message import LeafPersistRefused
    _maybe_fork(args, f"persist-message --pos {args.pos}")
    try:
        persist_message(args.session, chain_pos=args.pos, summary=args.summary,
                        dry_run=args.dry_run, no_backup=args.no_backup)
    except LeafPersistRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_persist_range(args):
    """Dispatcher: persist multiple kinds across a chain range."""
    kinds = tuple(k.strip() for k in (args.kinds or "text,thinking").split(",") if k.strip())
    _maybe_fork(args, f"persist-range --from {args.from_pos} --to {args.to_pos} "
                       f"--kinds {','.join(kinds)} --min-chars {args.min_chars} "
                       f"--keep-recent {args.keep_recent}")
    persist_range(
        args.session,
        from_pos=args.from_pos or 0,
        to_pos=args.to_pos,
        kinds=kinds,
        min_chars=args.min_chars or 0,
        keep_recent=args.keep_recent or 0,
        summaries_file=args.summaries_file,
        dry_run=args.dry_run, no_backup=args.no_backup,
    )


def cmd_migrate_persisted(args):
    """One-shot migration of pre-persist-everything layouts."""
    _maybe_fork(args, "migrate-persisted")
    migrate_persisted(args.session, dry_run=args.dry_run, no_backup=args.no_backup)


def cmd_fork(args):
    """Fork a session without applying any other operation."""
    cli_fork(args.session, custom_title=args.fork_title, operation=args.operation)


def cmd_compact_range(args):
    """Collapse a range of messages into one survivor with a summary marker."""
    from lib.compact_range import RangeRefused
    _maybe_fork(args, f"compact-range --from {args.from_pos} --to {args.to_pos}")
    try:
        compact_range(args.session, from_pos=args.from_pos, to_pos=args.to_pos,
                      summary=args.summary,
                      dry_run=args.dry_run, no_backup=args.no_backup)
    except RangeRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_list_images(args):
    """Enumerate image blocks in the active chain with sizes and SHA256 hashes."""
    list_images(args.session)


def cmd_replace_images(args):
    """Replace image blocks with text transcripts keyed by SHA256."""
    _maybe_fork(args, f"replace-images --dir {args.dir}")
    replace_images(
        args.session,
        descriptions_dir=args.dir,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        drop_missing=args.drop_missing,
    )


def add_common_args(parser):
    """Add common flags shared across subcommands."""
    parser.add_argument("session", help="Path to session JSONL file")
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't modify")
    parser.add_argument("--no-backup", action="store_true", help="Skip .bak backup creation")


def add_fork_args(parser):
    """Add --fork / --fork-title flags. When --fork is set, mutating commands
    operate on a forked copy (new sessionId, forkedFrom stamped on every
    envelope) and leave the original untouched."""
    parser.add_argument("--fork", action="store_true",
                        help="Fork the session before mutating: writes to a "
                             "new <newSessionId>.jsonl with forkedFrom "
                             "metadata; original is left untouched")
    parser.add_argument("--fork-title", type=str, default=None,
                        help="Custom title for the forked session "
                             "(default: <orig title> (Stripped))")


def add_usage_reset_arg(parser):
    """Opt out of the post-strip context-gauge reset."""
    parser.add_argument("--no-usage-reset", action="store_true",
                        help="Don't rewrite stale assistant usage counts after "
                             "stripping. By default, strip resets CC's context "
                             "gauge to the post-strip size so it stops blocking input.")


def add_range_args(parser):
    """Add --from and --to chain position flags."""
    parser.add_argument("--from", dest="from_pos", type=int, default=0,
                        help="Start chain position (default: 0)")
    parser.add_argument("--to", dest="to_pos", type=int, default=None,
                        help="End chain position (default: end)")


def add_tool_filter_args(parser):
    """Add tool-specific filter flags."""
    parser.add_argument("--only-inputs", action="store_true",
                        help="Only clear tool_use inputs")
    parser.add_argument("--only-results", action="store_true",
                        help="Only clear tool_result content")
    parser.add_argument("--tools", type=str, default=None,
                        help="Comma-separated list of tool names to strip (default: all)")
    parser.add_argument("--keep-last-lines", type=int, default=None,
                        help="Keep last N lines of tool_result content")


def main():
    parser = argparse.ArgumentParser(
        prog="stripper",
        description="Session Stripper — CLI tool for trimming Claude Code sessions that hit 'Prompt is too long'",
    )
    subparsers = parser.add_subparsers(dest="command")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze session with full report")
    p_analyze.add_argument("session", help="Path to session JSONL file")
    p_analyze.set_defaults(func=cmd_analyze)

    # strip-tools
    p_strip_tools = subparsers.add_parser("strip-tools", help="Strip tool call content")
    add_common_args(p_strip_tools)
    add_range_args(p_strip_tools)
    add_tool_filter_args(p_strip_tools)
    add_usage_reset_arg(p_strip_tools)
    add_fork_args(p_strip_tools)
    p_strip_tools.set_defaults(func=cmd_strip_tools)

    # strip-thinking
    p_strip_thinking = subparsers.add_parser("strip-thinking", help="Strip thinking blocks")
    add_common_args(p_strip_thinking)
    add_range_args(p_strip_thinking)
    add_usage_reset_arg(p_strip_thinking)
    add_fork_args(p_strip_thinking)
    p_strip_thinking.set_defaults(func=cmd_strip_thinking)

    # strip-all
    p_strip_all = subparsers.add_parser("strip-all", help="Strip tools + thinking")
    add_common_args(p_strip_all)
    add_range_args(p_strip_all)
    add_tool_filter_args(p_strip_all)
    add_usage_reset_arg(p_strip_all)
    add_fork_args(p_strip_all)
    p_strip_all.set_defaults(func=cmd_strip_all)

    # reset-usage — fix CC's context gauge without stripping anything
    p_reset_usage = subparsers.add_parser(
        "reset-usage",
        help="Rewrite stale assistant usage counts so CC's context gauge matches "
             "the on-disk size (use if a session is stripped but the meter is still full)",
    )
    add_common_args(p_reset_usage)
    add_fork_args(p_reset_usage)
    p_reset_usage.set_defaults(func=cmd_reset_usage)

    # compact
    p_compact = subparsers.add_parser("compact", help="Compact messages before a position")
    add_common_args(p_compact)
    p_compact.add_argument("--before", type=int, required=True,
                           help="Chain position to compact before")
    p_compact.add_argument("--output", type=str, default=None,
                           help="Custom output path (default: auto-generate)")
    p_compact.add_argument("--slug", type=str, default=None,
                           help="Custom slug for compacted session")
    add_fork_args(p_compact)
    p_compact.set_defaults(func=cmd_compact)

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify chain integrity")
    p_verify.add_argument("session", help="Path to session JSONL file")
    p_verify.set_defaults(func=cmd_verify)

    # show-tool
    p_show_tool = subparsers.add_parser("show-tool", help="Show or list tool calls")
    p_show_tool.add_argument("session", help="Path to session JSONL file")
    p_show_tool.add_argument("--id", type=str, default=None,
                             help="Tool use ID to show")
    p_show_tool.add_argument("--pos", type=int, default=None,
                             help="Chain position of assistant message containing tool_use")
    p_show_tool.add_argument("--list", action="store_true",
                             help="List all tool calls in active chain")
    p_show_tool.add_argument("--context", type=int, default=2,
                             help="Number of context messages before/after (default: 2)")
    p_show_tool.set_defaults(func=cmd_show_tool)

    # persist-tool
    p_persist_tool = subparsers.add_parser("persist-tool", help="Persist a single tool result to file")
    add_common_args(p_persist_tool)
    p_persist_tool.add_argument("--id", type=str, required=True,
                                help="Tool use ID to persist")
    p_persist_tool.add_argument("--summary", type=str, default=None,
                                help="Summary text to include in replacement")
    add_fork_args(p_persist_tool)
    p_persist_tool.set_defaults(func=cmd_persist_tool)

    # persist-tools
    p_persist_tools = subparsers.add_parser("persist-tools", help="Bulk persist tool results to files")
    add_common_args(p_persist_tools)
    add_range_args(p_persist_tools)
    p_persist_tools.add_argument("--tools", type=str, default=None,
                                 help="Comma-separated list of tool names to persist (default: all)")
    p_persist_tools.add_argument("--keep-recent", type=int, default=3,
                                 help="Keep last N tool results intact (default: 3)")
    add_fork_args(p_persist_tools)
    p_persist_tools.set_defaults(func=cmd_persist_tools)

    # show-thinking
    p_show_thinking = subparsers.add_parser("show-thinking", help="Show or list thinking blocks")
    p_show_thinking.add_argument("session", help="Path to session JSONL file")
    p_show_thinking.add_argument("--pos", type=int, default=None,
                                  help="Chain position of assistant message to show thinking for")
    p_show_thinking.add_argument("--list", action="store_true",
                                  help="List all thinking blocks in active chain (default)")
    p_show_thinking.add_argument("--context", type=int, default=2,
                                  help="Number of context messages before/after (default: 2)")
    p_show_thinking.set_defaults(func=cmd_show_thinking)

    # persist-thinking
    p_persist_thinking = subparsers.add_parser("persist-thinking", help="Persist a single thinking block to file")
    add_common_args(p_persist_thinking)
    p_persist_thinking.add_argument("--pos", type=int, required=True,
                                     help="Chain position of assistant message")
    p_persist_thinking.add_argument("--summary", type=str, default=None,
                                     help="Summary text to include in replacement")
    add_fork_args(p_persist_thinking)
    p_persist_thinking.set_defaults(func=cmd_persist_thinking)

    # persist-thinkings
    p_persist_thinkings = subparsers.add_parser("persist-thinkings", help="Bulk persist all thinking blocks to files")
    add_common_args(p_persist_thinkings)
    add_range_args(p_persist_thinkings)
    add_fork_args(p_persist_thinkings)
    p_persist_thinkings.set_defaults(func=cmd_persist_thinkings)

    # persist-text (single)
    p_persist_text = subparsers.add_parser("persist-text", help="Persist a single text block at a chain position")
    add_common_args(p_persist_text)
    p_persist_text.add_argument("--pos", type=int, required=True,
                                help="Chain position of the message containing the text block")
    p_persist_text.add_argument("--summary", type=str, default=None,
                                help="Summary string for the marker (optional)")
    add_fork_args(p_persist_text)
    p_persist_text.set_defaults(func=cmd_persist_text)

    # persist-texts (bulk)
    p_persist_texts = subparsers.add_parser("persist-texts", help="Bulk persist text blocks across a range")
    add_common_args(p_persist_texts)
    add_range_args(p_persist_texts)
    p_persist_texts.add_argument("--min-chars", type=int, default=0,
                                  help="Skip text blocks shorter than N chars")
    p_persist_texts.add_argument("--keep-recent", type=int, default=0,
                                  help="Skip the last N qualifying blocks (preserve tail)")
    add_fork_args(p_persist_texts)
    p_persist_texts.set_defaults(func=cmd_persist_texts)

    # persist-message
    p_persist_message = subparsers.add_parser("persist-message", help="Persist an entire message at a chain position")
    add_common_args(p_persist_message)
    p_persist_message.add_argument("--pos", type=int, required=True,
                                    help="Chain position of the message to persist")
    p_persist_message.add_argument("--summary", type=str, default=None,
                                    help="Summary string for the marker (optional)")
    add_fork_args(p_persist_message)
    p_persist_message.set_defaults(func=cmd_persist_message)

    # persist-range (dispatcher)
    p_persist_range = subparsers.add_parser("persist-range", help="Dispatcher: persist multiple kinds across a chain range")
    add_common_args(p_persist_range)
    add_range_args(p_persist_range)
    p_persist_range.add_argument("--kinds", type=str, default="text,thinking",
                                  help="Comma-separated kinds to persist (tool,thinking,text,image,message). Default: text,thinking")
    p_persist_range.add_argument("--min-chars", type=int, default=0,
                                  help="Skip text blocks shorter than N chars")
    p_persist_range.add_argument("--keep-recent", type=int, default=0,
                                  help="Skip the last N qualifying blocks (preserve tail)")
    p_persist_range.add_argument("--summaries-file", type=str, default=None,
                                  help="JSON file mapping pos:N / toolu_X / msg:UUID → summary")
    add_fork_args(p_persist_range)
    p_persist_range.set_defaults(func=cmd_persist_range)

    # migrate-persisted
    p_migrate = subparsers.add_parser("migrate-persisted",
                                       help="One-shot migration of pre-PR persisted layouts (<image> markers, .tool-results/ sidecars)")
    add_common_args(p_migrate)
    add_fork_args(p_migrate)
    p_migrate.set_defaults(func=cmd_migrate_persisted)

    # list-images
    p_list_images = subparsers.add_parser(
        "list-images",
        help="List image blocks with SHA256 hashes (active chain)",
    )
    p_list_images.add_argument("session", help="Path to session JSONL file")
    p_list_images.set_defaults(func=cmd_list_images)

    # replace-images
    p_replace_images = subparsers.add_parser(
        "replace-images",
        help="Replace image blocks with text transcripts from <dir>/<sha256>.txt",
    )
    add_common_args(p_replace_images)
    p_replace_images.add_argument(
        "--dir", required=True,
        help="Directory containing <sha256>.txt transcripts, one per unique image.",
    )
    p_replace_images.add_argument(
        "--drop-missing", action="store_true",
        help="If a transcript is missing, drop the image block entirely (default: keep).",
    )
    add_fork_args(p_replace_images)
    p_replace_images.set_defaults(func=cmd_replace_images)

    # compact-range — collapse N consecutive messages to one survivor
    p_compact_range = subparsers.add_parser(
        "compact-range",
        help="Collapse messages [from..to] into one survivor carrying a "
             "<persisted-range> summary marker. Originals saved to sidecar files.",
    )
    add_common_args(p_compact_range)
    p_compact_range.add_argument("--from", dest="from_pos", type=int, required=True,
                                  help="Range start chain position (inclusive)")
    p_compact_range.add_argument("--to", dest="to_pos", type=int, required=True,
                                  help="Range end chain position (inclusive)")
    p_compact_range.add_argument("--summary", type=str, default=None,
                                  help="Summary covering the whole range "
                                       "(default: '[range collapsed]')")
    add_fork_args(p_compact_range)
    p_compact_range.set_defaults(func=cmd_compact_range)

    # fork (standalone) — fork a session without applying any other operation
    p_fork = subparsers.add_parser(
        "fork",
        help="Fork a session: copy to <newSessionId>.jsonl with forkedFrom metadata; original untouched",
    )
    p_fork.add_argument("session", help="Path to session JSONL file")
    p_fork.add_argument("--fork-title", type=str, default=None,
                        help="Custom title for the forked session "
                             "(default: <orig title> (Stripped))")
    p_fork.add_argument("--operation", type=str, default=None,
                        help="Stamp strippedBy.operation (optional)")
    p_fork.set_defaults(func=cmd_fork)

    p_current = subparsers.add_parser(
        "current",
        help="Print THIS session's JSONL path (from $CLAUDE_CODE_SESSION_ID). "
             "Use this to target strips — never `ls -t`.",
    )
    p_current.add_argument("-q", "--quiet", action="store_true",
                           help="Print only the path (for $(...) capture)")
    p_current.set_defaults(func=cmd_current)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
