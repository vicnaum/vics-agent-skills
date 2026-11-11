#!/usr/bin/env python3
"""Session Stripper — CLI tool for trimming Claude Code sessions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import argparse

from lib.analyze import analyze_session, health_check
from lib.strip_tools import strip_tools
from lib.strip_thinking import strip_thinking
from lib.strip_attachments import strip_attachments, list_attachments
from lib.compact import compact_before
from lib.persist_tools import show_tool, persist_tool_result, persist_tools_bulk, show_thinking, persist_thinking, persist_thinking_bulk
from lib.persist_text import persist_text, persist_text_bulk
from lib.persist_message import persist_message
from lib.persist_range import persist_range
from lib.migrate_persisted import migrate_persisted
from lib.replace_images import list_images, replace_images
from lib.fork import fork_session, cli_fork
from lib.compact_range import compact_range


def _reset_usage_after_strip(session_path, dry_run, enabled=True, calibration=None):
    """After a strip, rewrite stale assistant `usage` counts so CC's context
    gauge reflects the post-strip size. Without this, the meter keeps reading
    the pre-strip token count and CC blocks new input even though the
    conversation is now tiny. Off when --dry-run or --no-usage-reset.

    `calibration` (lib.chain.calibration of the untouched source) scales the
    estimate to what this session's content really costs."""
    if dry_run or not enabled:
        return
    from lib.chain import (load_session, save_session,
                           compute_active_chain_tokens, reset_usage_metadata)
    objects = load_session(session_path)
    factor = calibration["factor"] if calibration else None
    est = compute_active_chain_tokens(objects, factor=factor)
    n = reset_usage_metadata(objects, est)
    save_session(session_path, objects, create_backup=False)
    print(f"Context gauge reset: {n} usage record(s) capped to ~{est:,} tokens "
          f"so CC's meter reflects the stripped size.")
    print(_calibration_note(calibration, est))


def _calibration_note(calibration, est=None):
    if not calibration:
        return ("  (uncalibrated chars/4 estimate: this session records no real API "
                "usage to scale it by, e.g. it was stripped before)")
    before = f"{calibration['real']:,} measured at the last API call"
    after = f" → ~{est:,} now" if est is not None else ""
    return (f"  Context: {before}{after} (estimate calibrated ×{calibration['factor']:.2f} "
            f"to that measurement)")


def _calibration_of(path):
    """Calibration from a session file's own recorded usage, else from the
    stripper state of a fork made earlier (whose usage the gauge reset
    rewrote), else None."""
    from lib.chain import calibration, load_session
    from lib.fork import read_state
    try:
        cal = calibration(load_session(path))
    except (OSError, ValueError):
        cal = None
    if cal is None:
        state = read_state(path) or {}
        if state.get("calibration"):
            cal = dict(state["calibration"], inherited_from=state.get("source"))
    return cal


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


