"""
Code analysis tools: dependency graph, complexity analysis, security scan.
"""
import os
import re
import json
from typing import Any, Dict, List, Set

from .utils import error_result, find_repo_root, ensure_within_root, relpath


def dependency_graph(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Analyze code dependencies.

    Usage: dependency_graph [--file <file>]
           dependency_graph [--dir <dir>]
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

    # Python imports
    py_import_re = re.compile(r"^(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
    # JavaScript/TypeScript imports
    js_import_re = re.compile(r"(?:import|from)\s+['\"]([^'\"]+)['\"]", re.MULTILINE)

    dependencies: Dict[str, List[str]] = {}

    def process_file(filepath: str) -> None:
        rel = relpath(root, filepath)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            deps: Set[str] = set()

            if filepath.endswith(".py"):
                for match in py_import_re.finditer(content):
                    module = match.group(1)
                    # Filter stdlib and relative imports
                    if not module.startswith("."):
                        deps.add(module)
            elif filepath.endswith((".js", ".jsx", ".ts", ".tsx")):
                for match in js_import_re.finditer(content):
                    imp = match.group(1)
                    # Filter relative and node modules
                    if not imp.startswith(".") and not imp.startswith("@"):
                        deps.add(imp.split("/")[0])

            if deps:
                dependencies[rel] = sorted(deps)
        except Exception:
            pass

    if file_arg:
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError as exc:
            return error_result("dependency_graph", str(exc))
        if os.path.isfile(full_path):
            process_file(full_path)
    elif dir_arg:
        try:
            search_path = ensure_within_root(root, dir_arg)
        except ValueError:
            search_path = root
        if os.path.isdir(search_path):
            for dirpath, _, filenames in os.walk(search_path):
                for name in filenames:
                    if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
                        process_file(os.path.join(dirpath, name))
    else:
        # Scan entire repo
        for dirpath, _, filenames in os.walk(root):
            # Skip common non-code directories
            if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__", ".venv")):
                continue
            for name in filenames:
                if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
                    process_file(os.path.join(dirpath, name))

    # Format output
    output_lines = ["Dependency analysis:"]
    for file, deps in sorted(dependencies.items()):
        output_lines.append(f"\n{file}")
        for dep in deps:
            output_lines.append(f"  -> {dep}")

    return {
        "ok": True,
        "tool": "dependency_graph",
        "dependencies": dependencies,
        "file_count": len(dependencies),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "(no dependencies found)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["dependency_graph"],
    }


def complexity_analysis(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Analyze code complexity.

    Usage: complexity_analysis [--file <file>]
           complexity_analysis [--dir <dir>]
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

    # Simple cyclomatic complexity estimation
    # Count decision points: if, elif, else, for, while, except, and, or, case, when
    decision_keywords = [
        r"\bif\b", r"\belif\b", r"\belse\b", r"\bfor\b", r"\bwhile\b",
        r"\bexcept\b", r"\band\b", r"\bor\b", r"\bcase\b", r"\bwhen\b",
        r"\?", r"&&", r"\|\|"
    ]
    complexity_re = re.compile("|".join(decision_keywords))

    results: List[Dict[str, Any]] = []

    def analyze_file(filepath: str) -> None:
        rel = relpath(root, filepath)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            content = "".join(lines)
            functions = 0
            classes = 0
            decision_points = len(complexity_re.findall(content))

            # Count functions and classes
            if filepath.endswith(".py"):
                functions = len(re.findall(r"^\s*(?:def|async def)\s+", content, re.MULTILINE))
                classes = len(re.findall(r"^\s*class\s+", content, re.MULTILINE))
            elif filepath.endswith((".js", ".jsx", ".ts", ".tsx")):
                functions = len(re.findall(r"(?:function|const|let|var)\s+\w+\s*=", content))
                classes = len(re.findall(r"class\s+\w+", content))

            # Simple complexity score
            complexity = 1 + decision_points

            results.append({
                "file": rel,
                "lines": len(lines),
                "functions": functions,
                "classes": classes,
                "complexity": complexity,
            })
        except Exception:
            pass

    if file_arg:
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError as exc:
            return error_result("complexity_analysis", str(exc))
        if os.path.isfile(full_path):
            analyze_file(full_path)
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
                    if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
                        analyze_file(os.path.join(dirpath, name))
    else:
        for dirpath, _, filenames in os.walk(root):
            if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__")):
                continue
            for name in filenames:
                if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
                    analyze_file(os.path.join(dirpath, name))

    # Sort by complexity descending
    results.sort(key=lambda x: x["complexity"], reverse=True)

    output_lines = ["Complexity analysis:"]
    for r in results:
        level = "low" if r["complexity"] < 10 else "medium" if r["complexity"] < 20 else "high"
        output_lines.append(f"\n{r['file']}")
        output_lines.append(f"  Lines: {r['lines']}, Functions: {r['functions']}, Classes: {r['classes']}")
        output_lines.append(f"  Complexity: {r['complexity']} ({level})")

    return {
        "ok": True,
        "tool": "complexity_analysis",
        "results": results,
        "file_count": len(results),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "(no files analyzed)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["complexity_analysis"],
    }


# Common security patterns to detect
SECURITY_PATTERNS = [
    (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password"),
    (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "Hardcoded API key"),
    (r"secret\s*=\s*['\"][^'\"]+['\"]", "Hardcoded secret"),
    (r"token\s*=\s*['\"][^'\"]+['\"]", "Hardcoded token"),
    (r"os\.system\s*\(", "os.system usage (shell injection risk)"),
    (r"subprocess\..*shell\s*=\s*True", "Shell=True in subprocess (shell injection risk)"),
    (r"eval\s*\(", "eval usage (code injection risk)"),
    (r"exec\s*\(", "exec usage (code injection risk)"),
    (r"pickle\.loads?\s*\(", "pickle deserialization risk"),
    (r"yaml\.load\s*\([^,)]*\)\s*(?!, Loader)", "unsafe yaml.load (deserialization risk)"),
    (r"SQL\s*:\s*['\"][^'\"]*\%s[^'\"]*['\"]", "SQL string formatting (SQL injection risk)"),
    (r"execute\s*\(\s*['\"][^'\"]*\%", "SQL execute with string formatting (SQL injection risk)"),
]


def security_scan(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Scan for security issues.

    Usage: security_scan [--file <file>]
           security_scan [--dir <dir>]
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

    findings: List[Dict[str, Any]] = []

    def scan_file(filepath: str) -> None:
        rel = relpath(root, filepath)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line_no, line in enumerate(lines, 1):
                for pattern, description in SECURITY_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        findings.append({
                            "file": rel,
                            "line": line_no,
                            "issue": description,
                            "snippet": line.strip()[:100],
                        })
        except Exception:
            pass

    if file_arg:
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError as exc:
            return error_result("security_scan", str(exc))
        if os.path.isfile(full_path):
            scan_file(full_path)
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
                    if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go")):
                        scan_file(os.path.join(dirpath, name))
    else:
        for dirpath, _, filenames in os.walk(root):
            if any(skip in dirpath for skip in (".git", "node_modules")):
                continue
            for name in filenames:
                if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go")):
                    scan_file(os.path.join(dirpath, name))

    output_lines = ["Security scan results:"]
    if findings:
        output_lines.append(f"\nFound {len(findings)} potential issues:\n")
        for f in findings:
            output_lines.append(f"{f['file']}:{f['line']}")
            output_lines.append(f"  Issue: {f['issue']}")
            output_lines.append(f"  Code: {f['snippet']}")
    else:
        output_lines.append("\nNo security issues found.")

    return {
        "ok": True,
        "tool": "security_scan",
        "findings": findings,
        "issue_count": len(findings),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["security_scan"],
    }
