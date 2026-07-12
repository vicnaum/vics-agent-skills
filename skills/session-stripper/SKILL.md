---
name: session-stripper
description: "Manually trim, compact, persist, and repair Claude Code conversation sessions stored as JSONL files. Use when the user wants to: (1) fix a session hitting 'Prompt is too long' errors, (2) manually compact or strip a session to reduce context, (3) analyze a session's token usage and message chain, (4) strip tool call inputs and results from a session, (5) strip thinking blocks from a session, (6) compact part of a session before a specific message, (7) verify or repair a broken session chain, (8) persist and summarize tool results to reduce context intelligently, (9) perform any JSONL-level surgery on Claude Code sessions. Triggers on mentions of session surgery, session trimming, session stripping, context compaction, session repair, prompt too long, session compact, manual session manipulation, summarize tool results, or persist tool output."
---

# Session Surgery

CLI tool for trimming Claude Code JSONL sessions. Sessions live at `~/.claude/projects/<project-path>/<uuid>.jsonl`.

## Identify the session FIRST — never guess by mtime

Every mutating command operates on the exact `<session.jsonl>` path you pass. Getting that path right is the single most important step: strip the wrong file and you silently corrupt a different, possibly active, session.

**To strip the CURRENT session, resolve it from `$CLAUDE_CODE_SESSION_ID`** — the env var Claude Code exports into every Bash tool call, which matches the real transcript filename:

```bash
SKILL=<skill-dir>/scripts/stripper.py
SESS=$(python3 "$SKILL" current --quiet)   # resolves $CLAUDE_CODE_SESSION_ID → its .jsonl (any cwd)
python3 "$SKILL" strip-all "$SESS"
```

`current` (without `--quiet`) prints the id, path, and whether it exists.

**NEVER select the session with `ls -t ... | head -1` (most-recently-modified).** In a project folder with more than one session running — increasingly common with parallel/multi-agent work — the newest file is frequently a *different* live session, and you will strip someone else's work. `$CLAUDE_CODE_SESSION_ID` is the only reliable source; if it is unset you are not inside a CC session, so ask the user which file to strip rather than guessing.

When the user names a *specific* session id or file (e.g. "strip session abc123"), use that instead; `current` is only for "strip *this*/*my* session".

## Restarting into the stripped session (respawn)

Stripping shrinks the file on disk, but the running CLI keeps its pre-strip context until it restarts. If the user implies they want to continue in the lighter session ("strip and continue", "strip and reload", "strip into a fork and respawn"), and the **`respawn` skill** is available, use it to relaunch the CLI:

- **In-place strip** (same session id): `respawn.sh` with no args resumes the current session.
- **Forked strip** (new session id from `--fork`): pass that new id — `respawn.sh <new-session-id>`.

Call `respawn.sh` as the LAST action, then end the turn (see the respawn skill). Without respawn, tell the user to `/exit` and `claude -r <id>` manually — the strip won't take effect until the CLI reloads.

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

# Drop superseded attachments (system-reminders re-sent on EVERY request)
python3 <skill-dir>/scripts/stripper.py strip-attachments <session.jsonl> --list   # what's there
python3 <skill-dir>/scripts/stripper.py strip-attachments <session.jsonl>

# Compact everything before chain position 150
python3 <skill-dir>/scripts/stripper.py compact <session.jsonl> --before 150

# Verify chain integrity
python3 <skill-dir>/scripts/stripper.py verify <session.jsonl>

