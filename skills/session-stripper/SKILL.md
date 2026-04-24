---
name: session-stripper
description: "Manually trim, compact, persist, and repair Claude Code conversation sessions stored as JSONL files. Use when the user wants to: (1) fix a session hitting 'Prompt is too long' errors, (2) manually compact or strip a session to reduce context, (3) analyze a session's token usage and message chain, (4) strip tool call inputs and results from a session, (5) strip thinking blocks from a session, (6) compact part of a session before a specific message, (7) verify or repair a broken session chain, (8) persist and summarize tool results to reduce context intelligently, (9) perform any JSONL-level surgery on Claude Code sessions. Triggers on mentions of session surgery, session trimming, session stripping, context compaction, session repair, prompt too long, session compact, manual session manipulation, summarize tool results, or persist tool output."
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
| Images → text transcripts | Variable (often 90%+) | `replace-images` |
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

## Fork Mode (any mutating command)

Every mutating command (`strip-*`, `persist-*`, `replace-images`, `compact`, `migrate-persisted`) accepts `--fork` and `--fork-title <title>`.

When `--fork` is set, the operation runs against a **forked copy** of the session — written to a new `<newSessionId>.jsonl` next to the original — leaving the original untouched. Both sessions remain resumable via `claude -r`.

```bash
# In-place (default)
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 90 --kinds text,thinking

# Fork mode — keeps original intact
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 90 --kinds text,thinking \
    --fork --fork-title "Polymarket-only thread (stripped)"

# Standalone fork without any operation
python3 <skill-dir>/scripts/stripper.py fork <session.jsonl> --fork-title "checkpoint"
```

### What gets stamped on the fork

Matches Claude Code's `/branch` convention exactly (verified against `~/github/claude-src/commands/branch/branch.ts`):

- New `sessionId` (random UUID), written to `<project-dir>/<newSessionId>.jsonl`
- `forkedFrom = {sessionId: <original>, messageUuid: <env's original uuid>}` on every conversation envelope
- Root envelope's `parentUuid` stays `null` (CC convention; the fork pointer is `forkedFrom`)
- A `custom-title` entry appended with " (Stripped)" suffix; collisions auto-increment to "(Stripped 2)", etc. `--fork-title` overrides
- **Plus a session-stripper-specific** `strippedBy = {tool, operation, at}` field on every envelope so future tooling can show strip lineage. CC ignores unknown fields, so this doesn't break anything.

Forked sessions appear as siblings in CC's session listings; `claude -r <newId>` resumes the stripped copy, `claude -r <originalId>` resumes the original.

## Persist Family — Unified Marker + Sidecar

Every `persist-*` command takes heavy content out of the JSONL, writes the original to a sidecar file in the session's accessory directory, and replaces the block with a `<persisted-X>` marker that carries a path + size + summary + preview. The model can `Read` the path if it needs the original; the chain stays intact; nothing is destroyed.

### Layout

```
~/.claude/projects/<encoded-cwd>/<sessionId>/
├── tool-results/<tool_use_id>.txt           ← shared with CC native (kind=tool)
└── persisted/
    ├── thinking/<msg_uuid>.txt
    ├── text/<msg_uuid>_<block_idx>.txt
    ├── image/<sha256>.txt                   ← image transcripts
    └── message/<msg_uuid>.json              ← whole-message persists
```

### Marker shape

```
<persisted-{kind}>
Saved to: <relative-path> (N chars)
Summary: <user-supplied or "[no summary provided]">

Preview:
<first ~1KB of original>
</persisted-{kind}>
```

