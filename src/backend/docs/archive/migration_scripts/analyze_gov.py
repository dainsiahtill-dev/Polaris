import json
from collections import Counter

with open("C:/Temp/gov2.txt", encoding="utf-8") as f:
    text = f.read()
text = text.replace("\r", "")
start = text.find("{")
data = json.loads(text[start:])

print("=== AUDIT SUMMARY ===")
print(f"  issue_count:       {data.get('issue_count')}")
print(f"  blocker_count:     {data.get('blocker_count')}")
print(f"  high_count:        {data.get('high_count')}")
print(f"  new_issue_count:   {data.get('new_issue_count')}")
print(f"  mc_blocker_count:  {data.get('mc_blocker_count')}")

issues = data.get("issues", [])
print(f"\n=== depends_on drift ({len(issues)} issues) - top cells ===")
cell_from = Counter()
for i in issues:
    path = i.get("path", "").replace("\\", "/")
    parts = path.split("/")
    if "cells" in parts:
        idx = parts.index("cells")
        if idx + 2 < len(parts):
            cell_from[f"{parts[idx + 1]}.{parts[idx + 2]}"] += 1
        elif idx + 1 < len(parts):
            cell_from[parts[idx + 1]] += 1
    else:
        cell_from[path[:60]] += 1
for c, n in cell_from.most_common(15):
    print(f"  {n:3d}  {c}")

print("\n=== All rule_id counts ===")
rules = Counter(i.get("rule_id", "?") for i in issues)
for r, n in rules.most_common():
    print(f"  {n:3d}  {r}")

mc = data.get("manifest_catalog_mismatch", {})
print(f"\n=== manifest_catalog_mismatch: count={mc.get('mismatch_count')} new={mc.get('new_mismatch_count')} ===")
mismatches = mc.get("mismatches", [])
mtype = Counter(m.get("mismatch_type", "?") for m in mismatches)
for t, n in mtype.most_common():
    print(f"  {n:3d}  {t}")
mc_cells = Counter(m.get("cell_id", "?") for m in mismatches)
print("  Top cells by mismatch count:")
for c, n in mc_cells.most_common(12):
    print(f"    {n:3d}  {c}")
