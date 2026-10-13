# Structure layer study

How we chose the structure rules, and how to rerun the study on pairs of your own.

**Data.** One reader rewrote 54 sentences on one internal page on 13 October 2026 and labelled each fault in one line. The pairs are that reader's work text and stay out of this repo. `extract_pairs.py` turns such a markdown list into `samples.json`.

The list format it reads:

```
## 1. hidden clause inside the subject
- was: the sentence as it stood
- now: the rewrite
```

The two runs, from the layer's venv:

```
~/.local/share/slopcheck/venv/bin/python eval_pairs.py samples.json [en_core_web_sm]
~/.local/share/slopcheck/venv/bin/python surprisal_pairs.py samples.json [gpt2]
```

The first script shows every rule that fired on each side and the per-rule counts. It ends with the originals that got no hard break, which is what the parser cannot see. The second script shows the mean and the peak surprisal per side.

**Results on the 54 pairs, small spaCy model, final rule set:**

| | original | rewrite |
| --- | --- | --- |
| pairs with a deny or hard break | 23 of 54 | 11 of 54 |
| pairs with any break | 34 of 54 | 20 of 54 |
| the side with the higher mean surprisal, gpt2 | 44 of 54 | 10 of 54 |
| the side with the higher mean surprisal, gpt2-medium | 41 of 54 | 13 of 54 |

Per rule, pairs flagged on the original against the rewrite:

| rule | original | rewrite |
| --- | --- | --- |
| passive | 10 | 6 |
| no-verb | 8 | 4 |
| heavy-subject | 7 | 4 |
| subject-verb-gap | 6 | 3 |
| appositive-in-list | 4 | 1 |
| appositive-wedged | 3 | 2 |
| parse-failure | 2 | 0 |
| fragment | 2 | 2 |
| noun-pile | 2 | 2 |

**What we pruned.** Four rules fired as often on the rewrite as on the original, because the rewrites keep the same domain names. We removed them.

- prepositional attachment, 11 originals against 10 rewrites
- coined hyphen compounds, 12 against 9
- three-noun piles, 10 against 9
- announced center-embedding, where a "that" or a comma opens the clause

Two more rules fired more on the rewrite side, each at 1 against 3, and are advisory now: zero-relative and center-embedding.

**The peak word does not separate the pairs.** Domain tokens produce a spike on both sides, a service name or a camel-case field for example. The mean per token does separate them, so we report the spike list as a pointer, not as a verdict.

**Caveat.** We tuned the thresholds on these same 54 pairs. The gap will be smaller on new text, and the small parser misreads some clean sentences.
