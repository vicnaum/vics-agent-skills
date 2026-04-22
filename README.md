# Vic's Agent Skills

My small collection of **Agent Skills** for AI coding agents.

## Skills

### [layered-summary](skills/layered-summary/SKILL.md)

Summarizes the whole repo to get a layered overview - each folder will have it's own `AGENTS.md` file that will describe what's contained.
The agent starts from the deepest folders, summarizing files, then goes up - summarizing the summaries. This is why it's called "layered" - `AGENTS.md` are layered summaries across a subtree (deep dirs are detailed; parents roll up children).

This helps agents working on a big repo to better understand where to look for things.

> Best used if you install `tree` locally with `brew install tree`

#### Example ask

```
Make a layered-summary of `crates/engine`
```

#### Example output

Agent creates `AGENTS.md` like this (in the `crates/engine` folder, with similar files in each subfolder):

````
# engine

## Purpose
Top-level engine subsystem directory: contains the crates that implement reth’s Engine API handling, live-sync “engine tree”, orchestration/persistence wiring, and supporting utilities for testing/debugging.

## Contents (one hop)
### Subdirectories
- [x] `invalid-block-hooks/` — Debugging hooks for invalid blocks (e.g., witness generation via `InvalidBlockHook`).
- [x] `local/` — Local/dev-chain components that generate Engine API traffic (`LocalMiner`, payload attributes builder).
- [x] `primitives/` — Shared Engine API types/traits/events/config (`BeaconEngineMessage`, `ConsensusEngineEvent`, `TreeConfig`).
- [x] `service/` — `EngineService` wiring that composes engine-tree + downloader + pipeline backfill into a pollable service.
- [x] `tree/` — Core `reth-engine-tree` implementation (Engine API request handling, in-memory tree state, backfill, downloads, persistence).
- [x] `util/` — Stream utilities around engine message streams (store/replay/skip/reorg simulation).

### Files
- (none)

## Key APIs (no snippets)
- **Main building blocks**:
  - `tree/` — `EngineApiTreeHandler`, `BasicEngineValidator`, `ChainOrchestrator`, `PipelineSync`, `PersistenceHandle`
  - `service/` — `EngineService`
  - `primitives/` — `BeaconEngineMessage`, `ConsensusEngineHandle`, `ConsensusEngineEvent`, `TreeConfig`

## Relationships
- **Integrated by**: node launch/wiring layers outside this subtree (Engine API RPC ingress → engine message stream → engine service/tree).
- **Design intent**: keep Engine API responsiveness high by minimizing DB writes on the critical path and delegating persistence/pruning to background workers.
````

### [repomix-analysis](skills/repomix-analysis/SKILL.md)

Ask your repo things for free and with huge context. This skill uses Repomix to pack a repo or a subset of interest into an AI-friendly file for whole-codebase, cross-file analysis. After packing, the agent gives you a question-prompt that you can paste to a free Gemini 3 Pro Playground along with the packed context file (Gemini 3 Pro supports up to 1 Million tokens context and is 100% free from the web Playground).

To the contrary of modern agents that read files L1-200 lines at a time, this helps if you want to investigate huge repos, find dependencies, see the architecture at a whole, or just ask "how does this thing work in this repo?" without missing anything important.

> Best used if you install Repomix locally with `brew install repomix`.

#### Example ask:
```
How does peer persistence work (disk format + lifecycle) in reth? I want how peer records are stored, loaded on startup, updated during runtime, and any pruning/expiration behavior. Use repomix-analysis skill for this and verify its claims manually later.
```

#### Example answer

The model will pack relevant information and present you with the following:

````
Repomix pack is ready at `reth/repomix-peer-persistence.xml ` (≈833k tokens; excluded `crates/net/eth-wire/testdata/**` and mdx/assets to fit size).  

Please upload that file to Gemini and run this prompt, then paste the result here so I can verify it against the repo:

