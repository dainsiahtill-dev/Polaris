"""
Network and development utilities.
"""
import json
import os
import re
import subprocess
import time
import urllib.parse
from typing import Any, Dict, List

from .utils import error_result, find_repo_root, ensure_within_root, relpath


def http_request(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Make HTTP requests.

    Usage: http_request --url <url> [--method GET|POST|PUT|DELETE|PATCH]
           http_request --url <url> --method POST --data <json>
           http_request --url <url> --headers <json>
    """
    url = ""
    method = "GET"
    data = ""
    headers = "{}"
    params = ""

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
        if token in ("--data", "-d", "--body", "-b") and i + 1 < len(args):
            data = args[i + 1]
            i += 2
            continue
        if token in ("--headers", "-h") and i + 1 < len(args):
            headers = args[i + 1]
            i += 2
            continue
        if token in ("--params", "-p") and i + 1 < len(args):
            params = args[i + 1]
            i += 2
            continue
        i += 1

    if not url:
        return error_result("http_request", "Usage: http_request --url <url> [options]")

    # Parse headers
    try:
        headers_dict = json.loads(headers)
    except json.JSONDecodeError:
        headers_dict = {}

    # Parse query params
    if params:
        try:
            params_dict = json.loads(params)
            url = f"{url}?{urllib.parse.urlencode(params_dict)}"
        except json.JSONDecodeError:
            url = f"{url}?{params}"

    start = time.time()

    try:
        import requests
        has_requests = True
    except ImportError:
        has_requests = False

    try:
        if has_requests:
            response = requests.request(
                method=method,
                url=url,
                json=data if data and method in ("POST", "PUT", "PATCH") else None,
                data=data if data and method not in ("POST", "PUT", "PATCH") else None,
                headers=headers_dict,
                timeout=min(timeout, 30) if timeout > 0 else 30
            )
            status_code = response.status_code
            response_headers = dict(response.headers)
            try:
                response_body = response.json()
                response_text = json.dumps(response_body, indent=2)
            except Exception:
                response_text = response.text
        else:
            # Fallback to urllib
            import urllib.request

            req_headers = headers_dict.copy()
            req_data = data.encode("utf-8") if data else None

            req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=min(timeout, 30) if timeout > 0 else 30) as resp:
                response_text = resp.read().decode("utf-8")
                status_code = resp.status
                response_headers = dict(resp.headers)

    except Exception as exc:
        return error_result("http_request", str(exc), exit_code=1)

    duration = time.time() - start

    return {
        "ok": True,
        "tool": "http_request",
        "url": url,
        "method": method,
        "status_code": status_code,
        "headers": response_headers,
        "body": response_text[:10000],
        "duration_ms": int(duration * 1000),
        "error": None,
        "exit_code": 0,
        "stdout": f"Status: {status_code}\n\n{response_text[:2000]}",
        "stderr": "",
        "duration": duration,
        "truncated": len(response_text) > 10000,
        "artifacts": [],
        "command": ["http_request", url],
    }


def url_validate(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Validate and parse URLs.

    Usage: url_validate --url <url>
    """
    _ = cwd
    _ = timeout
    url = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--url", "-u") and i + 1 < len(args):
            url = args[i + 1]
            i += 2
            continue
        if not url:
            url = token
        i += 1

    if not url:
        return error_result("url_validate", "Usage: url_validate --url <url>")

    # Add scheme if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return error_result("url_validate", f"Invalid URL: {exc}")

    result = {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path,
        "params": parsed.params,
        "query": dict(urllib.parse.parse_qsl(parsed.query)),
        "fragment": parsed.fragment,
    }

    output_lines = [
        f"URL: {url}",
        f"Scheme: {parsed.scheme}",
        f"Host: {parsed.hostname}",
        f"Port: {parsed.port or '(default)'}",
        f"Path: {parsed.path}",
    ]
    if parsed.query:
        output_lines.append(f"Query: {parsed.query}")

    return {
        "ok": True,
        "tool": "url_validate",
        "url": url,
        "valid": True,
        "parsed": result,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["url_validate", url],
    }


def file_diff(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Compare two files (not git-based).

    Usage: file_diff --file1 <path1> --file2 <path2>
           file_diff <path1> <path2>
    """
    _ = timeout
    file1 = ""
    file2 = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file1", "-1") and i + 1 < len(args):
            file1 = args[i + 1]
            i += 2
            continue
        if token in ("--file2", "-2") and i + 1 < len(args):
            file2 = args[i + 1]
            i += 2
            continue
        if not file1:
            file1 = token
        elif not file2:
            file2 = token
        i += 1

    if not file1 or not file2:
        return error_result("file_diff", "Usage: file_diff --file1 <path1> --file2 <path2>")

    root = find_repo_root(cwd)

    # Resolve paths
    try:
        path1 = ensure_within_root(root, file1) if not os.path.isabs(file1) else file1
    except ValueError:
        path1 = file1

    try:
        path2 = ensure_within_root(root, file2) if not os.path.isabs(file2) else file2
    except ValueError:
        path2 = file2

    if not os.path.isfile(path1):
        return error_result("file_diff", f"File not found: {file1}")
    if not os.path.isfile(path2):
        return error_result("file_diff", f"File not found: {file2}")

    try:
        with open(path1, "r", encoding="utf-8") as f:
            content1 = f.readlines()
        with open(path2, "r", encoding="utf-8") as f:
            content2 = f.readlines()
    except Exception as exc:
        return error_result("file_diff", str(exc), exit_code=1)

    # Simple line-by-line diff
    diff_lines: List[Dict[str, Any]] = []
    max_lines = max(len(content1), len(content2))

    for i in range(max_lines):
        line1 = content1[i] if i < len(content1) else None
        line2 = content2[i] if i < len(content2) else None

        if line1 != line2:
            diff_lines.append({
                "line": i + 1,
                "file1": line1.strip() if line1 else "<empty>",
                "file2": line2.strip() if line2 else "<empty>",
            })

    # Generate unified diff-like output
    output_lines = [f"Diff: {file1} vs {file2}", ""]

    for d in diff_lines[:50]:
        output_lines.append(f"Line {d['line']}:")
        output_lines.append(f"  - {d['file1']}")
        output_lines.append(f"  + {d['file2']}")

    if len(diff_lines) > 50:
        output_lines.append(f"\n... and {len(diff_lines) - 50} more changes")

    return {
        "ok": True,
        "tool": "file_diff",
        "file1": relpath(root, path1),
        "file2": relpath(root, path2),
        "changes": diff_lines,
        "change_count": len(diff_lines),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(diff_lines) > 50,
        "artifacts": [],
        "command": ["file_diff", file1, file2],
    }


def version_info(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Get version information for dependencies.

    Usage: version_info [--file requirements.txt]
           version_info [--package <package>]
    """
    _ = timeout
    file_arg = ""
    package = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--package", "-p") and i + 1 < len(args):
            package = args[i + 1]
            i += 2
            continue
        i += 1

    root = find_repo_root(cwd)
    versions: Dict[str, str] = {}

    if package:
        # Check single package
        try:
            result = subprocess.run(
                ["pip", "show", package],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=cwd
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("Version:"):
                        versions[package] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

    elif file_arg:
        # Check requirements file
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError:
            full_path = file_arg

        if not os.path.isfile(full_path):
            return error_result("version_info", f"File not found: {file_arg}")

        with open(full_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Parse package name
                    pkg = re.split("[<>=!]", line)[0].strip()
                    if pkg:
                        try:
                            result = subprocess.run(
                                ["pip", "show", pkg],
                                capture_output=True,
                                text=True,
                                timeout=5,
                                cwd=cwd
                            )
                            if result.returncode == 0:
                                for l in result.stdout.split("\n"):
                                    if l.startswith("Version:"):
                                        versions[pkg] = l.split(":", 1)[1].strip()
                                        break
                        except Exception:
                            versions[pkg] = "not found"
    else:
        # Check common dependency files
        for req_file in ("requirements.txt", "pyproject.toml", "setup.py"):
            if os.path.isfile(os.path.join(root, req_file)):
                file_arg = req_file
                break

        if file_arg:
            # Recursively call with file
            return version_info(["--file", file_arg], cwd, timeout)

    output_lines = ["Package versions:"]
    for pkg, ver in sorted(versions.items()):
        output_lines.append(f"  {pkg}: {ver}")

    return {
        "ok": True,
        "tool": "version_info",
        "source": file_arg or package or "auto-detected",
        "versions": versions,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "(no packages found)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["version_info"],
    }


def cron_parse(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Parse and validate cron expressions.

    Usage: cron_parse --expression <cron>
           cron_parse <cron>
    """
    _ = cwd
    _ = timeout
    expression = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--expression", "-e") and i + 1 < len(args):
            expression = args[i + 1]
            i += 2
            continue
        if not expression:
            expression = token
        i += 1

    if not expression:
        return error_result("cron_parse", "Usage: cron_parse --expression <cron>")

    # Parse cron expression
    parts = expression.split()
    if len(parts) not in (5, 6):
        return error_result("cron_parse", "Cron must have 5 or 6 fields")

    field_names = ["minute", "hour", "day of month", "month", "day of week"]
    if len(parts) == 6:
        field_names.insert(0, "second")

    # Simple validation
    validations = [
        (0, 59),   # minute
        (0, 23),  # hour
        (1, 31),  # day
        (1, 12),  # month
        (0, 6),   # day of week
    ]

    descriptions: List[str] = []

    for i, (part, (min_val, max_val)) in enumerate(zip(parts[:5], validations)):
        field = field_names[i + (len(parts) == 6)]

        if part == "*":
            desc = f"Every {field}"
        elif "," in part:
            desc = f"{field} in ({part})"
        elif "-" in part:
            start, end = part.split("-")
            desc = f"{field} from {start} to {end}"
        elif "/" in part:
            base, step = part.split("/")
            base = base if base != "*" else "0"
            desc = f"Every {step} {field}s starting at {base}"
        else:
            try:
                val = int(part)
                if val < min_val or val > max_val:
                    return error_result("cron_parse", f"Invalid {field}: {val}")
                desc = f"{field} = {val}"
            except ValueError:
                return error_result("cron_parse", f"Invalid {field}: {part}")

        descriptions.append(desc)

    output_lines = [f"Cron: {expression}", ""]
    output_lines.append("Fields:")
    for name, desc in zip(field_names[len(parts) == 6:], descriptions):
        output_lines.append(f"  {name}: {desc}")

    return {
        "ok": True,
        "tool": "cron_parse",
        "expression": expression,
        "fields": dict(zip(field_names[len(parts) == 6:], descriptions)),
        "is_valid": True,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["cron_parse", expression],
    }
