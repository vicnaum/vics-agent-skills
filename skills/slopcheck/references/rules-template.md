# Writing rules block for CLAUDE.md

Paste this into `~/.claude/CLAUDE.md` and edit the reader line. The hooks enforce the hard
rules. This text tells the agent what good looks like, which the hooks cannot do.

```markdown
## Writing rules, for everything I read

My reader: a senior engineer new to this company. Knows the field, does not know our
internal names, product names, slang or history. Reads carefully and slowly.

A style check runs on every final message and on every .md, .html or .txt file you write
(`slopcheck`). It lists named rule breaks, never a percentage. Final replies get one
advisory line. Slack, PR, Linear and Notion text is denied until the list is empty. Before
any message over 150 words, or any Slack, PR, doc or artifact text, pipe the draft through
`slopcheck --stdin --surface doc` and fix what it names.

- **Sentences of at most 25 words, never over 30.** One verb, one idea. At most 3 commas.
- **Three or more things are a bulleted list.** Under a short label line ending with a colon. Never inline with commas and "and".
- **One idea per paragraph.** One claim, or one date, or one caveat. Paragraphs of at most 3 sentences, bullets of at most 2.
- **A section is a bold lead line, then bullets or short paragraphs.** The bold line is the heading of the chunk.
- **No semicolons. No em-dashes.** A parenthesis holding a second fact is a sign the sentence wants to be two lines.
- **Show the shape before the sentences.** The reader should see "six things, three dates, two changes" from the layout.
- **Content.** The verdict or the measured fact first, then its reasons. Every number with what it counts and over what scope, in the same sentence. Every internal term explained once where it first appears. Name things, never "a well-known vendor". Final state, not the history of the work.
- **Voice.** People as subjects, "I" and "we". Mark what is uncertain with "I think" or "not sure". Plain verbs for unfinished work: read, drafted, compared, never decided, converged, shipped. Repeat the key word instead of varying it. No agreement openers, no sign-post colons, no "rather than" contrast frames.
- To record a piece of writing I disliked: `slopmark "<span>" <theme>`.
```

## Why these rules and not a word list

The public "AI slop" lists rank vocabulary and metaphor first. One reader's 45 complaints
over six weeks ranked the faults differently:

- jargon, and terms named but not explained
- lists run inline, and walls of text
- sensational or inflated words
- length

Metaphor and AI vocabulary came last. The rules above follow that ranking.

Banned-word lists in prompts backfire in the studies we found: the model picks synonyms
and keeps the skeleton. Positive rules with a reason attached hold better. Keep the word
list in the checker, where it is a detector, and keep the rules text positive.
