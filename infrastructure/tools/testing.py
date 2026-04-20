"""
Testing and documentation generation tools.
"""
import os
import re
from typing import Any, Dict, List

from .utils import error_result, find_repo_root, ensure_within_root, relpath


def test_generate(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Generate unit tests for a Python file.

    Usage: test_generate --file <path> [--framework pytest|unittest]
    """
    _ = timeout
    file_arg = ""
    framework = "pytest"

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--framework", "--fw") and i + 1 < len(args):
            framework = args[i + 1]
            i += 2
            continue
        i += 1

    if not file_arg:
        return error_result("test_generate", "Usage: test_generate --file <path>")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("test_generate", str(exc))

    if not os.path.isfile(full_path):
        return error_result("test_generate", f"File not found: {file_arg}")

    # Read the source file
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return error_result("test_generate", str(exc), exit_code=1)

    # Extract functions and classes
    functions = re.findall(r"^(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*\w+)?:", content, re.MULTILINE)
    classes = re.findall(r"^class\s+(\w+)", content, re.MULTILINE)

    test_content = f'''"""
Auto-generated tests for {os.path.basename(full_path)}
"""
import pytest
from {file_arg.replace(".py", "").replace("/", ".")} import *


# Generated test stubs
'''

    for func in functions[:10]:  # Limit to first 10 functions
        test_content += f'''
def test_{func}():
    """Test {func}"""
    # TODO: Implement test
    pass
'''

    for cls in classes[:5]:  # Limit to first 5 classes
        test_content += f'''


class Test{cls}:
    """Tests for {cls}"""

    def test_{cls.lower()}_init(self):
        """Test {cls} initialization"""
        # TODO: Implement test
        pass
'''

    # Write test file
    test_dir = os.path.join(root, "tests")
    os.makedirs(test_dir, exist_ok=True)
    test_file = os.path.join(test_dir, f"test_{os.path.basename(full_path)}")

    try:
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_content)
    except Exception as exc:
        return error_result("test_generate", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "test_generate",
        "source_file": relpath(root, full_path),
        "test_file": relpath(root, test_file),
        "functions_found": len(functions),
        "classes_found": len(classes),
        "error": None,
        "exit_code": 0,
        "stdout": f"Generated test file: {test_file}\nFound {len(functions)} functions, {len(classes)} classes",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["test_generate", file_arg],
    }


def doc_generate(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Generate documentation for a Python file.

    Usage: doc_generate --file <path>
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
        return error_result("doc_generate", "Usage: doc_generate --file <path>")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("doc_generate", str(exc))

    if not os.path.isfile(full_path):
        return error_result("doc_generate", f"File not found: {file_arg}")

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return error_result("doc_generate", str(exc), exit_code=1)

    # Extract docstrings
    docstring_re = re.compile(r'"""([^"]+)"""')
    functions = re.findall(
        r"^(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s]+)?:\s*(?:\"\"\"([^\"]+)\"\"\")?",
        content,
        re.MULTILINE
    )
    classes = re.findall(
        r"^class\s+(\w+)(?:\([^)]+\))?:\s*(?:\"\"\"([^\"]+)\"\"\")?",
        content,
        re.MULTILINE
    )

    # Generate markdown documentation
    doc_content = f"""# {os.path.basename(full_path)}

## Module Overview

"""

    if classes:
        doc_content += "## Classes\n\n"
        for cls_name, cls_doc in classes:
            doc_content += f"### `{cls_name}`\n\n"
            if cls_doc:
                doc_content += f"{cls_doc}\n\n"
            else:
                doc_content += "_No documentation_\n\n"

    if functions:
        doc_content += "## Functions\n\n"
        for func_name, func_doc in functions:
            doc_content += f"### `{func_name}()`\n\n"
            if func_doc:
                doc_content += f"{func_doc}\n\n"
            else:
                doc_content += "_No documentation_\n\n"

    # Write doc file
    doc_file = os.path.join(root, "docs", f"{os.path.basename(full_path)}.md")
    os.makedirs(os.path.dirname(doc_file), exist_ok=True)

    try:
        with open(doc_file, "w", encoding="utf-8") as f:
            f.write(doc_content)
    except Exception as exc:
        return error_result("doc_generate", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "doc_generate",
        "source_file": relpath(root, full_path),
        "doc_file": relpath(root, doc_file),
        "functions_found": len(functions),
        "classes_found": len(classes),
        "error": None,
        "exit_code": 0,
        "stdout": f"Generated doc file: {doc_file}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["doc_generate", file_arg],
    }


def api_test(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Test an API endpoint.

    Usage: api_test --url <url> [--method GET|POST|PUT|DELETE] [--body <json>] [--headers <json>]
    """
    _ = timeout
    url = ""
    method = "GET"
    body = ""
    headers = "{}"

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--url", "-u") and i + 1 < len(args):
            url = args[i + 1]
            i += 2
            continue
        if token in ("--method", "-m") and i + 1 < len(args):
            method = args[i + 1].upper()
            i += 2
            continue
        if token in ("--body", "-b") and i + 1 < len(args):
            body = args[i + 1]
            i += 2
            continue
        if token in ("--headers", "-h") and i + 1 < len(args):
            headers = args[i + 1]
            i += 2
            continue
        i += 1

    if not url:
        return error_result("api_test", "Usage: api_test --url <url> [--method] [--body] [--headers]")

    import json as json_module

    try:
        headers_dict = json_module.loads(headers)
    except json_module.JSONDecodeError:
        headers_dict = {}

    try:
        import requests
        has_requests = True
    except ImportError:
        has_requests = False

    import time
    start = time.time()

    try:
        if has_requests:
            if method in ("POST", "PUT", "PATCH"):
                response = requests.request(
                    method=method,
                    url=url,
                    json=body if body else None,
                    headers=headers_dict,
                    timeout=30
                )
            else:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers_dict,
                    timeout=30
                )
            status_code = response.status_code
            response_body = response.text
            try:
                response_json = response.json()
                response_body = json_module.dumps(response_json, indent=2)
            except Exception:
                pass
        else:
            # Fallback to urllib
            import urllib.request
            import urllib.parse

            req_headers = headers_dict.copy()
            req_data = body.encode("utf-8") if body else None

            if method == "GET" and body:
                url = f"{url}?{urllib.parse.urlencode(urllib.parse.parse_qsl(body[1:] if body.startswith('?') else body))}"

            req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode("utf-8")
                status_code = resp.status

    except Exception as exc:
        return error_result("api_test", str(exc), exit_code=1)

    duration = time.time() - start

    return {
        "ok": True,
        "tool": "api_test",
        "url": url,
        "method": method,
        "status_code": status_code,
        "response": response_body[:5000] if len(response_body) > 5000 else response_body,
        "duration_ms": int(duration * 1000),
        "error": None,
        "exit_code": 0,
        "stdout": f"Status: {status_code}\nResponse: {response_body[:500]}",
        "stderr": "",
        "duration": duration,
        "truncated": len(response_body) > 5000,
        "artifacts": [],
        "command": ["api_test", url],
    }
