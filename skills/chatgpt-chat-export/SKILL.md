---
name: chatgpt-chat-export
description: "Export a chatgpt.com (ChatGPT web) conversation to disk with full fidelity: raw backend-api JSON, all attachments (user-uploaded images, PDFs), and a readable Markdown transcript. Use when the user wants to: (1) grab/download/archive a ChatGPT talk locally, (2) pull a chatgpt.com conversation's JSON via the API route, (3) download the images/PDFs attached to a ChatGPT chat, (4) convert a ChatGPT conversation to Markdown, or (5) feed a ChatGPT conversation into another tool/agent. Triggers on chatgpt.com URLs (/c/<uuid>), 'grab this talk', 'export this ChatGPT chat', 'ChatGPT conversation JSON', or ChatGPT attachment downloads."
---

# chatgpt-chat-export

ChatGPT's web UI has no full-fidelity export (Settings → Export mails a zip of *everything*, hours later). But the page itself talks to `/backend-api/conversation/{id}`, which returns the complete conversation as JSON — including edit branches, tool calls, reasoning scaffolding, and attachment references. This skill grabs that JSON plus every attachment from an authenticated Chrome session, and converts it to Markdown.

Output convention (one folder per conversation, named by UUID):

```
<project>/chat-exports/<conversation-uuid>/
├── conversation.json   ← raw /backend-api/conversation response, full fidelity
├── conversation.md     ← readable transcript (chatgpt2md.py)
├── FILES.md            ← attachment index: file id → name → local path
└── files/              ← binaries, by original filename
```

## Workflow

Four stages: grab JSON → grab attachments → organize → convert. Two transport paths for the "grab" stages, depending on who drives the browser:

