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

## Fork-first: mutations never touch the session you pass

**Mutating commands write a fork by default.** The result is a new session, `<newSessionId>.jsonl` next to the original, with `forkedFrom` stamped on every record. The original is only read. Every command ends with the resume line:

```bash
python3 stripper.py strip-all <session.jsonl>
# ...
# Stripped session: ~/.claude/projects/<dir>/<newSessionId>.jsonl
# Resume it:        claude -r <newSessionId>
```

Nothing has to be swapped in, and nothing the original records later is ever discarded. The original can keep running while you strip; quit it whenever you like and `claude -r <newSessionId>`. If you kept working in the original after stripping, its newer turns are not in the fork: strip again (`--new-fork`) to include them.

Why (incident 2026-11-11): the old default wrote `<session>.jsonl.pending` and needed the CLI to quit so `apply` could swap it in. The user kept working in the session meanwhile, so the copy went stale: swapping it in would have discarded those turns, and the session never got smaller. Before that (incident 2026-08-05), in-place rewrites raced the live CLI's appends; one collapsed a 763-message chain to 14. A fork has neither problem.

Mechanics:

- **Back-to-back passes build ONE fork.** A second mutating command on the same session reuses its fork while nobody has resumed it (`analyze` → `persist-range` → `strip-all` → …). It says so, and notes if the original has grown since. A command aimed at the fork itself edits it in place, too.
- **A resumed fork is never edited.** The stripper records each fork's size and mtime after its last write, in `<fork>.jsonl.stripper.json` (outside CC's `*.jsonl` glob). Once a CLI has appended to the fork, the next command forks the original afresh. `--new-fork` always takes a fresh one.
- Dry runs never create a fork.
- No `.bak`s are written while mutating a fork: the untouched original is the backup.
- `compact` always writes a new session file anyway. `migrate-persisted` moves shared sidecar files on disk, so it keeps the in-place flow below.
- After a fork, anything keyed to the old session id is stale; for example, re-register on agent-chat in the resumed session.

### `--in-place`: keep the session id

For a session that must keep its id, `--in-place` keeps the older copy-first flow. It mutates `<session>.jsonl.pending` and swaps it over the original with one atomic rename:

```bash
python3 stripper.py strip-all <session.jsonl> --in-place   # writes <session.jsonl>.pending
python3 stripper.py apply     <session.jsonl>              # the one write to the real file
```

- Multi-pass `--in-place` strips reuse the same pending copy. `snapshot --refresh` re-snapshots.
- `apply` accepts either path (original or `.pending`). It refuses to swap when the original is **being written right now** (it grew during a 400ms settle check), when the original was **rewritten since the snapshot** (prefix-hash mismatch), or when the pending copy **fails `verify`**. Refusals leave everything in place.
- Anything appended to the original *after* the snapshot is **discarded by apply**, by design (counted and reported). In the self-strip flow that tail is the stripping turn itself.
- **`apply` refuses if someone typed messages into the session after the snapshot**: the copy is stale, and swapping it in would discard them. Slash commands, tool results and task notifications don't count, so the self-strip tail never trips this. Strip again (a fork loses nothing), or pass `--discard-tail`. `analyze` and fork-mode commands also warn when a stale copy is lying around.
- The one `.bak` of the original is made at apply time.
- A `"Prompt is too long"` session can't run anything, so its file is quiescent even if the stuck CLI is still open.

## Restarting into the stripped session (respawn)

The running CLI keeps its pre-strip context until it restarts into the stripped file. The **`respawn` skill** quits the CLI and relaunches it for a session stripping itself:

- **Fork (default):** `respawn.sh <newSessionId>`. Nothing to swap.
- **`--in-place` strip:** `respawn.sh --swap <session.jsonl>.pending`. The watcher runs `apply` after the process has died. If apply refuses, it relaunches the UNSTRIPPED session and says so in the kickoff prompt (fail-open).

This works the same for a Claude Code **background session** (`claude --bg`, or moved there with `←` / `/background`): respawn stops it through the daemon and resumes it.

