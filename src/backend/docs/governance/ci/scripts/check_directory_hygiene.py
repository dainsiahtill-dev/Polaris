#!/usr/bin/env python3
"""
Directory hygiene checker for Polaris backend.

Scans the backend root for temporary, cache, and auto-generated directories
that should be gitignored. Any such directory that exists on disk but is NOT
covered by .gitignore rules is reported as a violation.

Coverage logic (per directory):
  1. If `git check-ignore` matches the directory path itself -> covered.
  2. Else, if ANY .gitignore pattern would match a hypothetical file
     inside the directory (e.g. ``dirname/.gitkeep``) -> covered.
  3. Else -> violation (the directory exists without any gitignore protection).

This handles both trailing-slash patterns (ignore directory) and ``/*``
patterns (ignore contents only), and tolerates directories that contain
already-tracked files (the pattern check on step 2 still applies).

Usage:
    python check_directory_hygiene.py [--workspace BACKEND_ROOT]

Exit code equals the number of violations (0 = clean). Suitable for CI gates.

Checks performed:
  - Exact-name cache dirs: __pycache__, .mypy_cache, .pytest_cache,
    .ruff_cache, .transcripts, .polaris, workspace, runtime, htmlcov
  - Glob-matched temp dirs: .tmp_* (e.g. .tmp_pytest_context)
"""

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Directory patterns that should always be gitignored
# ---------------------------------------------------------------------------

# Exact directory names (relative to backend root)
EXACT_GITIGNORED_DIRS: List[str] = [
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".transcripts",
    ".polaris",
    "workspace",
    "runtime",
    "htmlcov",
]

# Glob patterns matching temp/cache directories (relative to backend root)
GLOB_GITIGNORED_DIRS: List[str] = [
    ".tmp_*",  # e.g. .tmp_pytest_context, .tmp_agent_router
]

# Sentinel filename used to test gitignore coverage of directory contents
_GITKEEP = ".gitkeep"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def find_repo_root(path: Path) -> Path:
    """Walk up from *path* to find the git repository root (.git directory)."""
    current = path.resolve()
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    # Fallback: return the resolved path itself
    return path.resolve()


