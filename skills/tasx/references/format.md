# tasx file format & API reference

## Task file anatomy

```
id: fix-day-border              ← optional; defaults to the filename without .md
kind: task                      ← task (default) | decision
owner: hoods-orchestrator       ← agent-chat name of whoever works it (nudge target)
group: rendering                ← workstream/area chip; free text
seq: 20                         ← ordering within a section (lower first; default 999)
title: Fix the day border in local assembly
waiting-on: you                 ← only meaningful in waiting/ — see values below
option: a | Raster overlay      ← decisions only, repeatable: "<id> | <label>"
option: b | WebGL layer
choice: a                       ← written when decided (board radio or manual edit)

## What
one paragraph: the actual deliverable.

## Why
why it matters / what it unblocks.

## Next steps
- concrete next actions

## Refs
- file paths, session ids, links

## Comments
- **user** (2026-07-28 16:20): comment text
- **hoods-orchestrator** (2026-07-28 17:02): reply
```

Header = leading `key: value` lines up to the first non-matching line. Everything
after is body. All header lines are optional — a bare markdown file is a valid task
(id = filename, title = first `# heading`, state = folder).

## State = folder

| Folder          | State        | Notes |
|-----------------|--------------|-------|
| `tasks/` (root) | inbox        | untriaged / not started |
| `in-progress/`  | in-progress  | must have an `owner:` |
| `waiting/`      | waiting      | must have `waiting-on:` (patched in by `tasx move`) |
| `done/`         | done         | `done/YYYY-MM/` archive subfolders also count |
| `cancelled/`    | cancelled    | file should say why |
| `decisions/`    | (kind)       | decisions don't move; decided = `choice:` present |

`waiting-on:` values: `you` (or anything containing you/user/human or your
`$TASX_USER` name → counts as **needs you**), `decision <id>` (unblocks + nudges when that decision is
picked on the board), `external: <thing>` (world stuff — not your bottleneck).

## Legacy compatibility (myhdd-era folders)

- `blocked/` is read as `waiting/`.
- Unknown subfolders (e.g. myhdd `tasks/backup/`): a `status:` header is honored via
  aliases — `done/completed/resolved → done`, `cancelled → cancelled`,
  `in-progress → in-progress`, `needs-you → waiting (waiting-on: you)`,
  `deferred/future → waiting (waiting-on: later)`, `pending → inbox` — and the
  subfolder name becomes the `group`. In canonical state folders the folder always
  wins over any `status:` line.
- `README*.md` files are ignored everywhere.

## Staleness rules (computed, not stored)

Freshness = max(mtime, ctime) of the file — moving a file counts as touching it.

| Condition                         | Stale after |
|-----------------------------------|-------------|
| in-progress, untouched            | 24 h        |
| needs-you (waiting on you)        | 3 d         |
| open decision                     | 3 d         |
| inbox, untriaged                  | 14 d        |

`tasx doctor` also reports: in-progress tasks whose `owner:` agent-chat status is
dead/offline, and duplicate ids across folders.

## Board HTTP API (`tasx serve`)

Port: `8720 + crc32(tasks-root-path) % 64` — stable per project; `--port` overrides.
Binds 127.0.0.1 only.

- `GET /` → `scripts/board.html`
- `GET /api/state` → `{project, root, now, agents: {owner: busy|idle|waiting|dead|offline},
  docs: [{id, kind, state, title, owner, group, seq, waiting_on, options, choice,
  body, path, age_s, updated, needs_you, stale}]}`
- `POST /api/save` `{id, op, value, author?}` where op:
  - `state` → moves the file (git mv when in a repo); `waiting` also patches
    `waiting-on:` (extra field `waiting_on`, default `you`)
  - `choice` → patches `choice:` into the decision header
  - `comment` → appends to `## Comments` with author + timestamp

Every save with an `owner:` differing from the author fires
`agent-chat send --to <owner> "[tasx:<project>] ..."` in a background thread
(auto-skipped when agent-chat isn't installed). Picking a decision additionally
nudges owners of all tasks whose `waiting-on:` mentions the decision id.
