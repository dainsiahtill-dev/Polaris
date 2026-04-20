from __future__ import annotations

import re

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,63}")
STOP_WORDS = {
    "fix",
    "issue",
    "problem",
    "bug",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "make",
}


def normalize_task_tokens(task: str) -> list[str]:
    raw = TOKEN_RE.findall(task.lower())
    deduped: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in STOP_WORDS:
            continue
        if token not in seen:
            deduped.append(token)
            seen.add(token)
    return deduped


def build_candidate_files(
    indexed_files: list[str],
    changed_files: list[str] | None = None,
) -> list[str]:
    unique = sorted(set(indexed_files))
    if not changed_files:
        return unique
    changed = [item.replace("\\", "/") for item in changed_files]
    changed_set = set(changed)
    # Strong constraint: always include changed files when they exist in index.
    prioritized = [path for path in unique if path in changed_set]
    remaining = [path for path in unique if path not in changed_set]
    return prioritized + remaining
