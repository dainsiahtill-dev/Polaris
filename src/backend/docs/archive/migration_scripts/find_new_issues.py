import json
import os
import re
import subprocess
import sys

os.chdir(r"/")

# Load baseline
baseline_path = "tests/architecture/allowlists/catalog_governance_gate.baseline.json"
with open(baseline_path) as f:
    baseline = json.load(f)
baseline_fps = set(baseline.get("issue_fingerprints", []))
print(f"Baseline has {len(baseline_fps)} fingerprints")

# Run gate
result = subprocess.run(
    [
        sys.executable,
        "docs/governance/ci/scripts/run_catalog_governance_gate.py",
        "--workspace",
        ".",
        "--mode",
        "fail-on-new",
        "--baseline",
        baseline_path,
        "--mismatch-baseline",
        "tests/architecture/allowlists/manifest_catalog_mismatches.baseline.jsonl",
    ],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

stdout = result.stdout

m = re.search(r"\{.*\}", stdout, re.DOTALL)
if m:
    data = json.loads(m.group())
    print("Total issues:", data.get("issue_count"))
    print("new_issue_count:", data.get("new_issue_count"))

    new_issues = []
    for iss in data.get("issues", []):
        fp = iss.get("fingerprint", "")
        if fp not in baseline_fps:
            new_issues.append((fp, iss.get("rule_id"), iss.get("path", ""), iss.get("message", "")))

    print(f"\n{len(new_issues)} NEW issues NOT in baseline:")
    for fp, rule, path, msg in new_issues:
        print(f"  FP: {fp}")
        print(f"  Rule: {rule}")
        print(f"  Path: {path}")
        print(f"  Msg: {msg}")
        print()
else:
    print("NO JSON FOUND")
