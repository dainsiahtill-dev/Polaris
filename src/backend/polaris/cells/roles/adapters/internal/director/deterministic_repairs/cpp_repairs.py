"""C++ deterministic repairs for cross-file coherence issues.

Handles common C++ compile errors from LLM multi-file generation:
- Include path mismatches (e.g., "engine/foo.hpp" vs "src/engine/foo.hpp")
- Missing include directories
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def run_all_cpp_post_repairs(workspace: Path) -> list[dict[str, str]]:
    """Run all C++ post-execution repairs.

    Entry point called from Director's post-execution repair hook.
    """
    all_repairs: list[dict[str, str]] = []
    all_repairs.extend(repair_cpp_include_paths(workspace))
    return all_repairs


def repair_cpp_include_paths(workspace: Path) -> list[dict[str, str]]:
    """Fix #include paths that don't match the actual file layout.

    When Director generates ``#include "engine/generator.hpp"`` but the file
    is at ``src/engine/generator.hpp``, this repair adds the ``src/`` prefix.

    Algorithm:
    1. Find all .hpp/.h files in the workspace
    2. Build a mapping of basename → relative path
    3. For each #include "..." directive, check if the path exists
    4. If not, try to find the correct path by matching the filename
    """
    # Collect all header files
    header_files: dict[str, list[str]] = {}  # basename → [relative paths]
    for ext in ("*.hpp", "*.h", "*.hxx"):
        for hdr in workspace.rglob(ext):
            if "build" in hdr.parts or "cmake-build" in hdr.parts:
                continue
            rel = str(hdr.relative_to(workspace))
            basename = hdr.name
            header_files.setdefault(basename, []).append(rel)

    if not header_files:
        return []

    # Find all C++ source and header files
    include_re = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
    repairs: list[dict[str, str]] = []

    for ext in ("*.cpp", "*.cc", "*.cxx", "*.c", "*.hpp", "*.h", "*.hxx"):
        for src_file in workspace.rglob(ext):
            if "build" in src_file.parts or "cmake-build" in src_file.parts:
                continue
            try:
                content = src_file.read_text(encoding="utf-8")
            except OSError:
                continue

            modified = False
            for match in include_re.finditer(content):
                inc_path = match.group(1)
                # Check if the include path resolves from workspace root
                if (workspace / inc_path).is_file():
                    continue  # Path is correct
                # Check if it resolves relative to the source file's directory
                src_dir = src_file.parent
                if (src_dir / inc_path).is_file():
                    continue  # Path is correct relative to source file
                # Try to find the correct path by matching the filename
                basename = Path(inc_path).name
                candidates = header_files.get(basename, [])
                if not candidates:
                    continue
                # Find the best candidate (one whose suffix matches the include path)
                best = None
                for candidate in candidates:
                    if candidate.endswith("/" + inc_path) or candidate.endswith(inc_path):
                        best = candidate
                        break
                if not best and len(candidates) == 1:
                    best = candidates[0]
                if best and best != inc_path:
                    content = content.replace(f'"{inc_path}"', f'"{best}"')
                    modified = True

            if modified:
                src_file.write_text(content, encoding="utf-8")
                repairs.append({"file": str(src_file.relative_to(workspace)), "action": "fixed_include_paths"})

    if repairs:
        logger.info("C++ include path repair: %d file(s) fixed", len(repairs))
    return repairs
