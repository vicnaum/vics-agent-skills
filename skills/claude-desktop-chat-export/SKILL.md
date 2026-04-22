---
name: claude-desktop-chat-export
description: "Export a claude.ai (Claude Desktop / web) conversation and convert it to a Claude Code CLI JSONL session so it becomes resumable via `claude -r <sessionId>`. Use when the user wants to: (1) continue a claude.ai/Claude Desktop chat in Claude Code CLI, (2) import a web conversation into CC, (3) archive a claude.ai conversation locally with images and attachments, (4) unfreeze a claude.ai chat that hit its context limit, or (5) convert conversation.json from the claude.ai API to CC's JSONL envelope format. Triggers on mentions of exporting/importing Claude Desktop chats, resuming a web chat in CLI, conversation.json → JSONL, or claude.ai chat archival."
---

# claude-desktop-chat-export

claude.ai chats aren't resumable — once you hit context limit or close the tab, the conversation is read-only. Claude Code CLI stores every session as JSONL at `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` and resumes via `claude -r <sessionId>`. This skill bridges the two: export a claude.ai conversation, convert it to CC's format, keep talking.

Same underlying model. Only the harness differs (system prompt, declared tools, output-length rules).

## Workflow

Three stages: grab from the browser → organize → convert. The converter is `scripts/convert_to_cli.py`.

