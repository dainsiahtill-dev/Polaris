import os
import sys
from typing import Dict, Any, List, Tuple, Optional

Result = Dict[str, Any]
MAX_READ_LINES = 200
MAX_READ_BYTES = 64 * 1024
MAX_LINE_CHARS = 300
MAX_RG_RESULTS_DEFAULT = 50
MAX_RG_RESULTS_LIMIT = 200
MAX_TREE_ENTRIES = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

def enforce_utf8() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("LANG", "en_US.UTF-8")
    os.environ.setdefault("LC_ALL", "en_US.UTF-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def build_utf8_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env

def normalize_args(args: List[str]) -> List[str]:
    if args and args[0] == "--":
        return args[1:]
    return args

def find_repo_root(start: str) -> str:
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(start)

def ensure_within_root(root: str, path: str) -> str:
    full = os.path.abspath(os.path.join(root, path)) if not os.path.isabs(path) else os.path.abspath(path)
    root = os.path.abspath(root)
    try:
        common = os.path.commonpath([root, full])
    except ValueError:
        raise ValueError(f"Path escapes repo root: {path}")
    if common != root:
        raise ValueError(f"Path escapes repo root: {path}")
    return full

def relpath(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path

def truncate_line(text: str) -> str:
    if len(text) <= MAX_LINE_CHARS:
        return text
    return text[:MAX_LINE_CHARS] + "..."

def format_slice(path: str, start: int, end: int, lines: List[Tuple[int, str]], truncated: bool) -> str:
    rel = path
    header = [
        f"FILE: {rel}",
        f"LINES: {start}-{end}",
        f"TRUNCATED: {'true' if truncated else 'false'}",
    ]
    body = [f"{line_no:>6} | {text}" for line_no, text in lines]
    return "\n".join(header + body)

def infer_tool_error_code(message: str, exit_code: int = 2) -> str:
    text = str(message or "").strip()
    lowered = text.lower()
    if lowered.startswith("not a file:"):
        return "PATH_NOT_FILE"
    if lowered.startswith("not a directory:"):
        return "PATH_NOT_DIRECTORY"
    if "path escapes repo root" in lowered:
        return "PATH_ESCAPES_REPO_ROOT"
    if lowered.startswith("usage:"):
        return "INVALID_TOOL_ARGS"
    if "invalid line range" in lowered:
        return "INVALID_TOOL_ARGS"
    if lowered.startswith("invalid pattern"):
        return "INVALID_TOOL_ARGS"
    if exit_code == 1:
        return "TOOL_RUNTIME_ERROR"
    return "TOOL_ERROR"

def error_result(tool: str, message: str, exit_code: int = 2) -> Result:
    return {
        "ok": False,
        "tool": tool,
        "error": message,
        "error_code": infer_tool_error_code(message, exit_code),
        "exit_code": exit_code,
        "stdout": "",
        "stderr": message,
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": [tool],
    }

BOMS_UTF = [
    (b'\xef\xbb\xbf', 'utf-8'),
    (b'\xff\xfe', 'utf-16-le'),
    (b'\xfe\xff', 'utf-16-be'),
    (b'\xff\xfe\x00\x00', 'utf-32-le'),
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
]


def _guess_utf16(data: bytes) -> str | None:
    if not data:
        return None
    even_nulls = sum(1 for i in range(0, len(data), 2) if data[i] == 0)
    odd_nulls = sum(1 for i in range(1, len(data), 2) if data[i] == 0)
    if even_nulls > odd_nulls * 2:
        return "utf-16-be"
    if odd_nulls > even_nulls * 2:
        return "utf-16-le"
    return None


def decode_text_utf8(data: bytes) -> Tuple[str, str, bool]:
    for bom, enc in BOMS_UTF:
        if data.startswith(bom):
            text = data[len(bom):].decode(enc, errors="replace")
            return text, enc, False
    guess = _guess_utf16(data)
    if guess:
        text = data.decode(guess, errors="replace")
        return text, guess, True
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8", True


def read_text_file_utf8(path: str) -> Tuple[str, Optional[str]]:
    with open(path, "rb") as handle:
        data = handle.read()
    text, encoding, had_issues = decode_text_utf8(data)
    warning = None
    if encoding != "utf-8":
        warning = f"non-utf8 encoding detected ({encoding}); converted to utf-8"
    elif had_issues:
        warning = "invalid utf-8 bytes detected; replaced during decode"
    return text, warning


def detect_utf8_warning(path: str, sample_bytes: int = 4096) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(sample_bytes)
    except Exception:
        return None
    _, encoding, had_issues = decode_text_utf8(data)
    if encoding != "utf-8":
        return f"non-utf8 encoding detected ({encoding}); sample converted to utf-8"
    if had_issues:
        return "invalid utf-8 bytes detected in sample; replaced during decode"
    return None


def read_text_file(path: str) -> str:
    text, _warning = read_text_file_utf8(path)
    return text
