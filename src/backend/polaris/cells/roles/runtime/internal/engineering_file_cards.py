"""Content-addressed FileCards for bounded source reads."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    guarded_compare_and_create_regular_file,
    guarded_create_directory,
    read_guarded_regular_file_snapshot,
)

_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_FILE_REF = re.compile(r"filecard:[0-9a-f]{64}")


def persist_file_snapshot(workspace: Path, path: str, content: str) -> str | None:
    """Persist full immutable bytes before exposing a recoverable FileCard ref."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    root = workspace / ".polaris" / "runtime" / "filecards"
    reference = f"filecard:{digest}"
    if (root / f"{digest}.json").exists():
        read_file_snapshot(workspace, reference)
        return reference
    for parent, relative in (
        (workspace, ".polaris"),
        (workspace / ".polaris", "runtime"),
        (workspace / ".polaris" / "runtime", "filecards"),
    ):
        if not (parent / relative).exists():
            guarded_create_directory(str(parent), relative)
    payload = json.dumps({"path": path, "sha256": digest, "content": content}, ensure_ascii=False).encode("utf-8")
    try:
        guarded_compare_and_create_regular_file(root, f"{digest}.json", payload, max_bytes=_MAX_SNAPSHOT_BYTES)
    except (ValueError, OSError, GuardedRegularFileSnapshotError):
        return None
    return f"filecard:{digest}"


def read_file_snapshot(workspace: Path, reference: str) -> dict[str, Any]:
    """Read the exact saved snapshot; never substitute the live file for a missing ref."""

    if not _FILE_REF.fullmatch(reference):
        raise ValueError("invalid filecard reference")
    root = workspace / ".polaris" / "runtime" / "filecards"
    name = reference.removeprefix("filecard:") + ".json"
    try:
        snapshot = read_guarded_regular_file_snapshot(root, name, _MAX_SNAPSHOT_BYTES)
    except (OSError, GuardedRegularFileSnapshotError) as error:
        raise ValueError(f"filecard snapshot unavailable: {reference}") from error
    payload = json.loads(snapshot.content.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        raise ValueError("filecard snapshot must be a JSON object")
    if (
        payload.get("sha256") != reference.removeprefix("filecard:")
        or hashlib.sha256(payload["content"].encode("utf-8")).hexdigest() != payload["sha256"]
    ):
        raise ValueError("filecard snapshot integrity mismatch")
    return payload


_SYMBOL_PATTERNS = (
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:class|def|function|interface|type|enum|fn|struct|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)"
    ),
    # `const <TYPE> <name>` 要的是 name：旧版直接抓第一个标识符，于是
    # `const unsigned x` → `unsigned`、`const std::string s` → `std`、
    # `const moon::Foo bar` → `moon`（实测 72 个符号里 25 个是类型关键字，
    # 14/20 个文件受影响）。取 `=`/`;`/`[` 之前的最后一个标识符。
    re.compile(
        r"^\s*(?:static\s+|inline\s+)*(?:export\s+)?(?:const|constexpr)\s+"
        r"(?:[A-Za-z_][A-Za-z0-9_:<>,\s]*?[\s*])?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;|\[)"
    ),
    re.compile(r"^\s*(?:pub\s+)?(?:type|func)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:class|struct|namespace)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    # C/C++ 函数定义：`<返回类型> <名字>(...) {`。旧版没有这条，于是
    # `int add(int a, int b) { ... }` 里的 add 抓不到，索引里只剩类型关键字。
    # 要求以 `{` 结尾，排除同名调用；排除控制流关键字。
    re.compile(
        r"^\s*(?:[A-Za-z_][A-Za-z0-9_:<>,\*&\[\]]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"
    ),
)

_NOT_SYMBOL_NAMES = frozenset(
    {
        "if", "for", "while", "switch", "catch", "return", "sizeof", "do", "else",
        "main",  # main 是入口，不是符号
    }
)


def _declarations(lines: Sequence[str]) -> dict[str, int]:
    declarations: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        for pattern in _SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                name = match.group(1)
                if name not in _NOT_SYMBOL_NAMES:
                    declarations.setdefault(name, number)
                break
        if len(declarations) >= 24:
            break
    return declarations


def build_file_card(
    path: str,
    content: str,
    *,
    revision: int | None,
    preview: str,
    truncated: bool,
    start_line: int,
    end_line: int,
    snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic card; full source remains available through content_ref."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    lines = content.splitlines()
    declarations = _declarations(lines)
    return {
        "schema_version": "engineering.file_card.v1",
        "path": path,
        "revision": revision,
        "sha256": digest,
        "size_bytes": len(content.encode("utf-8")),
        "line_count": len(lines),
        "symbols": list(declarations),
        "declaration_lines": declarations,
        "summary": f"{Path(path).name} · {len(lines)} lines · {len(declarations)} symbols",
        "content_ref": snapshot_ref or f"filecard:{digest}",
        "snapshot_ref": snapshot_ref,
        "recoverable": snapshot_ref is not None,
        "content_preview": preview,
        "coverage": {
            "complete_source": True,
            "source_truncated": False,
            "observed_line_ranges": [[start_line, end_line]],
            "presentation_truncated": truncated,
        },
        "content_bounds": {
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "truncated": truncated,
        },
        "stale": False,
    }


def card_from_observation(content: str) -> dict[str, Any] | None:
    """Read a FileCard from a durable read_file observation, if present."""

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    card = payload.get("file_card") if isinstance(payload, dict) else None
    return card if isinstance(card, dict) and card.get("schema_version") == "engineering.file_card.v1" else None


def observed_file_card(event: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = event["payload"]
    if (
        event["kind"] != "tool_observed"
        or payload.get("tool") != "read_file"
        or payload.get("ok") is not True
        or payload.get("interrupted")
        or payload.get("timed_out")
    ):
        return None
    return card_from_observation(payload.get("content", ""))


def recoverable_ref(card: Mapping[str, Any]) -> str | None:
    ref = card.get("snapshot_ref")
    if (
        card.get("recoverable") is True
        and isinstance(ref, str)
        and _FILE_REF.fullmatch(ref)
        and ref == card.get("content_ref")
        and ref.removeprefix("filecard:") == card.get("sha256")
    ):
        return ref
    return None


def resolve_observed_file(events: Sequence[Mapping[str, Any]], reference: str, path: str | None) -> dict[str, Any]:
    """Bind shared snapshot bytes back to a source path observed in this run."""
    if not _FILE_REF.fullmatch(reference):
        raise ValueError("content_ref must be a filecard:<sha256> reference")
    match = None
    for event in reversed(events):
        card = observed_file_card(event)
        if card is None or recoverable_ref(card) != reference:
            continue
        if path is not None:
            if card.get("path") == path:
                return card
        elif match is None:
            match = card
        elif match.get("path") != card.get("path"):
            raise ValueError("multiple source paths share this content_ref; supply file as well")
    if match is None:
        raise ValueError(f"unknown file_card reference for the requested path: {reference}")
    return match


def stale_file_observation(payload: Mapping[str, Any]) -> bool:
    if payload.get("tool") != "read_file":
        return False
    card = card_from_observation(payload.get("content", ""))
    return card is not None and (
        card.get("stale") is True
        or (bool(card.get("recovered_from")) and card.get("current_sha256") != card.get("sha256"))
    )
