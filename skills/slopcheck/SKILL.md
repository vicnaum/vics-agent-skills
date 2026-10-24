---
name: slopcheck
description: "Keep an AI agent's writing readable for one specific reader, with named rule breaks instead of a score. A standard-library Python checker plus five Claude Code hooks, one of them a per-turn reminder of the rules: an advisory line under every long reply, a report into the agent's context after it writes any document, a deny gate on artifact publishing, and a deny gate on Slack, pull-request, Linear and Notion text until the list is empty. Checks the hard shape rules (sentence length, comma count, semicolons, em-dashes, lists run inline, paragraph and bullet length, fact-carrying parentheses), the tone tells regex can see (achievement words for unfinished work, agreement openers, sign-post phrases, contrast frames, filler words), and an advisory list of terms whose first use has no gloss. Optional model layer, off by default, through the Anthropic API or the TypeSafe Jev API, for inflation, unexplained jargon, bare numbers and metaphors. Optional structure layer, installed by `slopcheck install-structure`, that parses each sentence with spaCy in its own venv and names what regex cannot see: a sentence the parser cannot read, no verb, an appositive wedged between subject and verb, four nouns in a row, a seven-word subject, and GPT-2 surprisal spikes on request. Use when: (1) the user complains about walls of text, jargon, sensational or inflated wording, or 'AI slop' in the agent's output, (2) the user wants their writing rules enforced on final messages, docs, artifacts or outward messages, (3) installing or tuning the hooks, (4) checking a draft before sending (slopcheck --stdin), (5) labelling a bad span for the dataset (slopmark), (6) a sentence is grammatical but unreadable: garden path, hidden clause, wedged appositive, noun pile, no verb. Triggers on: slopcheck, slopmark, structcheck, writing rules, readability hook, style check, structure check, wall of text, AI slop, jargon, sensationalism, em-dashes, inline list, garden path, appositive."
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
- em-dash or en-dash, anywhere. Ranges use a plain hyphen: 65-95%. This is the one rule checked in every field at any length, and the only character rule that denies.
- more than one semicolon in a sentence. Reported, never denied: semicolons are normal, chains are not.
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

**Model layer, off by default.** A small model judges what regex cannot, through the Anthropic API or the TypeSafe Jev API. See "Model layer" below.

**Structure layer, off by default.** A dependency parse of each sentence names faults of structure, from a verbless sentence to a wedged appositive. It runs in its own venv and never touches the network, see "Structure layer" below.

## The four surfaces

| Surface | Hook | Behaviour |
| --- | --- | --- |
| Every prompt you send | UserPromptSubmit | One reminder line with the rules, built from your config thresholds, so the rules stay in recent context. |
| Final reply to the user | Stop | One advisory line under replies of 60 words or more. Never blocks, so the user never sees two versions of one message. |
| Any .md, .html, .txt file the agent writes | PostToolUse on Write and Edit | The named list lands in the agent's context, so it fixes the file before finishing. Artifacts included. Code files ignored. |
| Text sent to other people | PreToolUse on Slack, GitHub, Linear and Notion tools | Denied until the blocking items are fixed. The denial lists those first, then the full report. Every string field counts, titles included. Dashes are checked at any length, code spans excluded. Notion markup such as callouts, spans, mentions and toggles is stripped before any layer reads the text. The agent fixes the draft and calls the tool again. |
| Publishing an artifact | PreToolUse on the Artifact tool | Denied when the page prose has an em-dash or an en-dash. Quoted samples inside figures and tables are not counted. Other breaks are reported and allowed. |

Subagent hand-backs are not checked. They are working material for the main agent, not
text the user reads. Subagents do hit the document and outward gates when they write files
or send messages.

## Install (new machine or new agent CLI)

```bash
git clone https://github.com/vicnaum/vics-agent-skills ~/github/vics-agent-skills   # once
ln -s ~/github/vics-agent-skills/skills/slopcheck/scripts/slopcheck ~/bin/slopcheck  # or any PATH dir
ln -s ~/github/vics-agent-skills/skills/slopcheck/scripts/slopmark  ~/bin/slopmark
slopcheck install --dry-run   # shows what it would change
slopcheck install             # backs up settings.json and CLAUDE.md, wires the hooks and the reminder, writes the rules block and the default config
```

Install puts the rules block between markers at the end of `~/.claude/CLAUDE.md`. Edit the
reader line there. A CLAUDE.md that already mentions slopcheck is left alone, so your own
block wins. `slopcheck rules` prints the block if you want to place it yourself.

The hooks enforce. The reminder line repeats the rules every turn. The rules text tells the
agent what good looks like.

Both are needed. Instructions alone decay within a few turns in every study we found, and
hooks alone catch faults without teaching the fix.