def _git_available() -> bool:
    """Return True if the git CLI is available on PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_git_ignored(repo_root: Path, repo_rel_path: str) -> Optional[bool]:
    """Check if *repo_rel_path* is ignored by git (via ``git check-ignore``).

    Returns:
        True  -- path is gitignored
        False -- path is NOT gitignored
        None  -- could not determine (git unavailable, timeout, etc.)
    """
    if not _git_available():
        return None

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", repo_rel_path],
            cwd=str(repo_root),
            capture_output=True,
            timeout=30,
        )
        # exit 0 = ignored, 1 = not ignored, 128+ = error
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None  # Non-standard exit
    except (subprocess.TimeoutExpired, OSError):
        return None


# ---------------------------------------------------------------------------
# Gitignore pattern matching (fallback / contents-coverage check)
# ---------------------------------------------------------------------------

def _gitignore_pattern_matches(pattern: str, path: str) -> bool:
    """Best-effort gitignore pattern matcher.

    Handles:
      - Trailing-slash patterns (directory-only)
      - Leading-slash or mid-slash anchored patterns
      - fnmatch globs (``*``, ``?``, ``[seq]``)
      - ``**`` wildcards (collapsed to single-level matching)
      - Negation (``!``) is handled by the caller.
    """
    dir_only = pattern.endswith("/")
    clean = pattern.rstrip("/")

    # Git treats patterns without a slash as matching any path component.
    has_slash = "/" in clean

    normalized = path.replace("\\", "/")

    if "**" in clean:
        # Collapse ** into single wildcard for simplified matching
        collapsed = clean.replace("**/", "*").replace("/**", "/*")
        if fnmatch.fnmatch(normalized, collapsed):
            return True
        tail = os.path.basename(normalized)
        if fnmatch.fnmatch(tail, collapsed):
            return True
        return False

    if has_slash:
        # Anchored pattern -- match against the full relative path
        # Also try prefix matching for directory patterns like ``src/backend/runtime/``
        if fnmatch.fnmatch(normalized, clean):
            return True
        if dir_only and normalized.startswith(clean):
            return True
        # For patterns like ``src/backend/runtime/*``, check if the path
        # lives under the directory prefix
        if clean.endswith("/*"):
            prefix = clean[:-2]
            if normalized.startswith(prefix + "/"):
                return True
        return False
    else:
        # Unanchored pattern -- match against any path component
        for part in normalized.split("/"):
            if fnmatch.fnmatch(part, clean):
                return True
        # Also match the full path itself
        if fnmatch.fnmatch(normalized, clean):
            return True
        return False


def _collect_gitignore_rules(repo_root: Path) -> List[Tuple[str, bool]]:
    """Walk up from *repo_root* collecting .gitignore rules.

    Returns list of (pattern, is_negation) tuples.
    Patterns from inner directories override outer ones (standard git
    semantics: later rules take precedence, and we insert inner rules first).
    """
    rules: List[Tuple[str, bool]] = []
    current = repo_root
    while current != current.parent:
        gi = current / ".gitignore"
        if gi.is_file():
            try:
                for line in gi.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    negate = stripped.startswith("!")
                    pattern = stripped[1:] if negate else stripped
                    # Insert at front so inner (later-processed) rules
                    # take priority in the final list order.
                    rules.insert(0, (pattern, negate))
            except Exception:
                pass
        current = current.parent
    return rules


def _any_gitignore_pattern_covers(repo_root: Path, repo_rel_path: str) -> bool:
    """Check if any .gitignore pattern would match *repo_rel_path*.

    Used as a fallback when ``git check-ignore`` isn't available, and also
    used to check hypothetical paths (e.g. ``dirname/.gitkeep``) for
    directory-contents coverage.
    """
    rules = _collect_gitignore_rules(repo_root)
    ignored = False
    for pattern, negate in rules:
        if _gitignore_pattern_matches(pattern, repo_rel_path):
            ignored = not negate
    return ignored


# ---------------------------------------------------------------------------
# Coverage decision
# ---------------------------------------------------------------------------

def is_directory_covered(
    repo_root: Path,
    repo_rel_dir: str,
    dirpath: Path,
) -> bool:
    """Return True if *dirpath* is protected by gitignore.

    Strategy:
      1. Try ``git check-ignore`` on the directory path itself.
      2. Try ``git check-ignore`` on a real file inside the directory (if any).
      3. Check if any .gitignore pattern would match a hypothetical
         ``dirname/.gitkeep`` file (catches ``/*`` content-only patterns).
      4. Check if any .gitignore pattern matches the directory path itself
         via manual parsing (catches trailing-slash patterns when git
         check-ignore was unavailable).
    """
    repo_rel_dir = repo_rel_dir.rstrip("/")

    # 1. git check-ignore on directory path
    result = check_git_ignored(repo_root, repo_rel_dir)
    if result is True:
        return True

    # 2. git check-ignore on a real contained file (handles /* patterns
    #    when at least one file exists -- git won't ignore the dir itself
    #    but will ignore its contents).
    if result is False:
        try:
            for child in dirpath.iterdir():
                if child.is_file():
                    child_rel = (repo_rel_dir + "/" + child.name).replace(
                        "\\", "/"
                    )
                    if check_git_ignored(repo_root, child_rel) is True:
                        return True
                    break  # Only need one sample
        except (OSError, PermissionError):
            pass

    # 3. Hypothetical .gitkeep match (catches content-only patterns
    #    like ``dirname/*`` even when the directory is empty).
    hypothetical = (repo_rel_dir + "/" + _GITKEEP).replace("\\", "/")
    if _any_gitignore_pattern_covers(repo_root, hypothetical):
        return True

    # 4. Pattern match on the directory path itself (catches trailing-slash
    #    patterns when git was unavailable or returned indeterminate).
    if result is None:
        if _any_gitignore_pattern_covers(repo_root, repo_rel_dir):
            return True

    return False


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_violations(
    workspace: Path,
    repo_root: Path,
) -> List[Tuple[str, str]]:
    """Scan *workspace* for hygiene violations.

    Returns list of (relative_path, reason) tuples.
    """
    violations: List[Tuple[str, str]] = []

    # Compute repo-relative prefix for the workspace
    try:
        workspace_rel = workspace.resolve().relative_to(repo_root.resolve())
    except ValueError:
        workspace_rel = Path(".")
        print(
            f"WARNING: Workspace {workspace} is not inside repo root"
            f" {repo_root}. Gitignore checks may be unreliable.",
            file=sys.stderr,
        )

    def _repo_rel(local_name: str) -> str:
        return str(workspace_rel / local_name).replace("\\", "/")

    # -- Exact-name checks --
    for dirname in EXACT_GITIGNORED_DIRS:
        dirpath = workspace / dirname
        if not dirpath.is_dir():
            continue

        repo_rel = _repo_rel(dirname)
        if is_directory_covered(repo_root, repo_rel, dirpath):
            continue

        violations.append(
            (
                dirname,
                f"Directory '{dirname}/' exists but is NOT covered by"
                " .gitignore",
            )
        )

    # -- Glob-pattern checks --
    for glob_pattern in GLOB_GITIGNORED_DIRS:
        for match in sorted(workspace.glob(glob_pattern)):
            if not match.is_dir():
                continue
            local_name = str(match.relative_to(workspace)).replace("\\", "/")
            repo_rel = _repo_rel(local_name)

            if is_directory_covered(repo_root, repo_rel, match):
                continue

            violations.append(
                (
                    local_name,
                    f"Temp directory '{local_name}/' (matches"
                    f" '{glob_pattern}') exists but is NOT covered by"
                    " .gitignore",
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check directory hygiene for Polaris backend"
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help=(
            "Path to backend root directory. "
            "Defaults to the backend root (four levels up from this script)."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-violation details; only print summary line.",
    )
    args = parser.parse_args()

    # Resolve workspace path
    if args.workspace:
        workspace = Path(args.workspace).resolve()
    else:
        # Script lives at: docs/governance/ci/scripts/check_directory_hygiene.py
        # Backend root is four levels up:
        #   scripts -> ci -> governance -> docs -> backend
        script_dir = Path(__file__).resolve().parent
        workspace = (script_dir / ".." / ".." / ".." / "..").resolve()

    if not workspace.is_dir():
        print(f"ERROR: Workspace does not exist: {workspace}", file=sys.stderr)
        return 1

    repo_root = find_repo_root(workspace)

    print(f"Workspace : {workspace}")
    print(f"Repo root : {repo_root}")
    print(f"Git avail : {_git_available()}")
    print()

    violations = scan_violations(workspace, repo_root)

    if violations:
        print(f"DIRECTORY HYGIENE VIOLATIONS: {len(violations)}")
        print("-" * 60)
        for path, reason in violations:
            if not args.quiet:
                print(f"  {path}")
                print(f"    {reason}")
                print()
        print(f"Total violations: {len(violations)}")
    else:
        print("CLEAN -- No directory hygiene violations detected.")

    return len(violations)


if __name__ == "__main__":
    sys.exit(main())