def _prepare_target(args, operation: str, default_mode: str = "fork"):
    """Mutating commands never write the session file they are given.

    Default — FORK: the result is a new session (`<newSessionId>.jsonl`,
    `forkedFrom` on every record) that you resume with `claude -r <id>`. The
    original is only read: nothing has to be swapped in, nothing it records
    later is ever discarded, and it can keep running while you strip.
      - The target is itself a stripper fork nobody has resumed yet: edit it
        in place (chained passes).
      - The target has such a fork already: reuse it, so back-to-back passes
        on the same session build ONE fork (`--new-fork` takes a fresh one).
      - Otherwise: fork it.

    `--in-place` — the older copy-first flow, for a session that must keep its
    id: mutate `<file>.jsonl.pending` and swap it over the original later with
    `apply` (or `respawn.sh --swap` for a session stripping itself). Records
    the original gains after the snapshot are discarded at the swap.

    No backups are made while mutating a fork or a pending copy — the
    untouched original is the backup.
    """
    from lib.pending import is_pending, ensure_pending, pending_path, original_of
    from lib.fork import find_reusable_fork, read_state, is_untouched, write_state
    target = Path(args.session).expanduser().absolute()
    if is_pending(target):
        # An explicit pending copy: the in-place flow, whatever the default.
        # (The preserved-thinking step and calibration read the live original.)
        args._pt_source = str(original_of(target))
        args._calibration = _calibration_of(args._pt_source)
        args.no_backup = True
        return args.session

    in_place = getattr(args, "in_place", False) or default_mode == "in-place"
    if not in_place:
        args.no_backup = True
        state = read_state(target)
        if state and state.get("source") and is_untouched(target, state):
            # A stripper fork nobody has resumed: chain this pass onto it.
            args._pt_source = state["source"]
            args._calibration = _calibration_of(state["source"]) or state.get("calibration")
            args._fork_result = str(target)
            if not args.dry_run:
                print(f"Editing stripper fork in place (not resumed yet): {target.stem}\n")
            return args.session
        args._pt_source = str(target)
        _hint_pending(str(target))
        # Calibrate against the untouched source, before anything changes.
        args._calibration = _calibration_of(target)
        reuse = None if getattr(args, "new_fork", False) else find_reusable_fork(target)
        if args.dry_run:
            # Never create a fork for a preview. Preview against the fork the
            # next real run would edit, if there is one.
            if reuse:
                args.session = str(reuse[0])
            return args.session
        if reuse:
            fork_path, state = reuse
            print(f"Reusing stripper fork {fork_path.stem} (not resumed yet). "
                  f"--new-fork takes a fresh one.")
            grown = target.stat().st_size - state.get("source_size", 0)
            if grown > 0:
                print(f"  note: {target.name} has grown by {grown:,} bytes since that "
                      f"fork was taken; what it recorded since is NOT in the fork.")
            print()
        else:
            source_size = target.stat().st_size
            fork_path = fork_session(target, custom_title=getattr(args, "fork_title", None),
                                     operation=operation)
            write_state(fork_path, source=target, source_size=source_size,
                        calibration=args._calibration)
            print(f"Forked: {fork_path}")
            print(f"New sessionId: {fork_path.stem}  (original untouched)\n")
        args.session = str(fork_path)
        args._fork_result = str(fork_path)
        return args.session

    # ── --in-place: the copy-first pending flow ──
    # The live file whose history produced the thinking signatures. The
    # preserved-thinking step compares the result against it (run_command).
    args._pt_source = str(target)
    args._calibration = _calibration_of(target)
    if getattr(args, "dry_run", False) and not pending_path(args.session).exists():
        # Dry runs write nothing — no point materializing a snapshot. If a
        # pending copy already exists, preview against IT (falls through),
        # so the report reflects what the next real mutation would do.
        return args.session
    pending, created = ensure_pending(args.session)
    verb = "Snapshot created" if created else "Reusing pending copy"
    print(f"{verb}: {pending}")
    print(f"Original untouched. Swap in when nothing is writing it:")
    print(f"  stripper.py apply {args.session}")
    print(f"  (own live session? schedule it: respawn.sh --swap {pending})\n")
    args.session = str(pending)
    args.no_backup = True
    return args.session


def _preview_result_via_copy(args):
    """Dry-run helper: run the same command for real on a throwaway copy of
    its target, silently, and return the path of the would-be result (plus the
    temp dir to delete). None when the command cannot run that way."""
    import contextlib
    import io
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ss-preview-"))
    copy_path = tmp / Path(args.session).name
    shutil.copy2(args.session, copy_path)
    clone = argparse.Namespace(**vars(args))
    clone.session = str(copy_path)
    clone.dry_run = False
    clone.fork = False
    clone.in_place = True   # mutate the throwaway copy itself, never fork it
    clone.new_fork = False
    clone.no_backup = True
    clone.no_usage_reset = True
    if hasattr(clone, "output"):
        clone.output = None  # compact: write next to the copy, never to a user path
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            clone.func(clone)
    except BaseException:  # noqa: BLE001 - incl. SystemExit from refusals
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None
    return getattr(clone, "_pt_result", None) or clone.session, tmp


def _preserved_thinking_step(args):
    """After any mutating command: on models with preserved thinking, find the
    surviving thinking blocks the edit invalidated (the API drops them,
    unbilled) and, unless --keep-dropped-thinking, remove them from the file
    so it matches what the model sees. Dry runs only report."""
    source = getattr(args, "_pt_source", None)
    if not source:
        return
    from lib.preserved_thinking import analyze_paths, format_report
    keep = getattr(args, "keep_dropped_thinking", False)
    tmp = None
    try:
        if args.dry_run:
            result, tmp = _preview_result_via_copy(args)
            if result is None:
                return
        else:
            result = getattr(args, "_pt_result", None) or args.session
        report = analyze_paths(source, result)
    finally:
        if tmp is not None:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    lines = format_report(report, will_remove=not keep, dry_run=args.dry_run)
    if not lines:
        return
    print()
    for line in lines:
        print(line)
    if args.dry_run or keep:
        return
    print()
    strip_thinking(result, dry_run=False, no_backup=True,
                   from_pos=report["first_pos"])
    _reset_usage_after_strip(result, False,
                             enabled=not getattr(args, "no_usage_reset", False),
                             calibration=getattr(args, "_calibration", None))


