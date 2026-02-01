---
name: layered-summary
description: Create hierarchical, bottom-up "layered summaries" by generating `AGENTS.md` files per directory in a subtree (e.g., `reth/crates/engine`). Deep directories summarize files and key APIs; parent directories roll up children. Also use to incrementally update an existing `AGENTS.md` subtree after content changes. No snippets; keep summaries navigational and checkable.
---

# Layered Summary (AGENTS.md)

Generate a *layered* set of `AGENTS.md` files so that:
- **Deeper directories** are **more detailed** (files + key types/functions + local relationships).
- **Higher directories** are **short rollups** of their direct children (architecture map).

This is designed to pair well with Cursor's nested `AGENTS.md` support: when you work in a folder, you automatically get its local summary plus its ancestors' higher-level context.

## Hard constraints

- Create/update **only** `AGENTS.md` files. Do not change other files.
- Operate **only** inside the target root subtree.
- **No snippets/excerpts** and no long quotes. Summaries must be short and checkable (paths/names).
- Use ASCII only; avoid non-ASCII characters.
- Delegate codebase exploration to **Explore subagents** (see "Subagent-first workflow"). The main agent remains the **only** writer of `AGENTS.md`.
- Don't stop mid-run: continue until the **Completion criteria** is satisfied. If you are forced to pause, leave a resumable checkpoint (see **Resume after interruption**) and ensure parent ledgers are accurate.
- Skip generated/vendored/third-party blobs. If present, mention them briefly (and why they exist) but don't document them exhaustively.
- Avoid speculation. Use `[unclear]` / `[TBD]` markers when needed.

## Scripts (recommended)

Use these helper scripts to make planning/scaffolding/verification deterministic and resumable.

- All scripts support Repomix-style scoping flags:
  - `--include "<glob1,glob2,...>"` (positive filter)
  - `-i/--ignore "<glob1,glob2,...>"` (negative filter; ignore wins on conflicts)
  - Globs are comma-separated and support `**` (e.g., `docs/**/*.md` matches `docs/README.md` and deeper files).
  - If you don't scope, the scripts will treat most directories with files as meaningful; use `--include`/`--ignore` to focus and avoid huge generated/vendor trees.
  - The scripts also avoid descending into huge dependency directories by default and omit them from ledgers: `node_modules/`, `bower_components/`, `jspm_packages/`. If there's some valid reason to include them, pass an explicit `--include` that targets them.

- `scripts/agents_plan.py`
  - Build a bottom-up "waves" schedule for a subtree (derived only; no writes).
  - Supports `--mode full` and `--mode update` (git-based incremental planning).
- `scripts/agents_scaffold.py`
  - Create-only scaffolder: creates missing `DIR/AGENTS.md` stubs for meaningful directories.
  - Does **not** touch existing `AGENTS.md` files. Default is dry-run; pass `--write` to create.
- `scripts/agents_verify.py`
  - Verify invariants: every meaningful dir has `AGENTS.md`, one-hop ledger matches filesystem, ASCII-only.
  - With `--strict`: also fail on remaining `[TBD]` or unchecked `- [ ]`.
- `scripts/normalize_agents_ascii.py`
  - Optional ASCII normalizer for `AGENTS.md` (default dry-run; `--write` to edit).
- `scripts/export_agents_md.py`
  - Optional export-only utility: copy `AGENTS.md` files from a subtree into a standalone folder.

## Subagent-first workflow (required)

Use Explore subagents for **every meaningful directory** you process. Explore is read-only; it should only return analysis. The main agent must create/update `AGENTS.md`.

### Concurrency / conflict rules

- Scheduling is bottom-up: start from the deepest meaningful leaf directories and work upward (postorder).
- Never run Explore on a directory until all meaningful descendants already have complete `AGENTS.md`. Always complete children first, then do the parent.
- If running multiple Explore tasks in parallel, only parallelize **siblings** (directories that do not contain each other).
- Prefer deterministic ordering (alphabetical) when choosing which sibling directories to process next, to keep results reproducible.

### Explore subagent contract (exploring mode: very thorough)

For each directory `DIR/` inside the target root:

