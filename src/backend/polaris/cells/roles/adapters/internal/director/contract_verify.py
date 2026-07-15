"""Machine-checkable verification command projection for Director contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
_PROJECT_TARGET_KEYS = (
    "project_declared_target_files",
    "project_declared_source_targets",
)
_TEST_COMMAND_PREFIXES = (
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
    "yarn run test",
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "go test",
    "cargo test",
)


@dataclass(frozen=True, slots=True)
class ContractStepVerifyResolution:
    """Task-boundary decision for one contract verification command.

    A project-wide test command belongs to the task that owns the declared
    test targets. Earlier source or entrypoint tasks retain compiler and
    artifact-quality gates, but do not fail because a downstream test asset
    has not been materialized yet.
    """

    command: str = ""
    disposition: str = "absent"
    reason: str = "no_machine_verify_command"
    downstream_validation_targets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the stable audit projection for this resolution."""

        return {
            "schema_version": "director.contract_step_verify_resolution.v1",
            "command": self.command,
            "disposition": self.disposition,
            "reason": self.reason,
            "downstream_validation_targets": list(self.downstream_validation_targets),
        }


def resolve_contract_step_verify_command(
    context: dict[str, Any] | None,
    *,
    task: Mapping[str, Any] | None = None,
) -> str:
    """Return the safest machine verify command available in a Director context."""

    return resolve_contract_step_verify(context, task=task).command


def resolve_contract_step_verify(
    context: dict[str, Any] | None,
    *,
    task: Mapping[str, Any] | None = None,
) -> ContractStepVerifyResolution:
    """Resolve a verify command and its task-boundary ownership disposition."""

    if not isinstance(context, dict):
        return ContractStepVerifyResolution()

    records = list(_candidate_records(context))
    if isinstance(task, Mapping):
        records.extend(_candidate_records(task))
    downstream_validation_targets = _downstream_validation_targets(records, task=task)
    defer_validation = _records_defer_validation_to_downstream(records) or bool(downstream_validation_targets)
    explicit = _explicit_construction_verify(context)
    if explicit:
        if defer_validation and _is_project_test_command(explicit):
            return ContractStepVerifyResolution(
                disposition="deferred",
                reason="project_test_targets_not_owned_by_current_task",
                downstream_validation_targets=downstream_validation_targets,
            )
        return ContractStepVerifyResolution(
            command=explicit,
            disposition="run",
            reason="explicit_construction_verify",
        )

    language = _infer_language(records)
    candidates = _dedupe_preserve_order(_iter_candidate_commands(records))
    selected = _select_verify_candidate(
        candidates,
        language=language,
        defer_validation=defer_validation,
    )
    if selected:
        return ContractStepVerifyResolution(
            command=selected,
            disposition="run",
            reason="contract_verify_candidate",
        )
    if defer_validation and any(_is_project_test_command(candidate) for candidate in candidates):
        return ContractStepVerifyResolution(
            disposition="deferred",
            reason="project_test_targets_not_owned_by_current_task",
            downstream_validation_targets=downstream_validation_targets,
        )
    if language == "go" and _records_have_go_compile_signal(records):
        return ContractStepVerifyResolution(
            command="go test ./...",
            disposition="run",
            reason="go_compile_fallback",
        )
    return ContractStepVerifyResolution()


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
        raw_command = raw_match.group(1)
        if _raw_command_has_narrative_tail(raw_command):
            return
        command = _safe_normalized_command(raw_command)
        if command:
            yield command


def _raw_command_has_narrative_tail(command: str) -> bool:
    tokens = str(command or "").strip().split()
    if len(tokens) <= 3:
        return False
    first = tokens[0].lower()
    if first not in {"npm", "pnpm", "yarn"}:
        return False
    if tokens[1].lower() != "run":
        return False
    return not (tokens[3].startswith("-") or tokens[3] in {"&&", "||", "|"})


def _safe_normalized_command(value: Any) -> str:
    command = normalize_step_verify(value)
    if not command:
        return ""
    safety = assess_legacy_step_verify_command_safety(command)
    if not safety.allowed:
        return ""
    return command


def _select_verify_candidate(candidates: list[str], *, language: str, defer_validation: bool = False) -> str:
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
        if defer_validation:
            return ""
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


def _downstream_validation_targets(
    records: Iterable[Mapping[str, Any]],
    *,
    task: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Return declared test assets owned outside the current task.

    The decision is derived from structured PM contract fields only. Prompt
    prose and compiler output are deliberately excluded from the authority
    calculation.
    """

    record_list = list(records)
    project_targets = _dedupe_preserve_order(
        path for record in record_list for key in _PROJECT_TARGET_KEYS for path in _string_list(record.get(key))
    )
    validation_targets = [path for path in project_targets if _is_validation_target(path)]
    if not validation_targets:
        return ()

    current_targets: list[str] = []
    if isinstance(task, Mapping):
        current_targets = _dedupe_preserve_order(
            path for key in ("target_files", "scope_paths") for path in _string_list(task.get(key))
        )
    if not current_targets:
        current_targets = _dedupe_preserve_order(
            path
            for record in record_list
            for key in ("target_files", "scope_paths")
            for path in _string_list(record.get(key))
        )

    normalized_current = {_normalize_contract_path(path) for path in current_targets if _normalize_contract_path(path)}
    current_validation_targets = [
        path for path in validation_targets if _normalize_contract_path(path) in normalized_current
    ]
    if current_validation_targets:
        return ()
    return tuple(validation_targets)


def _is_validation_target(path: str) -> bool:
    normalized = _normalize_contract_path(path).lower()
    name = normalized.rsplit("/", 1)[-1]
    return bool(
        normalized.startswith(("tests/", "test/"))
        or "/tests/" in normalized
        or "/test/" in normalized
        or re.search(r"(?:^|[._-])(?:test|tests|spec)(?:[._-]|$)", name)
        or name.endswith("_test.go")
        or name.endswith("_test.py")
        or name.endswith("_test.rs")
    )


def _normalize_contract_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_project_test_command(command: str) -> bool:
    normalized = " ".join(str(command or "").strip().lower().split())
    return any(normalized.startswith(prefix) for prefix in _TEST_COMMAND_PREFIXES)


def _records_defer_validation_to_downstream(records: Iterable[Mapping[str, Any]]) -> bool:
    for record in records:
        hygiene = _mapping_or_empty(record.get("validation_contract_hygiene"))
        if not hygiene:
            continue
        reason = str(hygiene.get("reason") or "").strip()
        if reason == "test_acceptance_deferred_to_downstream_validation_task":
            return True
        if _string_list(hygiene.get("downstream_validation_targets")):
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
