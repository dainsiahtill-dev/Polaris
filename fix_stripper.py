#!/usr/bin/env python3
"""Fix ReasoningStripper to preserve code indentation."""

import sys

def main():
    filepath = "src/backend/polaris/kernelone/llm/reasoning/stripper.py"

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Old problematic code
    old_code = '        result = re.sub(r"[ \\t]+\\n", "\\n", result)'

    # New fixed code
    new_code = '''        # FIX: Preserve code indentation - only strip trailing whitespace per line
        # The old regex r"[ \\t]+\\n" incorrectly removed leading indentation from code blocks
        lines = result.splitlines()
        cleaned_lines = [line.rstrip() for line in lines]
        result = "\\n".join(cleaned_lines)'''

    if old_code not in content:
        print("ERROR: Old code not found")
        # Try to find similar
        if "re.sub" in content and "[ \\t]" in content:
            print("Found similar pattern, checking...")
            import re
            match = re.search(r're\.sub\(r"\[.*?\\t\].*?\\n".*?\)', content)
            if match:
                print(f"Found: {match.group()}")
        sys.exit(1)

    content = content.replace(old_code, new_code)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print("SUCCESS: Fixed ReasoningStripper to preserve code indentation")

if __name__ == "__main__":
    main()