def run_command(args):
    """Run a parsed command, then the preserved-thinking step, then (fork
    mode) record the fork's state and say how to resume it."""
    args.func(args)
    _preserved_thinking_step(args)
    _finish_fork(args)


def _finish_fork(args):
    fork = getattr(args, "_fork_result", None)
    if not fork or getattr(args, "dry_run", False):
        return
    from lib.fork import write_state
    # Refresh size/mtime so the next pass can tell nobody resumed it yet.
    write_state(fork, calibration=getattr(args, "_calibration", None))
    sid = Path(fork).stem
    print()
    print(f"Stripped session: {fork}")
    print(f"Resume it:        claude -r {sid}")
    print(f"  (stripping your own live session? respawn.sh {sid}; the original "
          f"{Path(args._pt_source).stem} is untouched)")


def _hint_pending(session):
    """Warn when an in-place strip's pending copy exists for the session: it
    is a snapshot, and `apply` would discard everything recorded after it."""
    from lib.pending import is_pending, pending_path, meta_path_for
    if is_pending(session):
        return
    p = pending_path(session)
    if not p.exists():
        return
    import json as _json
    try:
        meta = _json.loads(meta_path_for(p).read_text(encoding="utf-8"))
        base = int(meta.get("base_size", 0))
        taken = meta.get("created_at", "?")
        grown = Path(session).expanduser().stat().st_size - base
    except (OSError, ValueError):
        taken, grown = "?", 0
    print(f"note: an in-place strip's pending copy exists (taken {taken}): {p}")
    if grown > 0:
        print(f"  The session has recorded {grown:,} more bytes since; `apply` would "
              f"discard them. Delete the copy if it is stale.")
    print("  (forks, the default, ignore it; `--in-place` commands keep editing it)\n")


def cmd_analyze(args):
    """Run full session analysis with token breakdown and health check."""
    _hint_pending(args.session)
    analyze_session(args.session, cal=_calibration_of(args.session))


def cmd_strip_tools(args):
    """Strip tool call content from a session."""
    _prepare_target(args, f"strip-tools --from {args.from_pos} --to {args.to_pos}")
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
                             enabled=not getattr(args, "no_usage_reset", False),
                             calibration=getattr(args, "_calibration", None))


def cmd_strip_thinking(args):
    """Strip thinking blocks from a session."""
    _prepare_target(args, f"strip-thinking --from {args.from_pos} --to {args.to_pos}")
    strip_thinking(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )
    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False),
                             calibration=getattr(args, "_calibration", None))


def cmd_strip_attachments(args):
    """Drop superseded attachments (system-reminders) from a session."""
    if args.list:
        list_attachments(args.session)
        return
    _prepare_target(args, "strip-attachments")
    types = None
    if args.types:
        types = [t.strip() for t in args.types.split(",")]
    strip_attachments(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        types=types,
        keep_recent=args.keep_recent,
        drop_all=args.drop_all,
        include_free=args.include_free,
    )
    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False),
                             calibration=getattr(args, "_calibration", None))


def cmd_strip_all(args):
    """Strip both tool content and thinking blocks."""
    _prepare_target(args, f"strip-all --from {args.from_pos} --to {args.to_pos}")
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

    # Superseded attachments are the third big block of re-sent context, and on
    # long sessions they can outweigh thinking several times over. Opt out with
    # --keep-attachments. Note this ignores --from/--to: the policy is
    # "keep the newest of each kind", which is a whole-session notion.
    if not getattr(args, "keep_attachments", False):
        print()
        strip_attachments(
            args.session,
            dry_run=args.dry_run,
            no_backup=True,  # already backed up above
        )

    _reset_usage_after_strip(args.session, args.dry_run,
                             enabled=not getattr(args, "no_usage_reset", False),
                             calibration=getattr(args, "_calibration", None))