# Fix CC's context gauge without stripping (if a session is stripped but still reads ~full)
python3 <skill-dir>/scripts/stripper.py reset-usage <session.jsonl>
```

All commands support `--dry-run` (report only) and `--no-backup` (skip backup). Range filtering via `--from N --to M` (chain positions).

### Backups are enumerated (never skipped)

Every mutating run that creates a backup writes a *fresh* one: `<session>.jsonl.bak`, then `.bak.1`, `.bak.2`, … It never silently reuses or skips a backup just because one already exists. (The old single-`.bak` scheme would skip the backup when a stale `.bak` was present — letting a strip run with no recovery point. That footgun is gone.) Pass `--no-backup` to opt out.

### The context gauge resets automatically after stripping

**CC's "context left" meter is not a live recount of the conversation — it reads the token counts (`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`) recorded on the most recent assistant turn in the JSONL.** So stripping content alone does NOT move the meter: the stored numbers still describe the pre-strip size, the gauge stays pinned near 100%, and CC blocks new input client-side ("Context limit reached") even though the file is now tiny — the strip *looks* like it did nothing.

To fix this, `strip-tools`, `strip-thinking`, and `strip-all` now **automatically rewrite the stale `usage` counts** to the post-strip active-chain content estimate once stripping finishes. Every assistant turn whose recorded context exceeds the estimate is capped down to it (smaller turns are left alone), and the active chain's final assistant turn is pinned to the estimate — even if it recorded 0 (e.g. a blocked "Prompt is too long" turn). The next real turn re-establishes exact counts.

- Opt out with `--no-usage-reset`.
- Run it standalone with `reset-usage` on an already-stripped session whose meter is still stuck (no need to re-strip). It also accepts `--fork`.
- The estimate counts message content **and attachments** (which CC re-sends every request). It still excludes the runtime system prompt + tool schemas (added at send time, unknowable offline), so it's a floor — the first live turn corrects it precisely.

> **The gauge can only lie in your favour.** Because the reset writes an *estimate* into the JSONL, a session that was barely stripped will still show a reassuring number — and then snap back to ~100% on the first real turn, which is the only number that was ever measured. If a strip freed little, the meter dropping is not evidence that it worked. Check what `analyze` says is left, and treat a real API turn (one with non-zero `cache_read`/`cache_creation`) as the only ground truth. Attachments were excluded from this estimate until they were found to be worth six figures of tokens on long sessions — that omission made the gauge read far below the true prompt size.

## What Gets Stripped

| Target | Impact | Command |
|--------|--------|---------|
| Tool call inputs (`tool_use.input`) | 10-20% of context | `strip-tools --only-inputs` |
| Tool results (`tool_result.content`) | 40-60% of context | `strip-tools --only-results` |
| Thinking blocks | 10-20% of context | `strip-thinking` |
| Superseded attachments (system-reminders) | 5-15% of context, more on long sessions | `strip-attachments` |
| Images | Variable (can be 50%+) | `strip-tools` (auto) |
| Images → text transcripts | Variable (often 90%+) | `replace-images` |
| Everything above | 60-90% of context | `strip-all` |

## Attachments — the context nobody counts

`attachment` lines look like transcript metadata. They are not: **CC re-expands every one of them into a `<system-reminder>` on every single request, forever, and nothing dedupes them.**

- `utils/messages.ts::reorderAttachmentsForAPI()` puts them in the API message array.
- `normalizeAttachmentForAPI()` expands each into user content at request-build time.
- `utils/conversationRecovery.ts` restores them from the JSONL on resume.

A long session accumulates hundreds. One real example: 217 `task_reminder` snapshots (of which 182 were byte-identical consecutive repeats), 1,276 `total_tokens_reminder`s, 523 `hook_success` echoes — **~108k tokens re-sent on every turn**, none of it useful.

`strip-attachments` keeps the newest of each kind and drops the superseded rest.

### Three traps this command handles for you

1. **Attachments are chain participants.** CC's `isChainParticipant()` excludes only `progress` — real messages chain *through* attachments (`att → att → user → assistant`). CC resumes by walking `parentUuid` back from the leaf and **stops dead at the first missing uuid**, silently dropping everything older. So attachment lines can't just be deleted; every child is re-parented to its nearest surviving ancestor (exactly how CC's own `progressBridge` handled removing `progress`). Naive deletion leaves a file that parses fine and loses half the conversation.

2. **Some types re-emit if you delete them.** `deferred_tools_delta` / `agent_listing_delta` / `mcp_instructions_delta` compute their delta by replaying the prior delta attachments still in the array. Delete them and CC considers every tool new again, re-emitting a full-size announcement next turn — you get the tokens back, plus a duplicate. Same for `skill_listing`, which sets a `suppressNextSkillListing()` latch on resume. **These are `keep all` / `keep last 1` in the default policy and should stay that way.**

   **`queued_command` is the nastiest of these, and it looks completely disposable.** It is not — it holds two irreplaceable things. First, **human input**: when the user types while a turn is still running, CC drains that input mid-turn and stores it *only* as a `queued_command` attachment (`commandMode: 'prompt'`) — there is no corresponding `user` message line, so the attachment is the sole copy of something the user actually said. Second, **background-task completion records** (`commandMode: 'task-notification'`): delete them and the next resume announces that every background shell has "no completion record" and marks them stopped. An earlier version of this policy kept "the last 2" of this type and silently deleted 9 real user messages from a live session. It is now `keep all`, and it is cheap (~7k tokens on a 4,000-message session).

3. **Rendered cost ≠ record size.** A `task_reminder` record carries full task objects including long `description` fields, but the renderer emits only `#{id}. [{status}] {subject}` — descriptions are never sent. Sizing the JSON record over-counts these ~4×. `--list` reports what is actually *rendered*.

Some types render to `[]` (most `hook_success` — only `SessionStart`/`UserPromptSubmit` hooks are sent). Dropping those saves zero context, so the default run skips them rather than rewiring the chain for nothing; `--include-free` drops them anyway to shrink the file.

