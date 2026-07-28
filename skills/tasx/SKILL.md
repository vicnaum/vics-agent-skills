---
name: tasx
description: "File-based task tracker for projects: tasks live as markdown files in a tasks/ folder, state = folder location (root = inbox, in-progress/, waiting/, done/, cancelled/, decisions/ for open choices), agents manage tasks by moving the files; plus a zero-dependency local board UI (tasx serve) where the user changes states, answers decisions, and leaves comments that are written straight back into the md files and nudge the owning agent via agent-chat. Use when: (1) the user asks to set up / init a task tracker or tasks folder in a project, (2) the user or agent needs to create, list, move, complete, cancel, or comment on tasks in a repo that has a tasks/ folder, (3) the user asks 'what's in progress', 'what needs me', 'what's stale', or wants a task board / dashboard served, (4) starting a work session in a repo with tasks/ (run tasx doctor and reconcile), (5) a task is blocked on the user's feedback or on a decision — file it as waiting/ or a decision instead of asking and losing the thread, (6) generalizing/migrating older ad-hoc task folders (myhdd-style) onto the shared convention. Triggers on: tasks folder, task tracker, task board, tasx, kanban, what's in progress, needs my feedback, stale tasks, task dashboard."
---

# tasx — file-based tasks, folder = state

Markdown files are the source of truth; the folder a file sits in IS its state. No
database, no daemon: `ls tasks/in-progress/` is a kanban, `git mv` is a status change,
and the board UI is just a viewer/editor over the same files. The CLI lives at
`scripts/tasx` (installed on PATH as `tasx`).

## Layout

```
tasks/               INBOX — new/untriaged tasks as *.md
  README.md          the convention doc (written by `tasx init`)
  in-progress/       actively worked — set `owner:` to your agent-chat name
  waiting/           blocked — `waiting-on:` header says on what (see below)
  done/              completed (kept; `tasx archive` rolls old ones into done/YYYY-MM/)
  cancelled/         dropped — say why in the file
  decisions/         open choices: `option:` lines; picking writes `choice:` → decided
```

Task file = optional header lines (`id:`, `kind:`, `owner:`, `group:`, `seq:`,
`title:`, `waiting-on:`, `option:`, `choice:`), blank line, then a markdown body
(**What / Why / Next steps / Refs**), optionally ending with `## Comments`.
Headerless files work too (id = filename, title = first `#` heading) — legacy
myhdd-style folders parse as-is, `blocked/` is read as `waiting/`.
Full format spec: [references/format.md](references/format.md).

## Commands

```bash
tasx init                          # create tasks/ tree + README + CLAUDE.md/AGENTS.md pointer
tasx new "Title" [--owner me] [--group area] [--body "..."] [--id slug]
tasx new "Which renderer?" --decision --option "a | raster" --option "b | webgl"
tasx list [--all]                  # kanban to stdout (needs-you / in-progress / inbox / waiting)
tasx move <id> <state> [--waiting-on "you|decision <id>|external: <thing>"]
tasx comment <id> "text" --as <your-agent-name>    # timestamped; nudges the owner
tasx doctor                        # staleness + hygiene report — run at session start
tasx archive [--days 30]           # roll old done/ files into done/YYYY-MM/
tasx serve [--port N]              # board UI on a stable per-project port (127.0.0.1)
```

Commands find the nearest `tasks/` walking up from cwd (`$TASX_TASKS` overrides).

## Agent contract

Working in a repo that has a `tasks/` folder means playing by these rules:

1. **Session start**: run `tasx doctor`. Reconcile what you own — finished things go
   to `done`, stalled things get a comment saying why, tasks owned by a dead agent
   get taken over or moved back to the inbox.
2. **Starting work**: `tasx move <id> in-progress` and set `owner:` to your
   agent-chat name (edit the header or create the task with `--owner`). One owner
   per task. Don't work on tasks owned by a live agent — `agent-chat status <name>`.
3. **Blocked on the user**: don't ask and idle. Move it: `tasx move <id> waiting
   --waiting-on you`, put the question in the file (or `tasx comment`). If it's a
   real fork in the road, create a decision (`tasx new ... --decision --option ...`)
   and set `--waiting-on "decision <id>"` — the user answers with one click on the board
   and you get nudged via agent-chat.
4. **Finishing**: `tasx move <id> done` + a short `tasx comment <id> "what shipped,
   where" --as <name>`. Cancelling requires a why.
5. **New work discovered mid-task**: don't silently expand scope — `tasx new` it into
   the inbox and continue.
6. **Session end**: nothing should be in `in-progress/` under your name unless you
   (or a respawn of you) are actually continuing it. Move it back or comment its state.
7. Keep files lightweight; body sections **What / Why / Next steps / Refs**. Comments
   are append-only with author + timestamp — never rewrite others' comments.

## The board (`tasx serve`)

For the human, not for agents (agents use the CLI/files). Serves `scripts/board.html`
on a stable per-project port. Reads nothing but the md files; every edit writes back:
status dropdown physically moves the file (git mv-aware), decision radios write
`choice:`, comments append to `## Comments` — and each of those **nudges the owning
agent** through `agent-chat send --to <owner>` (when agent-chat is installed), so
answering on the board resumes the work. Comments send only on an explicit button
or ⌘⏎ (drafts survive re-renders, nothing fires on blur). On needs-you cards the
primary action is **answer & resume**: append the answer, move the task back to
in-progress (inbox if unowned), clear `waiting-on:`, one nudge — the human never
needs to touch the state dropdown just to hand a question back. UI shows: pinned **Needs you** section +
count in the tab title, in-progress with owner liveness dots (`agent-chat status`)
and freshness ages, ⚠ stale chips (in-progress >24h, waiting-on-you >3d, inbox >14d),
a "since your last visit" digest, and a collapsed done/cancelled archive grouped by day.

Run it in background when asked: `nohup tasx serve > /tmp/tasx-board.log 2>&1 &`
then read the port from the log's first line.

## Install (new machine / new agent CLI)

Symlink the skill folder into the agent's skill dirs and put `tasx` on PATH:

```bash
ln -s ~/github/skills-creation/skills/tasx ~/.claude/skills/tasx
ln -s ~/github/skills-creation/skills/tasx ~/.codex/skills/tasx
ln -s ~/github/skills-creation/skills/tasx ~/.cursor/skills/tasx
ln -s ~/github/skills-creation/skills/tasx/scripts/tasx ~/bin/tasx   # or any PATH dir
```

Python 3.8+ stdlib only. agent-chat integration is optional and auto-detected.
Optionally `export TASX_USER=<yourname>`: board comments get authored with it, and
`waiting-on: <yourname>` counts as needs-you alongside the generic you/user/human.