Call `respawn.sh` as the LAST action, then end the turn (see the respawn skill). Stripping **another** session (not your own): strip it into a fork and hand over `claude -r <newSessionId>`; the user restarts it. Without respawn:

- **Fork, in a terminal:** `/exit`, then `claude -r <newSessionId>`.
- **`--in-place`, in a terminal:** `/exit`, `apply` from any shell, `claude -r <id>`.
- **In the background** (`claude agents` lists it, or `$CLAUDE_JOB_DIR` is set): `/exit` in an attached terminal only detaches. Run `claude stop <job>`, wait a few seconds, then `claude --bg --resume <newSessionId>` (fork), or `apply` and `claude attach <job>` (`--in-place`).

## Quick Reference

Run scripts from `<skill-dir>/scripts/`:

```bash
# Analyze session — token breakdown by type and tool name, cut points, health check
python3 <skill-dir>/scripts/stripper.py analyze <session.jsonl>

# Strip all tool content + thinking (most common — frees 60-90% of context).
# Writes a fork and prints `claude -r <newSessionId>`; the original is untouched.
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

# --in-place plumbing (keep the session id): explicit snapshot; atomic swap of the pending copy
python3 <skill-dir>/scripts/stripper.py snapshot <session.jsonl>            # optional — --in-place commands auto-create it
python3 <skill-dir>/scripts/stripper.py apply <session.jsonl>               # THE one write to the real file
python3 <skill-dir>/scripts/stripper.py apply <session.jsonl> --dry-run     # run all apply checks, swap nothing

# Fix CC's context gauge without stripping (if a session is stripped but still reads ~full)
python3 <skill-dir>/scripts/stripper.py reset-usage <session.jsonl>
```

All commands support `--dry-run` (report only) and `--no-backup` (skip backup). Range filtering via `--from N --to M` (chain positions).

### Backups are enumerated (never skipped)

`apply` (and any command that writes a real session file, e.g. mutating a `.pending` copy passed explicitly with backups on) writes a *fresh* backup: `<session>.jsonl.bak`, then `.bak.1`, `.bak.2`, … It never silently reuses or skips a backup just because one already exists. While mutating a pending copy no per-op backups are made — the untouched original is the recovery point; `apply` then creates the one real `.bak` at swap time. Pass `--no-backup` to opt out.

### The context gauge resets automatically after stripping

**CC's "context left" meter is not a live recount of the conversation — it reads the token counts (`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`) recorded on the most recent assistant turn in the JSONL.** Newer builds (CC 2.1.27x, Sep 2026) read the same three fields from the nested per-call **`usage.iterations[]`** list instead, so the reset nulls `iterations` (and zeroes `cache_creation.ephemeral_*`) on every record it touches — a stale `iterations[0].cache_read_input_tokens` alone is enough to keep a stripped session, and every fork of it, pinned at the pre-strip percentage. So stripping content alone does NOT move the meter: the stored numbers still describe the pre-strip size, the gauge stays pinned near 100%, and CC blocks new input client-side ("Context limit reached") even though the file is now tiny — the strip *looks* like it did nothing.

To fix this, `strip-tools`, `strip-thinking`, and `strip-all` now **automatically rewrite the stale `usage` counts** to the post-strip active-chain content estimate once stripping finishes. Every assistant turn whose recorded context exceeds the estimate is capped down to it (smaller turns are left alone), and the active chain's final assistant turn is pinned to the estimate — even if it recorded 0 (e.g. a blocked "Prompt is too long" turn). The next real turn re-establishes exact counts.

- Opt out with `--no-usage-reset`.
- Run it standalone with `reset-usage` on an already-stripped session whose meter is still stuck (no need to re-strip).
- The estimate counts message content **and attachments** (which CC re-sends every request). It also counts the system prompt + tool schemas when the session recorded them (`prompt_snapshot`, CC ≥ 2.1.267). Older sessions don't have that record, so for them the estimate is a floor. Either way, the first live turn corrects it precisely.