```bash
# See what's there and what the policy would do — changes nothing
stripper strip-attachments <session.jsonl> --list

# Apply the default policy (keep newest of each kind)
stripper strip-attachments <session.jsonl>

# Only touch specific types / keep more history
stripper strip-attachments <session.jsonl> --types task_reminder,hook_success --keep-recent 3

# Escape hatch: drop EVERY attachment of the named types, ignoring keep-all.
# Requires --types precisely because it can remove the capability attachments
# that tell the model which tools and skills exist.
stripper strip-attachments <session.jsonl> --types skill_listing --drop-all
```

**Unknown attachment types are always kept** and sized conservatively. CC adds and renames these between releases (`total_tokens_reminder` became `token_usage`), so the policy never drops what it doesn't recognize.

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

## Compact a Range — One Summary for Many Messages

When a topical chunk of the conversation is closed and you want to subsume the whole chunk under a single summary, use `compact-range`. It collapses N consecutive messages into **one survivor** carrying a `<persisted-range>` marker; the originals are saved one-per-file to the sidecar dir, fully recoverable.

```bash
# Drop a chunk as irrelevant (uses default placeholder summary)
stripper compact-range <session.jsonl> --from 10 --to 30

# Compress a chunk under a tailored summary
stripper compact-range <session.jsonl> --from 46 --to 77 \
    --summary "Certora audit done in 4 days; 21 issues; AAVE-3813/14/15 (M-02), AAVE-3866 (M-07)"

# Multi-arc cleanup: one invocation per range (each runs in <1s)
stripper compact-range ... --from 10 --to 30 --summary "..." --fork --fork-title "scope-X"
stripper compact-range ... --from 32 --to 45 --summary "..."
```

**Marker shape**:
```
<persisted-range from="A" to="B" count="N">
Saved to: <sessionId>/persisted/message (N files)
Summary: ...

Preview:
[role] first message excerpt
[role] second message excerpt
[... N-K more]
</persisted-range>
```

**Safety**:
- Refuses if the range includes the leaf message (would orphan resume).
- Refuses if the range contains a `tool_use` whose matching `tool_result` lives outside the range (or vice versa).
- Idempotent: re-running on a survivor that already carries a `<persisted-range>` marker is a no-op.

**When to use which**:

| Use case | Tool |
|---|---|
| Drop a topical chunk (one default placeholder for all) | `compact-range` |
| Compress a topical chunk with one summary | `compact-range` |
| Persist N messages with N tailored summaries (per-message granularity) | `persist-message` per position (loop or external script) |
| Persist individual text/thinking blocks across a range, leaving message structure intact | `persist-range --kinds text,thinking` |

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

Forked sessions appear as siblings in CC's session listings; `claude -r <newId>` resumes the stripped copy, `claude -r <originalId>` resumes the original. To resume the fork automatically instead of by hand, use the **`respawn` skill** with the new id: `respawn.sh <newId>` (see "Restarting into the stripped session" above).

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
- **Image tokens are computed via Anthropic's `(w×h)/750` formula (capped at 1600), not chars/4.** A typical screenshot is 380–1600 tokens regardless of base64 byte size — the chars/4 heuristic over-counts images by 50–100×. `analyze` shows the corrected number and notes the discrepancy. Implementation in `lib/image_tokens.py`; supports PNG, JPEG, WebP (VP8/VP8L/VP8X), GIF.
- `formatTranscript()` only strips orphaned (thinking-only messages) and trailing (last message) thinking -- the rest survives
- **Wrapped thinking is auto-detected.** Any text block whose whole content is a single `<thinking>…</thinking>` or `<think>…</think>` span (e.g. from `convert_to_cli.py --flatten-thinking`, or from open-source models that emit `<think>` tags) is treated as a real thinking block by `analyze`, `show-thinking`, `strip-thinking`, `strip-all`, and `persist-thinking`. No flag needed.
- Backups are created automatically and **enumerated** (`.bak`, `.bak.1`, `.bak.2`, …) — a fresh one per run, never skipped (use `--no-backup` to opt out)
- **The context gauge is driven by stored `usage` counts on the last assistant turn, not a live recount** — so stripping auto-resets those counts (or run `reset-usage`), otherwise CC keeps blocking input at the pre-strip size. This is the single most common reason a strip "didn't work."
- **`strip-thinking` alone is usually a rounding error.** Thinking is often only ~3% of a session. If a session keeps hitting the context limit right after a strip, check *which* strip ran: the `strippedBy` stamp on every forked envelope records the exact operation. A session stripped three times with `strip-thinking` has been stripped ~9%.
- **Attachments are real context, not metadata** — CC re-expands each into a `<system-reminder>` on every request and never dedupes them. They are also `parentUuid` chain participants, so they cannot simply be deleted; `strip-attachments` re-parents their children. See the attachments section above.
- No external dependencies -- Python 3.8+ stdlib only

For the full technical deep-dive, see [references/surgery-report.md](references/surgery-report.md).