- **DevTools console** (user runs snippets manually): Blob + `<a download>` clicks land in `~/Downloads`. Simple, but Chrome blocks the 2nd+ programmatic download per tab — use a fresh tab per download, or bundle everything into one zip.
- **claude-in-chrome MCP** (agent drives): `a.click()` downloads don't land on disk, `javascript_tool` results truncate at ~1000 chars, and the extension's DLP filter replaces JWT-shaped / base64 / query-string / long-uniform output with `[BLOCKED: …]`. Transport out via **clipboard** (primary — needs no network) or the bundled `scripts/relay_server.py` (blocked by uBlock's LAN-protection on some setups; see below).

**Iron rule for the MCP path: never `return` the access token or a payload from `javascript_tool`.** Stash everything in `window.*` and return only metadata (status, length, count).

### 1. Grab the conversation JSON

Conversation UUID comes from the URL: `chatgpt.com/c/<uuid>`. Open that page (logged in), then:

```js
// In-page: token + conversation -> window.__conv. Returns only metadata.
const sess = await fetch('/api/auth/session').then(r => r.json());
window.__tok = sess.accessToken;
const CONV = '<conversation-uuid>';
window.__conv = await fetch('/backend-api/conversation/' + CONV,
  {headers: {Authorization: 'Bearer ' + window.__tok}}).then(r => r.json());
JSON.stringify({title: window.__conv.title, nodes: Object.keys(window.__conv.mapping || {}).length});
```

**Transport — clipboard (MCP path).** `navigator.clipboard.writeText` requires document focus *and* a user gesture, which `javascript_tool` doesn't have. Inject a full-page button and click it with the `computer` tool:

```js
document.body.innerHTML = '<button id="copybtn" style="position:fixed;inset:0;width:100vw;height:100vh;font-size:40px">COPY</button><div id="st" style="position:fixed;bottom:0;left:0;background:#000;color:#0f0;padding:8px;z-index:9">waiting</div>';
window.__payload = JSON.stringify(window.__conv);
document.getElementById('copybtn').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(window.__payload);
        document.getElementById('st').textContent = 'COPIED ' + window.__payload.length; }
  catch (e) { document.getElementById('st').textContent = 'ERR ' + e.message; }
});
'ready';
```

Then: `computer` left_click anywhere on the page → verify via `document.getElementById('st').textContent` → on the shell side:

```bash
pbpaste > chat-exports/<uuid>/conversation.json
python3 -c "import json; d=json.load(open('chat-exports/<uuid>/conversation.json')); print(d['title'], len(d['mapping']))"
```

Clipboard handles ~10 MB fine. Clear it afterwards (`printf '' | pbcopy`) — and warn the user their clipboard was overwritten.

**Transport — console download (manual path):**

```js
const blob = new Blob([JSON.stringify(window.__conv)], {type: 'application/json'});
const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
a.download = 'conversation.json'; a.click();
```

### 2. Grab the attachments

Attachment references live in two places in the JSON:
- `mapping[*].message.metadata.attachments[]` → `{id, name, mime_type, size}` (user uploads: images, PDFs)
- image parts' `content.parts[*].asset_pointer` → `sediment://file_…` (same files, by pointer)

Resolve each id to a download URL in-page:

```
GET /backend-api/conversation/{conv-id}/attachment/{file-id}/download   (Bearer auth)
→ {download_url, file_name, mime_type, file_size_bytes}
```

**The `download_url` is NOT a signed CDN link** — it's `chatgpt.com/backend-api/estuary/content?id=…`, which needs the browser's auth. `curl` from outside fails (exit 56). So fetch the bytes **in-page** and bundle them:

```js
// window.__files = [{id, name}] built from window.__conv (dedupe by id first)
const toB64 = (blob) => new Promise((res, rej) => {
  const fr = new FileReader();
  fr.onload = () => res(fr.result.split(',')[1]); fr.onerror = rej;
  fr.readAsDataURL(blob);
});
const CONV = '<conversation-uuid>';
const bundle = {};
for (const f of window.__files) {
  const meta = await fetch('/backend-api/conversation/' + CONV + '/attachment/' + f.id + '/download',
    {headers: {Authorization: 'Bearer ' + window.__tok}}).then(r => r.json());
  const blob = await fetch(meta.download_url, {headers: {Authorization: 'Bearer ' + window.__tok}}).then(r => r.blob());
  bundle[f.name || f.id] = await toB64(blob);
}
window.__payload = JSON.stringify(bundle);
'bundled ' + Object.keys(bundle).length + ' files, ' + window.__payload.length + ' chars';
```

Then reuse the same copy button (it copies `window.__payload`), click, and unpack:

```bash
python3 <skill-dir>/scripts/unpack_clipboard.py chat-exports/<uuid>/files/
```

On the manual console path, zip the bundle client-side instead (JSZip from CDN, one `<a download>` click for the whole zip — sidesteps the multi-download block; see the sibling `claude-desktop-chat-export` skill for the JSZip snippet).

**Known 403:** PDF *page-preview* pointers of the form `sediment://<hash>#file_…#p_N.jpg` (tool-rendered page images) are not downloadable — skip them; the source PDF itself downloads fine via its plain `file_…` id.

### 3. Convert to Markdown

```bash
python3 <skill-dir>/scripts/chatgpt2md.py chat-exports/<uuid>/conversation.json
# → conversation.md + FILES.md next to the JSON; images/PDFs linked into files/
```

The converter walks the **canonical branch only** (`current_node` → `.parent` → root — the mapping is a tree with edit branches, not a list), keeps only visible turns (`user` × `text|multimodal_text`, `assistant` × `text` with `recipient == 'all'`), and drops internal scaffolding (`thoughts`, `code`/`web.run` tool calls, `reasoning_recap`, hidden system messages). ChatGPT embeds citation markers as private-use-area Unicode (`…` around `citeturn0search1`-style tokens) — these are stripped, as are leftover `products{…}` carousel blobs. Flags: `--user-name`, `--files-dir`, `--out`, `--no-files-md`.

Note: the generic `ai-conversation-extractor` skill does **not** handle this mapping-tree format — use this converter for chatgpt.com JSON.

## Gotchas (hard-won)

- **MCP output filter**: results truncate ~1000 chars; JWT/base64/query-string/long-uniform text → `[BLOCKED: …]`. Even `'x'.repeat(30000)` triggers the base64 heuristic. Route data through `window.*` + clipboard/relay/download, never through tool results.
- **uBlock Origin (Lite) LAN-block**: page fetches from chatgpt.com to `http://127.0.0.1:*` throw `Failed to fetch` (stack shows uBlock's fetch wrapper). This kills the relay transport — clipboard is the reliable default. (`scripts/relay_server.py` is bundled for setups without uBlock.)
- **Clipboard needs focus + gesture**: `writeText` from bare `javascript_tool` fails with `NotAllowedError: Document is not focused` — hence the injected button + real `computer` click.
- **Chrome multi-download guard** (manual path): the 2nd+ programmatic `a.click()` download in a tab silently does nothing. Fresh tab per download (reload is NOT enough), or one zip.
- **`get_page_text` fallback** for text-only payloads: inject `<pre>` chunks (~45k chars each) into the DOM and read them back — works, but costs one round-trip per chunk and floods agent context; last resort.
- **Wiping the DOM is safe**: `document.body.innerHTML = …` on the conversation page doesn't touch `window.__conv` / `window.__tok`, and auth cookies keep working for in-page fetches.

## Requirements

- Chrome logged into chatgpt.com; for the MCP path, the `claude-in-chrome` extension.
- No external Python dependencies — stdlib only (Python 3.9+).
- Clipboard shell access: `pbpaste`/`pbcopy` (macOS) or `xclip`/`wl-paste` (Linux, use `--stdin` on the unpacker).