```
You are analyzing a repository packed by Repomix (attached).
Use the attached Repomix file(s): repomix-peer-persistence.xml.

Task:
Explain how peer persistence works in reth: disk format + lifecycle. Include how peer records are stored, loaded on startup, updated during runtime, and any pruning/expiration behavior.

Constraints:
- Cite file paths for every non-trivial claim.
- When reasoning about behavior, trace cross-file call paths and data flow.
- Avoid speculation. If information is missing or ambiguous, state what’s missing and what additional pack/file would resolve it.

Output format:
- Summary (3–7 bullets)
- Key files/modules (path → responsibility)
- Detailed analysis
- Actionable next steps / follow-up questions (only if needed)
```

Once you paste the Gemini output, I’ll manually verify the claims in the codebase and deliver the final answer.
````

Then just copy the prompt into a big model and get the answer back to the Agent for verification.

### [init-context](skills/init-context/SKILL.md)

Build a complete mental map of a project through phased exploration. The agent reads root-level docs, spawns sub-agents to explore major source directories / tests / CI / GitHub history in parallel, fills gaps, and synthesizes a full project briefing — architecture, open work, developer guide, and a "where to find things" quick reference.

After this runs, the agent has full project context and shouldn't need to rediscover anything.

#### Example ask

```
Explore this repo and give me a full briefing
```

```
Init context — I want to start a deep work session on this codebase
```

### [session-stripper](skills/session-stripper/SKILL.md)

```
             ....
           .::::::::..
         .::: ::::'::::
         ,::::::'  `::::
         :: :::     `:::.
        .:::::_     _::::
       .:::::  `  -' ::::
      .::: :'-o-  -o-::::.
      ::: ::         |:.::
      ::::::     >   |::.:.
      `: :::     __   :::.:
      .:::::\   --' / :::::.
      ::: :: `-._.-. .::::::
     .::::::      | . :::.::
     ;:.::::.     |. .:::.::.
   _.::.:::::     | `..:::.::
  /.;::.: :::-._  \  :::::::'
 / ::::. :::.        ::::.::
.  :::::.::::.       `:::....
|  :::.:.:::::        :::::::
|  ::::::.:.:.         `::.::
|  `:.: ::::::.         .::::.
`.  ::::::::.::        .:.:::.
 | ::: :::::::'       .::;:::'
 | `:: :::.:::  '     ::::.::
 |  `::: :..:: /   \  :: ::::
 |   `::::::::/     \ ::.::::
 |    `::::::::      `-`:::::::'
 |    ||     ``           | ``'
 |    |`                  |\
 `.   | \                 \ \
  |  _|  \                 \ `-
  |   \   \                 \  `\
  |    \  |                  \   \
  `.   |  |                   >-  \
   |   `  |        o)        /_    )
   `    | /                 |/ |   |
    \   | |                    ||| |
        ` |                   //////
     \   ||                _-//////\
      .  `|              .'  \//// .
      `   \        ___.-'    /`-'  |
       \   \`-----'         /      |
       `    \_     :F_P:   /       |
        \     `\ _        /        |
         \    <\\ `-__.-|'        .'
        / \    \\>  |   `.        |
       /  |   . \   '    |        |
      /   \\ \ \ \ /     |        |
           \\ \ -//      `.       |
     /      '`-.//        |       |
    /           /         |       |
