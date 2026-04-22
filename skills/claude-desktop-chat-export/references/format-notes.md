# claude.ai ↔ Claude Code format notes

Read this when the converter fails, when debugging a corrupted resumed session, or when the user asks *why* something was stripped.

## Contents
- [Engine vs. harness](#engine-vs-harness)
- [File layouts](#file-layouts)
- [How CC handles images and attachments natively](#how-cc-handles-images-and-attachments-natively)
- [Gotchas](#gotchas-every-one-bit-us-in-production)
- [Chain integrity requirements](#chain-integrity-requirements)
- [What does and doesn't survive](#what-doesnt-survive-the-import)

## Engine vs. harness

Claude Code CLI and claude.ai/Claude Desktop both hit the same Opus/Sonnet/Haiku models via the same API — the model is **identical**. What differs is the **harness**: system prompt, declared tools, output-length rules. Long discursive conversations are possible in CC — just say so, or put it in `CLAUDE.md`.

## File layouts

**claude.ai JSON** (single file from `/api/organizations/<org>/chat_conversations/<uuid>`):
- Root: `uuid`, `name`, `model`, `created_at`, `chat_messages[]`
- Each message: `uuid`, `parent_message_uuid`, `sender: 'human'|'assistant'`, `created_at`, `content[]` (blocks), `attachments[]` (text with `extracted_content`), `files[]` (image + blob metadata only)

**Claude Code CLI JSONL** (one envelope per line at `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`):
- Per line: `parentUuid`, `uuid`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `version`, `isSidechain`, `userType`, `slug`, `type: 'user'|'assistant'|'system'`, `message: {role, content}`
- Meta line types also appear: `queue-operation`, `last-prompt`, `file-history-snapshot`, `compact_boundary`, `permission-mode`

### `~/.claude/` directory layout

Per-session data lives in two parallel places, keyed by `sessionId`:

- `projects/<encoded-cwd>/<sessionId>.jsonl` — the conversation itself
- `projects/<encoded-cwd>/<sessionId>/` — accessories:
  - `subagents/agent-<id>.jsonl` + `.meta.json` — sidechain (subagent) sessions
  - `tool-results/toolu_<id>.txt` — persisted large tool outputs
- `file-history/<sessionId>/<contenthash>@v<n>` — Edit/Write snapshot backups (content-addressed, versioned)
- `todos/<sessionId>-agent-<id>.json` — TODO state
- `session-env/<sessionId>/` — per-session env
- `tasks/<taskId>/` — Task tool state
- `paste-cache/<hash>.txt` — ephemeral staging for pastes
- `sessions/<pid>.json` — running-PID → sessionId map

Global: `history.jsonl` (deduped prompt history), `settings.json`, `statusline-command.sh`, `skills/`, `plugins/`.

## How CC handles images and attachments natively

- **Images**: fully self-contained in the JSONL line as `{type:"image", source:{type:"base64", media_type:"image/png", data:"<base64>"}}`. No external file reference.
- **Text pastes and attachments**: CC has **no attachment concept**. Dropping a file into the prompt triggers a Read tool call; pasted text goes straight into the user message as plain text.

That's why the converter inlines claude.ai attachments as `<attachment name="…">…</attachment>` text blocks and claude.ai images as base64 image blocks — it mirrors CC's native shape.

## Gotchas (every one bit us in production)

1. **Tool names collide.** claude.ai uses `view`, `web_fetch`, `web_search`, `bash_tool`, `memory_user_edits`; CC declares `Bash`, `Read`, `Edit`, `Grep`, etc. When CC resumes and the API sees a `tool_use` referencing an undeclared tool, it rejects with `"tool use concurrency issues"`. **The converter flattens tool_use/tool_result to text by default.**

2. **Thinking block signatures don't transfer.** They're cryptographic HMACs tied to the original API context. Imported ones fail with `"Invalid signature in thinking block"`. **The converter drops thinking blocks entirely.** (Text still visible in `conversation.md` for reading.)

3. **Empty text blocks fail.** API requires text blocks non-empty. claude.ai sometimes emits `{type:"text", text:""}` at the end of an assistant turn with only tool_use/tool_result. **Converter filters empty text.**

4. **Lone-thinking messages fail.** An assistant message with only thinking blocks is rejected. **Converter drops orphaned-thinking turns.**

5. **Extra fields in `tool_result.content` items.** claude.ai attaches `uuid`, `citations`, `start_timestamp` to each item. API rejects with `"Extra inputs are not permitted"`. **Converter whitelists API-valid keys.**

6. **Interleaved tool_use/tool_result.** claude.ai packs `[thinking, text, tool_use, tool_result, thinking, tool_use, tool_result]` into one assistant message. The API requires strict alternation (`assistant[tool_use+] / user[tool_result+] / assistant...`). **With `--keep-tools`, converter splits a single claude.ai turn into multiple CC envelopes.** When flattening (default), everything becomes text and stays in one turn.

7. **UUIDs regenerate on every run.** Each `convert_to_cli.py` invocation produces fresh random UUIDs for every message (only `sessionId` is stable via `--session-id`). **If a CC session was open during the previous conversion, quit it before re-running** — CC's in-memory state points to now-deleted UUIDs otherwise, and the chain breaks silently.

8. **CC appends to the JSONL as you interact.** `queue-operation`, `last-prompt`, `permission-mode`, `file-history-snapshot`, error envelopes. If you hit an error and re-convert without quitting CC, the file gets mixed. Recovery: quit CC, re-run converter (overwrites cleanly).

## Chain integrity requirements

For CC to show and resume the session, three invariants must hold:

1. **`parentUuid` chain unbroken** from the last message back to `null`.
2. **`slug` identical** across every envelope in the active segment.
3. **Timestamps monotonically non-decreasing.**

The converter enforces all three and `assert`s before writing.

## What doesn't survive the import

- Original image formats (claude.ai only exposes webp previews — no JPEG/PNG originals).
- Thinking block content (dropped; visible only in `conversation.md`).
- Tool_use/tool_result structural semantics (flattened to text by default).
- Blob file content that was never viewed by the assistant during the chat — the API has no download endpoint for those. But: they're usually already in the repo, and a `FILES.md` index can point to them.

## What does survive

- Every user and assistant text message, in order, with original timestamps.
- Every text attachment (inlined).
- Every image (as webp base64 inside the relevant user message).
- Assistant tool calls and results as readable text (so the model retains context of what was looked up).
- The original claude.ai conversation UUID as the CC sessionId (if `--session-id` is passed).
