"""PM 完整 JSON 合同解析。旧文本 helpers 不进入 PM stage 的授权链。"""

from __future__ import annotations

import json
import re
from typing import Any

from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_generated_outputs
from polaris.cells.orchestration.pm_planning.public.task_intent import (
    PM_TASK_INTENT_SCHEMA_VERSION,
    PmTaskIntentError,
    is_pm_task_intent,
    project_pm_task_intent_draft,
)

from ._protocol import _PMAdapterMixinBase
from .pm_text_utils import (
    _PM_DETAIL_BULLET_PREFIX,
    _TASK_LINE_PREFIX,
    _TASK_SECTION_HEADING,
    _pm_is_dependency_chain_text,
    _pm_is_prompt_echo_response,
    _pm_raw_task_is_dependency_chain,
    _pm_raw_task_is_meta_diagnostic,
    _pm_raw_task_is_non_delivery_constraint,
    _pm_strip_markdown_title_noise,
    _pm_strip_task_label_prefix,
)


class _PMContractError(ValueError):
    """Internal, non-authoritative syntax/shape feedback for the quality loop."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        super().__init__(str(diagnostic["message"]))
        self.diagnostic = diagnostic


def _schema_error(path: str, message: str) -> _PMContractError:
    return _PMContractError({"code": "pm_contract_schema_invalid", "path": path, "message": message})


def _decode_complete_pm_payload(response: str) -> Any:
    """Decode exactly one complete JSON value; never salvage nested fragments."""
    text = str(response or "").strip()
    if not text:
        raise _schema_error("$", "PM response is empty")
    if _pm_is_prompt_echo_response(text):
        raise _schema_error("$", "PM response echoes the prompt instead of a task contract")
    fence = re.fullmatch(r"```(?:json|yaml|yml|markdown|md)?\s*\n?(.*?)\n?```", text, flags=re.IGNORECASE | re.DOTALL)
    candidate = fence.group(1).strip() if fence else text

    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _schema_error("$", f"Duplicate JSON field {key!r} makes the contract ambiguous")
            result[key] = value
        return result

    try:
        # Complete syntax is checked before duplicate/schema diagnostics so a
        # broken tail always reports the actual decoder position first.
        json.loads(candidate)
        return json.loads(candidate, object_pairs_hook=unique_fields)
    except json.JSONDecodeError as exc:
        raise _PMContractError(
            {
                "code": "pm_contract_json_invalid",
                "path": "$",
                "message": str(exc),
                "position": exc.pos,
                "line": exc.lineno,
                "column": exc.colno,
            }
        ) from exc


def _complete_pm_tasks(payload: Any, path: str = "$", *, source_paths: list[str] | None = None) -> list[dict[str, Any]]:
    """Recognize only declared task containers and validate every task row."""
    if isinstance(payload, dict) and payload.get("schema_version") == PM_TASK_INTENT_SCHEMA_VERSION:
        try:
            draft_tasks: list[dict[str, Any]] = project_pm_task_intent_draft(payload)
        except PmTaskIntentError as exc:
            raise _PMContractError(exc.diagnostic) from exc
        if source_paths is not None:
            source_paths.extend(f"{path}.tasks[{i}]" for i in range(len(draft_tasks)))
        return draft_tasks
    candidate_keys = (
        "tasks",
        "task_list",
        "tasklist",
        "work_items",
        "workitems",
        "items",
        "todo",
        "todos",
        "deliverables",
        "backlog",
        "plan",
    )
    if isinstance(payload, dict):
        containers = [key for key in candidate_keys if key in payload]
        wrappers = [key for key in ("data", "result", "output") if key in payload]
        mapped = [key for key in payload if re.fullmatch(r"(?:task[-_ ]*\d+|t[-_ ]*\d+)", str(key), re.IGNORECASE)]
        if len(containers) + len(wrappers) + bool(mapped) != 1:
            raise _schema_error(path, "Expected one unambiguous known task envelope")
        if containers:
            key = containers[0]
            if not isinstance(payload[key], list):
                raise _schema_error(f"{path}.{key}", "Declared task container must be an array")
            return _complete_pm_tasks(payload[key], f"{path}.{key}", source_paths=source_paths)
        if wrappers:
            key = wrappers[0]
            if not isinstance(payload[key], dict):
                raise _schema_error(f"{path}.{key}", "Known wrapper must contain a task envelope object")
            return _complete_pm_tasks(payload[key], f"{path}.{key}", source_paths=source_paths)
        rows = [payload[key] for key in mapped]
        paths = [f"{path}.{key}" for key in mapped]
    elif isinstance(payload, list):
        rows = payload
        paths = [f"{path}[{index}]" for index in range(len(rows))]
    else:
        raise _schema_error(path, "Task container must be an array or known envelope object")
    if not rows:
        raise _schema_error(path, "Task array must not be empty")
    tasks: list[dict[str, Any]] = []
    for row, row_path in zip(rows, paths, strict=True):
        if not isinstance(row, dict):
            raise _schema_error(row_path, "Task must be an object")
        metadata = row.get("metadata")
        if row.get("schema_version") == PM_TASK_INTENT_SCHEMA_VERSION or (
            isinstance(metadata, dict) and metadata.get("planning_contract_version") == PM_TASK_INTENT_SCHEMA_VERSION
        ):
            raise _schema_error(row_path, "Semantic tasks require the complete versioned product envelope")
        if any(key in row for key in ("modality", "argv", "cwd")):
            raise _schema_error(row_path, "Verifier row fields belong inside verification_commands, not a task")
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            raise _schema_error(
                f"{row_path}.title", "Task title must be a non-empty string; verifier rows are not tasks"
            )
        for key in ("id", "goal", "description", "phase", "assigned_to"):
            if key in row and not isinstance(row[key], str):
                raise _schema_error(f"{row_path}.{key}", "Task field must be a string")
        for key in (
            "steps",
            "acceptance",
            "acceptance_criteria",
            "scope_paths",
            "target_files",
            "context_files",
            "depends_on",
            "dependencies",
            "execution_checklist",
        ):
            if key in row:
                values = row[key]
                if not isinstance(values, (str, list)) or (
                    isinstance(values, list) and any(not isinstance(value, str) for value in values)
                ):
                    raise _schema_error(f"{row_path}.{key}", "Task field must be a string or array of strings")
        if "scope" in row:
            scope = row["scope"]
            if not isinstance(scope, (str, list)) or (
                isinstance(scope, list) and any(not isinstance(value, str) for value in scope)
            ):
                raise _schema_error(f"{row_path}.scope", "Task scope must be a string or array of strings")
        for key in ("metadata", "projection", "delivery_plan_document", "delivery_depth_contract", "behavior_contract"):
            if key in row and not isinstance(row[key], dict):
                raise _schema_error(f"{row_path}.{key}", "Task field must be an object")
        if "generated_outputs" in row:
            _, errors = validate_generated_outputs(row["generated_outputs"], path=f"{row_path}.generated_outputs")
            if errors:
                raise _PMContractError(errors[0])
        tasks.append(row)
        if source_paths is not None:
            source_paths.append(row_path)
    return tasks


class PMContractParsingMixin(_PMAdapterMixinBase):
    """Validate complete JSON syntax and task shape before normalization."""

    def _extract_task_contracts(
        self,
        response: str,
        *,
        directive: str,
        projection_hint: dict[str, Any] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            payload = _decode_complete_pm_payload(response)
            source_paths: list[str] = []
            raw_tasks = _complete_pm_tasks(payload, source_paths=source_paths)
        except _PMContractError as exc:
            if diagnostics is not None:
                diagnostics.append(exc.diagnostic)
            return []

        if raw_tasks:
            contracts: list[dict[str, Any]] = []
            non_delivery_constraint_skips = 0
            excluded_diagnostics: list[dict[str, Any]] = []
            for item, source_path in zip(raw_tasks, source_paths, strict=True):
                contract = self._normalize_task_contract(item, len(contracts) + 1, directive)
                contract["metadata"]["pm_artifact_source_path"] = source_path
                canonical_path = f"$.tasks[{len(contracts)}]"
                verifier_diagnostics = contract["metadata"]["pm_verification_command_diagnostics"]
                for diagnostic in verifier_diagnostics:
                    diagnostic["path"] = source_path + diagnostic["path"][len(canonical_path) :]
                if not is_pm_task_intent(item) and (_pm_raw_task_is_dependency_chain(item) or _pm_raw_task_is_meta_diagnostic(item)):
                    excluded_diagnostics.extend(verifier_diagnostics)
                    continue
                if not is_pm_task_intent(item) and _pm_raw_task_is_non_delivery_constraint(item):
                    non_delivery_constraint_skips += 1
                    excluded_diagnostics.extend(verifier_diagnostics)
                    continue
                contracts.append(contract)
            if non_delivery_constraint_skips >= 2 and len(contracts) < 2:
                if diagnostics is not None:
                    diagnostics.extend(excluded_diagnostics)
                return []
            if excluded_diagnostics:
                if contracts:
                    contracts[0]["metadata"]["pm_verification_command_diagnostics"].extend(excluded_diagnostics)
                elif diagnostics is not None:
                    diagnostics.extend(excluded_diagnostics)
            return self._apply_projection_contract_hint(
                [item for item in contracts if item],
                projection_hint=projection_hint,
            )

        return []

    @classmethod
    def _extract_tasks_from_payload(cls, payload: Any) -> list[Any]:
        try:
            return _complete_pm_tasks(payload)
        except _PMContractError:
            return []

    @staticmethod
    def _extract_json_payload(response: str) -> Any:
        try:
            return _decode_complete_pm_payload(response)
        except _PMContractError:
            return None

    def _extract_tasks_from_sections(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        active_list_field = ""

        for raw_line in str(response or "").splitlines():
            line = str(raw_line or "").rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            heading_match = _TASK_SECTION_HEADING.match(stripped)
            if heading_match:
                if current:
                    sections.append(current)
                title = str(heading_match.group(2) or "").strip()
                current = {
                    "title": title or f"Task {len(sections) + 1}",
                    "description": "",
                    "steps": [],
                    "acceptance": [],
                    "depends_on": [],
                }
                active_list_field = ""
                continue

            if current is None:
                continue

            key_match = re.match(
                r"^\s*([a-zA-Z_][a-zA-Z0-9_ ]*|目标|描述|范围|步骤|验收标准|验收|依赖|阶段)\s*[:：]\s*(.*)$", stripped
            )
            if key_match:
                key = str(key_match.group(1) or "").strip().lower()
                value = str(key_match.group(2) or "").strip()
                if key in {"title", "任务", "task"}:
                    if value:
                        current["title"] = value
                    active_list_field = ""
                    continue
                if key in {"goal", "目标"}:
                    current["goal"] = value
                    active_list_field = ""
                    continue
                if key in {"description", "描述"}:
                    current["description"] = value
                    active_list_field = ""
                    continue
                if key in {"scope", "范围"}:
                    current["scope"] = value
                    active_list_field = ""
                    continue
                if key in {"phase", "阶段"}:
                    current["phase"] = value
                    active_list_field = ""
                    continue
                if key in {"depends_on", "依赖"}:
                    current["depends_on"] = self._normalize_list(value)
                    active_list_field = ""
                    continue
                if key in {"steps", "步骤", "执行步骤"}:
                    current["steps"] = self._normalize_list(value)
                    active_list_field = "steps"
                    continue
                if key in {"acceptance", "acceptance_criteria", "验收", "验收标准"}:
                    current["acceptance"] = self._normalize_list(value)
                    active_list_field = "acceptance"
                    continue

            bullet_match = re.match(r"^\s*[-*]\s+(.*)$", stripped)
            if bullet_match and active_list_field in {"steps", "acceptance"}:
                item = str(bullet_match.group(1) or "").strip()
                if item:
                    rows = current.get(active_list_field)
                    if not isinstance(rows, list):
                        rows = []
                    rows.append(item)
                    current[active_list_field] = rows
                continue

            if active_list_field in {"steps", "acceptance"}:
                rows = current.get(active_list_field)
                if not isinstance(rows, list):
                    rows = []
                rows.extend(self._normalize_list(stripped))
                current[active_list_field] = [item for item in rows if str(item).strip()]
                continue

            desc = str(current.get("description") or "").strip()
            current["description"] = f"{desc} {stripped}".strip() if desc else stripped

        if current:
            sections.append(current)

        contracts = [
            self._normalize_task_contract(section, idx + 1, directive)
            for idx, section in enumerate(sections)
            if isinstance(section, dict)
        ]
        return [item for item in contracts if item]

    def _extract_tasks_from_bullets(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        contracts: list[dict[str, Any]] = []
        for line in str(response or "").splitlines():
            token = line.strip()
            if not token:
                continue
            match = _TASK_LINE_PREFIX.match(token)
            if not match:
                continue
            payload = token[match.end() :].strip()
            payload = re.sub(r"^\*\*(.*?)\*\*$", r"\1", payload).strip()
            payload = re.sub(r"^`(.*?)`$", r"\1", payload).strip()
            payload = payload.lstrip("- ").strip()
            payload_without_label = _pm_strip_task_label_prefix(payload)
            if payload_without_label:
                payload = payload_without_label
            if _PM_DETAIL_BULLET_PREFIX.match(_pm_strip_markdown_title_noise(payload)):
                continue
            if _pm_is_dependency_chain_text(payload):
                continue
            if not payload:
                continue
            if ":" in payload or "：" in payload:
                title, desc = re.split(r"[:：]", payload, maxsplit=1)
            elif re.search(r"\s+[—–-]\s+", payload):
                title, desc = re.split(r"\s+[—–-]\s+", payload, maxsplit=1)
            else:
                title, desc = payload, ""
            contracts.append(
                self._normalize_task_contract(
                    {
                        "title": title.strip(),
                        "description": desc.strip(),
                    },
                    len(contracts) + 1,
                    directive,
                )
            )
        return [item for item in contracts if item]