**Estimates are calibrated to what Claude Code measured.** Text is sized at chars/4, an English rule of thumb. Real sessions measured 1.18× above it (English coding) and 1.51× (Russian genealogy: archive text at ~2.35 chars/token). The newest assistant turn with real usage gives the true size of exactly the prompt it was sent. So the stripper scales its text estimate by measured ÷ estimated, taken from the untouched original before anything changes; images are already exact and are not scaled. The gauge reset, `reset-usage` and `analyze` (CONTEXT ESTIMATE section) all use it, and the strip prints `Context: <measured> measured at the last API call → ~<estimate> now`.

- **Incident 2026-11-11:** uncalibrated, `analyze` said ~650k for a session CC measured at 971,681. A strip promised ~300k and would have left ~510k.
- A measurement is real only when its usage reads or writes the prompt cache. A strip's reset zeroes those fields. So once a strip has rewritten the newest turn, the file has no calibration until its next real turn; a fork keeps the factor in its `.stripper.json` state for later commands.
- On the stripped result the factor can drift: window-cam measured 1.18 before its strip and 1.26 after. That leaves the estimate ~6% low instead of ~20%.

> **The gauge can still lie in your favour.** The reset writes an *estimate* into the JSONL. A session that was barely stripped will still show a reassuring number, then snap back on the first real turn, which is the only number ever measured. If a strip freed little, the meter dropping is not evidence that it worked. Check what `analyze` says is left, and treat a real API turn (non-zero `cache_read`/`cache_creation`) as the only ground truth. Attachments were excluded from this estimate until they were found to be worth six figures of tokens on long sessions; that omission made the gauge read far below the true prompt size.

### Preserved thinking: an edit drops every later thinking block

On Claude Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5, each thinking block's `signature` binds it to the history before it. It also binds it to the previous thinking block.

If you edit an earlier turn, the API drops every later thinking block. Claude Code runs the API in drop mode, so the request still succeeds, unbilled. The model just continues without that reasoning.

**Safe:** removing thinking blocks from the front of the history, as in `strip-thinking --from 0 --to N`.

**Drops every later thinking block:**

- clearing tool results or tool inputs
- persisting tools, text, messages or ranges
- dropping attachments
- compacting a range or the whole front
- removing a thinking block from the middle

**Every mutating command handles this for you.** The code is in `lib/preserved_thinking.py`, and `run_command` calls it after each command:

- It compares the live original with the result. It prints how many later thinking blocks the API will drop, their size, and the first chain position.
- Dry runs preview this by running the command silently on a throwaway copy.
- By default it removes those blocks from the result. The file and the context gauge then match what the model actually sees.
- `--keep-dropped-thinking` keeps them. They cost no tokens either way.
- Older models such as Opus 4.8 don't run the check, and neither does Claude Mythos 5.1. The step stays silent for them.

**Practical rule: strip once, and deeply.** Each mid-session strip wipes the reasoning built up since the previous one.

### Compact boundaries can keep earlier messages

Since CC 2.1.3xx a `/compact` can keep a run of records from **before** its boundary verbatim. The boundary (`type: system`, `subtype: compact_boundary`, `parentUuid: null`) lists them in `compactMetadata.preservedMessages` (`{anchorUuid, uuids}`) and/or `preservedSegment` (`{headUuid, anchorUuid, tailUuid}`). On every load Claude Code splices them back in after the anchor and drops the rest of the pre-boundary records. No on-disk `parentUuid` leads from the leaf to them, but they are sent on every request.

