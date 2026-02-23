---
name: ai-conversation-extractor
description: "Convert AI conversation JSONL transcripts (Claude Code, Codex CLI, ChatGPT) to readable Markdown. Use when the user wants to: (1) convert .jsonl conversation transcripts to .md, (2) strip binary/base64 data from AI conversation logs, (3) make AI conversation history human-readable, (4) process session exports from ~/.claude/ or ~/.codex/, or (5) batch-convert a folder of conversation JSONL files. Triggers on mentions of JSONL conversations, transcript conversion, conversation export, or readable conversation logs."
---

# ai-conversation-extractor

Convert AI conversation JSONL transcripts (Claude Code, Codex CLI, ChatGPT/Gemini) to clean, readable Markdown files. Strips binary blobs (base64 images, PDFs — typically 95%+ of file size) while preserving all human-readable content: user messages, assistant text, thinking, tool calls, and tool results.

See `scripts/README.md` for full JSONL format documentation.

## Supported Formats

Auto-detected from file content — no flags needed:

| Format | Source | Typical location |
|--------|--------|------------------|
| **Claude Code** | Claude Code CLI sessions | `~/.claude/projects/*/*.jsonl` |
| **Codex CLI** | OpenAI Codex CLI sessions (event stream with `timestamp`/`type`/`payload`) | `~/.codex/sessions/*.jsonl` |
| **Codex history** | Codex CLI prompt history (`session_id`/`ts`/`text`) — user prompts only | `~/.codex/history.jsonl` |
| **Simple** | ChatGPT / Gemini exports (`role`/`message`) | varies |

## Usage

Run the script at `scripts/extract.py` (within this skill's directory):

```bash
# Single file — outputs .md alongside the .jsonl
python3 <skill-dir>/scripts/extract.py <file.jsonl>

# Single file with custom output path
python3 <skill-dir>/scripts/extract.py <file.jsonl> -o output.md

# All .jsonl in a directory
python3 <skill-dir>/scripts/extract.py <directory>

# Recursive (includes subdirectories)
python3 <skill-dir>/scripts/extract.py <directory> --recursive

# Messages-only view (user + final assistant response per turn, no tools/thinking)
python3 <skill-dir>/scripts/extract.py <file.jsonl> --ua-final-only

# Batch convert to a specific output directory
python3 <skill-dir>/scripts/extract.py <directory> --out-dir ./converted/

# Keep Codex environment context and boilerplate in messages-only mode
python3 <skill-dir>/scripts/extract.py <file.jsonl> --ua-final-only --keep-env-context --keep-agent-boilerplate
```

No external dependencies — uses only Python 3.12+ stdlib (`json`, `re`, `pathlib`, `argparse`).

### CLI Flags

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Custom output path (single-file mode only) |
| `-r`, `--recursive` | Recurse into subdirectories |
| `--ua-final-only` | Messages-only view: keeps user messages and final assistant response per turn, drops tool calls/results/thinking |
| `--out-dir` | Output directory for batch mode (writes `<stem>.md` into this folder) |
| `--keep-env-context` | With `--ua-final-only`: retain Codex `<environment_context>` user messages (dropped by default) |
| `--keep-agent-boilerplate` | With `--ua-final-only`: retain Codex AGENTS.md/skills boilerplate messages (dropped by default) |

## What Gets Stripped

- Base64-encoded images and documents (biggest savings)
- `<system-reminder>` tags injected by the tool
- `file-history-snapshot` records (editor state backups)
- `progress` records (hook events)
- `system` records (turn durations, metadata)
- Meta messages (`isMeta: true`, like `/init` injections)
- Line-number prefixes from file reads (`   123→`)

## What Gets Preserved

- User text messages (full)
- Assistant text responses (full)
- Assistant thinking blocks (truncated at 3000 chars, in `<details>` tags)
- Tool calls (name + JSON input, truncated at 4000 chars)
- Tool results (text content, full; no truncation)
- Image/document placeholders (e.g., `*[attached document: application/pdf]*`)
- Error results from rejected tool uses
- Per-message timestamps (Claude Code format only)
- Source file metadata (YAML frontmatter) and filesystem mtime (output .md matches source .jsonl mtime for natural sort-by-date)

## Output Format

- **YAML frontmatter**: `source`, `modified` (ISO 8601) for parseable metadata
- **Filesystem**: Output .md file's modification time is set to match the source .jsonl, so `ls -t` or Finder sort-by-date preserves conversation order
- Markdown with `## User` / `## Assistant` headings separated by `---`. Per-message timestamps shown as `*2026-02-09T23:51:07Z*` when available (Claude Code format). Tool calls formatted as `**Tool: \`Name\`**` with JSON code blocks. Tool results as `**Result** (\`id\`):` with code blocks.
