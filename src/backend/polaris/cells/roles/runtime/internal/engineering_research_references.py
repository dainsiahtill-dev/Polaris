"""Check local artifact references in explicitly selected versions of research reports."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from polaris.kernelone.fs import GuardedRegularFileSnapshotError, read_guarded_regular_file_snapshot

_SHA = re.compile(r"[0-9a-f]{64}")
_DEFAULT_MAX_BYTES = 64 * 1024 * 1024


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON number {value}")


def decode_research_json(content: bytes) -> Any:
    return json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_keys, parse_constant=_reject_constant)


def _field(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer; array positions use canonical decimal indices."""
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("invalid_pointer")
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(document, dict) and key in document:
            document = document[key]
        elif (
            isinstance(document, list)
            and re.fullmatch(r"0|[1-9][0-9]*", key)
            and len(key) <= 18
            and int(key) < len(document)
        ):
            document = document[int(key)]
        else:
            raise ValueError("invalid_pointer")
    return document


def _read(root: Path, name: str, limit: int) -> tuple[bytes, str | None]:
    try:
        return read_guarded_regular_file_snapshot(root, name, limit).content, None
    except GuardedRegularFileSnapshotError as error:
        return b"", error.code
    except ValueError:
        return b"", "invalid_path"
    except OSError as error:
        return b"", type(error).__name__


def validate_reference_specs(reports: object) -> list[dict[str, Any]]:
    if not isinstance(reports, list):
        raise ValueError("reports must be a list")
    for report in reports:
        if not isinstance(report, dict) or not isinstance(report.get("manifest"), str):
            raise ValueError("manifest path must be a string")
        if not isinstance(report.get("sha256"), str) or not _SHA.fullmatch(report["sha256"]):
            raise ValueError("manifest SHA-256 must be lowercase hexadecimal")
        refs = report.get("references")
        if not isinstance(refs, list) or not all(
            isinstance(ref, dict) and all(isinstance(ref.get(k), str) for k in ("path_pointer", "digest_pointer"))
            for ref in refs
        ):
            raise ValueError("references must be a list of path_pointer and digest_pointer pairs")
    return reports


def _reference(
    read: Callable[[str], tuple[bytes, str | None]],
    document: Any,
    pointers: dict[str, str],
    cache: dict[str, tuple[str | None, int, str | None]],
    recorded_root: PureWindowsPath | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {**pointers, "status": "unproven"}
    try:
        path = _field(document, pointers["path_pointer"])
        expected = _field(document, pointers["digest_pointer"])
    except ValueError:
        return {**row, "reason": "invalid_pointer"}
    if not isinstance(path, str) or not path or not isinstance(expected, str) or not _SHA.fullmatch(expected):
        return {**row, "reason": "invalid_reference"}
    row.update(path=path, expected_sha256=expected)
    recorded = PureWindowsPath(path)
    if recorded_root is not None and recorded.is_absolute():
        if ".." in recorded.parts or not recorded.is_relative_to(recorded_root):
            return {**row, "reason": "outside_recorded_root"}
        path = recorded.relative_to(recorded_root).as_posix()
        row["relative_path"] = path
    if path not in cache:
        data, error = read(path)
        cache[path] = (None if error else hashlib.sha256(data).hexdigest(), len(data), error)
    observed, size, error = cache[path]
    if error:
        return {**row, "reason": error}
    return {
        **row,
        "status": "matched" if observed == expected else "changed",
        "observed_sha256": observed,
        "bytes": size,
    }


def _invalidate(report: dict[str, Any], reason: str) -> None:
    report["status"] = reason
    for row in report["references"]:
        row.update(status="unproven", reason=reason)


def verify_references(
    root: Path,
    reports: object,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    recorded_windows_root: str | None = None,
    read_allowed: Callable[[str], bool] | None = None,
    total_bytes_limit: int | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Verify exact report versions and return evidence for each selected reference."""
    root = root.resolve(strict=True)
    if not root.is_dir() or max_bytes < 1:
        raise ValueError("a directory root and positive max_bytes are required")
    recorded_root = PureWindowsPath(recorded_windows_root) if recorded_windows_root else None
    if recorded_root is not None and (not recorded_root.is_absolute() or ".." in recorded_root.parts):
        raise ValueError("recorded_windows_root must be an absolute directory")
    remaining = total_bytes_limit

    def read(name: str) -> tuple[bytes, str | None]:
        nonlocal remaining
        if read_allowed is not None and not read_allowed(name):
            return b"", "read_not_granted"
        if deadline is not None and time.monotonic() >= deadline:
            return b"", "deadline"
        if remaining is not None and remaining <= 0:
            return b"", "byte_budget"
        limit = max_bytes if remaining is None else min(max_bytes, remaining)
        data, error = _read(root, name, limit)
        if remaining is not None:
            remaining -= len(data)
        return data, error

    cache: dict[str, tuple[str | None, int, str | None]] = {}
    checked: list[dict[str, Any]] = []
    for spec in validate_reference_specs(reports):
        refs = [{"path_pointer": r["path_pointer"], "digest_pointer": r["digest_pointer"]} for r in spec["references"]]
        report: dict[str, Any] = {
            "manifest": spec["manifest"],
            "sha256": spec["sha256"],
            "references": refs,
            "status": "checked",
        }
        checked.append(report)
        data, error = read(spec["manifest"])
        if error:
            _invalidate(report, error)
            continue
        report["observed_manifest_sha256"] = hashlib.sha256(data).hexdigest()
        if report["observed_manifest_sha256"] != spec["sha256"]:
            _invalidate(report, "manifest_changed")
            continue
        try:
            document = decode_research_json(data)
        except (UnicodeError, ValueError, RecursionError):
            _invalidate(report, "invalid_manifest")
            continue
        if isinstance(document, dict) and document.get("schema") == "engineering.imported-research-evidence.v1":
            report["published_producer"] = document.get("producer")
            report["published_scope"] = document.get("scope")
            report["published_dependencies"] = document.get("producer_dependencies", [])
        report["references"] = [_reference(read, document, ref, cache, recorded_root) for ref in refs]

    # A long audit can span a publication. Retire results whose report moved during the check.
    for report in checked:
        if report["status"] == "checked":
            data, error = read(report["manifest"])
            if error or hashlib.sha256(data).hexdigest() != report["sha256"]:
                _invalidate(report, error or "manifest_changed")
    counts = Counter(r["status"] for report in checked for r in report["references"])
    incomplete = not checked or any(report["status"] != "checked" or not report["references"] for report in checked)
    status = "changed" if counts["changed"] else "unproven" if incomplete or counts["unproven"] else "matched"
    return {
        "schema": "research.reference-check.v1",
        "root": str(root),
        "status": status,
        "counts": dict(counts),
        "distinct_targets_read": sum(error is None for _, _, error in cache.values()),
        "reports": checked,
    }
