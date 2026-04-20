"""Scan polaris/ for exception swallowing patterns."""

from __future__ import annotations

import os
import re
import sys

# Patterns to detect exception swallowing
PATTERNS = [
    # except Exception: pass (same line)
    re.compile(r"except\s+Exception\s*:\s*(?:pass|#[^\n]*\n\s*pass)\b"),
    # except Exception:\n    pass (multiline)
    re.compile(r"except\s+Exception\s*:\s*\n(\s+)pass\b"),
]

# Also find bare except Exception with pass within next 3 lines
BARE_EXCEPT = re.compile(r"except\s+Exception\s*:")


def scan_file(path: str) -> list[dict]:
    """Return list of exception swallowing incidents in a file."""
    results = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return results

    i = 0
    while i < len(lines):
        line = lines[i]
        # Check for 'except Exception:'
        if BARE_EXCEPT.search(line):
            # Check if pass follows within next 3 lines
            rest = "".join(lines[i + 1 : i + 4])
            if re.search(r"\bpass\b", rest):
                # This is a swallow pattern
                start = max(0, i - 1)
                end = min(len(lines), i + 4)
                context = "".join(lines[start:end])
                results.append(
                    {
                        "path": path,
                        "lineno": i + 1,
                        "context": context,
                        "except_line": line.rstrip(),
                    }
                )
                i += 1
                continue
        i += 1
    return results


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "polaris"
    all_results: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            incidents = scan_file(path)
            all_results.extend(incidents)

    print(f"Total exception swallowing incidents: {len(all_results)}")
    print()

    # Group by file
    from collections import defaultdict

    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in all_results:
        by_file[r["path"]].append(r)

    # Sort by count descending
    sorted_files = sorted(by_file.items(), key=lambda x: len(x[1]), reverse=True)

    print("=== TOP 25 FILES BY INCIDENT COUNT ===")
    for i, (path, incidents) in enumerate(sorted_files[:25]):
        print(f"{i + 1}. {path}: {len(incidents)} incidents")
        for inc in incidents:
            print(f"   L{inc['lineno']}: {inc['except_line'][:80]}")

    print()
    print("=== ALL FILES SUMMARY ===")
    for path, incidents in sorted_files:
        print(f"{path}: {len(incidents)}")


if __name__ == "__main__":
    main()
