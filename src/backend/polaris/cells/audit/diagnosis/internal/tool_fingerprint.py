"""Tool-call fingerprint chain for audit trail integrity.

Implements deterministic fingerprinting for tool calls so that any
file-modifying tool invocation can be uniquely identified across
the audit trail.

Architecture constraints:
- All text operations use UTF-8.
- No external I/O in this module (pure computation).
- Fingerprints are frozen dataclasses for hashability and dict-key use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Operation inference
# ---------------------------------------------------------------------------

_FILE_CREATE_SIGNALS = frozenset(
    {
        "content",
        "file_path",
        "path",
        "target_path",
    }
)

_FILE_DELETE_SIGNALS = frozenset(
    {
        "remove",
        "delete",
        "unlink",
    }
)


def _infer_operation(file_path: str, args: dict) -> str:
    """Infer file operation (create / modify / delete) from args.

    Args:
        file_path: The target file path.
        args: The tool-call arguments dict.

    Returns:
        "create" | "modify" | "delete" | "read"
    """
    args_lower = {k.lower(): v for k, v in args.items()}
    combined = set(args_lower.keys())

    # Explicit delete signals
    if combined & _FILE_DELETE_SIGNALS:
        return "delete"

    # Explicit create signals — new file that doesn't exist
    if combined & _FILE_CREATE_SIGNALS:
        # "content" with a path strongly implies create or overwrite
        return "create"

    # Heuristic: if the path ends with a known extension and we have
    # content, treat as create or modify.  Without content it's likely
    # a read / query operation.
    if _FILE_CREATE_SIGNALS & combined:
        return "create"

    # Safe default
    return "read"


# ---------------------------------------------------------------------------
# Hash computation helpers
# ---------------------------------------------------------------------------


def _sorted_args_hash(args: dict) -> str:
    """Return sha256 of sorted JSON-serialised args (stable across calls)."""
    # Normalise: sort keys recursively
    normalised = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _full_hash(tool_name: str, args_hash: str, file_path: str) -> str:
    """Return sha256(tool_name + args_hash + file_path)."""
    payload = f"{tool_name}{args_hash}{file_path}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Frozen fingerprint record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolFingerprint:
    """Immutable fingerprint for a single tool-call event.

    Attributes
    ----------
    tool_name:
        The tool identifier (e.g. "WRITE_FILE", "READ_FILE").
    args_hash:
        sha256 of the sorted, JSON-serialised args dict.
    file_path:
        The primary file path involved, or "" when no file is targeted.
    operation:
        One of "create" | "modify" | "delete" | "read".
    full_hash:
        sha256(tool_name + args_hash + file_path) — globally unique per call.
    """

    tool_name: str
    args_hash: str
    file_path: str
    operation: str
    full_hash: str

    @classmethod
    def from_tool_call(
        cls,
        tool_name: str,
        args: dict,
        file_path: str = "",
    ) -> ToolFingerprint:
        """Build a fingerprint from a raw tool call.

        Args:
            tool_name: The tool name string (whitespace is stripped).
            args: The tool arguments dict.
            file_path: The primary file path targeted, or "".

        Returns:
            A new ToolFingerprint instance.
        """
        tool_name = tool_name.strip()  # normalise for deterministic hashing
        args_hash = _sorted_args_hash(args)
        operation = _infer_operation(file_path, args)
        full_hash = _full_hash(tool_name, args_hash, file_path)
        return cls(
            tool_name=tool_name,
            args_hash=args_hash,
            file_path=file_path,
            operation=operation,
            full_hash=full_hash,
        )

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for JSON / audit events)."""
        return {
            "tool_name": self.tool_name,
            "args_hash": self.args_hash,
            "file_path": self.file_path,
            "operation": self.operation,
            "full_hash": self.full_hash,
        }


_FILE_PATH_KEYS = ("file", "path", "file_path", "target_path", "src_path")


def _resolve_file_path_from_data(data: dict) -> str:
    """Extract the first matching file path from data or nested args.

    Checks both flat (data["file"]) and nested (data["args"]["file"]) forms.
    """
    # 1. Top-level of data first
    for key in _FILE_PATH_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val:
            return val.strip()

    # 2. Nested inside data["args"]
    args = data.get("args")
    if isinstance(args, dict):
        for key in _FILE_PATH_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val:
                return val.strip()

    # 3. Nested inside data["result"] or data["raw_result"]
    for container_key in ("result", "raw_result"):
        container = data.get(container_key)
        if isinstance(container, dict):
            args = container.get("args")
            if isinstance(args, dict):
                for key in _FILE_PATH_KEYS:
                    val = args.get(key)
                    if isinstance(val, str) and val:
                        return val.strip()

    return ""


# ---------------------------------------------------------------------------
# Convenience factory used by the audit decorator
# ---------------------------------------------------------------------------


def compute_tool_fingerprint(event: dict) -> ToolFingerprint | None:
    """Extract tool-call info from a stream_turn event and return its fingerprint.

    Returns None when the event is not a tool_call or tool_result,
    so callers can skip safely.

    The expected event shape matches ``RoleConsoleHost.stream_turn`` output::

        {
            "type": "tool_call",
            "data": {
                "tool": "WRITE_FILE",
                "args": {"file": "src/a.py", "content": "..."},
            }
        }
    """
    event_type = str(event.get("type", ""))
    if event_type not in {"tool_call", "tool_result"}:
        return None

    data = event.get("data")
    if not isinstance(data, dict):
        return None

    # Resolve tool name
    tool_name = ""
    for key in ("tool", "name", "tool_name"):
        val = data.get(key)
        if isinstance(val, str) and val:
            tool_name = val.strip()
            break

    # Resolve args — tool_result may nest args under "result" / "raw_result"
    raw_args: dict = {}
    if "args" in data:
        raw_args = data["args"] if isinstance(data["args"], dict) else {}
    else:
        for container_key in ("result", "raw_result"):
            container = data.get(container_key)
            if isinstance(container, dict) and "args" in container:
                raw_args = container["args"] if isinstance(container["args"], dict) else {}
                break

    # Resolve file path — check top-level data first, then nested args.
    # This handles both conventions used across the codebase:
    #   A) data["args"]["file"]  (nested)
    #   B) data["file"]           (flat)
    file_path = _resolve_file_path_from_data(data)

    if not tool_name:
        return None

    return ToolFingerprint.from_tool_call(tool_name, raw_args, file_path)


__all__ = [
    "ToolFingerprint",
    "compute_tool_fingerprint",
]