Every command walks the chain the way CC does (`compact_relink()` in `lib/chain.py`, a port of CC 2.1.322's loader). `analyze` prints a COMPACTION section with the number of preserved records.

- **Incident 2026-11-10:** a session at 913k tokens showed ~174k to `analyze`. The walk stopped at the boundary, so 3,122 preserved records (thinking, tool calls, 64 tool-result images) were invisible. `strip-all` freed almost nothing, and the gauge went back to 91% on the next turn.
- **Deleting a preserved record must update the list.** CC refuses the whole relink if one listed uuid is missing. It then drops every preserved record from the conversation, silently. `remove_objects_and_rewire()` prunes deleted uuids from the list and moves a segment's head, tail and anchor to surviving records. `verify` and `apply` treat a broken list as a hard failure. A break already present in the original is only a warning, so it cannot block every strip.
- Preserved records can predate a `/branch` and carry the parent session's slug. The slug check skips them.

## What Gets Stripped

| Target | Impact | Command |
|--------|--------|---------|
| Tool call inputs (`tool_use.input`) | 10-20% of context | `strip-tools --only-inputs` |
| Tool results (`tool_result.content`) | 40-60% of context | `strip-tools --only-results` |
| Thinking blocks | 10-20% of context | `strip-thinking` |
| Superseded attachments (system-reminders) | 5-15% of context, more on long sessions | `strip-attachments` |
| Images (pasted, or returned by a tool inside its `tool_result`) | ~1.1k tokens per typical frame, up to 4,784 each | `strip-tools` (auto) |
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

**Hook-injected context is always kept.** `hook_additional_context` (a PostToolUse/UserPromptSubmit hook's `additionalContext`) and `hook_blocking_error` (a Stop hook's block reason) are how hooks hand the model information mid-session; agent-chat delivers every peer message this way. An older policy kept only the last 2. An older cost model also sized PostToolUse context at 0, and that error is the only reason most mail survived.

**Records that build the system prompt are kept and are not messages.** `prompt_snapshot` is the system prompt + tool list Claude Code records once and reuses on every request and resume. Only the latest one counts, and it is counted once in the context estimate. `deferred_tools_record` holds the schemas of tools loaded through ToolSearch.

**Cost is exact where Claude Code stored it.** Since CC 2.1.277 attachment lines carry `rendered`: the content blocks actually sent, reused on resume instead of rendered again. `--list` and the estimates use it, and fall back to the per-type model only for older lines.

**Unknown attachment types are always kept** and sized conservatively. CC adds and renames these between releases (`total_tokens_reminder` became `token_usage`), so the policy never drops what it doesn't recognize.

## Workflow

1. **Analyze first** -- Run `analyze` to see token breakdown and identify what to strip
2. **Strip** -- Run `strip-all` for maximum savings, or use granular flags. It writes a fork; repeat passes on the same session build on that fork while nobody has resumed it, and the original is untouched
3. **Verify after** -- Run `verify` on the fork (the path in the "Stripped session:" line) to confirm chain integrity
4. **Compact if needed** -- Use `compact --before N` to summarize old work and keep recent work intact
5. **Resume** -- `claude -r <newSessionId>`, or `respawn.sh <newSessionId>` for your own live session. (`--in-place` strips: `apply <session.jsonl>`, or `respawn.sh --swap <session.jsonl>.pending`)

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
- The survivor is always rewritten as a plain **user** message (type/role `user`), whatever the range's first record was. Before 2026-09-30 it kept the first record's envelope; ranges starting at 0 usually begin on a `system/informational` (or `attachment`) record, which CC neither renders nor sends to the model — the fork resumed with the summary invisible to both Victor and the agent. Repair such an old session with `compact-range <session> --from 0 --to 0` (converts the survivor in the `.pending` copy; then `apply`).

**When to use which**:

| Use case | Tool |
|---|---|
| Drop a topical chunk (one default placeholder for all) | `compact-range` |
| Compress a topical chunk with one summary | `compact-range` |
| Persist N messages with N tailored summaries (per-message granularity) | `persist-message` per position (loop or external script) |
| Persist individual text/thinking blocks across a range, leaving message structure intact | `persist-range --kinds text,thinking` |

## Fork Mode (the default for every mutating command)

Every mutating command (`strip-*`, `persist-*`, `replace-images`, `compact-range`, `reset-usage`) runs against a **fork** of the session: a new `<newSessionId>.jsonl` next to the original, which is left untouched. Both sessions remain resumable via `claude -r`. See "Fork-first" above for reuse across passes, `--new-fork`, and `--in-place`. (`--fork` is still accepted and changes nothing.)

```bash
# Default: fork, titled "<original title> (Stripped)"
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 90 --kinds text,thinking

# Custom title for the fork
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 90 --kinds text,thinking \
    --fork-title "Polymarket-only thread (stripped)"

# Keep the session id instead (pending copy + apply)
python3 <skill-dir>/scripts/stripper.py persist-range <session.jsonl> --from 0 --to 90 --kinds text,thinking --in-place

# Standalone fork without any operation
python3 <skill-dir>/scripts/stripper.py fork <session.jsonl> --fork-title "checkpoint"
```

### What gets stamped on the fork

Matches Claude Code's `/branch` convention exactly (verified against `~/github/claude-src/commands/branch/branch.ts`):

- New `sessionId` (random UUID), written to `<project-dir>/<newSessionId>.jsonl`
- `forkedFrom = {sessionId: <original>, messageUuid: <env's original uuid>}` on every conversation envelope
- Records bound to the original session are not copied, matching CC's own `--fork-session`: `bridge-session` (its Remote Control link), `continued-in`, `cost-state`, and the `api-request*` journal
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

Images in CC JSONL are base64-inlined. A 53-image session holds megabytes of base64, but the model is charged by pixels (see Key Facts). The *information* in the images is often far smaller still (chat screenshots, UIs, memes). `replace-images` swaps each image block for a text block carrying a pre-generated transcript.

Images live in two places, and every image command handles both:

- **Pasted images:** top-level `image` blocks in a user message.
- **Images a tool returned:** Read on a `.png`/`.jpg`, screenshots, MCP image tools. These are **nested inside the `tool_result` block's `content` list**, and in a working session they are usually most of the images. `list-images`, `replace-images`, `analyze`, `strip-tools` and `persist-tools` all used to look only at top-level blocks: on 2026-11-10 `list-images` found 1 of 71 images in context.

Images are keyed by **SHA256 of their decoded bytes** — the same image in two messages hits the same transcript, and identity is unambiguous across any CC session (not tied to claude.ai export formats).

### Workflow

1. Run `list-images` to enumerate all image blocks: chain position, where each sits (`message` or `tool_result:<tool>`), dimensions, token cost, size and SHA256.
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

Each replaced image becomes a text block carrying a `<persisted-image sha256="..." media_type="...">` marker; the transcript is copied to `<sessionId>/persisted/image/<sha256>.txt`. A nested image is replaced inside its `tool_result`. Default behavior when a transcript is missing: **keep the original image** (pass `--drop-missing` to drop instead; a result or message left empty gets `""` or an `[image removed]` text block, since the API rejects empty user content).

`strip-tools` removes nested images along with their tool result. That includes `--keep-last-lines`: an image is not a line, so it is not kept.

## Compact Operation

`compact --before N` splits the active chain at position N:
- Pre-cut (0 to N-1): extracted to dialogue-only summary behind compact boundary
- Post-cut (N onward): kept fully intact with fresh UUIDs, each record chained to the previous one *in chain order*. On-disk `parentUuid` is not chain order for records an older compact boundary preserved. Older compact boundaries are not copied: CC sends only what follows the last boundary, so a copy mid-chain would hide everything before it.

Three fields MUST be correct or the session breaks silently:
1. `parentUuid` -- unbroken chain to null root
2. `slug` -- same across all active messages
3. `timestamp` -- all after compact boundary

Output is a new session file. Resume with `claude -r <session-id>`.

## Key Facts

- Tool content is the #1 context consumer (~60% typical)
- Thinking blocks are sent to API and count in client-side estimates -- always strip them
- **Image tokens use Anthropic's pixel formula, never chars/4:** `ceil(w/28) × ceil(h/28)`, after the image is downscaled to its model's tier.
  - The **high-resolution** tier covers Claude 4.7 and later: Opus 4.7+, Sonnet 5/5.5, Haiku 5.5, Fable, Mythos. It allows ≤2,576 px on the long edge and ≤4,784 tokens per image.
  - The **standard** tier covers older models. It allows ≤1,568 px and ≤1,568 tokens.
  - The tier comes from the session's model; unknown models count as high-res.
  - The old rule, `(w×h)/750` capped at 1,600, under-counted every image past ~1.2 MP by up to 3× on current models.
  - Checked against real usage on a claude-opus-5-5 session: 69 image turns each cost the formula's figure plus ~40-90 tokens of overhead. A 2325×1300 frame cost 4,040 tokens; the formula says 3,948, the old rule said 1,600.
  - chars/4 on base64 over-counts 20–100×. That matters beyond reporting: it feeds the gauge reset, and would pin the meter past the limit.
  - Implementation is in `lib/image_tokens.py`, including a port of the docs' resize reference. It parses PNG, JPEG, WebP (VP8/VP8L/VP8X) and GIF.
- `formatTranscript()` only strips orphaned (thinking-only messages) and trailing (last message) thinking -- the rest survives
- **Wrapped thinking is auto-detected.** Any text block whose whole content is a single `<thinking>…</thinking>` or `<think>…</think>` span (e.g. from `convert_to_cli.py --flatten-thinking`, or from open-source models that emit `<think>` tags) is treated as a real thinking block by `analyze`, `show-thinking`, `strip-thinking`, `strip-all`, and `persist-thinking`. No flag needed.
- Backups are created automatically and **enumerated** (`.bak`, `.bak.1`, `.bak.2`, …) — a fresh one per run, never skipped (use `--no-backup` to opt out)
- **The context gauge is driven by stored `usage` counts on the last assistant turn, not a live recount** — so stripping auto-resets those counts (or run `reset-usage`), otherwise CC keeps blocking input at the pre-strip size. This is the single most common reason a strip "didn't work."
- **`strip-thinking` alone is usually a rounding error.** Thinking is often only ~3% of a session. If a session keeps hitting the context limit right after a strip, check *which* strip ran: the `strippedBy` stamp on every forked envelope records the exact operation. A session stripped three times with `strip-thinking` has been stripped ~9%.
- **Thinking weight is mostly the encrypted `signature`, not the text.** Recent sessions store each thinking block as near-empty `thinking` text plus a signature of several thousand chars (one real session: 801 readable chars vs 104,624 signature chars across 30 blocks). `strip-thinking`, `show-thinking` and `persist-thinking(s)` size blocks as text + signature, and `analyze` sizes whole blocks. The `<persisted-thinking>` marker still advertises only the readable chars, because that is all the sidecar holds. The off-chain line of `strip-thinking` stays text-only on purpose: it reports signature-only blocks as "already emptied", since those blocks are not removed. A savings report near 0 while `analyze` shows a large thinking bucket means a pre-fix version.
- **Attachments are real context, not metadata** — CC re-expands each into a `<system-reminder>` on every request and never dedupes them. They are also `parentUuid` chain participants, so they cannot simply be deleted; `strip-attachments` re-parents their children. See the attachments section above.
- **Every write is compact JSON, mode 0600, the way CC writes.** Claude Code's fast transcript scanner matches raw bytes such as `{"parentUuid":` and `"type":"last-prompt"`. A file written with Python's default `", "`/`": "` separators looks empty to it. Incident 2026-10-25 (CC 2.1.310): after a strip, the background-session dispatcher decided there was nothing to restore. It started a fresh conversation under the same id and moved the stripped file aside as `<id>.orphaned-<ts>-<rand>.jsonl`. A plain `claude -r` still loaded the file, because it parses fully. If you find such an `.orphaned-` file, it holds the real conversation; nothing was deleted.
- **`last-prompt` records point at the resume leaf** (`leafUuid`). Removing a record re-points them at the nearest surviving ancestor, as it does for children.
- **The chain is what CC loads, not what `parentUuid` alone reaches.** Records a compact boundary preserved are part of it (see "Compact boundaries can keep earlier messages"). If `analyze` reports far less than CC's own `/context`, check its COMPACTION section first.
- **The request journal is not context.** Since CC 2.1.300, transcripts record every request (`api-request`, `api-request-blob`, `api-request-shape`; often 10-15% of the file), so a shared transcript can reproduce it. Resume rebuilds the request from the chain (verified: the first request after a swap starts with `keep: 0`), so stripping works regardless; `analyze` shows the journal as on-disk only.
- No external dependencies -- Python 3.8+ stdlib only

For the full technical deep-dive, see [references/surgery-report.md](references/surgery-report.md).