- Run an Explore subagent with thoroughness level **very thorough** scoped to `DIR/`.
- The Explore subagent must:
  - Discover and validate the directory's **one-hop** structure (immediate subdirectories + files).
  - Identify which immediate subdirectories are meaningful vs skippable (and why).
  - Analyze local files in `DIR/` (if any). "Trust children" applies only to nested folders, not the current directory's own files.
  - If immediate child `AGENTS.md` files exist, read them and incorporate their responsibilities into PURPOSE/RELATIONSHIPS. Treat child `AGENTS.md` as the source of truth for nested subfolders.
  - Do not recursively explore child subdirectories' contents where `child/AGENTS.md` already exists. Prefer to synthesize from `child/AGENTS.md` instead.
    - Exception: if something is missing/contradictory or the filesystem ledger does not match, it is OK to spot-check the child directory to resolve the discrepancy; keep spot-checks minimal and report to orchestrator why.
  - Produce a structured report that the main agent can convert into the standard `AGENTS.md` template.
  - Provide a short "PARENT ROLLUP" the main agent can copy into the parent's subdirectory checkbox entry.
  - Use ASCII only, avoid big snippets/excerpts, and be explicit about uncertainty.

Use this exact output structure from the Explore subagent (so the main agent can paste/transform it reliably):

```text
DIRECTORY
- path: <path relative to target root>
- kind: leaf | non-leaf | aggregator-only

PURPOSE
<1-3 sentences>

PARENT ROLLUP
- <1-2 sentences or 1-3 bullets; keep it short; include up to 3 concrete pointers (subdirs/files/symbol names)>

CONTENTS (ONE HOP)
SUBDIRECTORIES
- <name>/ - process | skip (<reason>)
...

FILES
- <file> - <role (1-2 sentences)>
  - key-items: <2-10 names or config keys>
  - interactions: <optional 0-2 bullets, paths/modules only>
...

KEY APIS (NO SNIPPETS)
- types/classes/interfaces: <comma-separated names> - <optional 1-liners>
- modules/packages: <comma-separated names> - <optional 1-liners>
- functions: <comma-separated names> - <optional 1-liners>

RELATIONSHIPS
- depends-on: <paths/modules> - <why>
- used-by: <paths/modules> - <why>
- data-control-flow:
  - <3-12 bullets, high level>

LEAF-ONLY (IF LEAF)
FILES (DETAILED)
- <file>
  - role: <why it exists>
  - key-items: <3-10 names>
  - interactions: <how it connects to sibling files>
  - knobs-invariants: <important constraints/tuning>
...
END-TO-END FLOW
- <5-12 bullets>

NOTES
- <0-n bullets; include [unclear] where needed>
```

## Target root

- Use the user-provided root directory.
- If none is provided, default to `reth/crates/engine`.

## Directory eligibility (meaningful vs skippable)

This skill and the helper scripts do **not** try to detect “code” vs “non-code” by language/framework.

Instead, **scope is controlled by `--include` / `--ignore`**, and a directory is treated as *meaningful* (must have an `AGENTS.md`) when:

- It is **in-scope** (not ignored; and if `--include` is provided, it is in the include-closure), and
- It either contains **at least one in-scope non-`AGENTS.md` file**, or contains at least one **meaningful in-scope child directory** (so it can roll up children).

Directories that are out-of-scope or empty can still appear in the one-hop ledger, but must be marked as skipped with an explicit reason (e.g., `out of scope`, `generated`, `vendored`, `binary blobs`, `empty`).

## Optional: use `tree` for planning & quick verification (convenience)

If `tree` is available, use it for a fast, human-readable scan of what exists and to spot large/unwanted subtrees early.

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

**Invariant:** Every `AGENTS.md` must contain a **one-hop** inventory of its immediate subdirectories (excluding always-ignored and default-pruned dependency dirs unless explicitly included), and that list must match the filesystem under those rules.

Use checkbox statuses to make progress explicit:
- `- [ ] \`child/\` - [TBD]` = discovered, not processed yet
- `- [x] \`child/\` - <short summary>` = processed, summary filled from `child/AGENTS.md`
- `- [x] \`child/\` - (skip: <reason>)` = inspected and intentionally skipped (e.g. `out of scope`, `generated`, `vendored`, `empty`)

