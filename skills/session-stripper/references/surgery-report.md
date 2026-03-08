# Session Surgery: What Actually Works

Based on analysis of 4 conversations containing 14+ surgery attempts (9 successes, 5 failures) and verification against 3 real JSONL session files that were successfully modified and continued to work.

---

## Executive Summary

The current `session-surgery.md` is misleading. It focuses on creating new sessions with compact boundaries — a heavyweight approach. **You CAN strip content from existing sessions in-place.** The evidence:

- Session `b79098b3`: 157 tool_use blocks removed, 7 thinking blocks removed, 157 tool_result messages removed. Session continued for 1,371 more lines with 205 API calls, reaching 167K tokens.
- `/microcompact` on session `de65ca7c`: Cleared 90 tool calls + 90 tool results + 7 images + dropped 180 empty messages = 180K tokens saved. Model retained full conversation memory.

**The AI that said "we cannot just delete messages/thinking/tools from existing session" was wrong.** But there are specific rules you must follow or it breaks silently.

---

## Table of Contents

1. [Background: What Consumes Context](#1-background-what-consumes-context)
2. [Operation 1: Remove Tool Inputs/Results](#2-operation-1-remove-tool-inputsresults-from-a-session)
3. [Operation 2: Remove Thinking Blocks](#3-operation-2-remove-thinking-blocks)
4. [Operation 3: Compact Before a Specific Message](#4-operation-3-compact-before-a-specific-message)
5. [Failure Catalog](#5-failure-catalog)
6. [Verification Procedures](#6-verification-procedures)

---

## 1. Background: What Consumes Context

From an actual `context-debug` line captured during session surgery:

```
[context-debug] model=claude-opus-4-6 input_est=189715
  system=41663      # System prompt — irreducible (~22-26K typical, up to 42K with MCP tools)
  user=5248         # User text messages
  assistant=8818    # Assistant text responses
  thinking=0        # Thinking blocks (were cleared by /clear-thinking before this snapshot)
  tools=115986      # <-- THE BIGGEST PROBLEM: 61% of context
    tool_use=46592  #   Tool call inputs (code snippets, file paths, grep patterns)
    tool_result=69394 # Tool results (file contents, command output, search results)
  media=18000       # Images, PDFs
  attachments=0
```

**Key insight:** Tool calls and results consume 60%+ of context in a typical work session. They are the #1 target for surgery.

**Thinking blocks:** Also significant. They ARE sent to the API (source code confirmed). `formatTranscript()` only strips a tiny fraction — orphaned thinking (pure thinking-only messages) and trailing thinking (last message only). The vast majority of thinking blocks survive and consume context space. They contribute to the client-side estimate and cause "Prompt is too long". **Always strip thinking alongside tools.** See [Operation 2](#3-operation-2-remove-thinking-blocks) for details.

**Token estimation:** `character_count / 4` is reliable enough for planning. Exact counts come from `message.usage` on assistant messages: `cache_read_input_tokens + cache_creation_input_tokens`.

---

## 2. Operation 1: Remove Tool Inputs/Results from a Session

### What This Does

Replaces the content of `tool_use` inputs and `tool_result` outputs with minimal placeholders, then drops messages that become empty. This is the single most effective surgery — typically saves 50-70% of context.

### Why It Works

The API does NOT require historical tool call/result data. Once a tool call has been executed and the assistant has processed its result, the raw input/output is never needed again. The assistant's text response already captured whatever was important from the result.

**Proof:** Session `b79098b3` had all 157 tool_use and 157 tool_result messages completely removed. The session then continued for 205 more API calls over 1,371 lines, growing to 167K tokens. The API never complained.

### The Two Approaches

#### Approach A: In-Place Content Clearing (preserves message structure)

This is what `/microcompact` does. It keeps every message envelope intact but clears the heavy content inside tool blocks. Messages that become empty after clearing are dropped.

```python
#!/usr/bin/env python3
"""Strip tool content from a Claude Code session JSONL in-place."""

import json
import sys
import shutil
import uuid as uuid_mod
from pathlib import Path

def strip_tools(session_path, dry_run=False):
    session_path = Path(session_path).expanduser()

    if not dry_run:
        backup = session_path.with_suffix('.jsonl.bak')
        shutil.copy2(session_path, backup)
        print(f"Backup: {backup}")

    lines = session_path.read_text().splitlines()
    output_lines = []

    # First pass: build UUID index and find active chain
    uuid_to_idx = {}
    all_objs = []
    for i, line in enumerate(lines):
        obj = json.loads(line)
        all_objs.append(obj)
        uid = obj.get('uuid', '')
        if uid:
            uuid_to_idx[uid] = i

    # Find the active chain by walking parentUuid from the last leaf
    active_uuids = set()
    last = None
    for obj in reversed(all_objs):
        if obj.get('type') in ('user', 'assistant') and obj.get('uuid') and not obj.get('isSidechain'):
            last = obj
            break

    if last:
        cur = last['uuid']
        while cur and cur in uuid_to_idx:
            active_uuids.add(cur)
            cur = all_objs[uuid_to_idx[cur]].get('parentUuid')

    stats = {'tool_use_cleared': 0, 'tool_result_cleared': 0,
             'thinking_cleared': 0, 'images_cleared': 0,
             'messages_dropped': 0, 'chars_saved': 0}

    for obj in all_objs:
        uid = obj.get('uuid', '')
        msg = obj.get('message', {})

        # Only strip from active chain, non-sidechain messages
        if uid not in active_uuids or obj.get('isSidechain'):
            output_lines.append(json.dumps(obj, ensure_ascii=False))
            continue

        content = msg.get('content', '') if isinstance(msg, dict) else ''

        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    new_blocks.append(block)
                    continue

                btype = block.get('type', '')

                if btype == 'tool_use':
                    old_input = json.dumps(block.get('input', {}))
                    stats['chars_saved'] += len(old_input)
                    # Keep the block but clear input
                    new_block = {
                        'type': 'tool_use',
                        'id': block['id'],
                        'name': block.get('name', ''),
                        'input': {}  # cleared
                    }
                    new_blocks.append(new_block)
                    stats['tool_use_cleared'] += 1

                elif btype == 'tool_result':
                    old_content = json.dumps(block.get('content', ''))
                    stats['chars_saved'] += len(old_content)
                    # Keep the block but clear content
                    new_block = {
                        'type': 'tool_result',
                        'tool_use_id': block['tool_use_id'],
                        'content': ''  # cleared
                    }
                    # Preserve is_error if present
                    if block.get('is_error'):
                        new_block['is_error'] = True
                    new_blocks.append(new_block)
                    stats['tool_result_cleared'] += 1

                elif btype == 'image':
                    stats['chars_saved'] += len(json.dumps(block))
                    stats['images_cleared'] += 1
                    # Drop image blocks entirely
                    continue

                else:
                    new_blocks.append(block)

            # Check if message is now empty (no meaningful blocks)
            meaningful = [b for b in new_blocks if isinstance(b, dict) and
                         b.get('type') not in ('tool_use', 'tool_result') or
                         (b.get('type') == 'tool_use') or  # keep tool_use stubs for chain
                         (b.get('type') == 'tool_result')]  # keep tool_result stubs for chain

            msg['content'] = new_blocks

        output_lines.append(json.dumps(obj, ensure_ascii=False))

    est_tokens_saved = stats['chars_saved'] // 4
    print(f"Tool calls cleared: {stats['tool_use_cleared']}")
    print(f"Tool results cleared: {stats['tool_result_cleared']}")
    print(f"Images cleared: {stats['images_cleared']}")
    print(f"Characters saved: {stats['chars_saved']:,}")
    print(f"Estimated tokens saved: {est_tokens_saved:,}")

    if not dry_run:
        session_path.write_text('\n'.join(output_lines) + '\n')
        print(f"Written: {session_path}")
    else:
        print("(dry run — no changes written)")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 strip_tools.py <session.jsonl> [--dry-run]")
        sys.exit(1)
    strip_tools(sys.argv[1], dry_run='--dry-run' in sys.argv)
```

**Why this works:** The parentUuid chain is completely untouched. Every message stays in place. Only the heavy CONTENT inside tool blocks is cleared. The API sees tool_use with `input: {}` and tool_result with `content: ""` — both are valid and consume minimal tokens.

**Observed results:** 180K tokens saved from a 240K token session (75% reduction). Model retained full conversation memory because all assistant text responses (which contain the actual analysis/reasoning about tool results) are preserved.

#### Approach B: Full Message Removal (dialogue extraction)

Remove entire tool_result user messages and tool_use-only assistant messages. This is more aggressive but requires chain repair.

**WARNING:** You CANNOT just delete individual tool_result user messages. This creates consecutive assistant messages, which violates the API's alternation requirement. You must either:
1. Remove BOTH the tool_result user message AND its corresponding tool_use-only assistant message (keeping only messages with text content), OR
2. Merge all dialogue into ONE summary message behind a compact boundary

The proven approach is option 2 — see [Operation 3](#4-operation-3-compact-before-a-specific-message).

**Evidence of failure (Attempts 10-11):** Stripping tool_result from individual user messages made them empty → Claude Code skipped empties → 36 consecutive assistant messages → API rejected the request. This happened TWICE.

**Evidence of success (session `b79098b3`):** All 157 tool_result messages and corresponding tool_use-only assistant messages were completely removed. 0 shared UUIDs with original — all regenerated. No compact boundary — starts with `parentUuid: null`. The session continued for 205 API calls.

The key difference: success removed entire MESSAGE PAIRS (assistant tool_use + user tool_result), not just content within messages. The remaining 112 messages were ALL text-only dialogue, maintaining proper user/assistant alternation.

### Which Approach to Use

| Approach | Tokens Saved | Preserves Tool Names? | Requires Chain Repair? | Risk |
|----------|-------------|----------------------|----------------------|------|
| A: Content clearing | 50-70% | Yes (just clears inputs) | No | Very low |
| B: Message removal | 70-90% | No | Yes (full rebuild) | Medium |

**Recommendation:** Use Approach A (content clearing) for quick in-place fixes. Use Approach B only when creating a new session with a compact boundary (Operation 3).

---

## 3. Operation 2: Remove Thinking Blocks

### The Full Picture (Source Code + Empirical Analysis)

Thinking blocks have a complex lifecycle. Two independent analyses were performed:

**Source code analysis** (reading the actual decoded Claude Code source):
- Thinking blocks ARE sent to the API in the request body
- `formatTranscript()` runs three filters, but only strips SOME thinking:
  - `filterOrphanedThinking()` — removes assistant messages that are ONLY thinking/redacted_thinking blocks (no text, no tool_use). But if a message has `[thinking, text, tool_use]`, ALL blocks survive including thinking.
  - `removeTrailingThinking()` — strips trailing thinking from the VERY LAST assistant message only. All other messages' thinking is untouched.
  - `filterWhitespaceMessages()` — removes empty text messages. Does NOT touch thinking.
- `formatAssistantMessage_4548()` passes thinking blocks through to the API unchanged via spread operator (`...B`)
- No additional stripping occurs between `formatTranscript()` and the API call

**Empirical analysis** (statistical analysis of 1,038 token increments across 10 sessions):
- One empirical test suggested low correlation between thinking chars and reported `usage` token deltas (r = -0.053)
- There is a server-side `clear_thinking_20251015` context management feature (the client sends `context_management: { edits: [{ type: "clear_thinking_20251015", keep: "all" }] }`)
- However, the `usage` fields may not reflect the full picture — the server may still process thinking blocks even if it reports them differently in usage stats

**The critical point:** Regardless of how the server accounts for thinking internally, **Claude Code's CLIENT-SIDE token estimate DOES include thinking blocks**. The `context-debug` line shows `input_est=189715` which includes thinking in the estimate. The "Prompt is too long" error is triggered by this client-side check BEFORE the request is even sent to the API. **Thinking blocks are real context that takes real space — always strip them.**

### What This Means in Practice

| Scenario | Does stripping thinking help? |
|----------|------------------------------|
| "Prompt is too long" error | **YES** — lowers the client-side estimate, allows the request to be sent |
| Context window pressure | **YES** — thinking blocks are sent to the API and take space in the request |
| JSONL file size / parse time | **YES** — thinking blocks are 2-10K chars each, adds up fast |
| Combined with tool stripping | **YES** — both contribute to context usage; strip both for maximum effect |

**Bottom line:** Thinking blocks consume real context space. They are sent to the API. `formatTranscript()` only strips a tiny fraction of them. **Always strip thinking blocks when doing session surgery** — it's the second most impactful operation after stripping tools.

### What `formatTranscript()` Actually Strips (and What Survives)

```
Assistant message: [thinking, text]           → thinking SURVIVES (has non-thinking content)
Assistant message: [thinking, tool_use]       → thinking SURVIVES (has non-thinking content)
Assistant message: [thinking, text, tool_use] → thinking SURVIVES (has non-thinking content)
Assistant message: [thinking]                 → thinking REMOVED (orphaned — only thinking)
Assistant message: [thinking, thinking]       → both REMOVED (orphaned — all thinking)
Last message:      [text, thinking]           → trailing thinking REMOVED (last msg only)
Last message:      [thinking, text, thinking] → trailing thinking REMOVED, first thinking SURVIVES
```

So in a typical 100-message session, only a handful of thinking blocks are stripped:
- Pure thinking-only messages (rare — most have text or tool_use too)
- Trailing thinking on the very last message (1 message max)

**The vast majority of thinking blocks survive `formatTranscript()` and ARE sent to the API.**

### How to Strip Thinking

```python
#!/usr/bin/env python3
"""Strip thinking blocks from a Claude Code session JSONL."""

import json
import sys
import shutil
from pathlib import Path

def strip_thinking(session_path, dry_run=False):
    session_path = Path(session_path).expanduser()

    if not dry_run:
        backup = session_path.with_suffix('.jsonl.bak')
        if not backup.exists():  # don't overwrite existing backup
            shutil.copy2(session_path, backup)
            print(f"Backup: {backup}")

    lines = session_path.read_text().splitlines()
    output_lines = []
    stats = {'thinking_cleared': 0, 'chars_saved': 0, 'messages_affected': 0}

    for line in lines:
        obj = json.loads(line)
        msg = obj.get('message', {})

        if obj.get('type') != 'assistant' or not isinstance(msg, dict):
            output_lines.append(line)  # preserve original formatting
            continue

        content = msg.get('content', '')
        if not isinstance(content, list):
            output_lines.append(line)
            continue

        new_blocks = []
        msg_modified = False
        for block in content:
            if isinstance(block, dict) and block.get('type') in ('thinking', 'redacted_thinking'):
                thinking_text = block.get('thinking', '') or block.get('data', '')
                stats['chars_saved'] += len(thinking_text)
                stats['thinking_cleared'] += 1
                msg_modified = True
                # Remove entirely — don't keep stubs
                continue
            else:
                new_blocks.append(block)

        if msg_modified:
            stats['messages_affected'] += 1
            # If message has no blocks left, keep a minimal text block
            if not new_blocks:
                new_blocks = [{'type': 'text', 'text': ''}]
            msg['content'] = new_blocks
            output_lines.append(json.dumps(obj, ensure_ascii=False))
        else:
            output_lines.append(line)  # preserve original formatting

    est_tokens_saved = stats['chars_saved'] // 4
    print(f"Thinking blocks cleared: {stats['thinking_cleared']}")
    print(f"Messages affected: {stats['messages_affected']}")
    print(f"Characters saved: {stats['chars_saved']:,}")
    print(f"Estimated token savings: ~{est_tokens_saved:,}")
    print(f"(Reduces context pressure — thinking blocks are sent to the API)")

    if not dry_run:
        session_path.write_text('\n'.join(output_lines) + '\n')
        print(f"Written: {session_path}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 strip_thinking.py <session.jsonl> [--dry-run]")
        sys.exit(1)
    strip_thinking(sys.argv[1], dry_run='--dry-run' in sys.argv)
```

### Important Notes

- **Always strip thinking when doing session surgery** — it's the #2 most impactful operation after tools
- Thinking blocks ARE sent to the API and consume context space
- `formatTranscript()` only strips orphaned (thinking-only messages) and trailing (last message) — the vast majority of thinking blocks survive and are sent unchanged
- For maximum effect, combine thinking stripping with tool stripping (Operation 1)
- In the observed case, `/clear-thinking` alone wasn't enough (session was 190K est, tools were 116K of that), but it DID reduce the estimate by ~36K — the session was still over the limit because tools dominated
- Typical thinking savings: 10-20% of context (vs 50-70% for tools)

---

## 4. Operation 3: Compact Before a Specific Message

### What This Does

Takes everything BEFORE a chosen message, compresses it into a single summary, and keeps everything AFTER that message fully intact (with all tool calls, results, thinking preserved).

### The Proven Pattern (7/7 success rate)

```
Line 0: compact_boundary  (parentUuid: null — this is the new root)
Line 1: compact_summary   (ONE single user message with all pre-cut dialogue)
Line 2+: intact messages   (copied from original, with remapped UUIDs)
```

This pattern was used 7 times and succeeded every time after the three critical fields were discovered.

### Step-by-Step Procedure

#### Step 1: Backup

```bash
SESSION=~/.claude/projects/<project-path>/<uuid>.jsonl
cp "$SESSION" "${SESSION}.bak"
```

#### Step 2: Analyze and Choose Cut Point

```python
import json

SESSION = "path/to/session.jsonl"
with open(SESSION) as f:
    lines = [json.loads(line) for line in f]

# Build UUID index
uuid_to_obj = {}
for obj in lines:
    uid = obj.get('uuid', '')
    if uid:
        uuid_to_obj[uid] = obj

# Walk active chain from last message
last = None
for obj in reversed(lines):
    if obj.get('type') in ('user', 'assistant') and obj.get('uuid') and not obj.get('isSidechain'):
        last = obj
        break

chain = []
cur = last['uuid']
while cur and cur in uuid_to_obj:
    chain.append(uuid_to_obj[cur])
    cur = uuid_to_obj[cur].get('parentUuid')
chain.reverse()

print(f"Active chain: {len(chain)} messages")

# Show human messages (these are your candidate cut points)
for i, obj in enumerate(chain):
    if obj.get('type') == 'user':
        content = obj.get('message', {}).get('content', '')
        if isinstance(content, str) and '<command' not in content and len(content) > 10:
            est_tokens = sum(len(json.dumps(o.get('message', {}))) for o in chain[:i]) // 4
            print(f"  pos {i}: ~{est_tokens:,}tok | {content[:100]}")
```

Pick the position where you want to keep everything AFTER.

#### Step 3: Extract Dialogue from Pre-Cut Section

```python
def extract_dialogue(chain_slice):
    """Extract human-readable dialogue from a chain slice.

    Returns a single string with all user/assistant text,
    suitable for a compact summary message.
    """
    dialogue_parts = []

    for obj in chain_slice:
        t = obj.get('type', '')
        msg = obj.get('message', {})
        content = msg.get('content', '') if isinstance(msg, dict) else ''

        if t == 'user':
            # Skip tool_result-only messages
            if isinstance(content, list):
                has_tool_result = any(
                    isinstance(b, dict) and b.get('type') == 'tool_result'
                    for b in content
                )
                texts = [
                    b.get('text', '') for b in content
                    if isinstance(b, dict) and b.get('type') == 'text'
                ]
                text = '\n'.join(t for t in texts if t.strip())
                if not text and has_tool_result:
                    continue  # pure tool result, skip
                if text:
                    dialogue_parts.append(f"User: {text}")
            elif isinstance(content, str):
                clean = content.strip()
                # Skip meta/commands
                if '<command-name>' in clean or '<local-command' in clean:
                    continue
                if '[Request interrupted' in clean:
                    continue
                if clean:
                    dialogue_parts.append(f"User: {clean}")

        elif t == 'assistant':
            if isinstance(content, list):
                texts = [
                    b.get('text', '').strip() for b in content
                    if isinstance(b, dict) and b.get('type') == 'text'
                ]
                texts = [t for t in texts if t]
                if texts:
                    dialogue_parts.append(f"Assistant: {chr(10).join(texts)}")
            elif isinstance(content, str) and content.strip():
                dialogue_parts.append(f"Assistant: {content.strip()}")

    return '\n\n'.join(dialogue_parts)

# Extract pre-cut dialogue
CUTPOINT = 243  # <-- your chosen position
summary_text = extract_dialogue(chain[:CUTPOINT])
print(f"Summary: {len(summary_text):,} chars (~{len(summary_text)//4:,} tokens)")
```

#### Step 4: Build the New JSONL

```python
import uuid as uuid_mod
from datetime import datetime, timedelta

# Configuration
NEW_SESSION_ID = str(uuid_mod.uuid4())  # or keep original sessionId
SLUG = "surgery-session"  # any string works; custom slugs like "slimmed-session" verified working
CWD = "/path/to/your/project"
VERSION = "2.1.63"  # your Claude Code version
BASE_TS = datetime(2026, 3, 1, 20, 0, 0)  # any reasonable timestamp

output = []

# --- Line 0: Compact Boundary ---
boundary_uuid = str(uuid_mod.uuid4())
output.append({
    "parentUuid": None,           # CRITICAL: null = this is the root
    "type": "system",
    "subtype": "compact_boundary",
    "uuid": boundary_uuid,
    "sessionId": NEW_SESSION_ID,
    "slug": SLUG,
    "timestamp": BASE_TS.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    "isSidechain": False,
    "userType": "external",
    "cwd": CWD,
    "version": VERSION,
    "level": "info",
    "isMeta": False,
    "content": "Conversation compacted",
    "compactMetadata": {
        "trigger": "manual",
        "preTokens": 180000   # approximate — doesn't need to be exact
    }
})

# --- Line 1: Compact Summary ---
summary_uuid = str(uuid_mod.uuid4())
summary_content = f"[Prior context summary]:\n\n{summary_text}"
output.append({
    "parentUuid": boundary_uuid,    # links to boundary
    "type": "user",
    "uuid": summary_uuid,
    "sessionId": NEW_SESSION_ID,
    "slug": SLUG,
    "timestamp": (BASE_TS + timedelta(milliseconds=1)).strftime('%Y-%m-%dT%H:%M:%S.001Z'),
    "isSidechain": False,
    "userType": "external",
    "cwd": CWD,
    "version": VERSION,
    "message": {
        "role": "user",
        "content": summary_content
    },
    "isCompactSummary": True,            # CRITICAL
    "isVisibleInTranscriptOnly": True     # CRITICAL
})

# --- Lines 2+: Intact Messages from Cut Point Onward ---
source_msgs = chain[CUTPOINT:]

# Build UUID mapping (old -> new)
uuid_map = {}
for obj in source_msgs:
    old_uid = obj.get('uuid', '')
    if old_uid:
        uuid_map[old_uid] = str(uuid_mod.uuid4())

# Copy with remapped UUIDs, fixed slugs, shifted timestamps
ts = BASE_TS + timedelta(seconds=2)
for i, obj in enumerate(source_msgs):
    obj = json.loads(json.dumps(obj))  # deep copy

    # Remap UUID
    old_uid = obj.get('uuid', '')
    if old_uid in uuid_map:
        obj['uuid'] = uuid_map[old_uid]

    # Remap parentUuid
    if i == 0:
        obj['parentUuid'] = summary_uuid  # attach to summary
    else:
        old_parent = obj.get('parentUuid', '')
        if old_parent in uuid_map:
            obj['parentUuid'] = uuid_map[old_parent]

    # Set session/slug
    obj['sessionId'] = NEW_SESSION_ID
    obj['slug'] = SLUG                    # CRITICAL: must match

    # Shift timestamp (CRITICAL: must be after boundary)
    ts += timedelta(milliseconds=500)
    obj['timestamp'] = ts.strftime('%Y-%m-%dT%H:%M:%S.') + f"{ts.microsecond // 1000:03d}Z"

    # Clean fork artifacts
    if 'forkedFrom' in obj:
        del obj['forkedFrom']

    output.append(obj)

# --- Write ---
out_path = f"~/.claude/projects/<project-path>/{NEW_SESSION_ID}.jsonl"
import os
out_path = os.path.expanduser(out_path)
with open(out_path, 'w') as f:
    for msg in output:
        f.write(json.dumps(msg, ensure_ascii=False) + '\n')

print(f"Written: {out_path}")
print(f"Messages: {len(output)}")
print(f"Resume with: claude -r {NEW_SESSION_ID}")
```

#### Step 5: Verify

```python
# Read back and verify chain integrity
with open(out_path) as f:
    written = [json.loads(line) for line in f]

uid_set = {m['uuid'] for m in written if m.get('uuid')}

# Walk from last message to root
last = [m for m in reversed(written) if m.get('type') in ('user','assistant')][0]
depth = 0
cur = last['uuid']
visited = set()
while cur:
    assert cur not in visited, f"CYCLE at {cur}"
    visited.add(cur)
    assert cur in uid_set, f"BROKEN CHAIN: {cur} not found"
    obj = next(m for m in written if m.get('uuid') == cur)
    cur = obj.get('parentUuid')
    depth += 1

assert cur is None, f"Chain doesn't reach null root! Dangling: {cur}"
print(f"Chain OK: {depth} messages, null-terminated")

# Verify slug consistency
slugs = set(m.get('slug') for m in written if m.get('slug'))
assert len(slugs) == 1, f"Multiple slugs: {slugs}"
print(f"Slug OK: {slugs.pop()}")

# Verify timestamp ordering
timestamps = []
for m in written:
    ts = m.get('timestamp', '')
    if ts:
        timestamps.append(ts)
for i in range(1, len(timestamps)):
    assert timestamps[i] >= timestamps[i-1], f"Timestamp out of order at pos {i}: {timestamps[i-1]} > {timestamps[i]}"
print("Timestamps OK: monotonically increasing")
```

#### Step 6: Resume

```bash
claude -r <new-session-id>
```

### The Three Critical Fields (Discovered Through Failure)

Every surgery MUST get these right or the session will appear broken:

| Field | Requirement | What Happens If Wrong |
|-------|------------|----------------------|
| `parentUuid` | Unbroken chain from last message back to `null` root | Session won't load; shows empty or partial |
| `slug` | Same value on ALL messages in active segment | Messages invisible in UI |
| `timestamp` | ALL messages chronologically AFTER compact boundary | Messages invisible in UI — the "silent killer" |

These were discovered through 3 consecutive failures (Attempts 1-3) before the fix was found.

### Alternative: AI-Summarized Compact

Instead of raw dialogue extraction, use an AI to produce a structured summary. This gives better compression for complex multi-phase sessions.

Prompt for the summarizer:
```
Summarize this conversation for context continuation. Include:
1. Steps completed (what was built/fixed)
2. Key discoveries and decisions made
3. Problems encountered and their root causes
4. Current state (what works, what's broken)
5. Files created or modified
6. Next steps that were planned

Be concise but complete. Another AI will continue this work using only your summary.
```

This was used in Attempt 6 and reduced context more effectively than raw dialogue while preserving all important context.

---

## 5. Failure Catalog

Every failure encountered, why it happened, and how to avoid it.

### Failure 1: Timestamps Before Boundary (Attempts 1b, 2)
**Symptom:** Session loads but shows only "Conversation compacted" with nothing after it.
**Cause:** Transplanted messages had timestamps from the ORIGINAL session, older than the compact boundary.
**Fix:** Shift ALL message timestamps to be after the boundary timestamp.

### Failure 2: Fork Reconnects Compact Boundaries (Attempt 1a)
**Symptom:** Session shows full history instead of just post-compact segment.
**Cause:** `/fork` changes old compact boundaries' `parentUuid` from `null` to actual UUIDs, so the chain walks THROUGH old boundaries.
**Fix:** When working with forked sessions, verify ALL compact boundaries have `parentUuid: null`.

### Failure 3: Compact Boundary Without Summary (Attempt 5)
**Symptom:** Session shows "Conversation compacted" and nothing else.
**Cause:** Used `compact_boundary` for a plain dialogue-only session without proper `isCompactSummary`/`isVisibleInTranscriptOnly` flags on the summary.
**Fix:** Either use proper compact boundary + flagged summary, OR skip the boundary entirely and make the first user message the root (`parentUuid: null`).

### Failure 4: Individual Message Stripping Breaks Alternation (Attempts 10, 11)
**Symptom:** Session won't load; API rejects the request.
**Cause:** Stripping `tool_result` from individual user messages makes them empty → Claude Code skips empties → 36 consecutive assistant messages → API requires alternating user/assistant.
**Fix:** NEVER strip content from individual messages and keep them as-is. Either clear content but keep message envelopes (Approach A), or remove entire message PAIRS and rebuild the chain (Approach B → compact before).

### Failure 5: Wrong Source File (Attempt 7)
**Symptom:** Session loads but has wrong/outdated content.
**Cause:** Used the `.bak` file (stale backup) instead of the current `.jsonl`.
**Fix:** Always use the live session file. Verify the last message matches what you expect.

### Failure 6: Wrong Branch (Attempt 4a)
**Symptom:** Session loads but doesn't contain expected messages.
**Cause:** Session had multiple branches from interrupts/forks. Chain walker followed the wrong leaf.
**Fix:** Verify the last message in your chain matches the expected content before proceeding.

---

## 6. Verification Procedures

### Quick Health Check

```bash
python3 -c "
import json, sys
lines = open(sys.argv[1]).readlines()
objs = [json.loads(l) for l in lines]
uids = {o['uuid']: o for o in objs if o.get('uuid')}

# Find last leaf
last = None
for o in reversed(objs):
    if o.get('type') in ('user','assistant') and o.get('uuid') and not o.get('isSidechain'):
        last = o; break

# Walk chain
depth = 0; cur = last['uuid']
while cur and cur in uids:
    cur = uids[cur].get('parentUuid'); depth += 1

print(f'Chain: {depth} msgs, terminated: {cur is None}')
print(f'Slugs: {set(o.get(\"slug\") for o in objs if o.get(\"slug\"))}')
print(f'Lines: {len(lines)}')

# Token estimate
chars = sum(len(json.dumps(uids[u].get('message',{}))) for u in uids if uids[u].get('type') in ('user','assistant') and not uids[u].get('isSidechain'))
print(f'Est tokens: ~{chars//4:,}')
" ~/.claude/projects/<project>/<session>.jsonl
```

### Token Breakdown by Type

```python
import json

with open("session.jsonl") as f:
    objs = [json.loads(l) for l in f]

# Walk active chain (same as above)...
# Then:
tool_use_chars = 0
tool_result_chars = 0
thinking_chars = 0
text_chars = 0

for obj in chain:
    msg = obj.get('message', {})
    content = msg.get('content', '') if isinstance(msg, dict) else ''
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict): continue
            bt = block.get('type', '')
            bc = json.dumps(block)
            if bt == 'tool_use': tool_use_chars += len(bc)
            elif bt == 'tool_result': tool_result_chars += len(bc)
            elif bt == 'thinking': thinking_chars += len(bc)
            elif bt == 'text': text_chars += len(bc)
    elif isinstance(content, str):
        text_chars += len(content)

total = tool_use_chars + tool_result_chars + thinking_chars + text_chars
print(f"tool_use:    {tool_use_chars//4:>8,} tokens ({tool_use_chars*100//total:>2}%)")
print(f"tool_result: {tool_result_chars//4:>8,} tokens ({tool_result_chars*100//total:>2}%)")
print(f"thinking:    {thinking_chars//4:>8,} tokens ({thinking_chars*100//total:>2}%) [STRIP — sent to API, causes 'Prompt is too long']")
print(f"text:        {text_chars//4:>8,} tokens ({text_chars*100//total:>2}%) [keep]")
print(f"total:       {total//4:>8,} tokens")
print(f"strippable:  {(tool_use_chars+tool_result_chars+thinking_chars)//4:>8,} tokens (tools + thinking)")
```

---

## Summary: Decision Tree

```
Session hitting "Prompt is too long"?
│
├── Want to keep recent work intact?
│   └── YES → Operation 3: Compact before a specific message
│       ├── Quick: dialogue extraction for pre-cut section
│       └── Better: AI-summarized compact for pre-cut section
│
├── Just need more room, keep everything?
│   └── YES → Operation 1 + Operation 2 together
│       └── Strip tool content (saves 50-70% of estimate)
│       └── Strip thinking (saves 10-20% of estimate)
│       └── Model retains full memory
│       └── Combined: typically enough to rescue a session
│
├── Session too large to even load?
│   └── YES → Operation 1 + 2 on the JSONL file directly
│       └── Reduces file size for faster parsing
│
└── Starting fresh but want the knowledge?
    └── YES → Operation 3 with cut point at position 0
        └── Entire conversation becomes the summary

Priority order: Tools (#1, biggest savings) > Thinking (#2) > Compact (#3, most effort)
```