```

CLI tool for trimming Claude Code JSONL sessions that hit "Prompt is too long". Strips tool content (60%+ of context), thinking blocks, images — or persists them to files with AI-generated summaries. 12 commands, no external dependencies (Python 3.8+ stdlib only).

Full technical deep-dive in [references/surgery-report.md](skills/session-stripper/references/surgery-report.md).

#### Commands

| Command | What it does |
|---------|-------------|
| `analyze` | Token breakdown by type and tool name, cut points, health check |
| `strip-tools` | Clear tool inputs/results (granular: by tool name, inputs/results only, keep last N lines) |
| `strip-thinking` | Remove thinking blocks |
| `strip-all` | Strip tools + thinking in one pass |
| `compact` | Summarize early messages behind compact boundary, keep recent work intact |
| `verify` | Chain integrity check (parentUuid, slug, timestamps) |
| `show-tool` | List or inspect individual tool calls with context |
| `persist-tool` | Save tool result to file, replace with summary + reference |
| `persist-tools` | Bulk persist (like microcompact, keeps last 3 intact) |
| `show-thinking` | List or inspect individual thinking blocks with context |
| `persist-thinking` | Save thinking to file, replace with summary |
| `persist-thinkings` | Bulk persist all thinking blocks |

#### Example ask

```
My session is hitting "Prompt is too long" — help me strip it
```

```
Analyze this session and tell me where the tokens are going
```

### [ai-conversation-extractor](skills/ai-conversation-extractor/SKILL.md)

Convert AI conversation JSONL transcripts (Claude Code, Codex CLI, ChatGPT/Gemini) to clean, readable Markdown. Strips binary blobs (base64 images, PDFs — typically 95%+ of file size) while preserving the full human-readable conversation: user messages, assistant text, thinking, tool calls, and tool results.

Supports four auto-detected formats: **Claude Code** sessions (`~/.claude/`), **Codex CLI** sessions (`~/.codex/`), **Codex history** prompt logs, and **ChatGPT/Gemini** simple exports. Also includes a messages-only mode (`--ua-final-only`) that keeps just user prompts and final assistant responses — useful for clean conversation summaries.

Includes a detailed [JSONL format reference](skills/ai-conversation-extractor/scripts/README.md) documenting record types, content blocks, and folder structures.

> No external dependencies — uses only Python 3.12+ stdlib.

#### Example ask

```
Convert my Claude Code conversations in docs/conversations/ to readable Markdown
```

```
Convert my Codex sessions to messages-only Markdown in ./converted/
```

#### Example usage

```bash
# Full conversion (all formats auto-detected)
python3 <skill-dir>/scripts/extract.py docs/conversations/ --recursive

# Messages-only view to a specific output directory
python3 <skill-dir>/scripts/extract.py ~/.codex/sessions/ --out-dir ./converted/ --ua-final-only
```

Converts a 34MB JSONL transcript to ~670KB of readable Markdown (1.7% of original).

### [claude-desktop-chat-export](skills/claude-desktop-chat-export/SKILL.md)

Export a claude.ai (Claude Desktop / web) conversation and convert it to a Claude Code CLI JSONL session, so it becomes resumable via `claude -r <sessionId>`.

claude.ai chats aren't resumable — once you hit the context limit or close the tab, the conversation is read-only. Claude Code CLI stores every session as JSONL at `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` and resumes on demand. Same underlying model; only the harness (system prompt, tools, output length) differs.

The skill walks you through: (1) grabbing `conversation.json` + images from the browser console at claude.ai (or via the `claude-in-chrome` MCP + bundled `scripts/relay_server.py`), (2) organizing them locally, (3) running `scripts/convert_to_cli.py` to produce a CC-compatible JSONL, (4) resuming with `claude -r <uuid>`.

> No external Python dependencies — stdlib only (Python 3.10+).

#### Example ask

```
I hit the context limit on this claude.ai chat — help me continue it in Claude Code
```

```
Export this Claude Desktop conversation and import it into CC
```

## Install / use

### Cursor

Cursor discovers skills from `.cursor/skills/` (project) or `~/.cursor/skills/` (user/global).

### Claude (Claude Code)

Claude discovers skills from `.claude/skills/` (project) or `~/.claude/skills/` (user/global).

### Codex (OpenAI)

Codex discovers skills from `.codex/skills/` (project) or `~/.codex/skills/` (user/global).

Then open agent chat and ask for something related to a skill (or invoke the skill explicitly).