**Rule:** Before leaving a directory, refresh the one-hop list from the filesystem and ensure **no ledger-eligible child directory is missing from the list**.

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
- `<important local file>` - <role (1-2 sentences)> (e.g. `README.md`, `docs.md`, `package.json`, `pyproject.toml`)
  - **Key items**: <2-6 relevant names (headings/commands/config keys), or "n/a" if purely descriptive>
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

If a directory has files but **no meaningful subdirectories**, treat it as a *leaf* and write a deeper, navigational summary:

- Replace "Files" one-liners with **per-file mini-sections**.
- For each file, include:
  - **Role** (1-3 sentences: why it exists)
  - **Key items** (3-10 names: important types/functions/constants)
  - **Interactions** (how it calls/feeds other local files; mention channels/tasks/state where relevant)
  - **Knobs / invariants** (important config flags, thresholds, safety requirements, ordering assumptions)
- Add a short **End-to-end flow** for the directory (5-12 bullets) describing how the pieces work together.

Use this leaf template (still no big snippets/excerpts):

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

## Workflow (script-driven, bottom-up)

Use scripts to avoid manual recursion bookkeeping. The agent still writes `AGENTS.md`, but planning/scaffolding/verification are deterministic.

### Full run

1. **Plan waves (derived only, no writes)**
   - `python3 scripts/agents_plan.py --root <target-root>`
   - Use the bottom-up waves it prints. Within a wave, you may parallelize sibling Explore runs.

2. **Create missing stubs (create-only)**
   - `python3 scripts/agents_scaffold.py --root <target-root> --write`
   - This creates missing `DIR/AGENTS.md` stubs for meaningful directories and does not modify existing `AGENTS.md`.

3. **Process waves deepest -> shallowest**
   - For each planned directory with status `missing`/`incomplete`:
     - Run Explore (very thorough) scoped to the directory (see "Explore subagent contract").
     - Update `DIR/AGENTS.md` from the Explore report:
       - Preserve `## Notes`.
       - Ensure "Contents (one hop) -> Subdirectories" lists **all** immediate subdirectories (alphabetical), with correct `[ ]`/`[x]` and explicit skip reasons where applicable.
     - Bubble the child's Explore **PARENT ROLLUP** into the parent's checkbox entry for `child/`.

4. **Verify + normalize**
   - `python3 scripts/agents_verify.py --root <target-root> --strict`
   - `python3 scripts/normalize_agents_ascii.py --root <target-root> --fail-on-remaining` (or `--write`)
   - Optional: `python3 scripts/export_agents_md.py --src <target-root> --out <output-dir>`

## Idempotency (safe re-runs)

If an `AGENTS.md` already exists:
- Preserve any content under `## Notes`.
- Refresh "Contents (one hop)" to match the filesystem (add/remove children/files), following the same ledger-eligibility rules (always-ignored + default-pruned dependency dirs omitted unless explicitly included).
- Do not clobber existing child rollups: keep `[x]` + summary text for children that still exist, unless you are intentionally updating that child (or it is now missing/invalid).
- Avoid churn: only rewrite summaries when you found clear evidence in files or child summaries.

## Update mode (incremental, only run on-demand after content changes)

Use this mode when the subtree already has `AGENTS.md` and you want to update only what changed (plus rollups above it).

- Plan update set (git-derived):
  - `python3 scripts/agents_plan.py --root <target-root> --mode update --base-ref <ref>`
  - If you omit `--base-ref`, it uses working tree changes (staged + unstaged + untracked).
- Process only the planned directories (deepest -> shallowest) using the same Explore/write/bubble rules as a full run.
- Re-run `scripts/agents_verify.py --strict` to ensure rollups and ledgers are consistent.

## Resume after interruption

Re-run planning and continue from the deepest remaining work:

- `python3 scripts/agents_plan.py --root <target-root>`
- Continue processing directories that are still `missing`/`incomplete`, deepest waves first.
- Use `scripts/agents_verify.py --strict` as the final correctness check.

## Completion criteria

You're done when:
- Every meaningful directory in the subtree has an `AGENTS.md`.
- In every `AGENTS.md`, the "Subdirectories" list matches the filesystem (no missing children).
- No `[TBD]` remains for directories that were processed; everything is either `[x]` summarized or `[x] (skip: <reason>)`.
- The target root `AGENTS.md` reads like an architecture overview (components + how they connect), with details pushed down to children.
- `python3 scripts/agents_verify.py --root <target-root> --strict` exits 0.

