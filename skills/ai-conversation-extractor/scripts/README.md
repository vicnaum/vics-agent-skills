# Claude Code JSONL Format Reference

Claude Code stores conversation transcripts as JSONL files — one JSON object per line. Each line is a **record** with a `type` field that determines its structure.

Sources: [ccrider schema](https://github.com/neilberkman/ccrider/blob/main/research/schema.md), [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts), [deepwiki guide](https://deepwiki.com/FlorianBruniaux/claude-code-ultimate-guide/4.4-the-.claude-folder-structure).

---

## File Locations

```
~/.claude/
├── history.jsonl                              # Global command history
├── settings.json                              # Global settings (cleanupPeriodDays, etc.)
├── CLAUDE.md                                  # Global personal memory
└── projects/
    └── <encoded-project-path>/                # e.g. -Users-neil-xuku-invoice
        ├── <sessionId>.jsonl                  # Main session transcripts
        ├── agent-<shortId>.jsonl              # Subagent session transcripts
        ├── CLAUDE.md                          # Project-local memory
        └── memory/
            └── MEMORY.md                      # Persistent project memory
```

- **Session IDs** are UUIDs (e.g., `859cd961-d542-49dc-926f-6cd2c9d55be4`)
- **Project path encoding**: `/Users/neil/xuku/invoice` → `-Users-neil-xuku-invoice`
- **Log retention**: 30 days by default. Set `"cleanupPeriodDays": 99999` in `~/.claude/settings.json` to keep longer.

---

## Record Types

### 1. `summary` — Session Title (First Line)

Always the first line of a session file. Contains the generated session title.

```json
{
  "type": "summary",
  "summary": "Human-readable session title",
  "leafUuid": "uuid-of-final-message"
}
```

| Field | Description |
|-------|-------------|
| `summary` | Generated title describing the session |
| `leafUuid` | UUID of the last message in the conversation (or null) |

> **Note:** Older session files may lack a summary line and start directly with `file-history-snapshot` or `user` records.

### 2. `file-history-snapshot` — Editor State Backup

Tracks which files were modified and their backup versions. Appears at session start and after file modifications.

```json
{
  "type": "file-history-snapshot",
  "messageId": "<uuid>",
  "snapshot": {
    "messageId": "<uuid>",
    "trackedFileBackups": {
      "src/detect.py": {
        "backupFileName": "43435dffea7b8ba3@v5",
        "version": 5,
        "backupTime": "2026-02-09T23:51:07.948Z"
      }
    },
    "timestamp": "2026-02-09T23:51:07.948Z"
  },
  "isSnapshotUpdate": false
}
```

### 3. `user` — User Messages & Tool Results

User-typed messages and tool results returned to the assistant.

#### Common Envelope Fields

Present on all `user` and `assistant` records:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `"user"` or `"assistant"` |
| `uuid` | string | This record's UUID |
| `parentUuid` | string\|null | Parent message UUID (null for first) |
| `timestamp` | string | ISO 8601 |
| `sessionId` | string | Session UUID (matches filename) |
| `cwd` | string | Working directory |
| `version` | string | Claude Code version (e.g., `"2.1.37"`) |
| `gitBranch` | string | Current git branch |
| `slug` | string | Session slug (e.g., `"lazy-finding-token"`) |
| `isSidechain` | boolean | Branched conversation |
| `isMeta` | boolean | Auto-injected message (e.g., `/init` instructions) |
| `userType` | string | Usually `"external"` |

#### Plain text content

```json
{
  "type": "user",
  "message": { "role": "user", "content": "Please read this file" }
}
```

#### Structured content blocks

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      { "type": "text", "text": "Here's my question..." },
      { "type": "image", "source": { "type": "base64", "media_type": "image/png", "data": "..." } },
      { "type": "document", "source": { "type": "base64", "media_type": "application/pdf", "data": "..." } }
    ]
  }
}
```

#### Tool result (largest records — can be megabytes)

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [{
      "type": "tool_result",
      "tool_use_id": "toolu_01GDkdQqCUmNQihyJQJWW6R2",
      "content": "File created successfully at: /path/to/file.py",
      "is_error": false
    }]
  },
  "toolUseResult": { ... },
  "sourceToolAssistantUUID": "<uuid>"
}
```

The `content` inside `tool_result` can be a **string** or a **list** of content blocks (text, image, document).

#### `toolUseResult` variants

The top-level `toolUseResult` field carries structured metadata. Format varies by tool:

**File read:**
```json
{ "type": "text", "file": { "filePath": "...", "content": "...", "numLines": 154, "startLine": 1, "totalLines": 154 } }
```

**Shell command (Bash):**
```json
{ "stdout": "output...", "stderr": "", "interrupted": false, "isImage": false }
```

**File write (Write/Create):**
```json
{ "type": "create", "filePath": "...", "content": "...", "structuredPatch": [], "originalFile": null }
```

