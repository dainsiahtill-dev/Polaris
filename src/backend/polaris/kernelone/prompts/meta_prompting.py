"""Runtime meta-prompting helpers for role-level prompt hardening.

Polaris role alias normalization is delegated to the roles Cell via
dependency injection (Port/Adapter pattern) to maintain KernelOne → Cells fence.

Architecture (ACGA 2.0):
    +-------------------+       +-------------------+
    |   KernelOne       |       |      Cells        |
    |  meta_prompting   |       |  role_alias       |
    +-------------------+       +-------------------+
              |                         |
              v                         v
    +-------------------+       +-------------------+
    | kernelone/ports/ |       | cells/adapters/  |
    | IRoleProvider    | ----> | RoleProviderAdapter
    +-------------------+       +-------------------+
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import RoleId
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


# =============================================================================
# Backward Compatibility (Re-export from adapter)
# =============================================================================
# We need to import at module level for backward compatibility
# This is acceptable as it's a stable public API (cells/adapters is part of ACGA 2.0)
from polaris.cells.adapters.kernelone import RoleProviderAdapter  # noqa: E402

normalize_role_alias = RoleProviderAdapter().normalize_role_alias


def _role_matches_hint(record: Mapping[str, Any], role: str | RoleId) -> bool:
    role_token = normalize_role_alias(str(role))
    record_role = normalize_role_alias(str(record.get("role") or ""))
    if record_role and record_role == role_token:
        return True

    next_role = normalize_role_alias(str(record.get("next_role") or ""))
    if next_role and next_role == role_token:
        return True

    if role_token == RoleId.QA.value and next_role == "auditor":
        return True
    if role_token == RoleId.DIRECTOR.value and next_role == "chiefengineer":
        return True
    if role_token == RoleId.ARCHITECT.value and next_role == "pm":
        return False
    return bool(not record_role and not next_role)


def _read_json_file(path: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (RuntimeError, ValueError) as exc:
        logger.debug("meta_prompting: failed to read JSON file %s: %s", path, exc)
        return {}


def _read_jsonl_lines(path: str, max_lines: int = 300) -> list[dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except (RuntimeError, ValueError) as exc:
        logger.debug("meta_prompting: failed to read JSONL file %s: %s", path, exc)
        return rows
    for raw in lines[-max(1, int(max_lines or 1)) :]:
        line = str(raw or "").strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (RuntimeError, ValueError) as exc:
            logger.debug("meta_prompting: failed to parse JSONL line: %s", exc)
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _hint_text(record: Mapping[str, Any]) -> str:
    candidates = [
        str(record.get("suggested_improvement") or "").strip(),
        str(record.get("hint") or "").strip(),
        str(record.get("reason") or "").strip(),
    ]
    for item in candidates:
        if item:
            return item
    return ""


def _learning_paths(workspace_root: str) -> dict[str, str]:
    root = str(workspace_root or "").strip()
    return {
        "improvement": resolve_artifact_path(
            root,
            "",
            "runtime/learning/polaris.improvements.jsonl",
        ),
        "meta_hints": resolve_artifact_path(
            root,
            "",
            "runtime/learning/meta_prompt_hints.jsonl",
        ),
        "meta_state": resolve_artifact_path(
            root,
            "",
            "runtime/state/meta_prompt_hints.state.json",
        ),
    }


def load_meta_prompt_hints(workspace_root: str, role: str, limit: int = 4) -> list[str]:
    role_token = normalize_role_alias(role)
    paths = _learning_paths(workspace_root)
    rows: list[dict[str, Any]] = []
    rows.extend(_read_jsonl_lines(paths["improvement"], max_lines=500))
    rows.extend(_read_jsonl_lines(paths["meta_hints"], max_lines=500))

    deduped: list[str] = []
    for row in reversed(rows):
        if not _role_matches_hint(row, role_token):
            continue
        hint = _hint_text(row)
        if not hint:
            continue
        if hint in deduped:
            continue
        deduped.append(hint)
        if len(deduped) >= max(1, int(limit or 1)):
            break
    return deduped


def build_meta_prompting_appendix(workspace_root: str, role: str, limit: int = 4) -> str:
    hints = load_meta_prompt_hints(workspace_root, role, limit=limit)
    if not hints:
        return ""
    lines = [
        "\n\nMeta-Prompting Hardening Hints (auto-learned from recent failures):",
    ]
    for idx, hint in enumerate(hints, start=1):
        lines.append(f"- [{idx}] {hint}")
    lines.append("- Apply these hints when they do not conflict with the active task contract.")
    return "\n".join(lines)


def append_meta_prompt_hint(
    *,
    workspace_root: str,
    role: str,
    hint: str,
    trigger: str,
    run_id: str = "",
    pm_iteration: int = 0,
    source: str = "runtime_failure",
) -> bool:
    text = str(hint or "").strip()
    role_token = normalize_role_alias(role)
    if not workspace_root or not role_token or not text:
        return False
    paths = _learning_paths(workspace_root)
    state = _read_json_file(paths["meta_state"])
    raw_fingerprints = state.get("fingerprints")
    fingerprints: list[Any] = raw_fingerprints if isinstance(raw_fingerprints, list) else []
    normalized_fingerprints = [str(item).strip() for item in fingerprints if str(item).strip()]
    fingerprint = hashlib.sha256(f"{role_token}|{text}|{str(trigger or '').strip()}".encode()).hexdigest()
    if fingerprint in normalized_fingerprints:
        return False

    append_jsonl(
        paths["meta_hints"],
        {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "role": role_token,
            "hint": text,
            "trigger": str(trigger or "").strip(),
            "source": str(source or "").strip(),
            "run_id": str(run_id or "").strip(),
            "pm_iteration": int(pm_iteration or 0),
            "fingerprint": fingerprint,
        },
        buffered=False,
    )

    updated_fingerprints = ([*normalized_fingerprints, fingerprint])[-200:]
    write_json_atomic(
        paths["meta_state"],
        {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fingerprints": updated_fingerprints,
        },
    )
    return True


__all__ = [
    "append_meta_prompt_hint",
    "build_meta_prompting_appendix",
    "load_meta_prompt_hints",
    "normalize_role_alias",  # Re-exported from role_alias for backward compatibility
]
