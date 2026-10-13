import re, json, sys, os
src = open(sys.argv[1], encoding="utf-8").read()
entries, cur = [], None
for line in src.splitlines():
    m = re.match(r"^## (\d+)\. (.+)$", line)
    if m:
        cur = {"id": int(m.group(1)), "fault": m.group(2).strip(), "was": "", "now": ""}
        entries.append(cur)
        continue
    m = re.match(r"^- (was|now): (.*)$", line)
    if m and cur:
        cur[m.group(1)] = m.group(2).strip().replace(" / ", " ")
tmp = sys.argv[2] + ".tmp"
json.dump(entries, open(tmp, "w"), indent=1)
os.replace(tmp, sys.argv[2])
print(len(entries), "entries", sum(1 for e in entries if e["was"] and e["now"]), "complete")
