from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class PmProductPlanningInputV1:
    """Directive-bound advisory semantics, with no task or write authority."""

    directive_sha256: str
    project_id: str
    title: str
    domain: str
    project_type: str
    primary_language: str
    creative_hook: str
    feature_keywords: tuple[str, ...]
    test_focus: str

    def __post_init__(self) -> None:
        if not isinstance(self.directive_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.directive_sha256):
            raise ValueError("invalid directive digest")
        for name in (
            "project_id",
            "title",
            "domain",
            "project_type",
            "primary_language",
            "creative_hook",
            "test_focus",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text")
            if name in {"project_id", "project_type", "primary_language", "creative_hook"} and not value.strip():
                raise ValueError(f"{name} must be nonempty")
        if not isinstance(self.feature_keywords, tuple) or any(not isinstance(v, str) for v in self.feature_keywords):
            raise ValueError("feature_keywords must contain only text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pm.product_planning_input.v1",
            "source": "factory.catalog_contract",
            "directive_sha256": self.directive_sha256,
            "project_id": self.project_id,
            "title": self.title or self.project_id,
            "domain": self.domain,
            "project_type": self.project_type,
            "primary_language": self.primary_language,
            "creative_hook": self.creative_hook,
            "feature_keywords": list(self.feature_keywords),
            "test_focus": self.test_focus,
        }

    def semantic_text(self) -> str:
        return "\n".join(
            (
                self.title or self.project_id,
                self.domain,
                self.project_type,
                self.primary_language,
                self.creative_hook,
                " ".join(self.feature_keywords),
                self.test_focus,
            )
        )


def parse_pm_product_planning_input(payload: object, *, directive: str) -> PmProductPlanningInputV1 | None:
    """Reject stale or malformed advisory data without relaxing planning gates."""
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("schema_version") != "pm.product_planning_input.v1"
        or payload.get("source") != "factory.catalog_contract"
    ):
        return None
    fields = {
        "directive_sha256",
        "project_id",
        "title",
        "domain",
        "project_type",
        "primary_language",
        "creative_hook",
        "feature_keywords",
        "test_focus",
    }
    if set(payload) != fields | {"schema_version", "source"}:
        return None
    if payload.get("directive_sha256") != sha256(directive.encode("utf-8")).hexdigest():
        return None
    keywords = payload.get("feature_keywords")
    if not isinstance(keywords, list) or any(not isinstance(v, str) for v in keywords):
        return None
    values = {key: payload[key] for key in fields}
    values["feature_keywords"] = tuple(keywords)
    try:
        return PmProductPlanningInputV1(**values)
    except (ValueError, TypeError):
        return None


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class GeneratePmTaskContractCommandV1:
    run_id: str
    workspace: str
    directive: str
    task_count_hint: int = 0
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "directive", _require_non_empty("directive", self.directive))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        if self.task_count_hint < 0:
            raise ValueError("task_count_hint must be >= 0")


@dataclass(frozen=True)
class GetPmPlanningStatusQueryV1:
    run_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class PmTaskContractGeneratedEventV1:
    event_id: str
    run_id: str
    workspace: str
    contract_path: str
    generated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "contract_path", _require_non_empty("contract_path", self.contract_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class PmTaskContractResultV1:
    ok: bool
    run_id: str
    workspace: str
    status: str
    contract_ids: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "contract_ids", tuple(str(v) for v in self.contract_ids if str(v).strip()))


class PmPlanningError(RuntimeError):
    """Raised when `orchestration.pm_planning` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pm_planning_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "GeneratePmTaskContractCommandV1",
    "GetPmPlanningStatusQueryV1",
    "PmPlanningError",
    "PmProductPlanningInputV1",
    "PmTaskContractGeneratedEventV1",
    "PmTaskContractResultV1",
    "parse_pm_product_planning_input",
]
