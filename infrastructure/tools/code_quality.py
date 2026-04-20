"""
Code quality tools: unused imports, dead code, etc.
"""
import os
import re
from typing import Any, Dict, List, Set

from .utils import error_result, find_repo_root, ensure_within_root, relpath


def find_unused_imports(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Find unused imports in Python files.

    Usage: find_unused_imports [--file <file>]
           find_unused_imports [--dir <dir>]
    """
    _ = timeout
    file_arg = ""
    dir_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--dir", "-d") and i + 1 < len(args):
            dir_arg = args[i + 1]
            i += 2
            continue
        i += 1

    root = find_repo_root(cwd)

    # Import patterns
    import_re = re.compile(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))", re.MULTILINE)
    name_usage_re = re.compile(r"\b(\w+)\b")

    results: List[Dict[str, Any]] = []

    def check_file(filepath: str) -> None:
        rel = relpath(root, filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Find all imports
            imports: Set[str] = set()
            for match in import_re.finditer(content):
                module = match.group(1) or match.group(2)
                if module:
                    # Handle "from x import y, z"
                    if "," in module:
                        module = module.split(",")[0].strip()
                    imports.add(module.split(".")[0])

            # Find all name usages
            usages = set(name_usage_re.findall(content))

            # Find unused
            unused = imports - usages - {"os", "sys", "re", "json", "typing", "any", "Optional", "List", "Dict"}

            if unused:
                results.append({
                    "file": rel,
                    "unused": sorted(unused),
                })
        except Exception:
            pass

    if file_arg:
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError as exc:
            return error_result("find_unused_imports", str(exc))
        if os.path.isfile(full_path):
            check_file(full_path)
    elif dir_arg:
        try:
            search_path = ensure_within_root(root, dir_arg)
        except ValueError:
            search_path = root
        if os.path.isdir(search_path):
            for dirpath, _, filenames in os.walk(search_path):
                if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__")):
                    continue
                for name in filenames:
                    if name.endswith(".py"):
                        check_file(os.path.join(dirpath, name))
    else:
        for dirpath, _, filenames in os.walk(root):
            if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__", ".polaris")):
                continue
            for name in filenames:
                if name.endswith(".py"):
                    check_file(os.path.join(dirpath, name))

    output_lines = ["Unused imports:"]
    for r in results:
        output_lines.append(f"\n{r['file']}")
        for imp in r["unused"]:
            output_lines.append(f"  - {imp}")

    return {
        "ok": True,
        "tool": "find_unused_imports",
        "results": results,
        "files_with_issues": len(results),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "(no unused imports found)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["find_unused_imports"],
    }


def sort_imports(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Sort imports in Python files.

    Usage: sort_imports --file <file>
    """
    _ = timeout
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        i += 1

    if not file_arg:
        return error_result("sort_imports", "Usage: sort_imports --file <path>")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("sort_imports", str(exc))

    if not os.path.isfile(full_path):
        return error_result("sort_imports", f"File not found: {file_arg}")

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as exc:
        return error_result("sort_imports", str(exc), exit_code=1)

    # Separate import lines and non-import lines
    import_lines: List[str] = []
    other_lines: List[str] = []
    in_import_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            import_lines.append(line)
            in_import_block = True
        elif in_import_block and not stripped:
            # Empty line in import block
            other_lines.append(line)
            in_import_block = False
        else:
            other_lines.append(line)

    # Sort imports (standard library first, then third-party, then local)
    def sort_key(imp: str) -> tuple:
        imp = imp.strip()
        if imp.startswith("from ") or imp.startswith("import "):
            module = imp.split()[1].split(".")[0]
        else:
            module = imp.split(".")[0]

        # Standard library
        stdlib = {"os", "sys", "re", "json", "typing", "datetime", "collections", "itertools", "functools", "pathlib"}
        if module in stdlib:
            return (0, module)
        # Local imports (relative or project)
        if module.startswith("."):
            return (2, module)
        return (1, module)

    import_lines.sort(key=sort_key)

    # Write back
    new_content = "".join(import_lines + other_lines)

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as exc:
        return error_result("sort_imports", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "sort_imports",
        "file": relpath(root, full_path),
        "imports_sorted": len(import_lines),
        "error": None,
        "exit_code": 0,
        "stdout": f"Sorted {len(import_lines)} imports in {file_arg}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["sort_imports", file_arg],
    }


def find_dead_code(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Find dead code (unused functions, unreachable code).

    Usage: find_dead_code [--file <file>]
           find_dead_code [--dir <dir>]
    """
    _ = timeout
    file_arg = ""
    dir_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--dir", "-d") and i + 1 < len(args):
            dir_arg = args[i + 1]
            i += 2
            continue
        i += 1

    root = find_repo_root(cwd)

    # Collect all function/class definitions across all files
    all_definitions: Dict[str, List[str]] = {}  # name -> [files]

    def collect_defs(filepath: str) -> None:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Functions
            for match in re.finditer(r"^(?:async\s+)?def\s+(\w+)\s*\(", content, re.MULTILINE):
                name = match.group(1)
                if name not in all_definitions:
                    all_definitions[name] = []
                all_definitions[name].append(relpath(root, filepath))
        except Exception:
            pass

    # First pass: collect all definitions
    if file_arg:
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError:
            full_path = root
        if os.path.isfile(full_path):
            collect_defs(full_path)
    elif dir_arg:
        try:
            search_path = ensure_within_root(root, dir_arg)
        except ValueError:
            search_path = root
        if os.path.isdir(search_path):
            for dirpath, _, filenames in os.walk(search_path):
                if any(skip in dirpath for skip in (".git", "node_modules")):
                    continue
                for name in filenames:
                    if name.endswith(".py"):
                        collect_defs(os.path.join(dirpath, name))
    else:
        for dirpath, _, filenames in os.walk(root):
            if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__", ".polaris")):
                continue
            for name in filenames:
                if name.endswith(".py"):
                    collect_defs(os.path.join(dirpath, name))

    # Second pass: check usage
    results: List[Dict[str, Any]] = []

    def check_usage(filepath: str) -> None:
        rel = relpath(root, filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Exclude __main__ block
            if "__main__" in content:
                content = content.split("__main__")[0]

            unused: List[str] = []
            for name, def_files in all_definitions.items():
                if rel in def_files:
                    # Function defined in this file, check if used elsewhere
                    used = False
                    for other_file in def_files:
                        if other_file != rel:
                            try:
                                with open(os.path.join(root, other_file.replace("/", os.sep)), "r") as f2:
                                    if name in f2.read():
                                        used = True
                                        break
                            except Exception:
                                pass
                    if not used:
                        # Also check within same file (simple check)
                        if content.count(name) <= 1:  # Definition only
                            unused.append(name)

            if unused:
                results.append({
                    "file": rel,
                    "unused": list(set(unused))[:10],  # Limit results
                })
        except Exception:
            pass

    # Check each file
    for dirpath, _, filenames in os.walk(root):
        if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__", ".polaris")):
            continue
        for name in filenames:
            if name.endswith(".py"):
                check_file = os.path.join(dirpath, name)
                if not file_arg and not dir_arg:
                    check_usage(check_file)

    output_lines = ["Potential dead code:"]
    for r in results[:20]:  # Limit output
        output_lines.append(f"\n{r['file']}")
        for item in r["unused"]:
            output_lines.append(f"  - {item}")

    return {
        "ok": True,
        "tool": "find_dead_code",
        "results": results[:20],
        "files_with_issues": len(results),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "(no dead code found)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["find_dead_code"],
    }


def regex_validate(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Validate and test a regular expression.

    Usage: regex_validate --pattern <pattern> [--text <text>] [--flags <flags>]
    """
    _ = cwd
    _ = timeout
    pattern = ""
    text = ""
    flags = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--pattern", "-p") and i + 1 < len(args):
            pattern = args[i + 1]
            i += 2
            continue
        if token in ("--text", "-t") and i + 1 < len(args):
            text = args[i + 1]
            i += 2
            continue
        if token in ("--flags", "-f") and i + 1 < len(args):
            flags = args[i + 1]
            i += 2
            continue
        if not pattern:
            pattern = token
        elif not text:
            text = token
        i += 1

    if not pattern:
        return error_result("regex_validate", "Usage: regex_validate --pattern <pattern> [--text <text>]")

    import re

    # Parse flags
    flag_val = 0
    if "i" in flags.lower():
        flag_val |= re.IGNORECASE
    if "m" in flags.lower():
        flag_val |= re.MULTILINE
    if "s" in flags.lower():
        flag_val |= re.DOTALL

    try:
        regex = re.compile(pattern, flag_val)
    except re.error as exc:
        return error_result("regex_validate", f"Invalid regex: {exc}")

    if not text:
        return {
            "ok": True,
            "tool": "regex_validate",
            "pattern": pattern,
            "flags": flags,
            "valid": True,
            "error": None,
            "exit_code": 0,
            "stdout": f"Valid regex: {pattern}",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["regex_validate", pattern],
        }

    # Test against text
    matches = regex.findall(text)
    match_objs = list(regex.finditer(text))

    output_lines = [f"Pattern: {pattern}", f"Text: {text[:100]}...", f""]
    output_lines.append(f"Matches found: {len(matches)}")

    for i, m in enumerate(match_objs[:10]):
        output_lines.append(f"Match {i+1}: '{m.group()}' at {m.start()}-{m.end()}")

    return {
        "ok": True,
        "tool": "regex_validate",
        "pattern": pattern,
        "text": text,
        "flags": flags,
        "valid": True,
        "matches": [m.group() for m in match_objs],
        "match_count": len(matches),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(match_objs) > 10,
        "artifacts": [],
        "command": ["regex_validate", pattern],
    }
