---
name: ai-conversation-extractor
description: Convert Claude Code conversation JSONL transcript files to readable Markdown. Use when the user wants to (1) convert .jsonl conversation transcripts to .md, (2) strip binary/base64 data from Claude Code conversation logs, (3) make AI conversation history human-readable, (4) process Claude Code session exports from ~/.claude/, or (5) batch-convert a folder of conversation JSONL files. Triggers on mentions of JSONL conversations, transcript conversion, conversation export, or readable conversation logs.
---

# ai-conversation-extractor

Convert Claude Code conversation JSONL transcripts (from `~/.claude/projects/`) to clean, readable Markdown files. Strips binary blobs (base64 images, PDFs — typically 95%+ of file size) while preserving all human-readable content: user messages, assistant text, thinking, tool calls, and tool results.

See `scripts/README.md` for full Claude Code JSONL format documentation.

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
```

No external dependencies — uses only Python 3.12+ stdlib (`json`, `re`, `pathlib`, `argparse`).

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
