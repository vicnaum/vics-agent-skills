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


def cmd_analyze(args):
    """Run full session analysis with token breakdown and health check."""
    analyze_session(args.session)


def cmd_strip_tools(args):
    """Strip tool call content from a session."""
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


def cmd_strip_thinking(args):
    """Strip thinking blocks from a session."""
    strip_thinking(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )


def cmd_strip_all(args):
    """Strip both tool content and thinking blocks."""
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


def cmd_compact(args):
    """Compact messages before a given chain position."""
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
    persist_tool_result(
        args.session,
        tool_use_id=args.id,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_tools(args):
    """Bulk persist tool results to files."""
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
    persist_thinking(
        args.session,
        chain_pos=args.pos,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_thinkings(args):
    """Bulk persist all thinking blocks to files."""
    persist_thinking_bulk(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )


def add_common_args(parser):
    """Add common flags shared across subcommands."""
    parser.add_argument("session", help="Path to session JSONL file")
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't modify")
    parser.add_argument("--no-backup", action="store_true", help="Skip .bak backup creation")


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
    p_strip_tools.set_defaults(func=cmd_strip_tools)

    # strip-thinking
    p_strip_thinking = subparsers.add_parser("strip-thinking", help="Strip thinking blocks")
    add_common_args(p_strip_thinking)
    add_range_args(p_strip_thinking)
    p_strip_thinking.set_defaults(func=cmd_strip_thinking)

    # strip-all
    p_strip_all = subparsers.add_parser("strip-all", help="Strip tools + thinking")
    add_common_args(p_strip_all)
    add_range_args(p_strip_all)
    add_tool_filter_args(p_strip_all)
    p_strip_all.set_defaults(func=cmd_strip_all)

    # compact
    p_compact = subparsers.add_parser("compact", help="Compact messages before a position")
    add_common_args(p_compact)
    p_compact.add_argument("--before", type=int, required=True,
                           help="Chain position to compact before")
    p_compact.add_argument("--output", type=str, default=None,
                           help="Custom output path (default: auto-generate)")
    p_compact.add_argument("--slug", type=str, default=None,
                           help="Custom slug for compacted session")
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
    p_persist_tool.set_defaults(func=cmd_persist_tool)

    # persist-tools
    p_persist_tools = subparsers.add_parser("persist-tools", help="Bulk persist tool results to files")
    add_common_args(p_persist_tools)
    add_range_args(p_persist_tools)
    p_persist_tools.add_argument("--tools", type=str, default=None,
                                 help="Comma-separated list of tool names to persist (default: all)")
    p_persist_tools.add_argument("--keep-recent", type=int, default=3,
                                 help="Keep last N tool results intact (default: 3)")
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
    p_persist_thinking.set_defaults(func=cmd_persist_thinking)

    # persist-thinkings
    p_persist_thinkings = subparsers.add_parser("persist-thinkings", help="Bulk persist all thinking blocks to files")
    add_common_args(p_persist_thinkings)
    add_range_args(p_persist_thinkings)
    p_persist_thinkings.set_defaults(func=cmd_persist_thinkings)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
