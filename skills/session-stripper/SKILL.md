---
name: session-stripper
description: Manually trim, compact, persist, and repair Claude Code conversation sessions stored as JSONL files. Use when the user wants to: (1) fix a session hitting 'Prompt is too long' errors, (2) manually compact or strip a session to reduce context, (3) analyze a session's token usage and message chain, (4) strip tool call inputs and results from a session, (5) strip thinking blocks from a session, (6) compact part of a session before a specific message, (7) verify or repair a broken session chain, (8) persist and summarize tool results to reduce context intelligently, (9) perform any JSONL-level surgery on Claude Code sessions. Triggers on mentions of session surgery, session trimming, session stripping, context compaction, session repair, prompt too long, session compact, manual session manipulation, summarize tool results, or persist tool output.
---

# Session Surgery

CLI tool for trimming Claude Code JSONL sessions. Sessions live at `~/.claude/projects/<project-path>/<uuid>.jsonl`.

## Quick Reference

Run scripts from `<skill-dir>/scripts/`:

```bash
# Analyze session — token breakdown by type and tool name, cut points, health check
python3 <skill-dir>/scripts/stripper.py analyze <session.jsonl>

# Strip all tool content + thinking (most common — frees 60-90% of context)
python3 <skill-dir>/scripts/stripper.py strip-all <session.jsonl>

# Strip only specific tools, keep last 3 lines of results
python3 <skill-dir>/scripts/stripper.py strip-tools <session.jsonl> --tools Bash,Read --only-results --keep-last-lines 3

# Strip thinking blocks only
python3 <skill-dir>/scripts/stripper.py strip-thinking <session.jsonl>

# Compact everything before chain position 150
python3 <skill-dir>/scripts/stripper.py compact <session.jsonl> --before 150

# Verify chain integrity
python3 <skill-dir>/scripts/stripper.py verify <session.jsonl>
```

All commands support `--dry-run` (report only) and `--no-backup` (skip .bak). Range filtering via `--from N --to M` (chain positions).

## What Gets Stripped

| Target | Impact | Command |
|--------|--------|---------|
| Tool call inputs (`tool_use.input`) | 10-20% of context | `strip-tools --only-inputs` |
| Tool results (`tool_result.content`) | 40-60% of context | `strip-tools --only-results` |
| Thinking blocks | 10-20% of context | `strip-thinking` |
| Images | Variable (can be 50%+) | `strip-tools` (auto) |
| Everything above | 60-90% of context | `strip-all` |

## Workflow

1. **Analyze first** -- Run `analyze` to see token breakdown and identify what to strip
2. **Strip in-place** -- Run `strip-all` for maximum savings, or use granular flags
3. **Verify after** -- Run `verify` to confirm chain integrity
4. **Compact if needed** -- Use `compact --before N` to summarize old work and keep recent work intact

## Tool Stripping Options

- `--tools Bash,Read,Agent` -- Only strip named tools (19 known: Bash, Read, Agent, Write, Edit, TaskOutput, Grep, Glob, WebFetch, TaskCreate, TaskUpdate, AskUserQuestion, TaskStop, WebSearch, Skill, EnterPlanMode, ExitPlanMode, ToolSearch, plus MCP tools)
- `--only-inputs` -- Only clear tool_use inputs
- `--only-results` -- Only clear tool_result content
- `--keep-last-lines N` -- Keep last N lines of tool results (preserves errors/output)

## Persist & Summarize Workflow

For smarter context reduction, persist tool results to files with optional AI-generated summaries instead of blindly clearing them.

### Workflow
1. Run `analyze` to identify heavy tool calls
2. Run `show-tool --list` to see all tool calls with sizes
3. Run `show-tool --id <id>` to read specific tool calls (agent reads and decides)
4. Run `persist-tool --id <id> --summary "..."` to replace with summary + file reference
5. Run `persist-tools` for bulk persist of remaining tool results

### Commands

```bash
# List all tool calls with sizes
python3 <skill-dir>/scripts/stripper.py show-tool <session.jsonl> --list

# Show specific tool call with context
python3 <skill-dir>/scripts/stripper.py show-tool <session.jsonl> --id toolu_xxx --context 3

# Persist single tool result with AI summary
python3 <skill-dir>/scripts/stripper.py persist-tool <session.jsonl> --id toolu_xxx --summary "Read config.py: 50-line Flask config with DB connection string and Redis cache settings"

# Bulk persist all tool results (keep last 3)
python3 <skill-dir>/scripts/stripper.py persist-tools <session.jsonl> --keep-recent 3
```

### Thinking Persistence

Persist thinking blocks to files to reduce context while preserving the reasoning for later review.

```bash
# List all thinking blocks with sizes
python3 <skill-dir>/scripts/stripper.py show-thinking <session.jsonl>

# Show specific thinking block with context
python3 <skill-dir>/scripts/stripper.py show-thinking <session.jsonl> --pos 42 --context 3

# Persist single thinking block with summary
python3 <skill-dir>/scripts/stripper.py persist-thinking <session.jsonl> --pos 42 --summary "Decided to use content-based matching because Bun strips original paths"

# Bulk persist all thinking blocks
python3 <skill-dir>/scripts/stripper.py persist-thinkings <session.jsonl>
```

## Compact Operation

`compact --before N` splits the active chain at position N:
- Pre-cut (0 to N-1): extracted to dialogue-only summary behind compact boundary
- Post-cut (N onward): kept fully intact with fresh UUIDs and remapped chain

Three fields MUST be correct or the session breaks silently:
1. `parentUuid` -- unbroken chain to null root
2. `slug` -- same across all active messages
3. `timestamp` -- all after compact boundary

Output is a new session file. Resume with `claude -r <session-id>`.

## Key Facts

- Tool content is the #1 context consumer (~60% typical)
- Thinking blocks are sent to API and count in client-side estimates -- always strip them
- `formatTranscript()` only strips orphaned (thinking-only messages) and trailing (last message) thinking -- the rest survives
- Backups are created automatically as `.bak` files
- No external dependencies -- Python 3.8+ stdlib only

For the full technical deep-dive, see [references/surgery-report.md](references/surgery-report.md).