def cmd_reset_usage(args):
    """Rewrite stale assistant usage counts so CC's context gauge matches the
    on-disk (post-strip) conversation size. Standalone: use on a session that
    was already stripped but whose meter is still pinned at the old size."""
    from lib.chain import (load_session, save_session,
                           compute_active_chain_tokens, reset_usage_metadata)
    _prepare_target(args, "reset-usage")
    cal = getattr(args, "_calibration", None)
    objects = load_session(args.session)
    est = compute_active_chain_tokens(objects, factor=cal["factor"] if cal else None)
    if args.dry_run:
        print(f"[dry run] would cap stale assistant usage to ~{est:,} tokens.")
        print(_calibration_note(cal, est))
        return
    n = reset_usage_metadata(objects, est)
    save_session(args.session, objects, create_backup=not args.no_backup)
    print(f"Reset {n} usage record(s) to ~{est:,} tokens. "
          f"CC's context gauge will now reflect the on-disk size.")
    print(_calibration_note(cal, est))


def cmd_compact(args):
    """Compact messages before a given chain position. It always writes a NEW
    session file, so there is nothing to fork."""
    args._pt_source = args.session  # for the preserved-thinking step
    stats = compact_before(
        args.session,
        before_pos=args.before,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        output_path=args.output,
        slug=args.slug,
    )
    if stats and stats.get("output_path") and not args.dry_run:
        args._pt_result = stats["output_path"]


def cmd_verify(args):
    """Verify chain integrity of a session file."""
    _hint_pending(args.session)
    ok = health_check(args.session)
    sys.exit(0 if ok else 1)


def cmd_snapshot(args):
    """Explicitly create (or refresh) the .pending copy of a session."""
    from lib.pending import create_snapshot
    try:
        pending = create_snapshot(args.session, refresh=args.refresh)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"Snapshot: {pending}")
    print(f"Mutating commands on the original will now target this copy.")
    print(f"Apply with: stripper.py apply {args.session}")


