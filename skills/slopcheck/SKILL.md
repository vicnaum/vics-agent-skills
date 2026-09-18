---
name: slopcheck
description: "Keep an AI agent's writing readable for one specific reader, with named rule breaks instead of a score. A standard-library Python checker plus four Claude Code hooks: an advisory line under every long reply, a report into the agent's context after it writes any document, a deny gate on artifact publishing, and a deny gate on Slack, pull-request, Linear and Notion text until the list is empty. Checks the hard shape rules (sentence length, comma count, semicolons, em-dashes, lists run inline, paragraph and bullet length, fact-carrying parentheses), the tone tells regex can see (achievement words for unfinished work, agreement openers, sign-post phrases, contrast frames, filler words), and an advisory list of terms whose first use has no gloss. Optional model layer through the Anthropic API for inflation, unexplained jargon, bare numbers and metaphors. Use when: (1) the user complains about walls of text, jargon, sensational or inflated wording, or 'AI slop' in the agent's output, (2) the user wants their writing rules enforced on final messages, docs, artifacts or outward messages, (3) installing or tuning the hooks, (4) checking a draft before sending (slopcheck --stdin), (5) labelling a bad span for the dataset (slopmark). Triggers on: slopcheck, slopmark, writing rules, readability hook, style check, wall of text, AI slop, jargon, sensationalism, em-dashes, inline list."
---

# slopcheck: named rule breaks, four surfaces, no score

A percentage score cannot tell good writing from bad. We measured that on 45 texts one
reader complained about and 17 he praised. A readability score failed 93% of the first set
and 82% of the second.

What separates them is local and nameable:

- one term not explained
- one dramatic label
- one list run inline
- one number without its scope

So this tool reports a list of named breaks and leaves the decision to the agent. Where
the text goes to other people, it denies the send until the list is empty.

The CLI lives at `scripts/slopcheck` (installed on PATH as `slopcheck`). It is standard
library Python, no dependencies, and makes no network calls unless the model layer is on.

## What it checks

**Hard rules, reported as a named list.** Thresholds live in the config and default to:

- sentence over 30 words, or with more than 3 commas
- semicolon
- em-dash or en-dash, anywhere. Ranges use a plain hyphen: 65-95%.
- a list of 3 or more items run inline with commas and "and"
- a parenthesis carrying a second fact (an address, a date, a "so that")
- paragraph over 3 sentences, bullet over 2 sentences

**Tone and voice tells, regex only.** The achievement words are the one hard tell: they deny an outward send. The rest is advisory.

| Tell | Examples | Effect |
| --- | --- | --- |
| Achievement words for unfinished work | decided, converged, shipped, intel, breakthrough | denies an outward send |
| Agreement opener | "You're right", "Good catch" | advisory |
| Sign-post phrase | "Here's what", "Let me show", "the real question" | advisory |
| Contrast frame | "rather than", "not X but Y" | advisory |
| Filler or self-rating word | "genuinely", "honestly", "robust", "delve" | advisory |
| Hedged guarantee with a fallback | "should appear... if it does not, run..." | advisory |
| Bold sentence over 25 words | | advisory |

**Terms to check, advisory.** Acronyms and multiword names whose first use has no gloss
pattern in the same sentence. Noisy on capitalised names, so it is a prompt to the agent, not a verdict.

**Model layer, off by default.** A small Claude model judges what regex cannot. See "Model layer" below for the list and the cost.

## The four surfaces

| Surface | Hook | Behaviour |
| --- | --- | --- |
| Final reply to the user | Stop | One advisory line under replies of 60 words or more. Never blocks, so the user never sees two versions of one message. |
| Any .md, .html, .txt file the agent writes | PostToolUse on Write and Edit | The named list lands in the agent's context, so it fixes the file before finishing. Artifacts included. Code files ignored. |
| Text sent to other people | PreToolUse on Slack, GitHub, Linear and Notion tools | Denied with the list until the list is empty. Every string field counts, titles included. Dashes and semicolons are checked at any length. The agent fixes the draft and calls the tool again. |
| Publishing an artifact | PreToolUse on the Artifact tool | Denied when the page prose has an em-dash, an en-dash, or a semicolon. Quoted samples inside figures and tables are not counted. Other breaks are reported and allowed. |

Subagent hand-backs are not checked. They are working material for the main agent, not
text the user reads. Subagents do hit the document and outward gates when they write files
or send messages.

## Install (new machine or new agent CLI)