Two transport options for the "grab" stage:
- **DevTools console** — downloads `conversation.json` and `chat-images.zip` to `~/Downloads` via the browser's download mechanism. Works when the user runs the snippets manually.
- **MCP + local relay** — works when driving the browser via the `claude-in-chrome` MCP. The MCP's `javascript_tool` return filters block cookie-like and base64 payloads, and extension-driven `a.click()` downloads don't land on disk. Solution: run `scripts/relay_server.py` locally, have the browser POST each blob to it. See [MCP path](#mcp-path-via-relay) below.

### 1. Grab the conversation JSON + images from claude.ai

Requires an authenticated browser session at claude.ai. Use DevTools console or the `claude-in-chrome` MCP.

First, identify the org and conversation UUIDs:
- **Org UUID**: from `/api/bootstrap` → `memberships[0].organization.uuid`
- **Conversation UUID**: from the chat URL

Run in the browser console (on claude.ai, already logged in):

```js
const ORG = '<your-org-uuid>';
const CONV = '<conversation-uuid>';

// Fetch full conversation
fetch(`/api/organizations/${ORG}/chat_conversations/${CONV}?tree=True&rendering_mode=messages&render_all_tools=true`,
      {credentials:'include'})
  .then(r => r.blob())
  .then(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = 'conversation.json'; a.click(); });
```

For images (API only serves webp previews — no originals):

```js
// Load JSZip once:
await new Promise((res,rej)=>{ const s=document.createElement('script'); s.src='https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js'; s.onload=res; s.onerror=rej; document.head.appendChild(s); });

// Fetch + zip all images:
const convo = await (await fetch(`/api/organizations/${ORG}/chat_conversations/${CONV}?tree=True&rendering_mode=messages&render_all_tools=true`, {credentials:'include'})).json();
const images = [];
for (const m of convo.chat_messages) for (const f of (m.files||[])) if (f.file_kind==='image') images.push(f);
const zip = new JSZip();
for (const img of images) {
  const r = await fetch(`/api/${ORG}/files/${img.file_uuid}/preview`, {credentials:'include'});
  if (r.ok) zip.file(`${img.file_uuid}.webp`, await r.arrayBuffer());
}
const blob = await zip.generateAsync({type:'blob'});
const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'chat-images.zip'; a.click();
```

Chrome blocks a second auto-download from the same page — either run the two snippets in separate fresh tabs, or click "Allow" on the download-permission prompt.

#### MCP path (via relay)

When driving the browser via the `claude-in-chrome` MCP, use the bundled relay. Start it pointing at the target export directory:

```bash
python3 <skill-dir>/scripts/relay_server.py <export-dir>/<new-uuid>/ &
```

It listens on `http://127.0.0.1:8765` by default and writes each POST body to `<out-dir>/<X-Filename>`. Nested paths like `files/<uuid>.webp` are preserved; `..` / absolute paths are rejected.

Then, via `javascript_tool` on a claude.ai tab, POST the conversation JSON and each image blob:

```js
// Conversation JSON
(async () => {
  const ORG = '<org-uuid>';
  const CONV = '<conversation-uuid>';
  const r = await fetch(`/api/organizations/${ORG}/chat_conversations/${CONV}?tree=True&rendering_mode=messages&render_all_tools=true`, {credentials:'include'});
  const blob = await r.blob();
  const up = await fetch('http://127.0.0.1:8765/upload', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Filename':'conversation.json'},
    body: blob
  });
  return {size: blob.size, relay: up.status};
})()
```

```js
// All image previews, written to files/<uuid>.webp
(async () => {
  const ORG = '<org-uuid>';
  const CONV = '<conversation-uuid>';
  const convo = await (await fetch(`/api/organizations/${ORG}/chat_conversations/${CONV}?tree=True&rendering_mode=messages&render_all_tools=true`, {credentials:'include'})).json();
  const imgs = [];
  for (const m of convo.chat_messages) for (const f of (m.files||[])) if (f.file_kind==='image') imgs.push(f.file_uuid);
  let ok=0, fail=0;
  for (const uuid of imgs) {
    try {
      const r = await fetch(`/api/${ORG}/files/${uuid}/preview`, {credentials:'include'});
      if (!r.ok) { fail++; continue; }
      const up = await fetch('http://127.0.0.1:8765/upload', {
        method:'POST',
        headers:{'Content-Type':'application/octet-stream','X-Filename':`files/${uuid}.webp`},
        body: await r.blob()
      });
      if (up.ok) ok++; else fail++;
    } catch(e) { fail++; }
  }
  return {total: imgs.length, saved: ok, failed: fail};
})()
```

Stop the relay (`kill %1` / Ctrl+C) once both uploads are done. This bypasses both the MCP's cookie-data filter (only metadata, not page data, is returned from JS) and the extension-initiated-download issue.

### 2. Organize

```
mkdir -p <export-dir>/<conversation-uuid>/files
mv ~/Downloads/conversation.json <export-dir>/<conversation-uuid>/conversation.json
unzip ~/Downloads/chat-images.zip -d <export-dir>/<conversation-uuid>/files/
```

Result:
```
<export-dir>/<conversation-uuid>/
├── conversation.json      ← full claude.ai API response
└── files/
    └── <file_uuid>.webp   ← one per attached image
```

(With the MCP + relay path, files land directly in this layout — no unzip needed.)

### 3. Convert

```bash
python3 <skill-dir>/scripts/convert_to_cli.py \
  <export-dir>/<conversation-uuid>/conversation.json \
  --cwd <project-path> \
  --session-id <conversation-uuid> \
  --slug <any-slug-string>
```

Output lands at `~/.claude/projects/<encoded-cwd>/<conversation-uuid>.jsonl`. Resume:

```bash
claude -r <conversation-uuid>
```

No external Python dependencies — stdlib only (Python 3.10+).

## CLI flags

| Flag | Default | Purpose |
|---|---|---|
| `--cwd PATH` | `os.getcwd()` | Which `~/.claude/projects/<encoded-cwd>/` folder the output lands in |
| `--session-id UUID` | fresh uuid4 | CC sessionId (pass the original claude.ai conversation UUID to keep identity) |
| `--slug STRING` | `imported-chat-ai` | Slug stamped on every envelope. Must be consistent across messages |
| `--files-dir PATH` | `<conv_path>/../files/` | Where to look for images, named `<file_uuid>.<ext>` |
| `--version VERSION` | `2.1.114` | CC version stamped on envelopes |
| `--git-branch NAME` | `master` | `gitBranch` field |
| `--keep-tools` | off | Keep `tool_use`/`tool_result` blocks. Default flattens them to text (see limitations) |
| `--flatten-thinking` | off | Preserve thinking as `<thinking>…</thinking>` text blocks instead of dropping. Signature-free, so no resume failures. Strippable later via `session-stripper strip-thinking` (detects the wrappers automatically) |
| `--out PATH` | default path | Override output location |
| `--dry-run` | off | Report summary but don't write |

## Defaults to know

**Tools flattened by default.** claude.ai tool names (`view`, `web_fetch`, `bash_tool`, …) collide with CC's declared tool names (`Read`, `Bash`, …). Resuming with raw tool blocks produces API errors. The default `--keep-tools off` flattens them to readable text so the model retains context of what was looked up. Only pass `--keep-tools` if you know what you're doing.

**Thinking blocks dropped by default.** Their cryptographic signatures are tied to the original API context and don't transfer, so keeping them as structural `thinking` blocks triggers `"Invalid signature in thinking block"` on resume. Pass `--flatten-thinking` to preserve thinking as plain `<thinking>…</thinking>` text blocks — no signatures, no resume failures, model reads it as ordinary context. Later, `session-stripper strip-thinking` detects these wrappers and can clear them without re-converting. Otherwise, the raw thinking text is still readable in `conversation.json`.

**UUIDs regenerate on every converter run.** Only `sessionId` is stable (via `--session-id`). If a CC session for this conversation is currently open, **quit CC before re-running the converter** — otherwise CC's in-memory state points to deleted UUIDs and the chain breaks silently.

For the full list of format-level gotchas, chain integrity requirements, and what does/doesn't survive the import, see [references/format-notes.md](references/format-notes.md).

## When something goes wrong

- **"tool use concurrency issues" on resume**: ran with `--keep-tools` but tool names don't match CC's declarations. Re-run without `--keep-tools`.
- **"Invalid signature in thinking block"**: thinking blocks leaked through. Re-run; converter drops them by default.
- **"Extra inputs are not permitted"**: tool_result had stray fields (`uuid`, `citations`, `start_timestamp`). Converter whitelists valid keys — re-run.
- **Chain broken silently / empty session list**: CC was open during the previous conversion. Quit CC, re-run.
- **Images missing in resumed chat**: check `--files-dir` points to a folder with `<file_uuid>.webp` files matching the JSON's `files[].file_uuid`.

See [references/format-notes.md](references/format-notes.md) for the full gotcha list and the chain integrity invariants (`parentUuid` unbroken, `slug` identical, timestamps monotonic).
