from __future__ import annotations

import json
import mmap
import os
import tempfile
from pathlib import Path
from typing import Any

INDEX_FILE_NAMES: dict[str, str] = {
    "symbols": "symbols.jsonl",
    "references": "references.jsonl",
    "dependencies": "deps.jsonl",
    "test_ownership": "test_ownership.jsonl",
}


def base_path_for_kind(index_dir: Path, kind: str) -> Path:
    if kind not in INDEX_FILE_NAMES:
        allowed = ", ".join(sorted(INDEX_FILE_NAMES.keys()))
        raise ValueError(f"Invalid kind: {kind!r}. Allowed: {allowed}")
    file_name = INDEX_FILE_NAMES[kind]
    return index_dir / file_name


def delta_path_for_base(base_path: Path) -> Path:
    stem = base_path.stem
    return base_path.with_name(f"{stem}.delta.jsonl")


def load_jsonl_mmap(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        file_size = handle.tell()
        if file_size <= 0:
            return rows
        handle.seek(0)
        with mmap.mmap(handle.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            while True:
                line = mm.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


def count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        file_size = handle.tell()
        if file_size <= 0:
            return 0
        handle.seek(0)
        count = 0
        with mmap.mmap(handle.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            while mm.readline():
                count += 1
        return count


def group_rows_by_key(rows: list[dict[str, Any]], key_field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(key_field, ""))
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    return grouped


def flatten_grouped_rows(
    grouped: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        rows.extend(grouped[key])
    return rows


def load_grouped_rows_with_delta(
    index_dir: Path,
    kind: str,
    key_field: str,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    base_path = base_path_for_kind(index_dir, kind)
    delta_path = delta_path_for_base(base_path)

    grouped = group_rows_by_key(load_jsonl_mmap(base_path), key_field)
    delta_ops = load_jsonl_mmap(delta_path)

    for op in delta_ops:
        op_kind = str(op.get("op", "")).strip().lower()
        op_key = str(op.get("key", "")).strip()
        if not op_key:
            continue
        if op_kind == "delete":
            grouped.pop(op_key, None)
            continue
        if op_kind == "set":
            rows = op.get("rows", [])
            if isinstance(rows, list):
                grouped[op_key] = [item for item in rows if isinstance(item, dict)]

    return grouped, len(delta_ops)


def load_index_rows(index_dir: Path, kind: str, key_field: str) -> list[dict[str, Any]]:
    grouped, _ = load_grouped_rows_with_delta(index_dir=index_dir, kind=kind, key_field=key_field)
    return flatten_grouped_rows(grouped)


def append_delta_ops(index_dir: Path, kind: str, ops: list[dict[str, Any]]) -> int:
    if not ops:
        return 0
    base_path = base_path_for_kind(index_dir, kind)
    delta_path = delta_path_for_base(base_path)
    delta_path.parent.mkdir(parents=True, exist_ok=True)

    with delta_path.open("a", encoding="utf-8") as handle:
        for op in ops:
            handle.write(json.dumps(op, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(ops)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        newline="\n",
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        try:
            for row in rows:
                tmp_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


def clear_delta_file(index_dir: Path, kind: str) -> None:
    base_path = base_path_for_kind(index_dir, kind)
    delta_path = delta_path_for_base(base_path)
    write_jsonl_atomic(delta_path, [])
