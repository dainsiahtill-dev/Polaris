"""Workflow contract parsing and validation for the embedded runtime."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS, MAX_WORKFLOW_TIMEOUT_SECONDS

_SUPPORTED_TASK_TYPES = {"activity", "workflow", "noop"}
_TASK_ID_MAX_LENGTH = 128
_MAX_TASK_COUNT = 2000
_MAX_INPUT_BYTES = 256 * 1024

logger = logging.getLogger(__name__)


class WorkflowContractError(ValueError):
    """Raised when workflow contract validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = [str(item).strip() for item in errors if str(item).strip()]
        message = "; ".join(self.errors) if self.errors else "invalid_workflow_contract"
        super().__init__(message)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry/backoff policy for one task."""

    max_attempts: int = 1
    initial_interval_seconds: float = 0.2
    backoff_coefficient: float = 2.0
    max_interval_seconds: float = 5.0
    jitter_ratio: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Any) -> RetryPolicy:
        mapping = raw if isinstance(raw, dict) else {}
        max_attempts = _coerce_int(mapping.get("max_attempts"), default=1, minimum=1)
        initial = _coerce_float(
            mapping.get("initial_interval_seconds"),
            default=0.2,
            minimum=0.01,
        )
        backoff = _coerce_float(
            mapping.get("backoff_coefficient"),
            default=2.0,
            minimum=1.0,
        )
        max_interval_seconds = _coerce_float(
            mapping.get("max_interval_seconds"),
            default=max(initial, 5.0),
            minimum=initial,
        )
        jitter = _coerce_float(
            mapping.get("jitter_ratio"),
            default=0.0,
            minimum=0.0,
            maximum=1.0,
        )
        return cls(
            max_attempts=max_attempts,
            initial_interval_seconds=initial,
            backoff_coefficient=backoff,
            max_interval_seconds=max_interval_seconds,
            jitter_ratio=jitter,
        )


@dataclass(frozen=True)
class TaskSpec:
    """One executable node in a workflow DAG."""

    task_id: str
    task_type: str
    handler_name: str
    depends_on: tuple[str, ...] = ()
    input_payload: dict[str, Any] = field(default_factory=dict)
    input_from: dict[str, str] = field(default_factory=dict)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS
    continue_on_error: bool = False
    # --- Saga compensation fields (Chronos Hourglass) ---
    compensation_handler: str | None = None  # Handler name for Saga compensation
    compensation_input: dict[str, Any] = field(default_factory=dict)  # Input to compensation handler
    # --- Human-in-the-loop field (Chronos Hourglass) ---
    is_high_risk: bool = False  # If True, task suspends for human review before execution
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        *,
        default_timeout_seconds: float,
        default_retry_policy: RetryPolicy,
    ) -> TaskSpec:
        mapping = raw if isinstance(raw, dict) else {}
        task_id = str(mapping.get("id") or "").strip()
        task_type = str(mapping.get("type") or mapping.get("task_type") or "activity").strip().lower()
        handler_name = str(mapping.get("handler") or mapping.get("activity") or mapping.get("workflow") or "").strip()
        depends_on = (
            tuple(str(item).strip() for item in (mapping.get("depends_on") or []) if str(item).strip())
            if isinstance(mapping.get("depends_on"), list)
            else ()
        )
        input_payload = mapping.get("input") if isinstance(mapping.get("input"), dict) else {}
        input_from_raw = mapping.get("input_from")
        if not isinstance(input_from_raw, dict):
            input_from_raw = {}
        input_from = {
            str(key).strip(): str(value).strip()
            for key, value in input_from_raw.items()
            if str(key).strip() and str(value).strip()
        }
        timeout_seconds = _coerce_float(
            mapping.get("timeout_seconds", default_timeout_seconds),
            default=default_timeout_seconds,
            minimum=0.01,
        )
        retry_policy = RetryPolicy.from_mapping(
            mapping.get("retry") if isinstance(mapping.get("retry"), dict) else default_retry_policy.__dict__
        )
        continue_on_error = bool(mapping.get("continue_on_error", False))
        # Saga compensation fields
        compensation_handler = mapping.get("compensation_handler") if mapping.get("compensation_handler") else None
        compensation_input = (
            mapping.get("compensation_input") if isinstance(mapping.get("compensation_input"), dict) else {}
        )
        # Human-in-the-loop field
        is_high_risk = bool(mapping.get("is_high_risk", False))
        metadata = mapping.get("metadata") if isinstance(mapping.get("metadata"), dict) else {}
        return cls(
            task_id=task_id,
            task_type=task_type,
            handler_name=handler_name,
            depends_on=depends_on,
            input_payload={str(key): value for key, value in input_payload.items()}
            if isinstance(input_payload, dict)
            else {},
            input_from=input_from,
            retry_policy=retry_policy,
            timeout_seconds=timeout_seconds,
            continue_on_error=continue_on_error,
            compensation_handler=compensation_handler,
            compensation_input={str(key): value for key, value in compensation_input.items()}
            if isinstance(compensation_input, dict)
            else {},
            is_high_risk=is_high_risk,
            metadata={str(key): value for key, value in metadata.items()} if isinstance(metadata, dict) else {},
        )


@dataclass(frozen=True)
class WorkflowContract:
    """Normalized workflow contract accepted by the embedded engine."""

    mode: str
    task_specs: tuple[TaskSpec, ...]
    max_concurrency: int
    continue_on_error: bool
    workflow_timeout_seconds: float = MAX_WORKFLOW_TIMEOUT_SECONDS  # 1 hour default
    # --- Human-in-the-loop fields (Chronos Hourglass) ---
    high_risk_actions: frozenset[str] = frozenset()  # Set of task IDs requiring human review
    human_review_webhook: str | None = None  # Webhook URL for human review notifications

    @property
    def task_count(self) -> int:
        return len(self.task_specs)

    @property
    def is_dag(self) -> bool:
        return self.mode == "dag"

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        default_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
        default_max_concurrency: int = 8,
    ) -> WorkflowContract:
        normalized_payload = payload if isinstance(payload, dict) else {}
        requested_mode = str(normalized_payload.get("_workflow_contract_mode") or "").strip().lower()
        orchestration = (
            normalized_payload.get("orchestration") if isinstance(normalized_payload.get("orchestration"), dict) else {}
        )
        if requested_mode == "legacy":
            return cls(
                mode="legacy",
                task_specs=(),
                max_concurrency=max(1, int(default_max_concurrency)),
                continue_on_error=False,
                workflow_timeout_seconds=_coerce_float(
                    orchestration.get("workflow_timeout_seconds") if isinstance(orchestration, dict) else None,
                    default=MAX_WORKFLOW_TIMEOUT_SECONDS,
                    minimum=1.0,
                    maximum=MAX_WORKFLOW_TIMEOUT_SECONDS,
                ),
            )
        raw_tasks = orchestration.get("tasks") if isinstance(orchestration, dict) else None
        if not isinstance(raw_tasks, list):
            raw_tasks = normalized_payload.get("tasks") if isinstance(normalized_payload, dict) else None
        if not isinstance(raw_tasks, list):
            return cls(
                mode="legacy",
                task_specs=(),
                max_concurrency=max(1, int(default_max_concurrency)),
                continue_on_error=False,
                workflow_timeout_seconds=_coerce_float(
                    orchestration.get("workflow_timeout_seconds") if isinstance(orchestration, dict) else None,
                    default=MAX_WORKFLOW_TIMEOUT_SECONDS,
                    minimum=1.0,
                ),
                high_risk_actions=frozenset(),
                human_review_webhook=None,
            )

        default_retry_policy = RetryPolicy.from_mapping(
            orchestration.get("default_retry")
            if isinstance(orchestration, dict) and isinstance(orchestration.get("default_retry"), dict)
            else {}
        )
        task_specs = tuple(
            TaskSpec.from_mapping(
                item,
                default_timeout_seconds=default_timeout_seconds,
                default_retry_policy=default_retry_policy,
            )
            for item in raw_tasks
        )
        # Human-in-the-loop: parse high_risk_actions set
        raw_high_risk = orchestration.get("high_risk_actions") if isinstance(orchestration, dict) else None
        if isinstance(raw_high_risk, list):
            high_risk_actions = frozenset(str(item) for item in raw_high_risk if item)
        else:
            high_risk_actions = frozenset()
        # Human-in-the-loop: parse webhook URL
        human_review_webhook = None
        if isinstance(orchestration, dict) and orchestration.get("human_review_webhook"):
            human_review_webhook = str(orchestration.get("human_review_webhook")).strip() or None
        contract = cls(
            mode="dag",
            task_specs=task_specs,
            max_concurrency=_coerce_int(
                orchestration.get("max_concurrency") if isinstance(orchestration, dict) else None,
                default=default_max_concurrency,
                minimum=1,
                maximum=256,
            ),
            continue_on_error=bool(orchestration.get("continue_on_error", False))
            if isinstance(orchestration, dict)
            else False,
            workflow_timeout_seconds=_coerce_float(
                orchestration.get("workflow_timeout_seconds") if isinstance(orchestration, dict) else None,
                default=3600.0,
                minimum=1.0,
            ),
            high_risk_actions=high_risk_actions,
            human_review_webhook=human_review_webhook,
        )
        errors = validate_contract(contract)
        if errors:
            raise WorkflowContractError(errors)
        return contract


def validate_contract(contract: WorkflowContract) -> list[str]:
    """Validate contract and return all detected violations."""
    if not isinstance(contract, WorkflowContract):
        return ["contract_type_invalid"]
    if not contract.is_dag:
        return []

    errors: list[str] = []
    task_specs = list(contract.task_specs)
    if not task_specs:
        errors.append("dag_contract_requires_tasks")
        return errors
    if len(task_specs) > _MAX_TASK_COUNT:
        errors.append(f"task_count_exceeds_limit:{len(task_specs)}>{_MAX_TASK_COUNT}")

    by_id: dict[str, TaskSpec] = {}
    for task in task_specs:
        if not task.task_id:
            errors.append("task_id_missing")
            continue
        if len(task.task_id) > _TASK_ID_MAX_LENGTH:
            errors.append(f"task_id_too_long:{task.task_id}")
        if task.task_id in by_id:
            errors.append(f"task_id_duplicated:{task.task_id}")
            continue
        by_id[task.task_id] = task
        if task.task_type not in _SUPPORTED_TASK_TYPES:
            errors.append(f"task_type_unsupported:{task.task_id}:{task.task_type}")
        if task.task_type != "noop" and not task.handler_name:
            errors.append(f"task_handler_missing:{task.task_id}")
        input_size = _json_size(task.input_payload)
        if input_size > _MAX_INPUT_BYTES:
            errors.append(f"task_input_too_large:{task.task_id}:{input_size}>{_MAX_INPUT_BYTES}")
        for ref_key, ref_value in task.input_from.items():
            if "." not in ref_value:
                errors.append(f"task_input_from_invalid_reference:{task.task_id}:{ref_key}:{ref_value}")

    for task in task_specs:
        if task.task_id not in by_id:
            continue
        for dependency in task.depends_on:
            if dependency == task.task_id:
                errors.append(f"task_dependency_self_reference:{task.task_id}")
            elif dependency not in by_id:
                errors.append(f"task_dependency_unknown:{task.task_id}:depends_on:{dependency}")
        for _, ref_value in task.input_from.items():
            source_task = ref_value.split(".", 1)[0].strip()
            if source_task and source_task not in by_id:
                errors.append(f"task_input_from_unknown_source:{task.task_id}:{source_task}")

    if not errors and _has_cycle(by_id):
        errors.append("task_dependency_cycle_detected")
    return errors


def _json_size(payload: dict[str, Any]) -> int:
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        logger.warning("_json_size serialization failed: %s", exc)
        return _MAX_INPUT_BYTES + 1
    return len(text.encode("utf-8"))


def _has_cycle(by_id: dict[str, TaskSpec]) -> bool:
    # Iterative DFS to avoid RecursionError on long dependency chains.
    state: dict[str, int] = {}
    # 0/absent = unvisited, 1 = active, 2 = completed
    for root_id in by_id:
        if state.get(root_id) == 2:
            continue
        stack: list[tuple[str, bool]] = [(root_id, False)]
        while stack:
            task_id, expanded = stack.pop()
            marker = state.get(task_id, 0)
            if expanded:
                state[task_id] = 2
                continue
            if marker == 1:
                return True
            if marker == 2:
                continue
            state[task_id] = 1
            stack.append((task_id, True))
            task_spec = by_id.get(task_id)
            if task_spec is None:
                state[task_id] = 2
                continue
            for dependency in task_spec.depends_on:
                if dependency not in by_id:
                    continue
                dep_state = state.get(dependency, 0)
                if dep_state == 1:
                    return True
                if dep_state == 0:
                    stack.append((dependency, False))
    return False


def _coerce_int(
    value: Any,
    *,
    default: int,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (ValueError, TypeError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _coerce_float(
    value: Any,
    *,
    default: float,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(str(value).strip())
    except (ValueError, TypeError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed
