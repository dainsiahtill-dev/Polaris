"""Shared workspace-relative path normalization for repair_kernel planners.

Historical copies of ``_normalize_repair_path`` / ``_normalize_base_files`` diverged
across language modules (strict vs permissive, posix-norm vs substring ``/../``
checks). New code must import from this module instead of re-implementing.

Semantics (frozen until explicit migration PRs change call sites):

* **strict** — reject absolute paths, ``..`` segments / ``/../`` substrings, and
  empty results after ``./`` stripping. Matches the majority of ``*_syntax``
  modules and ``typescript_syntax`` (v4).
* **permissive** — strip ``./`` only; keep absolute and ``..`` paths. Matches
  several ``*_runtime`` modules that historically accepted raw keys.

Do not silently unify call sites: each consumer must opt into a named API so
path acceptance sets stay auditable.
"""

from __future__ import annotations

from collections.abc import Mapping


def normalize_repair_path_strict(path: str) -> str:
    """Normalize a workspace-relative repair path with traversal rejection.

    Returns an empty string when the path is missing, absolute, or contains a
    parent-directory reference (``../`` prefix or ``/../`` substring).
    """

    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.startswith("../")
        or "/../" in normalized
    ):
        return ""
    return normalized


def normalize_repair_path_permissive(path: str) -> str:
    """Normalize separators and strip leading ``./`` without rejecting traversal.

    Preserves absolute paths and ``..`` segments. Prefer ``strict`` for any
    planner that writes or plans file mutations unless a runtime module has an
    explicit historical dependency on permissive acceptance.
    """

    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_base_files_strict(base_files: Mapping[str, str]) -> dict[str, str]:
    """Return path→content map using :func:`normalize_repair_path_strict` keys.

    Entries whose path normalizes to empty are dropped. Content is coerced with
    ``str(content or "")``.
    """

    normalized: dict[str, str] = {}
    for raw_path, content in dict(base_files or {}).items():
        path = normalize_repair_path_strict(str(raw_path or ""))
        if path:
            normalized[path] = str(content or "")
    return normalized


def normalize_base_files_permissive(base_files: Mapping[str, str]) -> dict[str, str]:
    """Return path→content map using :func:`normalize_repair_path_permissive` keys.

    Empty keys after normalization are dropped.
    """

    normalized: dict[str, str] = {}
    for raw_path, content in dict(base_files or {}).items():
        path = normalize_repair_path_permissive(str(raw_path or ""))
        if path:
            normalized[path] = str(content or "")
    return normalized


__all__ = (
    "normalize_base_files_permissive",
    "normalize_base_files_strict",
    "normalize_repair_path_permissive",
    "normalize_repair_path_strict",
)
