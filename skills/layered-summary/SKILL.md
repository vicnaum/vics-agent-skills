---
name: layered-summary
description: Create hierarchical, bottom-up "layered summaries" by generating `AGENTS.md` files per directory in a subtree (e.g., `reth/crates/engine`). Deep directories summarize files and key APIs; parent directories roll up children. Use when you need a navigable architecture map and per-module responsibilities without code snippets, while ensuring no subfolders are missed via explicit coverage checklists.
---

# Layered Summary (AGENTS.md)

Generate a *layered* set of `AGENTS.md` files so that:
- **Deeper directories** are **more detailed** (files + key types/functions + local relationships).
- **Higher directories** are **short rollups** of their direct children (architecture map).

This is designed to pair well with Cursor's nested `AGENTS.md` support: when you work in a folder, you automatically get its local summary plus its ancestors' higher-level context.

## Hard constraints

- Create/update **only** `AGENTS.md` files. Do not change code.
- Operate **only** inside the target root subtree.
- **No code snippets** and no long excerpts. Summaries must be short and checkable (paths/names).
- Use ASCII only; avoid non-ASCII characters.
- Don't stop mid-run: continue until the **Completion criteria** is satisfied. If you are forced to pause, leave a resumable checkpoint (see **Resume after interruption**) and ensure parent ledgers are accurate.
- Skip generated/vendored/third-party blobs. If present, mention them briefly (and why they exist) but don't document them exhaustively.
- Avoid speculation. Use `[unclear]` / `[TBD]` markers when needed.

## Target root

- Use the user-provided root directory.
- If none is provided, default to `reth/crates/engine`.

## What "counts as code" (directory eligibility)