**Glob:**
```json
{ "filenames": ["/path/a.py", "/path/b.py"], "durationMs": 538, "numFiles": 2, "truncated": false }
```

**WebFetch:**
```json
{ "bytes": 12345, "code": 200, "codeText": "OK", "result": "...", "durationMs": 450, "url": "..." }
```

**TodoWrite:**
```json
{ "oldTodos": [...], "newTodos": [...] }
```

#### Optional user fields

| Field | Description |
|-------|-------------|
| `thinkingMetadata` | `{ "level": "high", "disabled": false, "triggers": [] }` |
| `planContent` | Full plan markdown (when exiting plan mode) |
| `todos` | Current todo list state |
| `permissionMode` | e.g. `"bypassPermissions"` |

### 4. `assistant` — Assistant Messages

Follows the Anthropic Messages API format.

```json
{
  "type": "assistant",
  "message": {
    "model": "claude-opus-4-6",
    "id": "msg_01Qkv88sj4UQBCuFueGbZnbe",
    "type": "message",
    "role": "assistant",
    "content": [ ... ],
    "stop_reason": "end_turn",
    "usage": {
      "input_tokens": 3,
      "cache_creation_input_tokens": 5398,
      "cache_read_input_tokens": 15436,
      "output_tokens": 25,
      "service_tier": "standard"
    }
  },
  "requestId": "req_011CXyPjbCramHhoo564y7si"
}
```

`stop_reason`: `"end_turn"`, `"max_tokens"`, `null` (while streaming).

#### Content block types

**`text`** — Visible response:
```json
{ "type": "text", "text": "I'll start by exploring the codebase." }
```

**`thinking`** — Internal reasoning (extended thinking):
```json
{ "type": "thinking", "thinking": "The user wants me to...", "signature": "EvECCkYICxgCKkD..." }
```

**`tool_use`** — Tool invocation:
```json
{ "type": "tool_use", "id": "toolu_01GDkd...", "name": "Read", "input": { "file_path": "/path/to/file" } }
```

Common tool names: `Read`, `Write`, `Glob`, `Grep`, `Bash`, `Task`, `TodoWrite`, `ExitPlanMode`, `WebFetch`, `WebSearch`.

#### Streaming: one response = multiple JSONL lines

A single assistant turn is streamed as multiple records sharing the same `message.id`:

1. Line with `text` block
2. Line with `tool_use` block
3. (User lines with `tool_result` blocks return)
4. More assistant lines continuing the response

### 5. `progress` — Hook Events

Internal notifications. Not conversation content.

```json
{
  "type": "progress",
  "data": {
    "type": "hook_progress",
    "hookEvent": "PostToolUse",
    "hookName": "PostToolUse:Read",
    "command": "callback"
  },
  "parentToolUseID": "toolu_019CB9...",
  "toolUseID": "toolu_019CB9..."
}
```

### 6. `system` — System Metadata

Turn duration, local commands, status updates.

**Turn duration:**
```json
{ "type": "system", "subtype": "turn_duration", "durationMs": 44534 }
```

**Local command (e.g., `/resume`, `/compact`):**
```json
{ "type": "system", "subtype": "local_command", "content": "<command-name>/compact</command-name>", "level": "info" }
```

---

## Message Threading

Messages form a tree via `parentUuid` → `uuid` links. The main conversation is a linear chain; `isSidechain: true` marks branched explorations.

To reconstruct order: start at `parentUuid: null`, follow the chain. The summary's `leafUuid` points to the final message.

## Session Continuation

When a session is resumed (`/resume`), messages append to the **same** `.jsonl` file with the same `sessionId`.

## Agent Sessions

Subagent transcripts use `agent-<shortId>.jsonl` naming. Same message format, but `isSidechain` is typically `true`.

---

## Size Profile (typical long session)

| Category | % of file | Description |
|----------|-----------|-------------|
| Base64 blobs | ~95% | Images/PDFs in tool results and document blocks |
| Tool result text | ~2% | Shell output, file contents (plain text) |
| Envelope metadata | ~2% | UUIDs, timestamps, usage stats |
| Thinking | <1% | Extended thinking blocks + signatures |
| Visible text | <1% | User messages + assistant responses |
| Tool calls | <1% | Tool name + JSON input |

---

## History File

`~/.claude/history.jsonl` — global command history (not a conversation):

```json
{ "display": "the command text", "pastedContents": {}, "timestamp": 1759022024295, "project": "/Users/neil/project" }
```

---

## Related Tools

- **[claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)** — converts JSONL to paginated HTML (supports local + web sessions)
- **[ccrider](https://github.com/neilberkman/ccrider)** — schema research + session analysis
- **[conversation-logger](https://github.com/sirkitree/conversation-logger)** — MCP-based saving/searching of transcripts
