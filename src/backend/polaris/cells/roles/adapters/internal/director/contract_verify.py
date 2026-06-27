"""Machine-checkable verification command projection for Director contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from polaris.kernelone.quality.step_verify import (
    assess_legacy_step_verify_command_safety,
    normalize_step_verify,
)

_BACKTICK_COMMAND_RE = re.compile(r"`([^`\n]+)`")
_RAW_COMMAND_RE = re.compile(
    r"^\s*((?:go|python3?|pytest|npm|pnpm|yarn|node|npx|tsc|cargo|rustc|java|javac|mvn|gradle|make)\b[^\n。；;]*)",
    re.IGNORECASE,
)
_VERIFY_TEXT_KEYS = (
    "verify",
    "verification",
    "verification_commands",
    "verification_plan",
    "steps",
    "execution_checklist",
    "acceptance",
    "acceptance_criteria",
)
_PATH_KEYS = (
    "target_files",
    "scope_paths",
    "source_files",
    "code_files",
)
_LANGUAGE_KEYS = ("language", "primary_language", "project_type")


def resolve_contract_step_verify_command(context: dict[str, Any] | None) -> str:
    """Return the safest machine verify command available in a Director context."""

    if not isinstance(context, dict):
        return ""
    explicit = _explicit_construction_verify(context)
    if explicit:
        return explicit

    records = list(_candidate_records(context))
    language = _infer_language(records)
    candidates = _dedupe_preserve_order(_iter_candidate_commands(records))
    selected = _select_verify_candidate(candidates, language=language)
    if selected:
        return selected
    if language == "go" and _records_have_go_compile_signal(records):
        return "go test ./..."
    return ""


def _explicit_construction_verify(context: Mapping[str, Any]) -> str:
    for record in (context, _mapping_or_empty(context.get("metadata"))):
        step = _mapping_or_empty(record.get("construction_step"))
        command = normalize_step_verify(step.get("verify"))
        if command:
            return command
    return ""


def _candidate_records(context: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    seen: set[int] = set()
    stack: list[Any] = [context, _mapping_or_empty(context.get("metadata"))]
    while stack:
        item = stack.pop(0)
        if not isinstance(item, Mapping):
            continue
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        yield item
        for key in ("task_payload", "task", "metadata", "phase_context", "delivery_plan_document"):
            child = item.get(key)
            if isinstance(child, Mapping):
                stack.append(child)


def _iter_candidate_commands(records: Iterable[Mapping[str, Any]]) -> Iterable[str]:
    for record in records:
        for key in _VERIFY_TEXT_KEYS:
            yield from _commands_from_value(record.get(key))
        delivery_depth_contract = _mapping_or_empty(record.get("delivery_depth_contract"))
        acceptance_contract = _mapping_or_empty(delivery_depth_contract.get("acceptance_contract"))
        for value in acceptance_contract.values():
            yield from _commands_from_value(value)


def _commands_from_value(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _commands_from_value(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _commands_from_value(item)
        return
    text = str(value or "").strip()
    if not text:
        return
    for match in _BACKTICK_COMMAND_RE.finditer(text):
        command = _safe_normalized_command(match.group(1))
        if command:
            yield command
    raw_match = _RAW_COMMAND_RE.match(text)
    if raw_match:
        command = _safe_normalized_command(raw_match.group(1))
        if command:
            yield command


def _safe_normalized_command(value: Any) -> str:
    command = normalize_step_verify(value)
    if not command:
        return ""
    safety = assess_legacy_step_verify_command_safety(command)
    if not safety.allowed:
        return ""
    return command


def _select_verify_candidate(candidates: list[str], *, language: str) -> str:
    if not candidates:
        return ""
    if language == "go":
        for command in candidates:
            if _command_startswith(command, "go test"):
                return command
        return ""
    if language == "python":
        for command in candidates:
            if _command_startswith(command, "python -m unittest", "python -m pytest", "pytest"):
                return command
        return ""
    if language in {"javascript", "typescript"}:
        for command in candidates:
            if _command_startswith(command, "npm test", "pnpm test", "yarn test", "npm run test", "pnpm run test"):
                return command
        return ""
    if language == "rust":
        for command in candidates:
            if _command_startswith(command, "cargo test", "cargo check"):
                return command
        return ""
    return candidates[0]


def _command_startswith(command: str, *prefixes: str) -> bool:
    lowered = command.strip().lower()
    return any(lowered.startswith(prefix) for prefix in prefixes)


def _infer_language(records: list[Mapping[str, Any]]) -> str:
    language_tokens: list[str] = []
    paths: list[str] = []
    for record in records:
        for key in _LANGUAGE_KEYS:
            value = record.get(key)
            if isinstance(value, str):
                language_tokens.append(value.lower())
        for key in _PATH_KEYS:
            paths.extend(_string_list(record.get(key)))
    language_blob = " ".join(language_tokens)
    if "go" in language_blob or "golang" in language_blob:
        return "go"
    if "python" in language_blob:
        return "python"
    if "typescript" in language_blob:
        return "typescript"
    if "javascript" in language_blob or "node" in language_blob:
        return "javascript"
    if "rust" in language_blob:
        return "rust"

    normalized_paths = [path.lower() for path in paths]
    if any(path == "go.mod" or path.endswith(".go") for path in normalized_paths):
        return "go"
    if any(path.endswith(".py") for path in normalized_paths):
        return "python"
    if any(path == "cargo.toml" or path.endswith(".rs") for path in normalized_paths):
        return "rust"
    if any(path.endswith(".ts") for path in normalized_paths):
        return "typescript"
    if any(
        path in {"package.json", "pnpm-lock.yaml", "yarn.lock"} or path.endswith(".js") for path in normalized_paths
    ):
        return "javascript"
    return ""


def _records_have_go_compile_signal(records: list[Mapping[str, Any]]) -> bool:
    blob = " ".join(_record_strings(records)).lower()
    if "go_compile" in blob or "go test" in blob:
        return True
    for record in records:
        for path in _string_list(record.get("target_files")):
            lowered = path.lower()
            if lowered == "go.mod" or lowered.endswith(".go"):
                return True
    return False


def _record_strings(records: Iterable[Mapping[str, Any]]) -> Iterable[str]:
    for record in records:
        for value in record.values():
            if isinstance(value, str):
                yield value
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str):
                        yield item


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    token = str(value or "").strip()
    if not token:
        return []
    return [part.strip() for part in token.split(",") if part.strip()] or [token]


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result