Treat a directory as *meaningful* (must have an `AGENTS.md`) if it is any of:
- **Code-bearing**: contains source files (common examples: `*.ts`, `*.tsx`, `*.js`, `*.py`, `*.go`, `*.java`, `*.kt`, `*.swift`, `*.c`, `*.h`, `*.cpp`, `*.cs`, `*.rb`, `*.rs`) or build entrypoints.
- **Module root**: contains a package/manifest/build definition (examples: `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `build.gradle`, etc.).
- **Aggregator**: contains subdirectories that are inside the target subtree (even if it has no local code), so it can roll up children.

If a directory is clearly **assets-only** (e.g., only `*.jpg/*.jpeg/*.png/*.gif/*.svg/*.webp`), still list it in the parent's "Subdirectories" section but mark it as **skipped** so it can't be "missed silently".

## Optional: use `tree` for planning & quick verification (convenience)

If `tree` is available, use it for a fast, human-readable scan of what exists and what file types appear where (useful for spotting assets-only directories vs code-bearing directories).

- **Quick overview (bounded depth)**:

```bash
tree -a -L 4 reth/crates/engine
```

- **See where `AGENTS.md` already exists**:

```bash
tree -a --prune -P 'AGENTS.md' reth/crates/engine
```

If `tree` is not available, use `find`/directory listings instead. `tree` is a convenience; the **ledger invariant** (one-hop subdirectory checklist in every `AGENTS.md`) is the correctness mechanism.

## The grounding mechanism (don't miss folders)

**Invariant:** Every `AGENTS.md` must contain a **one-hop** inventory of its immediate subdirectories, and that list must match the filesystem.

Use checkbox statuses to make progress explicit:
- `- [ ] \`child/\` - [TBD]` = discovered, not processed yet
- `- [x] \`child/\` - <short summary>` = processed, summary filled from `child/AGENTS.md`
- `- [x] \`child/\` - (skip: <reason>)` = inspected and intentionally skipped (e.g. `no code`, `generated`, `assets only`)

**Rule:** Before leaving a directory, refresh the one-hop list from the filesystem and ensure **no child directory is missing from the list**.

## Standard `AGENTS.md` template

Use this structure everywhere (omit sections that truly don't apply). **Leaf directories must be more detailed than one-line-per-file** (see "Leaf directories: required detail" below).

```markdown
# <directory-name>

## Purpose
<1-3 sentences describing what this directory provides and why it exists.>

## Contents (one hop)
### Subdirectories
- [ ] `child-a/` - [TBD]
- [ ] `child-b/` - [TBD]

### Files
- `<manifest/build file>` - <role (1-2 sentences)> (e.g. `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`)
  - **Key items**: <2-6 relevant names (scripts/targets/feature flags), or "n/a" if it's purely declarative>
- `src/<file>` - <role (1-2 sentences)>
  - **Key items**: `TypeA`, `TypeB`, `fn_x()`, `CONST_Y`
  - **Interactions** (optional): <mentions of sibling files/modules this connects to>
- `<top-level file>` - <role (1-2 sentences)> (only if relevant to this directory's responsibility)
  - **Key items**: <2-6 names>

## Key APIs (no snippets)
- **Types / Classes / Interfaces**: `Foo`, `Bar` - <1-liners>
- **Modules / Packages**: `<name>` - <1-liners> (if applicable)
- **Functions**: `qux()` - <1-liners>

## Relationships
- **Depends on**: <paths/modules> - <why>
- **Used by**: <paths/modules> - <why>
- **Data/control flow**: <3-7 bullets, high level>

## Notes
<Optional. Preserve this section across re-runs. Put [unclear] items here if needed.>
```

## All directories: minimum per-file detail

Even for non-leaf directories, avoid "just `<role>`" file entries.

Minimum requirement for every listed file:
- **Role**: 1-2 sentences describing why the file exists (not just "helpers").
- **Key items**: 2-6 concrete names (types/functions/constants/config keys) that help a reader know what to search for.
- **Interactions**: optional 1 bullet when it materially improves navigation (e.g., "feeds updates into `<other file>`", "spawns tasks consumed by `<module>`").

## Leaf directories: required detail (make it "actionable")

If a directory has code but **no subdirectories with code**, treat it as a *leaf* and write a deeper, navigational summary:

- Replace "Files" one-liners with **per-file mini-sections**.
- For each file, include:
  - **Role** (1-3 sentences: why it exists)
  - **Key items** (3-10 names: important types/functions/constants)
  - **Interactions** (how it calls/feeds other local files; mention channels/tasks/state where relevant)
  - **Knobs / invariants** (important config flags, thresholds, safety requirements, ordering assumptions)
- Add a short **End-to-end flow** for the directory (5-12 bullets) describing how the pieces work together.

Use this leaf template (still no code snippets):

```markdown
## Files (detailed)

### `<file-a>`
- **Role**: <why it exists>
- **Key items**: `TypeA`, `TypeB`, `fn_x()`, `CONST_Y`
- **Interactions**: <uses/calls/emits/consumes; point to sibling files>
- **Knobs / invariants**: <important constraints or tuning>

### `<file-b>`
- **Role**: ...
- **Key items**: ...
- **Interactions**: ...
- **Knobs / invariants**: ...

## End-to-end flow (high level)
- <step 1: where work starts, what enters>
- <step 2: how it fans out (tasks/channels), what's computed>
- <step 3: how results are joined/ordered, what is returned/emitted>
```

## Traversal order (best of both worlds)

Use a single DFS that **scaffolds on the way down** and **fills on the way up**:

1. **On entry to a directory**
   - Create or refresh `AGENTS.md`.
   - Populate **Contents (one hop)**:
    - List **all immediate subdirectories** with `- [ ] ... - [TBD]` (alphabetical).
     - List relevant local files (alphabetical).
   - If the directory has local code:
     - Write **Purpose / Key APIs / Relationships** based on local files.
     - If this directory is a **leaf**, write **Files (detailed)** + **End-to-end flow** (not just one-liners).

2. **Recurse into children (deepening)**
  - For each child directory listed under "Subdirectories":
     - If it has code or meaningful descendants: recurse into it.
     - If it has no code/descendants: mark it `- [x] ... (skip: no code/assets only/generated)`.

3. **Bubble summaries up immediately (as you return)**
  - After finishing `child/AGENTS.md`, update the parent's entry:
    - Change `[ ]` to `[x]`
    - Replace `[TBD]` with a **short** rollup (1-3 bullets or 1-2 sentences).
    - Make the rollup *actionable*: include up to **3 concrete "where to look next" pointers** (child subdirectories/files/types) when they help navigation.
  - Keep parent rollups **strictly shorter** than the child's content.

4. **Revisit parent understanding**
   - If a directory has both local code and children, after all children are completed, re-check:
    - Does the directory's **Purpose** need refinement based on what children do?
     - Do **Relationships** need an update now that you understand internal components?

## Idempotency (safe re-runs)

If an `AGENTS.md` already exists:
- Preserve any content under `## Notes`.
- Refresh "Contents (one hop)" to match the filesystem (add/remove children/files).
- Avoid churn: only rewrite summaries when you found clear evidence in code or child summaries.

## Resume after interruption

Use the checkbox ledger as your source of truth:
- Start at the target root `AGENTS.md`.
- Follow the first unchecked child entry (`- [ ]`) depth-first until you reach the next leaf to process.
- After finishing a directory, bubble its rollup up immediately (marking `[ ]` as `[x]`).

## Completion criteria

You're done when:
- Every meaningful directory in the subtree has an `AGENTS.md`.
- In every `AGENTS.md`, the "Subdirectories" list matches the filesystem (no missing children).
- No `[TBD]` remains for directories that were processed; everything is either `[x]` summarized or `[x] (skip: no code)`.
- The target root `AGENTS.md` reads like an architecture overview (components + how they connect), with details pushed down to children.

