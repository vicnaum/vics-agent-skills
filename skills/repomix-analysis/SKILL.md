---
name: repomix-analysis
description: Pack local or remote repositories with Repomix for whole-codebase, cross-file analysis. Use when you need more context than incremental file reading provides (architecture mapping, hidden couplings, security review, multi-file bug tracing). Manage context size with --top-files-len, --token-count-tree, include/ignore filtering, comment/whitespace removal, --compress, and semantic or --split-output splitting; then hand off the Repomix output + a ready-to-paste Gemini prompt to the user.
---

# Repomix Code Analysis

Use Repomix to pack a repository (or a carefully chosen subset) into a single AI-friendly file (usually XML), optimize the pack to fit a target context budget, then ask the user to run the analysis in a large-context model (Gemini) and paste back the result.

## Non-negotiables

- Treat Repomix output files as **read-only artifacts**. Make changes only in the original repo files.
- Keep Repomix security checks enabled by default. Use `--no-security-check` only if the user explicitly requests it and confirms the output is safe to share externally.
- If packing a remote repo is required but network/git access is blocked by the sandbox, ask the user to run outside the sandbox.

## Workflow

1. Define the analysis question and scope (whole repo vs subset vs multiple semantic packs).
2. Generate an initial pack (default to XML).
3. Measure size and find the true token hogs (`--top-files-len`, `--token-count-tree`).
4. Reduce/trim noise (ignore patterns, comment/whitespace removal, compression, base64 truncation).
5. Split if needed (prefer semantic splitting. Don't use `--split-output`).
6. Hand off the final pack(s) to the user with a single ready-to-paste Gemini prompt; ingest the response and continue.

## Command templates (copy/paste)

Prefer the system-installed `repomix` binary when available:

```bash
repomix --version
```

If `repomix` is not on `PATH`, replace `repomix` below with `pnpm dlx repomix@latest` (or `npx repomix@latest`).

### Local repo (local directory) → XML pack + token stats

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --top-files-len 100 --token-count-tree
```

### Focused pack (semantic split) with full tree context

```bash
repomix source_directory --style xml -o engine.xml --top-files-len 100 --token-count-tree \
  --include "src/engine/**,src/shared/**,README.md" \
  --ignore "**/*.svg,**/*.png,**/*.jpg,**/*.min.js,**/*.map,**/dist/**,**/build/**,**/node_modules/**" \
  --include-full-directory-structure
```

### Reduce tokens without losing code (much) semantics

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --remove-empty-lines --truncate-base64
```

### Architecture-first (structure/signatures over bodies)

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --compress
```

### Split output by size (for tool file-size limits)

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --split-output 1mb
```

Note: `--split-output` groups by **top-level directory**; a single file/directory will never be split across multiple output files. Prefer semantic splitting for analysis quality.

### Remote repository pack

```bash
repomix --remote user/repo --remote-branch main --style xml -o repomix-output.your-file-name.xml --top-files-len 100 --token-count-tree
```

### Add git context (only when it helps the question)

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --include-diffs --include-logs --include-logs-count 25
```

## Scope / filtering heuristics (practical)

- Keep: entrypoints, core source (`src/`), configuration, schemas/migrations, key docs (`README`, `SPEC`, ADRs), and tests that define behavior.
- Exclude first: build outputs (`dist/`, `build/`, `target/`), caches, coverage, vendored deps, large assets, huge fixtures/dumps, generated code blobs (unless directly relevant).
- Use `.repomixignore` for repeated iterations; use `--ignore` for one-offs.
- Use `--include-full-directory-structure` when you pack only a subset but still want the model to see the full repo tree.

## Include vs ignore (priority rules)

- `--include` is a **positive filter** (pick candidates). `--ignore` is a **negative filter** (remove candidates). If a file matches both, it is **excluded** (ignore wins).
- Ignore sources apply in this order (highest priority first): custom ignore patterns (`--ignore` / `ignore.customPatterns`) → ignore files (`.repomixignore`, `.ignore`, `.gitignore`, `.git/info/exclude`) → default patterns (e.g. `node_modules/**`, `dist/**`). If `--include` “doesn’t work”, it’s usually because one of these ignore sources is still excluding the file.
- To include files that are being excluded by default ignore sources, disable the relevant layer: `--no-gitignore`, `--no-dot-ignore`, and/or `--no-default-patterns`.
- `--include-full-directory-structure` only affects the **Directory Structure section**; it does not override ignore filtering.

## Gemini handoff (what to ask the user to do)

When the pack(s) are ready, ask the user to upload the file(s) to Gemini and run a prompt like this:

```text
You are analyzing a repository packed by Repomix (attached).
Use the attached Repomix file(s): <filenames>.

Task:
<the exact question and objective>

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

If you created multiple semantic packs (e.g., `engine.xml` then `plugins.xml`), provide one prompt per pack, specify the order, so user can carry forward a short summary between runs.
