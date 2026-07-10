"""Policy for skeptical architecture review report validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

RULE_ID = "skeptical_architecture_review_report"

SCHEMA_RELATIVE_PATH = Path("src/backend/docs/governance/schemas/skeptical-architecture-review-report.schema.yaml")
TEMPLATE_RELATIVE_PATH = Path(
    "src/backend/docs/governance/templates/skeptical-architecture-review-report.template.yaml"
)
BACKEND_SCHEMA_RELATIVE_PATH = Path("docs/governance/schemas/skeptical-architecture-review-report.schema.yaml")
BACKEND_TEMPLATE_RELATIVE_PATH = Path("docs/governance/templates/skeptical-architecture-review-report.template.yaml")


@dataclass(frozen=True)
class SkepticalArchitectureReviewPolicyResult:
    """Evaluation result for skeptical architecture review report validation."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _read_yaml_mapping(path: Path) -> tuple[Mapping[str, Any] | None, str]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Failed to read YAML {path}: {exc}"
    if not isinstance(payload, Mapping):
        return None, f"YAML document must be a mapping: {path}"
    return payload, ""


def _fact_chain_statuses(report: Mapping[str, Any]) -> list[tuple[str, str]]:
    fact_chain = report.get("fact_chain")
    if not isinstance(fact_chain, Mapping):
        return []
    statuses: list[tuple[str, str]] = []
    for name, raw_node in fact_chain.items():
        if not isinstance(raw_node, Mapping):
            statuses.append((str(name), "missing"))
            continue
        statuses.append((str(name), str(raw_node.get("status") or "missing")))
    return statuses


def _true_red_flags(report: Mapping[str, Any]) -> list[str]:
    red_flags = report.get("red_flags")
    if not isinstance(red_flags, Mapping):
        return []
    return [str(name) for name, value in red_flags.items() if str(value).strip().lower() == "true"]


def _reliability_claim_violations(report: Mapping[str, Any], *, label: str) -> list[str]:
    verdict = report.get("verdict")
    if not isinstance(verdict, Mapping) or not bool(verdict.get("architecture_reliable")):
        return []

    violations: list[str] = []
    fresh_run = report.get("fresh_isolated_run")
    claim = report.get("architecture_claim")
    if not isinstance(fresh_run, Mapping) or not bool(fresh_run.get("completed_verified")):
        violations.append(f"{label}: architecture_reliable=true without fresh completed_verified evidence")
    if isinstance(fresh_run, Mapping) and not str(fresh_run.get("evidence_ref") or "").strip():
        violations.append(f"{label}: architecture_reliable=true without fresh isolated run evidence_ref")
    if not isinstance(claim, Mapping) or str(claim.get("proof_level") or "") != "system_oracle":
        violations.append(f"{label}: architecture_reliable=true requires proof_level=system_oracle")

    true_flags = _true_red_flags(report)
    if true_flags:
        violations.append(f"{label}: architecture_reliable=true while red flags are true: {', '.join(true_flags)}")

    non_present = [f"{name}={status}" for name, status in _fact_chain_statuses(report) if status not in {"present"}]
    if non_present:
        violations.append(
            f"{label}: architecture_reliable=true requires every fact-chain status present; found "
            + ", ".join(non_present)
        )
    return violations


def _resolve_existing_path(workspace: Path, *relative_paths: Path) -> Path:
    for relative in relative_paths:
        candidate = workspace / relative
        if candidate.exists():
            return candidate
    return workspace / relative_paths[0]


def _validate_payload(
    *,
    schema: Mapping[str, Any],
    path: Path,
    payload: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    evidence: list[str] = []
    violations: list[str] = []
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        violations.append(f"{path}: schema validation failed at {'/'.join(str(p) for p in exc.path)}: {exc.message}")
        return evidence, violations

    evidence.append(f"{path}: schema validation passed")
    violations.extend(_reliability_claim_violations(payload, label=str(path)))
    return evidence, violations


def evaluate_skeptical_architecture_review(
    workspace: Path,
    *,
    report_paths: Iterable[Path | str] = (),
) -> SkepticalArchitectureReviewPolicyResult:
    """Validate skeptical architecture review template and optional reports.

    Complexity:
        O(r + f) time over reports and fact-chain fields; O(r + f) space for
        emitted evidence and violations.
    """

    schema_path = _resolve_existing_path(workspace, SCHEMA_RELATIVE_PATH, BACKEND_SCHEMA_RELATIVE_PATH)
    template_path = _resolve_existing_path(workspace, TEMPLATE_RELATIVE_PATH, BACKEND_TEMPLATE_RELATIVE_PATH)
    evidence: list[str] = []
    violations: list[str] = []
    warnings: list[str] = []

    schema, schema_error = _read_yaml_mapping(schema_path)
    if schema_error:
        return SkepticalArchitectureReviewPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(schema_error,),
        )
    assert schema is not None

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return SkepticalArchitectureReviewPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"{schema_path}: invalid JSON schema: {exc.message}",),
        )
    evidence.append(f"{schema_path}: JSON schema is valid")

    raw_targets = list(report_paths)
    targets = [workspace / target if not Path(target).is_absolute() else Path(target) for target in raw_targets]
    if not targets:
        targets = [template_path]
        warnings.append("No report paths provided; validated the template only")

    for target in targets:
        payload, error = _read_yaml_mapping(target)
        if error:
            violations.append(error)
            continue
        assert payload is not None
        target_evidence, target_violations = _validate_payload(schema=schema, path=target, payload=payload)
        evidence.extend(target_evidence)
        violations.extend(target_violations)

    return SkepticalArchitectureReviewPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