(For `kind=tool` the body uses CC's native shape: `Output too large (N). Full output saved to: PATH`.)

Paths are stored relative to the project dir (sibling of the JSONL) so the session survives if the `~/.claude/projects/` tree is moved.

### Commands

```bash
# Single text block at a chain position
python3 <skill-dir>/scripts/stripper.py persist-text <session.jsonl> --pos 47 --summary "Audit M-02 spec draft"

# Bulk text across a range, only large blocks, keep last 3
python3 <skill-dir>/scripts/stripper.py persist-texts <session.jsonl> --from 0 --to 95 --min-chars 500 --keep-recent 3

# Whole message — collapses ALL its blocks to one marker
python3 <skill-dir>/scripts/stripper.py persist-message <session.jsonl> --pos 47 --summary "Adam dialogue"

# Mixed dispatcher: persist tool, thinking, and text together over a range
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 95 \
    --kinds tool,thinking,text --min-chars 500 --keep-recent 3 \
    --summaries-file ./summaries.json

# Migrate pre-PR layouts (<image sha256> markers, .tool-results/ sidecars)
python3 <skill-dir>/scripts/stripper.py migrate-persisted <session.jsonl>
```

### Summaries file

`--summaries-file <path>` is JSON keyed by:
- `pos:N` — chain position (text, thinking, message)
- `toolu_X` — tool_use_id (tools, images)
- `msg:<uuid>` — message uuid

```json
{
  "pos:47": "Adam dialogue analysis",
  "pos:55": "Aave Pro audit M-02 spec",
  "toolu_abc123": "Bash: ran test suite, all green"
}
```

Session-stripper does not generate summaries itself (it stays stdlib-pure). The calling layer (Claude Code main loop, a wrapper script, etc.) generates summaries — typically by spawning subagents — and writes the JSON.

### Safety rules

- `persist-message` refuses the leaf message of the active chain (would orphan the resume cursor).
- `persist-message` on a `tool_use`-bearing message also persists the matching `tool_result` user message — otherwise the API rejects the resumed chain.
- Already-persisted blocks (whose text starts with `<persisted-`) are skipped on re-runs (idempotent).
- Every persist command emits via the same code path that maintains chain integrity (parentUuid unbroken, slug consistent, timestamps monotonic).

## Image Replacement

Images in CC JSONL are base64-inlined. A 53-image session can be 1M+ tokens of base64 while the *information* in the images is often an order of magnitude smaller (chat screenshots, UIs, memes). `replace-images` swaps each image block for a text block carrying a pre-generated transcript.

Images are keyed by **SHA256 of their decoded bytes** — the same image in two messages hits the same transcript, and identity is unambiguous across any CC session (not tied to claude.ai export formats).

### Workflow

1. Run `list-images` to enumerate all image blocks with their sizes and SHA256 hashes.
2. Generate one transcript per unique hash (e.g. via Claude Code subagents with Read on the source image files; Claude's vision handles PNG/JPG/WebP natively). Save each as `<transcripts-dir>/<sha256>.txt` (UTF-8).
3. Run `replace-images --dir <transcripts-dir>` to perform the surgery.

```bash
# Enumerate
python3 <skill-dir>/scripts/stripper.py list-images <session.jsonl>

# Replace (dry-run first)
python3 <skill-dir>/scripts/stripper.py replace-images <session.jsonl> --dir ./image-transcripts --dry-run
python3 <skill-dir>/scripts/stripper.py replace-images <session.jsonl> --dir ./image-transcripts

# Drop images without a transcript instead of keeping them
python3 <skill-dir>/scripts/stripper.py replace-images <session.jsonl> --dir ./image-transcripts --drop-missing
```

Each replaced image becomes a text block: `<image sha256="..." media_type="...">\n{transcript}\n</image>`. Default behavior when a transcript is missing: **keep the original image** (pass `--drop-missing` to drop instead).

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
- **Wrapped thinking is auto-detected.** Any text block whose whole content is a single `<thinking>…</thinking>` or `<think>…</think>` span (e.g. from `convert_to_cli.py --flatten-thinking`, or from open-source models that emit `<think>` tags) is treated as a real thinking block by `analyze`, `show-thinking`, `strip-thinking`, `strip-all`, and `persist-thinking`. No flag needed.
- Backups are created automatically as `.bak` files
- No external dependencies -- Python 3.8+ stdlib only

For the full technical deep-dive, see [references/surgery-report.md](references/surgery-report.md).
