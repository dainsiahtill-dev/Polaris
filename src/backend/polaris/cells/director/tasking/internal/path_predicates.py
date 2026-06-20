"""Pure path / token predicates for Director worker target resolution.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 3). Every function here is a pure leaf: it depends only on its arguments
and the standard library (``os``, ``re``), holds no ``self`` state, and performs
no I/O beyond ``os.path`` string predicates.

It MUST NOT import ``code_generation_engine`` / ``file_apply_service`` at module
top (lazy circular-import contract documented in ``worker_executor``).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import os
import re

from polaris.domain.entities import Task

_TASK_TOKEN_STOPWORDS = {
    "according",
    "acceptance",
    "criteria",
    "execute",
    "goal",
    "implementation",
    "task",
    "test",
    "tests",
}


def is_probable_file_path(path: str) -> bool:
    """Check if string looks like a file path."""
    if not path:
        return False
    # Must have extension or look like a path
    has_ext = "." in path and len(path.rsplit(".", maxsplit=1)[-1]) <= 5
    has_slash = "/" in path or "\\" in path
    return has_ext or has_slash


def is_concrete_target_file_path(path: str) -> bool:
    """Return true when a PM target path names a file, not a directory scope."""
    token = str(path or "").strip().replace("\\", "/")
    if not token or token.endswith("/"):
        return False
    if os.path.isabs(token) or ".." in token.split("/"):
        return False
    leaf = os.path.basename(token)
    if not leaf:
        return False
    known_extensionless = {
        ".dockerignore",
        ".env",
        ".env.example",
        ".gitignore",
        "dockerfile",
        "go.mod",
        "go.sum",
        "license",
        "makefile",
        "readme",
    }
    if leaf.lower() in known_extensionless:
        return True
    return "." in leaf


def is_test_like_target_file(path: str) -> bool:
    """Return whether a target path is likely to produce expensive test/spec output."""
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    return (
        "/test/" in f"/{normalized}"
        or "/tests/" in f"/{normalized}"
        or normalized.endswith(".test.ts")
        or normalized.endswith(".test.tsx")
        or normalized.endswith(".test.js")
        or normalized.endswith(".spec.ts")
        or normalized.endswith(".spec.tsx")
        or normalized.endswith(".spec.js")
    )


def path_under_scope(path: str, scope: str) -> bool:
    normalized_path = path.strip().replace("\\", "/").strip("/")
    normalized_scope = scope.strip().replace("\\", "/").strip("/")
    if not normalized_path or not normalized_scope:
        return False
    if "." in os.path.basename(normalized_scope):
        normalized_scope = os.path.dirname(normalized_scope).replace("\\", "/").strip("/")
    return normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/")


def looks_like_test_scope(scope: str) -> bool:
    """Return whether a scope is intended for tests."""
    scope_text = str(scope or "").strip().lower().replace("\\", "/")
    normalized = f"/{scope_text}/"
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or normalized.endswith("/test/")
        or normalized.endswith("/tests/")
    )


def test_filename(slug: str, extension: str) -> str:
    """Return a conventional test filename for the given source extension.

    ``extension`` is supplied by the caller (the coordinator resolves it via the
    workspace-dependent ``_preferred_source_extension``); this keeps the
    predicate pure and free of workspace I/O.
    """
    if extension == ".py":
        return f"test_{slug.replace('-', '_')}.py"
    if extension == ".js":
        return f"{slug}.test.js"
    return f"{slug}.test.ts"


def task_ascii_tokens(task: Task) -> list[str]:
    """Extract stable ASCII tokens from task fields."""
    parts = [
        str(getattr(task, "subject", "") or ""),
        str(getattr(task, "description", "") or ""),
    ]
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    for key in ("acceptance_criteria", "execution_checklist"):
        value = metadata.get(key)
        if isinstance(value, list):
            parts.extend(str(item or "") for item in value)
    text = " ".join(parts).replace("_", " ").replace("-", " ").lower()
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z][a-z0-9]{1,}", text):
        if token in _TASK_TOKEN_STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= 12:
            break
    return tokens


def task_slug(task: Task) -> str:
    """Build an ASCII slug from task text for synthesized file names."""
    tokens = task_ascii_tokens(task)
    if not tokens:
        return "implementation"
    return "-".join(tokens[:4])


def scope_candidate_sort_key(path: str, tokens: list[str]) -> tuple[int, int, str]:
    """Prefer paths whose names match task tokens, then shorter paths."""
    lowered = str(path or "").lower()
    token_hits = sum(1 for token in tokens if token and token in lowered)
    return (-token_hits, len(lowered), lowered)


def extract_description_target_path(line: str) -> str | None:
    """Extract an explicit target path from one description line.

    This fallback is intentionally conservative. PM acceptance text can
    contain API routes, filenames used as evidence labels, or prose with
    punctuation; those are not target-file contracts and must not pollute
    Director prompts.
    """
    cleaned = str(line or "").strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"^[\d]+[.)]\s*", "", cleaned)
    cleaned = re.sub(r"^[-*•]\s*", "", cleaned)
    cleaned = cleaned.strip()
    labeled = re.match(
        r"(?i)^(?:file|path|target|target_file|target files?|目标文件|文件)\s*[:：]\s*(.+?)\s*$",
        cleaned,
    )
    if labeled:
        cleaned = labeled.group(1).strip()
    cleaned = cleaned.strip("`'\"")
    if any(ch.isspace() for ch in cleaned):
        return None
    if any(ch in cleaned for ch in "[]{}()，。；；：,;"):
        return None
    normalized = cleaned.replace("\\", "/")
    if is_probable_file_path(normalized) and is_concrete_target_file_path(normalized):
        return normalized
    return None
