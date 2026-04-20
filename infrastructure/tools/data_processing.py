"""
Data processing tools: JSON, YAML, hash, encoding.
"""
import base64
import hashlib
import json
import os
from typing import Any, Dict, List

from .utils import error_result, find_repo_root, ensure_within_root, relpath


def json_format(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Format or minify JSON.

    Usage: json_format --input <json_string>
           json_format --file <path>
           json_format --file <path> --minify
    """
    _ = timeout
    input_str = ""
    file_arg = ""
    minify = False

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--input", "-i") and i + 1 < len(args):
            input_str = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--minify", "-m"):
            minify = True
            i += 1
            continue
        if not input_str:
            input_str = token
        i += 1

    json_str = ""

    if file_arg:
        root = find_repo_root(cwd)
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError as exc:
            return error_result("json_format", str(exc))

        if not os.path.isfile(full_path):
            return error_result("json_format", f"File not found: {file_arg}")

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                json_str = f.read()
        except Exception as exc:
            return error_result("json_format", str(exc), exit_code=1)
    elif input_str:
        json_str = input_str
    else:
        return error_result("json_format", "Usage: json_format --input <json> or --file <path>")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        return error_result("json_format", f"Invalid JSON: {exc}")

    if minify:
        output = json.dumps(data, separators=(",", ":"))
    else:
        output = json.dumps(data, indent=2, ensure_ascii=False)

    return {
        "ok": True,
        "tool": "json_format",
        "input_source": "file" if file_arg else "string",
        "minified": minify,
        "output": output,
        "size": len(output),
        "error": None,
        "exit_code": 0,
        "stdout": output[:5000],
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(output) > 5000,
        "artifacts": [],
        "command": ["json_format"],
    }


def yaml_parse(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Parse or validate YAML.

    Usage: yaml_parse --input <yaml_string>
           yaml_parse --file <path>
           yaml_parse --file <path> --to-json
    """
    _ = timeout
    input_str = ""
    file_arg = ""
    to_json = False

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--input", "-i") and i + 1 < len(args):
            input_str = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--to-json", "--json", "-j"):
            to_json = True
            i += 1
            continue
        if not input_str:
            input_str = token
        i += 1

    yaml_str = ""

    if file_arg:
        root = find_repo_root(cwd)
        try:
            full_path = ensure_within_root(root, file_arg)
        except ValueError:
            full_path = file_arg

        if not os.path.isfile(full_path):
            return error_result("yaml_parse", f"File not found: {file_arg}")

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                yaml_str = f.read()
        except Exception as exc:
            return error_result("yaml_parse", str(exc), exit_code=1)
    elif input_str:
        yaml_str = input_str
    else:
        return error_result("yaml_parse", "Usage: yaml_parse --input <yaml> or --file <path>")

    # Try to parse YAML
    try:
        import yaml
        data = yaml.safe_load(yaml_str)
    except ImportError:
        # Fallback: try simple parsing for basic cases
        return error_result(
            "yaml_parse",
            "PyYAML not installed. Install with: pip install pyyaml"
        )
    except Exception as exc:
        return error_result("yaml_parse", f"Invalid YAML: {exc}")

    if to_json:
        output = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        output = yaml.dump(data, default_flow_style=False)

    return {
        "ok": True,
        "tool": "yaml_parse",
        "input_source": "file" if file_arg else "string",
        "output": output,
        "size": len(output),
        "error": None,
        "exit_code": 0,
        "stdout": output[:5000],
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(output) > 5000,
        "artifacts": [],
        "command": ["yaml_parse"],
    }


def hash_compute(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Compute hash of a string or file.

    Usage: hash_compute --text <text> [--algorithm md5|sha1|sha256|sha512]
           hash_compute --file <path> [--algorithm md5|sha1|sha256|sha512]
    """
    _ = timeout
    text = ""
    file_arg = ""
    algorithm = "sha256"

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--text", "-t") and i + 1 < len(args):
            text = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--algorithm", "-a") and i + 1 < len(args):
            algorithm = args[i + 1].lower()
            i += 2
            continue
        if not text:
            text = token
        i += 1

    if not text and not file_arg:
        return error_result("hash_compute", "Usage: hash_compute --text <text> or --file <path>")

    if algorithm not in ("md5", "sha1", "sha256", "sha512"):
        return error_result("hash_compute", "Algorithm must be: md5, sha1, sha256, or sha512")

    try:
        if algorithm == "md5":
            hasher = hashlib.md5()
        elif algorithm == "sha1":
            hasher = hashlib.sha1()
        elif algorithm == "sha256":
            hasher = hashlib.sha256()
        else:
            hasher = hashlib.sha512()

        if file_arg:
            root = find_repo_root(cwd)
            try:
                full_path = ensure_within_root(root, file_arg)
            except ValueError:
                full_path = file_arg

            if not os.path.isfile(full_path):
                return error_result("hash_compute", f"File not found: {file_arg}")

            with open(full_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            hash_value = hasher.hexdigest()
            source = f"file: {file_arg}"
        else:
            hash_value = hasher.update(text.encode("utf-8")) or hasher.hexdigest()
            source = f"text: {text[:50]}..."

    except Exception as exc:
        return error_result("hash_compute", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "hash_compute",
        "algorithm": algorithm,
        "hash": hash_value,
        "source": source[:100],
        "error": None,
        "exit_code": 0,
        "stdout": f"{algorithm.upper()}: {hash_value}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["hash_compute", algorithm],
    }


def base64_encode(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Encode text or file to base64.

    Usage: base64_encode --text <text>
           base64_encode --file <path>
    """
    _ = timeout
    text = ""
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--text", "-t") and i + 1 < len(args):
            text = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if not text:
            text = token
        i += 1

    if not text and not file_arg:
        return error_result("base64_encode", "Usage: base64_encode --text <text> or --file <path>")

    try:
        if file_arg:
            root = find_repo_root(cwd)
            try:
                full_path = ensure_within_root(root, file_arg)
            except ValueError:
                full_path = file_arg

            with open(full_path, "rb") as f:
                data = f.read()
            encoded = base64.b64encode(data).decode("ascii")
        else:
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")

    except Exception as exc:
        return error_result("base64_encode", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "base64_encode",
        "encoded": encoded,
        "error": None,
        "exit_code": 0,
        "stdout": encoded[:5000],
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(encoded) > 5000,
        "artifacts": [],
        "command": ["base64_encode"],
    }


def base64_decode(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Decode base64 to text or file.

    Usage: base64_decode --text <base64>
           base64_decode --text <base64> --file <output_path>
    """
    _ = timeout
    text = ""
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--text", "-t") and i + 1 < len(args):
            text = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if not text:
            text = token
        i += 1

    if not text:
        return error_result("base64_decode", "Usage: base64_decode --text <base64>")

    try:
        decoded = base64.b64decode(text)

        # Try to decode as text
        try:
            text_output = decoded.decode("utf-8")
            is_binary = False
        except UnicodeDecodeError:
            text_output = "<binary data>"
            is_binary = True

        # Write to file if requested
        if file_arg:
            root = find_repo_root(cwd)
            try:
                full_path = ensure_within_root(root, file_arg)
            except ValueError:
                full_path = file_arg

            with open(full_path, "wb") as f:
                f.write(decoded)

    except Exception as exc:
        return error_result("base64_decode", f"Invalid base64: {exc}")

    return {
        "ok": True,
        "tool": "base64_decode",
        "decoded_text": text_output[:5000] if not is_binary else None,
        "is_binary": is_binary,
        "output_file": relpath(root, full_path) if file_arg else None,
        "error": None,
        "exit_code": 0,
        "stdout": text_output[:5000] if not is_binary else f"<decoded to binary file: {file_arg}>",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["base64_decode"],
    }
