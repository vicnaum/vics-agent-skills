---
name: repomix-analysis
description: Pack repositories with Repomix for whole-codebase, cross-file analysis. Default to a whole-repo measurement pass, then a filtered whole-repo pack; if it fits under ~1M tokens, stop and hand off to Gemini. Only if it doesn’t fit after the initial noise filter, reduce scope at folder granularity (avoid file-by-file selection), then iterate based on Gemini feedback.
---

# Repomix Code Analysis

Use Repomix to pack a repository into a single AI-friendly file (usually XML), then ask the user to run the analysis in a large-context model (Gemini) and paste back the result.

Default behavior: **broad strokes first**. Do a quick whole-repo measurement pass, then re-pack with obvious non-code/unrelated noise excluded. If that filtered whole-repo pack fits within ~**1,000,000 tokens**, do not over-optimize—hand it off to Gemini.

## Non-negotiables

- Treat Repomix output files as **read-only artifacts**. Make changes only in the original repo files.
- Keep Repomix security checks enabled by default. Use `--no-security-check` only if the user explicitly requests it and confirms the output is safe to share externally.
- If packing a remote repo is required but network/git access is blocked by the sandbox, ask the user to run outside the sandbox.
- Do **not** open/search the Repomix output (XML) before Gemini handoff. Decide what to exclude using only token stats + folder/filename paths.
- Exception: only do a targeted search when the user explicitly asks to find a specific known identifier.

## Workflow

1. Define the analysis question.
2. First pass (measurement): generate a **whole-repo** pack (default to XML) and measure tokens (`--top-files-len`, `--token-count-tree`). Treat this pack as **measurement-only**; you usually won’t upload it to Gemini.
3. Define an **initial “obvious noise” filter** (file types + folders) from the token stats (no content inspection; use only paths).
4. Second pass (filtered whole repo): re-pack the **whole repo** with that initial filter applied. If the filtered pack is **less than 1,000,000 tokens**, **stop here** and **handoff to Gemini** (upload the **filtered** pack). Do **not** spend time “curating” code files (unless there are clearly some auxiliary non-code artifacts that must be excluded anyway).
5. If still over budget, widen exclusions in this order (re-pack and re-check after each): **other non-essential** → **tests** → **examples**.
6. If still over budget after that, reduce scope at **folder granularity** based on the question + token tree (avoid file-by-file selection). Prefer docs for usage questions; prefer code for implementation questions. Use `--include-full-directory-structure` so Gemini still sees the full tree.
7. If needed, create a small number of **semantic packs by subsystem/folder** (e.g. 2–5 packs), not a pile of single-file packs (user will run the prompt once per pack, sequentially).
8. Handoff pack(s) to Gemini. If Gemini’s answer is vague/weird but points at relevant areas, do a **second pass**: re-pack focusing on those folders and ask again with a narrower question.

## Command templates (copy/paste)

Prefer the system-installed `repomix` binary when available:

```bash
repomix --version
```

If `repomix` is not on `PATH`, replace `repomix` below with `pnpm dlx repomix@latest` (or `npx repomix@latest`).

### Local repo (first pass / measurement) → XML pack + token stats

```bash
repomix source_directory --style xml -o repomix-output.your-file-name.xml --top-files-len 100 --token-count-tree
```

### Local repo (second pass / filtered whole repo) → XML pack + token stats

Start with a small “obvious noise” ignore list and adjust it based on the first pass token stats.

```bash
repomix source_directory --style xml -o repomix-output.filtered.xml --top-files-len 100 --token-count-tree \
  --ignore "**/dist/**,**/build/**,**/target/**,**/coverage/**,**/node_modules/**,**/*.svg,**/*.png,**/*.jpg,**/*.jpeg,**/*.gif,**/*.pdf,**/*.zip" \
  --remove-empty-lines --truncate-base64
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

- Default: pack the **whole repo first** to get token stats. Apply an initial “obvious noise” filter (non-code/unrelated folders + file types), then re-pack. If still over budget, widen exclusions (**other non-essential** → **tests** → **examples** → **docs**). Only then exclude code/docs by relevance (prefer folders/modules).
- Keep: entrypoints, core source (`src/`), configuration, schemas/migrations, key docs (`README`, `SPEC`, ADRs), and tests that define behavior.
- Exclude first: build outputs (`dist/`, `build/`, `target/`), caches, coverage, vendored deps, large assets, huge fixtures/dumps, generated code blobs, and large repo-meta (e.g. changelogs/release notes) unless directly relevant.
- Use `.repomixignore` for repeated iterations; use `--ignore` for one-offs.
- Use `--include-full-directory-structure` when you pack only a subset but still want the model to see the full repo tree.
- Prefer **folder-level** `--include`/`--ignore` patterns (and a few key docs/config files) over curating individual files.

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
