# Vic's Agent Skills

My small collection of **Agent Skills** for AI coding agents.

## Skills

### [layered-summary](`skills/layered-summary/`)

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

### [repomix-analysis](`skills/repomix-analysis/`)

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

## Install / use

### Cursor

Cursor discovers skills from `.cursor/skills/` (project) or `~/.cursor/skills/` (user/global).

### Claude (Claude Code)

Claude discovers skills from `.claude/skills/` (project) or `~/.claude/skills/` (user/global).

### Codex (OpenAI)

Codex discovers skills from `.codex/skills/` (project) or `~/.codex/skills/` (user/global).

Then open agent chat and ask for something related to a skill (or invoke the skill explicitly).

