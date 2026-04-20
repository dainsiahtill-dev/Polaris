#!/usr/bin/env python
"""Validate and summarize tool_calling_matrix test cases."""

import json
import os
from collections import Counter

cases_dir = "polaris/cells/llm/evaluation/fixtures/tool_calling_matrix/cases"

levels: Counter[str] = Counter()
tags: Counter[str] = Counter()
errors: list[tuple[str, str]] = []

for f in os.listdir(cases_dir):
    if not f.endswith(".json"):
        continue
    path = os.path.join(cases_dir, f)
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
            levels[data["level"]] += 1
            for tag in data.get("tags", []):
                tags[tag] += 1
    except (RuntimeError, ValueError) as exc:
        errors.append((f, str(exc)))

print("Test cases by level:")
for level in sorted(levels.keys()):
    print(f"  {level}: {levels[level]}")

print(f"\nTotal: {sum(levels.values())}")

print("\nTop tags:")
for tag, count in tags.most_common(10):
    print(f"  {tag}: {count}")

if errors:
    print("\nErrors:")
    for f, e in errors:
        print(f"  {f}: {e}")
else:
    print("\nAll files valid!")