```bash
git clone https://github.com/vicnaum/vics-agent-skills ~/github/vics-agent-skills   # once
ln -s ~/github/vics-agent-skills/skills/slopcheck/scripts/slopcheck ~/bin/slopcheck  # or any PATH dir
ln -s ~/github/vics-agent-skills/skills/slopcheck/scripts/slopmark  ~/bin/slopmark
slopcheck install --dry-run   # shows what it would change
slopcheck install             # backs up ~/.claude/settings.json, wires the four hooks, writes the default config
```

Then paste the rules block from `references/rules-template.md` into your `~/.claude/CLAUDE.md`
and adjust it to your reader. The hooks enforce. The rules text tells the agent what good
looks like.

Both are needed. Instructions alone decay within a few turns in every study we found, and
hooks alone catch faults without teaching the fix.

Claude Code reloads hooks live. If no style line appears under the next long reply, run
`/hooks` once or restart the session. `slopcheck uninstall` removes the entries.

Other agents (Cursor, Codex) have no hook system. Use the CLI by hand or from the agent's own
pre-send step: `slopcheck --stdin --surface doc < draft.md`.

## Use

```bash
slopcheck FILE                          # report for a file, exit 0 clean, 1 style flags, 2 rule breaks
slopcheck --stdin --surface doc < draft # the agent's own pre-send check on a draft
slopcheck FILE --surface external       # judge as text for other people
slopcheck FILE --json                   # machine-readable
slopmark "the offending phrase" jargon  # label a bad span; themes: jargon, wall, tone, long, log, compressed, dash, metaphor, other
slopcheck labels                        # count labels by theme
```

The full report of the last check is always at `~/.config/slopcheck/last.txt`.

**Agent rule to add to CLAUDE.md.** Before any final message over 150 words, and before any
text for other people, pipe the draft through `slopcheck --stdin --surface doc` and fix what it
names. The Stop hook is advisory, so the improvement has to happen before sending.

## Config

`~/.config/slopcheck/config.json` is created on first run with these keys:

- `stop_mode`: `"advise"` prints one line and never blocks. `"block-chars"` blocks once only for a dash or a semicolon. `"block-once"` blocks once for any rule break. A block means the agent writes a second message under the first.
- `min_words`: below this the Stop hook is silent. Default 60.
- `external_min_words`: below this outward text is not checked. Default 25.
- `block_external`: deny outward sends with rule breaks or hard flags. Default true.
- `rules`: the thresholds listed above.
- `achievement_words`: the word list that denies an outward send.
- `doc_extensions`, `ignore_globs`: which written files are checked. Memory files, datasets and samples are skipped by default.
- `acronym_whitelist`: acronyms the reader knows, kept out of the terms list.
- `external_tools`: the tool names the PreToolUse gate matches. The default is the claude.ai Slack, GitHub, Linear and Notion MCP tools.
- After editing `external_tools`, run `slopcheck install` again.
- `reader_profile`: one sentence about who reads the text. Used by the model layer.
- `model_check` and the other `model_*` keys: the model layer, described below.

Kill switch: `SLOPCHECK_OFF=1` in the environment, or an empty file at `~/.config/slopcheck/off`.

## Model layer

Regex cannot see general sensationalism. It cannot tell an explained term from an unexplained one. A small model can.

To switch it on, set `model_check` to true and put an Anthropic API key in `ANTHROPIC_API_KEY`
or in `~/.config/slopcheck/anthropic.key`. The checker then asks `claude-haiku-4-5` for a JSON
judgement, in 2 to 3 seconds and for about a third of a cent per check.

What it judges:

- inflation, with the offending spans quoted
- terms the configured reader would not know
- bare numbers, metaphors where a literal phrase was available, hints instead of names
- log versus state, and whether the verdict comes first

It runs on documents of 60 words or more and outward text of 25 words or more. An outward send
is denied at a sensational score of 0.5 or higher, and also at three or more quoted spans. Every
failure path returns "skipped" and never blocks.

Two things to know:

- `model_backend: "cli"` runs a nested `claude -p` on the user's subscription instead of the API. We measured 6 to 70 seconds per call and frequent timeouts, so it is kept as an option and not recommended.
- Do not point this at a model vendor whose terms retain your text unless you accept that. The Anthropic API is the same vendor as Claude Code, so nothing new leaves.

## Design notes, from the research behind this

- Code owns counting and layout. Models cannot count reliably and read scope conditions such as "in the same sentence" literally.
- A model judges one isolated candidate well and a whole text badly. Extract the number, the term, the label in code, then ask one narrow question about it.
- Pairwise beats absolute. "Which of these two is easier to restate" was right 12 of 12 times on clean pairs where the same question as an absolute score was useless.
- Whole-text scores of any kind did not separate praised from complained texts. Local checks did.
- The report template with bold lead lines and bullets is not slop for a reader who asked for it. Do not penalise layout.