def cmd_apply(args):
    """Atomically swap the pending copy over its original."""
    from lib.pending import apply_pending, ApplyRefused
    try:
        apply_pending(args.session, dry_run=args.dry_run,
                      no_backup=args.no_backup,
                      discard_tail=getattr(args, "discard_tail", False))
    except ApplyRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)


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
    _prepare_target(args, f"persist-tool --id {args.id}")
    persist_tool_result(
        args.session,
        tool_use_id=args.id,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_tools(args):
    """Bulk persist tool results to files."""
    _prepare_target(args, f"persist-tools --from {args.from_pos} --to {args.to_pos}")
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
    _prepare_target(args, f"persist-thinking --pos {args.pos}")
    persist_thinking(
        args.session,
        chain_pos=args.pos,
        summary=args.summary,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


def cmd_persist_thinkings(args):
    """Bulk persist all thinking blocks to files."""
    _prepare_target(args, f"persist-thinkings --from {args.from_pos} --to {args.to_pos}")
    persist_thinking_bulk(
        args.session,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        from_pos=args.from_pos,
        to_pos=args.to_pos,
    )


def cmd_persist_text(args):
    """Persist a single text block at a chain position."""
    _prepare_target(args, f"persist-text --pos {args.pos}")
    persist_text(args.session, chain_pos=args.pos, summary=args.summary,
                 dry_run=args.dry_run, no_backup=args.no_backup)


def cmd_persist_texts(args):
    """Bulk persist text blocks across a chain range."""
    _prepare_target(args, f"persist-texts --from {args.from_pos} --to {args.to_pos} "
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
    _prepare_target(args, f"persist-message --pos {args.pos}")
    try:
        persist_message(args.session, chain_pos=args.pos, summary=args.summary,
                        dry_run=args.dry_run, no_backup=args.no_backup)
    except LeafPersistRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_persist_range(args):
    """Dispatcher: persist multiple kinds across a chain range."""
    kinds = tuple(k.strip() for k in (args.kinds or "text,thinking").split(",") if k.strip())
    _prepare_target(args, f"persist-range --from {args.from_pos} --to {args.to_pos} "
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
    # Moves shared sidecar files on disk, so a fork would strand the
    # original's references: this one stays on the in-place flow.
    _prepare_target(args, "migrate-persisted", default_mode="in-place")
    migrate_persisted(args.session, dry_run=args.dry_run, no_backup=args.no_backup)


def cmd_fork(args):
    """Fork a session without applying any other operation."""
    cli_fork(args.session, custom_title=args.fork_title, operation=args.operation)


def cmd_compact_range(args):
    """Collapse a range of messages into one survivor with a summary marker."""
    from lib.compact_range import RangeRefused
    _prepare_target(args, f"compact-range --from {args.from_pos} --to {args.to_pos}")
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
    _prepare_target(args, f"replace-images --dir {args.dir}")
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
    parser.add_argument("--keep-dropped-thinking", action="store_true",
                        help="On models with preserved thinking (Fable 5.1, Opus 5.5, "
                             "Sonnet 5.5, Haiku 5.5), keep the later thinking blocks an "
                             "edit invalidates. Default: remove them, since the API "
                             "drops them anyway")


def add_fork_args(parser):
    """Where a mutating command writes. Default: a fork (new sessionId,
    forkedFrom stamped on every envelope); the original is only read."""
    parser.add_argument("--fork", action="store_true",
                        help="Write the result to a fork (the default; kept for "
                             "older invocations)")
    parser.add_argument("--in-place", action="store_true",
                        help="Keep the session id: mutate <session>.jsonl.pending "
                             "and swap it in later with `apply` (or respawn.sh "
                             "--swap). Records the original gains after the "
                             "snapshot are discarded at the swap")
    parser.add_argument("--new-fork", action="store_true",
                        help="Take a fresh fork even if an unresumed fork of this "
                             "session exists (default: keep building on that one)")
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

    # strip-attachments
    p_strip_att = subparsers.add_parser(
        "strip-attachments",
        help="Drop superseded attachments (system-reminders re-sent every request)")
    add_common_args(p_strip_att)
    add_usage_reset_arg(p_strip_att)
    add_fork_args(p_strip_att)
    p_strip_att.add_argument("--list", action="store_true",
                             help="Show attachments by type with rendered cost and "
                                  "policy verdict; change nothing")
    p_strip_att.add_argument("--types", type=str, default=None,
                             help="Comma-separated attachment types to act on "
                                  "(default: all reducible types)")
    p_strip_att.add_argument("--keep-recent", type=int, default=None,
                             help="Override how many of each type to keep "
                                  "(default: per-type policy, e.g. 1 task_reminder)")
    p_strip_att.add_argument("--drop-all", action="store_true",
                             help="Drop EVERY attachment of the named --types, "
                                  "ignoring the keep-all policy. Escape hatch: can "
                                  "remove the capability attachments that tell the "
                                  "model which tools/skills exist")
    p_strip_att.add_argument("--include-free", action="store_true",
                             help="Also drop attachments that render to nothing "
                                  "(saves no context; shrinks the file only)")
    p_strip_att.set_defaults(func=cmd_strip_attachments)

    # strip-all
    p_strip_all = subparsers.add_parser("strip-all", help="Strip tools + thinking + superseded attachments")
    add_common_args(p_strip_all)
    add_range_args(p_strip_all)
    add_tool_filter_args(p_strip_all)
    add_usage_reset_arg(p_strip_all)
    add_fork_args(p_strip_all)
    p_strip_all.add_argument("--keep-attachments", action="store_true",
                             help="Don't drop superseded attachments (they are "
                                  "re-sent on every request; dropped by default)")
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

    # snapshot — explicit copy-first entry point (mutating commands also
    # auto-create this on first use)
    p_snapshot = subparsers.add_parser(
        "snapshot",
        help="Create the .pending copy of a session (mutating commands then "
             "target it; the original is never written until `apply`)",
    )
    p_snapshot.add_argument("session", help="Path to session JSONL file")
    p_snapshot.add_argument("--refresh", action="store_true",
                            help="Discard an existing pending copy and "
                                 "re-snapshot from the current original")
    p_snapshot.set_defaults(func=cmd_snapshot)

    # apply — the ONE write to a real session file
    p_apply = subparsers.add_parser(
        "apply",
        help="Atomically swap the .pending copy over its original. Refuses if "
             "the original is being written right now, was rewritten since "
             "the snapshot, the pending copy fails verify, or someone typed new "
             "messages into the session since the snapshot. Other post-snapshot "
             "appends (the stripping turn itself) are discarded by design.",
    )
    p_apply.add_argument("session",
                         help="Path to the session JSONL or its .pending copy")
    p_apply.add_argument("--dry-run", action="store_true",
                         help="Run all checks and report; swap nothing")
    p_apply.add_argument("--no-backup", action="store_true",
                         help="Skip the .bak of the original at swap time")
    p_apply.add_argument("--discard-tail", action="store_true",
                         help="Swap even though messages were typed into the session "
                              "after the snapshot (they are discarded)")
    p_apply.set_defaults(func=cmd_apply)

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
        help="List image blocks (also inside tool results) with tokens and SHA256 hashes (active chain)",
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

    run_command(args)


if __name__ == "__main__":
    main()
