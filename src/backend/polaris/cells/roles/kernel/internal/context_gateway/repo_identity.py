"""Deterministic repo-identity card — anti-hallucination grounding (Phase-1 B1).

run20 evidence (2026-06-11): weak models guess paths by OTHER ecosystems'
conventions in any repository — 560 not-found probes over 18 instances, top
offenders README.md / src/main.py / main.py / package.json / app.py /
Cargo.toml; several final patches even CREATED package.json inside a Django
checkout. Temperature does not fix a prior; facts do. This module computes a
small, deterministic card of repository facts (dominant language, which
conventional project markers exist at the root, which do NOT, top-level
layout) for injection through the RoleSignalPlane.

§8 note: the marker/extension tables below are generic ecosystem knowledge
(the same nature as language maps or skip-dir lists elsewhere in the
platform), applied dynamically against the ACTUAL file set of whatever
workspace is open — no target-project specifics are encoded.
"""

from __future__ import annotations

import os
from collections import Counter

# Conventional project markers weak models habitually assume (positive AND
# negative assertions both derive from this one table + the real file set).
# Order = display order AND absent-list priority: the run20 not-found
# frequency spectrum leads (README 80 / main 45 / package 25 / app 25 /
# Cargo 14 …) so the cap at _MAX_ABSENT_MARKERS never drops the names weak
# models hallucinate most.
_ROOT_MARKERS: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "main.py",
    "app.py",
    "index.js",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "manage.py",
    "tsconfig.json",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "CMakeLists.txt",
    "Makefile",
)

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C/C++ header",
    ".cpp": "C++",
    ".cs": "C#",
}

_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".polaris"}
)

_MAX_SCAN_FILES = 20000
_MAX_ABSENT_MARKERS = 10
_MAX_TOP_ENTRIES = 12
_MAX_CARD_CHARS = 1400


def _scan_language_share(workspace: str) -> tuple[list[tuple[str, float]], int]:
    """Bounded walk: (top languages with share, classified source-file count)."""
    counts: Counter[str] = Counter()
    scanned = 0
    try:
        for _current_root, dirnames, filenames in os.walk(workspace):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for filename in filenames:
                scanned += 1
                if scanned > _MAX_SCAN_FILES:
                    raise StopIteration
                _, ext = os.path.splitext(filename)
                language = _EXTENSION_LANGUAGES.get(ext.lower())
                if language:
                    counts[language] += 1
    except StopIteration:
        pass
    total = sum(counts.values())
    if not total:
        return [], 0
    top = [(language, count / total) for language, count in counts.most_common(2)]
    return top, total


def build_repo_identity_card(workspace: str) -> str | None:
    """Build the deterministic identity card; None when the workspace is unusable."""
    if not workspace or not os.path.isdir(workspace):
        return None
    try:
        root_entries = sorted(os.listdir(workspace))
    except OSError:
        return None

    root_set = set(root_entries)
    present = [marker for marker in _ROOT_MARKERS if marker in root_set]
    absent = [marker for marker in _ROOT_MARKERS if marker not in root_set][:_MAX_ABSENT_MARKERS]

    top_languages, classified = _scan_language_share(workspace)

    visible = [entry for entry in root_entries if not entry.startswith(".")]
    top_level = [
        entry + "/" if os.path.isdir(os.path.join(workspace, entry)) else entry for entry in visible[:_MAX_TOP_ENTRIES]
    ]

    lines = ["【仓库身份】(确定性扫描结果,以下为事实而非推测)"]
    if top_languages:
        share_text = ", ".join(f"{language} {share * 100.0:.0f}%" for language, share in top_languages)
        lines.append(f"- 主要语言: {share_text} (基于 {classified} 个源文件)")
    if present:
        lines.append(f"- 仓库根存在的项目标记: {', '.join(present)}")
    if absent:
        lines.append(f"- 仓库根不存在(勿假设): {', '.join(absent)}")
    if top_level:
        suffix = " …" if len(visible) > _MAX_TOP_ENTRIES else ""
        lines.append(f"- 顶层条目: {', '.join(top_level)}{suffix}")
    lines.append("- 路径不存在时,用 repo_rg 搜索符号或 repo_tree 浏览;不要按其它项目的惯例假设文件存在。")

    card = "\n".join(lines)
    if len(card) > _MAX_CARD_CHARS:
        card = card[:_MAX_CARD_CHARS]
    return card
