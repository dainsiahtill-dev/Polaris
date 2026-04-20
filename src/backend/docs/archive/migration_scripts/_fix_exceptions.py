"""Fix remaining exception swallowing patterns in polaris/."""

from __future__ import annotations

import os
import re

# Files that need logging import added
NEED_LOGGER = {
    "polaris/infrastructure/db/repositories/lancedb_code_search.py": [
        ("import hashlib", "import hashlib\nimport logging\n\nlogger = logging.getLogger(__name__)"),
    ],
    "polaris/infrastructure/llm/providers/anthropic_compat_provider.py": [
        (
            "import aiohttp",
            "import aiohttp\nimport logging\n\nlogger = logging.getLogger(__name__)\n\n# Type alias for the module\nexcept Exception as exc:",
        ),
    ],
    "polaris/infrastructure/llm/providers/codex_cli_provider.py": [
        ("import subprocess", "import logging\nimport subprocess\n\nlogger = logging.getLogger(__name__)\n\nimport "),
    ],
    "polaris/infrastructure/log_pipeline/query.py": [
        ("import os", "import logging\nimport os\n\nlogger = logging.getLogger(__name__)\n\nfrom"),
    ],
    "polaris/infrastructure/log_pipeline/run_context.py": [
        ("import threading", "import logging\nimport threading\n\nlogger = logging.getLogger(__name__)\n\nfrom"),
    ],
}


def add_logger_import(path: str) -> bool:
    """Add logging import to a file."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False

    if "import logging" in content or "from logging" in content:
        return True  # Already has logging

    # Find the right insertion point - after __future__ import, before other imports
    lines = content.split("\n")
    new_lines = []
    inserted = False
    for i, line in enumerate(lines):
        new_lines.append(line)
        if not inserted and line.strip() == "" and i > 0 and "__future__" in lines[i - 1]:
            # Add logger import after the blank line following __future__
            new_lines.append("import logging")
            new_lines.append("")
            new_lines.append("logger = logging.getLogger(__name__)")
            new_lines.append("")
            inserted = True
        elif not inserted and line.startswith("import ") and i > 0 and "__future__" in lines[i - 1]:
            # __future__ was the last line, add logger here
            new_lines.append("import logging")
            new_lines.append("")
            new_lines.append("logger = logging.getLogger(__name__)")
            new_lines.append("")
            inserted = True

    if inserted:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))
        return True
    return False


def fix_file(path: str) -> tuple[int, int]:
    """Fix all except Exception: pass patterns in a file. Returns (count, fixed)."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return (0, 0)

    original = content

    # Pattern: except Exception:\s*\n(\s+)pass
    # Replace with except Exception as exc:\n\1logger.debug(...)
    lines = content.split("\n")
    fixed_count = 0
    new_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"\s*except\s+Exception\s*:\s*(?://.*)?$", line):
            # Found except Exception: line
            # Check next line(s) for pass
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip() == "pass":
                indent = " " * (len(lines[i]) - len(lines[i].lstrip()))
                exc_var = "exc"
                log_msg = " (non-critical)"

                # Determine log level based on context
                comment = ""
                if "cleanup" in line.lower() or "close" in line.lower():
                    log_msg = f"{exc_var}"
                    level = "debug"
                elif (
                    "read" in line.lower()
                    or "write" in line.lower()
                    or "subscribe" in line.lower()
                    or "publish" in line.lower()
                ):
                    level = "debug"
                else:
                    level = "debug"

                # Build the replacement
                replacement = f"{indent}except Exception as {exc_var}:"
                new_lines.append(replacement)

                # Determine appropriate log message
                filename = os.path.basename(path)
                log_format = f'"{level.upper()}: {filename}:{{{i + 1}}} ' + "{" + exc_var + '} (swallowed)"'

                # Check for existing comment
                comment_match = re.search(r"#\s*(.+)", line)
                if comment_match:
                    log_format = (
                        f'"{level.upper()}: {filename}:{{{i + 1}}} {comment_match.group(1).strip()} '
                        + "{"
                        + exc_var
                        + '}"'
                    )

                new_lines.append(f"{indent}    logger.{level}({log_format})")

                i = j + 1
                fixed_count += 1
                continue
        new_lines.append(line)
        i += 1

    if fixed_count > 0:
        content = "\n".join(new_lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    return (fixed_count, 1 if content != original else 0)


def main():
    root = "polaris"
    total_fixed = 0
    total_files = 0

    # First, add logger imports where needed
    for path in NEED_LOGGER:
        if os.path.exists(path):
            if add_logger_import(path):
                print(f"ADDED_LOGGER: {path}")

    # Then fix all remaining files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            count, _ = fix_file(path)
            if count > 0:
                print(f"FIXED: {path}: {count} patterns")
                total_fixed += count
                total_files += 1

    print(f"\nTOTAL: {total_fixed} patterns fixed in {total_files} files")


if __name__ == "__main__":
    main()
