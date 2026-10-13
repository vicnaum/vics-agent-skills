"""Run the structure layer over labelled (was, now) pairs and count what fires on each side.

  <venv>/bin/python eval_pairs.py samples.json [en_core_web_sm]

samples.json: a list of {"id", "fault", "was", "now"}; extract_pairs.py builds it from a markdown
list of "## N. fault" / "- was:" / "- now:" entries. The data stays out of the repo (it is the
owner's own text). Prints one line per pair, the per-rule counts, and the pairs with no hard break
on the "was" side, which is what the parser cannot see.
"""
import json, sys, os, collections, importlib.machinery, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_loader("structcheck", importlib.machinery.SourceFileLoader("structcheck", os.path.join(HERE, "..", "..", "scripts", "structcheck")))
sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)

model = sys.argv[2] if len(sys.argv) > 2 else "en_core_web_sm"
nlp = sc.load_spacy(model); wn = sc.load_wordnet()
entries = json.load(open(sys.argv[1]))
cnt = {"was": collections.Counter(), "now": collections.Counter()}
hard = {"was": 0, "now": 0}; anyb = {"was": 0, "now": 0}; missed = []
for e in entries:
    res = {}
    for side in ("was", "now"):
        r = sc.analyse(nlp, e[side], wn)
        rl = sorted({b["rule"] for b in r["breaks"]}); hr = sorted({b["rule"] for b in r["breaks"] if b["severity"] == "hard"})
        res[side] = (rl, hr, r)
        for x in rl: cnt[side][x] += 1
        hard[side] += bool(hr); anyb[side] += bool(rl)
    w, n = res["was"], res["now"]
    print(f"{e['id']:>2} {e['fault'][:38]:<38} WAS[{'+'.join(w[1]) or '-'}|{'+'.join(x for x in w[0] if x not in w[1]) or '-'}]  NOW[{'+'.join(n[1]) or '-'}|{'+'.join(x for x in n[0] if x not in n[1]) or '-'}]")
    if not w[1]: missed.append((e["id"], e["fault"], [b["msg"] for b in w[2]["breaks"]][:2]))
N = len(entries)
print(f"\nmodel {model}: pairs with a HARD break on the was side {hard['was']}/{N}, on the now side {hard['now']}/{N}")
print(f"pairs with ANY break: was {anyb['was']}/{N}, now {anyb['now']}/{N}\nper rule, pairs flagged (was / now):")
for rl in sorted(set(cnt["was"]) | set(cnt["now"]), key=lambda r: -cnt["was"][r]):
    print(f"  {rl:<28} {cnt['was'][rl]:>2} / {cnt['now'][rl]:>2}  {'hard' if rl in sc.HARD else 'advisory'}")
print("\nWAS entries with no hard break (what the parser cannot see):")
for i, f, adv in missed: print(f"  {i:>2} {f}  advisory: {adv}")
