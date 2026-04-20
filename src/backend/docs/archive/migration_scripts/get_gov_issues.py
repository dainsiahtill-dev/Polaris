import json
import os
import re
import subprocess
import sys

os.chdir(r"/")

result = subprocess.run(
    [
        sys.executable,
        "docs/governance/ci/scripts/run_catalog_governance_gate.py",
        "--workspace",
        ".",
        "--mode",
        "fail-on-new",
        "--baseline",
        "tests/architecture/allowlists/catalog_governance_gate.baseline.json",
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
    print("new_issue_count:", data.get("new_issue_count"))
    for iss in data.get("issues", []):
        print("ISSUE:", json.dumps(iss, indent=2))
    mc = data.get("manifest_catalog", {})
    print("mc_new_mismatch_count:", mc.get("new_mismatch_count", 0))
    for m2 in mc.get("new_mismatches", []):
        print("NEW_MISMATCH:", json.dumps(m2, indent=2))
    # Also print all mismatch fingerprints for comparison
    for m2 in mc.get("mismatches", []):
        fp = m2.get("fingerprint", "NO_FP")
        print("MISMATCH_FP:", fp[:16], "|", m2.get("cell_id", ""), "|", m2.get("mismatch_type", ""))
else:
    print("NO JSON FOUND")
    print("STDOUT:", stdout[:500])
    print("STDERR:", result.stderr[:500])
