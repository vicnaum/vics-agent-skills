"""GPT-2 surprisal over labelled (was, now) pairs: does the original read harder than its rewrite?

  <venv>/bin/python surprisal_pairs.py samples.json [gpt2|gpt2-medium]

Prints per pair the mean bits per token and the top word on each side, then how often the "was"
side has the higher mean and the higher single word. Needs torch and transformers in the venv and
the weights in the local cache (install-structure --with-surprisal caches gpt2).
"""
import json, sys, os, importlib.machinery, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_loader("structcheck", importlib.machinery.SourceFileLoader("structcheck", os.path.join(HERE, "..", "..", "scripts", "structcheck")))
sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)

name = sys.argv[2] if len(sys.argv) > 2 else "gpt2"
S = sc.Surprisal(name)
entries = json.load(open(sys.argv[1]))

def stats(text):
    ws, bits = S.words(text)
    mean = sum(bits) / len(bits); top = max(ws, key=lambda x: x[1])
    return mean, top[1], top[0].strip(".,;:!?\"'")

mean_higher = max_higher = 0; gaps = []
for e in entries:
    a, b = stats(e["was"]), stats(e["now"])
    mean_higher += a[0] > b[0]; max_higher += a[1] > b[1]; gaps.append(a[0] - b[0])
    print(f"{e['id']:>2} {e['fault'][:30]:<30} was mean {a[0]:4.1f} max {a[1]:4.1f} @{a[2]:<14} | now mean {b[0]:4.1f} max {b[1]:4.1f} @{b[2]}")
N = len(entries)
print(f"\n{name}: the was side has the higher mean in {mean_higher}/{N} pairs, the higher single word in {max_higher}/{N}, mean gap {sum(gaps)/N:+.2f} bits per token")
