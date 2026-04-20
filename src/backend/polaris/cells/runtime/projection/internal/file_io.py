import json
import os
from datetime import datetime
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import write_text_atomic as _kernel_write_text_atomic


def read_json(path: str) -> dict[str, Any] | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        fs = KernelFileSystem(os.path.dirname(path) or ".", get_default_adapter())
        content = fs.workspace_read_text(os.path.basename(path), encoding="utf-8")
        return json.loads(content)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def decode_bytes(data: bytes, *, allow_fallback: bool = True) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        if not allow_fallback:
            return text
        if text.count("\ufffd") <= max(1, len(text) // 200):
            return text
        try:
            return data.decode("gbk")
        except (UnicodeDecodeError, LookupError):
            return text


def read_file_tail(
    path: str,
    max_lines: int = 400,
    max_chars: int = 20000,
    *,
    allow_fallback: bool = True,
) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            if file_size == 0:
                return ""

            pos = file_size
            block_size = 4096
            chunks = []
            lines_found = 0
            chars_read = 0

            target_lines = max_lines if max_lines and max_lines > 0 else None

            while pos > 0:
                read_size = block_size if pos >= block_size else pos
                pos -= read_size
                handle.seek(pos)
                chunk = handle.read(read_size)
                if not chunk:
                    break

                chunks.append(chunk)
                chars_read += len(chunk)
                lines_found += chunk.count(b"\n")

                if (
                    target_lines is not None
                    and lines_found >= target_lines + 1
                    and (max_chars <= 0 or chars_read >= max_chars)
                ):
                    break

                if max_chars > 0 and chars_read >= max_chars * 2:
                    break

            data = b"".join(reversed(chunks))
            text = decode_bytes(data, allow_fallback=allow_fallback)

            lines = text.splitlines()
            if max_lines > 0 and len(lines) > max_lines:
                lines = lines[-max_lines:]
            content = "\n".join(lines)

            if max_chars > 0 and len(content) > max_chars:
                content = content[-max_chars:]

            return content
    except (OSError, ValueError):
        return ""


def read_file_head(path: str, max_chars: int = 20000, *, allow_fallback: bool = True) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_chars if max_chars and max_chars > 0 else 20000)
        return decode_bytes(data, allow_fallback=allow_fallback)
    except (OSError, ValueError):
        return ""


def read_incremental(
    path: str,
    state: dict[str, Any],
    max_chars: int = 20000,
    *,
    allow_fallback: bool = True,
    complete_lines_only: bool = False,
) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    pos = int(state.get("pos", 0))
    if size < pos:
        pos = 0
        state.pop("_line_buffer", None)
    try:
        with open(path, "rb") as handle:
            handle.seek(pos)
            chunk = handle.read()
            state["pos"] = handle.tell()
    except OSError:
        return []
    if not chunk:
        return []
    text = decode_bytes(chunk, allow_fallback=allow_fallback)
    if complete_lines_only:
        buffered = str(state.get("_line_buffer", "") or "")
        if buffered:
            text = buffered + text
        state["_line_buffer"] = ""
    else:
        state.pop("_line_buffer", None)
    if max_chars > 0 and len(text) > max_chars:
        text = text[-max_chars:]
        if complete_lines_only:
            first_newline = text.find("\n")
            if first_newline < 0:
                state["_line_buffer"] = text
                return []
            text = text[first_newline + 1 :]

    if not text:
        return []

    lines = text.splitlines()
    if not complete_lines_only:
        return lines

    has_trailing_newline = text.endswith("\n") or text.endswith("\r")
    if has_trailing_newline:
        state["_line_buffer"] = ""
        return lines

    if not lines:
        state["_line_buffer"] = text
        return []

    state["_line_buffer"] = lines[-1]
    return lines[:-1]


def format_mtime(path: str) -> str:
    if not path or not os.path.exists(path):
        return "missing"
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "unknown"


def build_file_status(entries: list[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for label, path in entries:
        mtime = format_mtime(path)
        lines.append(f"{label}: {mtime}")
    return lines


def write_text_atomic(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Delegate to KernelOne atomic write for consistency and durability."""
    if not path:
        return
    _kernel_write_text_atomic(path, text or "", encoding=encoding)


def read_readme_title(workspace: str) -> str:
    if not workspace:
        return ""
    path = os.path.join(workspace, "tui_runtime.md")
    if not os.path.isfile(path):
        return ""
    try:
        fs = KernelFileSystem(workspace, get_default_adapter())
        content = fs.workspace_read_text("tui_runtime.md", encoding="utf-8")
        for line in content.split("\n"):
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                text = text.lstrip("#").strip()
            return text[:120]
    except (OSError, UnicodeDecodeError):
        return ""
    return ""
