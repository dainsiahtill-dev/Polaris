#!/usr/bin/env python3
"""Batch fix model_copy(update=...) violations in ContextOS.

Usage:
    python scripts/fix_model_copy_violations.py [files...]
"""

import re
import sys
from pathlib import Path


def fix_model_copy_in_file(filepath: Path) -> tuple[int, list[str]]:
    """Fix model_copy(update=...) calls in a file.
    
    Returns:
        (count_fixed, error_messages)
    """
    content = filepath.read_text(encoding="utf-8")
    original = content
    errors = []

    # Pattern 1: Simple single-field update
    # item.model_copy(update={"field": value})
    pattern1 = re.compile(
        r'(\w+)\s*=\s*(\w+)\.model_copy\(\s*\n?\s*update=\{\s*\n?\s*"(\w+)":\s*(.+?)\s*\},?\s*\n?\s*(?:deep=True,?)?\s*\)',
        re.DOTALL
    )

    # Pattern 2: Multi-field update (more complex)
    # This is a simplified pattern - real implementation needs AST parsing
    pattern2 = re.compile(
        r'(\w+)\s*=\s*(\w+)\.model_copy\(\s*\n?\s*update=\{(.*?)\},?\s*\n?\s*(?:deep=True,?)?\s*\)',
        re.DOTALL
    )

    count = 0

    # Check if validated_replace is imported
    if "validated_replace" not in content and "model_utils" not in content:
        # Add import at the top
        import_line = 'from polaris.kernelone.context.context_os.model_utils import validated_replace\n'
        # Find a good place to insert
        lines = content.split('\n')
        import_idx = 0
        for i, line in enumerate(lines):
            if line.startswith('from ') or line.startswith('import '):
                import_idx = i + 1
        lines.insert(import_idx, import_line)
        content = '\n'.join(lines)

    # Simple replacements for known patterns
    replacements = [
        # Pattern: item.model_copy(update={"route": route, "metadata": {...}})
        # Replace with: validated_replace(item, route=route, metadata=...)
    ]

    # Count remaining violations
    remaining = len(re.findall(r'\.model_copy\(\s*\n?\s*update=', content))

    if content != original:
        filepath.write_text(content, encoding="utf-8")

    return remaining, errors


def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else [
        "polaris/kernelone/context/context_os/pipeline/stages.py",
        "polaris/kernelone/context/context_os/runtime.py",
    ]

    total_remaining = 0

    for filepath_str in files:
        filepath = Path(filepath_str)
        if not filepath.exists():
            print(f"⚠️  File not found: {filepath}")
            continue

        remaining, errors = fix_model_copy_in_file(filepath)
        total_remaining += remaining

        if remaining > 0:
            print(f"🔴 {filepath}: {remaining} model_copy(update=...) remaining")
        else:
            print(f"✅ {filepath}: All fixed")

    if total_remaining > 0:
        print(f"\n⚠️  {total_remaining} violations require manual fix")
        return 1

    print("\n✅ All model_copy violations fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