Claude Code reloads hooks live. If no style line appears under the next long reply, run
`/hooks` once or restart the session. `slopcheck uninstall` removes every hook entry, the reminder,
and the managed rules block. `--no-reminder` and `--no-rules` skip those two steps. An existing
reminder or rules block of your own is kept as it is.

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
slopcheck install-structure [--with-surprisal]   # venv with spaCy, WordNet and optionally GPT-2, then the structure layer is on
structcheck FILE                        # the structure layer alone, exit 0 clean, 1 advisory, 2 hard breaks
```

The full report of the last check is always at `~/.config/slopcheck/last.txt`.

**Agent rule to add to CLAUDE.md.** Before any final message over 150 words, and before any
text for other people, pipe the draft through `slopcheck --stdin --surface doc` and fix what it
names. The Stop hook is advisory, so the improvement has to happen before sending.

Structure items in that report are breaks to fix, the same as the shape rules. The rules template
says so, and the per-turn reminder repeats it once the structure layer is on. Agents skip advisory
items they have no instruction for, so the instruction has to exist in all three places.

## Config

`~/.config/slopcheck/config.json` is created on first run with these keys:

- `stop_mode`: `"advise"` prints one line and never blocks. `"block-chars"` blocks once only for a dash. `"block-once"` blocks once for any rule break. A block means the agent writes a second message under the first.
- `min_words`: below this the Stop hook is silent. Default 60.
- `external_min_words`: below this outward text is not checked. Default 25.
- `block_external`: deny outward sends with rule breaks or hard flags. Default true.
- `rules`: the thresholds listed above.
- `SLOPCHECK_CONFIG=/path/to/config.json` in the environment points the checker at another config, for a project or a test.
- `achievement_words`: the word list that denies an outward send.
- `doc_extensions`, `ignore_globs`: which written files are checked. Memory files, datasets and samples are skipped by default.
- `acronym_whitelist`: acronyms the reader knows, kept out of the terms list.
- `external_tools`: the tool names the PreToolUse gate matches. The default is the claude.ai Slack, GitHub, Linear and Notion MCP tools.
- After editing `external_tools`, run `slopcheck install` again.
- `reader_profile`: one sentence about who reads the text. Used by the model layer.
- `model_check` and the other `model_*` keys: the model layer, described below.
- `structure_check`: the structure layer, described below. `slopcheck install-structure` switches it on.
- `structure_venv`: where that layer's venv lives. Default `~/.local/share/slopcheck/venv`.
- `structure_model`: the spaCy model it parses with. Default `en_core_web_sm`.
- `structure_surprisal`: GPT-2 surprisal spikes, local and offline. Needs the `--with-surprisal` install.
- `structure_deny`: the structure rules that deny an outward send. Default: none, the layer only reports.
- `structure_timeout`: seconds before a hook gives up on the layer and goes on without it. Default 15.

Kill switch: `SLOPCHECK_OFF=1` in the environment, or an empty file at `~/.config/slopcheck/off`.

**Override, one blocked call at a time.** `slopcheck allow` lets the next blocked send or publish through, then the gate closes again. It is meant for a false alarm, or for a text the user wants sent as it is.
- `slopcheck allow 3 --minutes 30 --reason "user approved the wording"` lets three calls through within 30 minutes. The defaults are one call within 10 minutes.
- `slopcheck allow --status` shows a pending override, and `slopcheck allow --clear` drops it.
- Each use is logged to `~/.config/slopcheck/overrides.jsonl` with the reason and what it would have blocked. The session also sees a line that names the override.
- An agent runs it only when the user asks. The user can run it from the Claude Code prompt as `! slopcheck allow`.

## Model layer

Regex cannot see general sensationalism. It cannot tell an explained term from an unexplained one. A small model can. The layer is off by default, and two backends exist. Both send the checked text to a third party under that party's terms, so switch one on only for an account whose terms you accept.

**Anthropic backend, `model_backend: "api"`.** One JSON judgement from `claude-haiku-4-5`, in 2 to 3 seconds, for about a third of a cent per check. Key in `ANTHROPIC_API_KEY` or `~/.config/slopcheck/anthropic.key`. Same vendor as Claude Code, so nothing new leaves if you already use Claude Code.

**TypeSafe Jev backend, `model_backend: "jev"`.** Jev is a System One model: it answers yes-or-no questions with calibrated probabilities and generates nothing. The checker asks one narrow question per candidate, which is the design that tested best: each sentence for achievement words, metaphor and drama, each number for its scope, each term for whether this reader knows it and whether the text explains it, and the whole text for tone, naming, final state and verdict first. Four to six requests, about 4 seconds in total. Key in `TYPESAFE_API_KEY`, `~/.config/slopcheck/typesafe.key`, or `~/.config/typesafe/.env`. Model name in `jev_model`, default `jev-latest`.

What either backend reports:

- inflation, with the offending spans quoted
- terms the configured reader would not know, used without explanation
- bare numbers, metaphors where a literal phrase was available, hints instead of names
- log versus state, and whether the verdict comes first

To switch on: set `model_check` to true and `model_backend` to `"api"` or `"jev"`, and provide the key. The layer runs on documents of 60 words or more and outward text of 25 words or more. An outward send is denied at a sensational score of 0.5 or higher, and also at three or more quoted spans. Every failure path returns "skipped" and never blocks.

`model_backend: "cli"` runs a nested `claude -p` on the user's subscription instead of an API key. We measured 6 to 70 seconds per call and frequent timeouts, so it is kept as an option and not recommended.

## Structure layer

Regex counts words and commas. It cannot see that "Items this work needs ship now" hides a clause inside its subject, or that an appositive sits between a subject and its verb. A dependency parser can.

The layer lives in `scripts/structcheck` and runs in its own venv, so slopcheck itself stays standard-library. It is off until installed.

**Install.** `slopcheck install-structure` creates the venv, installs spaCy with the small English model and WordNet, runs a self-test and sets `structure_check` to true. With `--with-surprisal` it also installs torch and caches the GPT-2 weights, about 1 GB in all. Nothing leaves the machine afterwards: the layer runs with the Hugging Face offline flags set, so a missing model is an error, never a download.

Hard rules, listed first in the report:

- parse-failure: the parser found no well-formed reading. The example sentence above gets this.
- no-verb and no-finite-verb: the sentence has no verb, or only a participle.
- fragment: a finite verb with no subject.
- appositive-wedged: three or more words between a subject and its verb, or between a verb and its complement.
- appositive-in-list: an appositive inside a comma list.
- noun-pile: four nouns in a row.
- heavy-subject: seven or more words before the verb.

Advisory rules:

- center-embedding and zero-relative: a clause inside the subject with no "that" and no comma.
- subject-verb-gap and passive.
- bare-present-plan: "the fix lands before the upgrade", a rule written as a prediction.
- ambiguous-subordinator: "now, while", where "now" primes the time reading.
- adverb-as-modifier and bare-noun-subject: "a now value", "Owner lane names the team".
- surprisal-spike, described next.

Nothing denies by default. The layer reports and the agent fixes, like the terms list. The key `structure_deny` names rules that refuse an outward send, and the study below is too small to trust one with that.

The small parser also misreads some clean sentences, such as "the first prints every rule" with "prints" as a noun. That is the other reason the layer only reports.

Three exemptions keep the verb rules honest. A bullet or a bold lead line under 15 words may be a noun phrase, and a line with a colon label such as "Gate: none" is not a sentence. Quoted spans are replaced by a placeholder noun before parsing, because a quoted word is a mention, not prose.

**Surprisal spikes.** With `structure_surprisal` on, GPT-2 scores every word by how unexpected it is, in bits. A word far above its sentence's level and above 16 bits is reported, at most eight per text. Domain names spike too, so read the list as words a newcomer may stumble on.

**What it measured.** One reader rewrote 54 sentences on one page and labelled each fault. The layer ran on both versions of every pair:

| | original | rewrite |
| --- | --- | --- |
| pairs with a deny or hard break | 23 of 54 | 11 of 54 |
| pairs with any break | 34 of 54 | 20 of 54 |
| the side with the higher mean surprisal | 44 of 54 | 10 of 54 |

The misses are the semantic faults, about 12 of the 54. Those belong to the model layer. The spikes landed on the complained-about word in about half of the lexical faults, such as a verb used as a noun.

The semantic faults in that reader's labels:

- vague
- unclear
- meaning inverted
- an instruction written as a fact
- a rule written as a prediction

Thresholds were tuned on these same pairs, so expect a smaller gap on new text. Rules that fired as often on the rewrite as on the original were dropped: prepositional attachment, coined hyphen compounds, three-noun piles. The scripts are in `research/structure`, and the pairs stay with their owner.

**Cost and limits.** About 2 seconds per check for the parse and 5 with surprisal, most of it loading models. A hook gives up on the layer after `structure_timeout` seconds and reports the rest.

Every rule depends on the parse, and the small spaCy model misparses some clean sentences. The transformer model needs a Rust toolchain on Python 3.14 and was not tested.

## Design notes, from the research behind this

- Code owns counting and layout. Models cannot count reliably and read scope conditions such as "in the same sentence" literally.
- A model judges one isolated candidate well and a whole text badly. Extract the number, the term, the label in code, then ask one narrow question about it.
- Pairwise beats absolute. "Which of these two is easier to restate" was right 12 of 12 times on clean pairs where the same question as an absolute score was useless.
- Whole-text scores of any kind did not separate praised from complained texts. Local checks did.
- The report template with bold lead lines and bullets is not slop for a reader who asked for it. Do not penalise layout.
