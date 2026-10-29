"""PM 合同归一化 mixin：标题/路径/scope/projection 字段归一与结构化校验。

本 mixin 由 :class:`PMAdapter` 组合；严格验证结构化命令并保留非权威诊断。
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    validate_pm_artifact_contracts,
    validate_pm_verification_commands,
)
from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.cells.orchestration.pm_planning.public.task_intent import is_pm_task_intent
from polaris.kernelone.planning import (
    Plan,
    PlanStep,
    PlanValidationResult,
    StructuralPlanValidator,
)

from ._protocol import _PMAdapterMixinBase
from .language_contracts import directive_requires_typescript_package_contract
from .pm_text_utils import (
    _ACTION_MARKERS,
    _DEFAULT_PHASE_SEQUENCE,
    _PM_CONTRACT_SCOPE_PATH_LIMIT,
    _PM_NON_PATH_SCOPE_RE,
    _PM_SCOPE_PATH_FILENAMES,
    _PM_SCOPE_PATH_ROOTS,
    _PM_SCOPE_PATH_SUFFIXES,
    _STOPWORDS,
    _pm_extract_inline_list_field,
    _pm_extract_requirement_subject,
    _pm_is_generic_product_test_path,
    _pm_is_placeholder_task_title,
    _pm_root_workspace_target_files_from_context,
    _pm_should_drop_generic_product_test_for_documentation_contract,
    _pm_split_concrete_targets_and_scopes,
    _pm_target_files_include_tests,
    _pm_title_fragment,
)

_PM_VERIFICATION_MODALITY_ORDER = ("environment_prep", "build", "test", "lint", "entrypoint")
_PM_VERIFICATION_MODALITIES = frozenset(_PM_VERIFICATION_MODALITY_ORDER)
_PM_SHELL_LAUNCHERS = frozenset({"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"})


def _normalize_pm_verification_commands(value: Any) -> list[dict[str, Any]]:
    """Keep exact argv authority only when every declared row is valid."""
    rows, _diagnostics = _validate_pm_verification_commands(value)
    return rows


def _validate_pm_verification_commands(
    value: Any, *, path: str = "$.verification_commands"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility caller of the shared pure command schema validator."""
    result: tuple[list[dict[str, Any]], list[dict[str, Any]]] = validate_pm_verification_commands(value, path=path)
    return result


def _directive_requires_typescript_package_contract(directive: str) -> bool:
    return directive_requires_typescript_package_contract(directive)


def _pm_contract_target_files(contracts: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for contract in contracts:
        raw_targets = contract.get("target_files")
        values = raw_targets if isinstance(raw_targets, list) else []
        for value in values:
            token = str(value or "").replace("\\", "/").strip().lstrip("./")
            if token and token not in targets:
                targets.append(token)
    return targets


def _pm_declared_relative_files(contract: dict[str, Any]) -> set[str]:
    """Read exact structured targets without normalizing unsafe declarations."""
    values = contract.get("target_files")
    files: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value or "\\" in value:
            continue
        path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            path.is_absolute()
            or windows_path.drive
            or windows_path.root
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or path.as_posix() != value
        ):
            continue
        files.add(value)
    return files


def _pm_task_declares_html_entrypoint(contract: dict[str, Any]) -> bool:
    """Recognize declared HTML entrypoints without prescribing CE topology.

    Nested HTML needs an explicit entrypoint verifier, but scripts may live
    elsewhere or be inline. Runtime verification checks the executable wiring.
    """
    files = _pm_declared_relative_files(contract)
    if str(contract.get("phase") or "").strip().lower() in {"requirements", "documentation", "docs", "blueprint"}:
        return False
    if "index.html" in files:
        return True
    rows, diagnostics = _validate_pm_verification_commands(contract.get("verification_commands", []))
    if diagnostics or not any(row["modality"] == "entrypoint" for row in rows):
        return False
    return any(PurePosixPath(path).suffix.lower() in {".html", ".htm"} for path in files)


def _pm_contract_declares_html_entrypoint(contracts: list[dict[str, Any]]) -> bool:
    for contract in contracts:
        metadata = contract.get("metadata")
        # Normalization records rejection before path cleanup/inference. A model
        # flag never grants coverage: the structured declaration is checked too.
        if isinstance(metadata, dict) and metadata.get("pm_html_entrypoint_declared") is False:
            continue
        if _pm_task_declares_html_entrypoint(contract):
            return True
    return False


def _pm_typescript_factory_contract_missing(contracts: list[dict[str, Any]], directive: str) -> list[str]:
    if not _directive_requires_typescript_package_contract(directive):
        return []

    targets = _pm_contract_target_files(contracts)
    lower_targets = {target.lower() for target in targets}
    missing: list[str] = []
    if len(contracts) < 2:
        missing.append("task_count>=2")
    for required in ("package.json", "tsconfig.json", "README.md"):
        if required.lower() not in lower_targets:
            missing.append(required)
    ce_owns_topology = any(
        str((item.get("metadata") or {}).get("topology_authority") or "").strip() == "chief_engineer"
        for item in contracts
        if isinstance(item, dict)
    )
    if not ce_owns_topology and not any(target.endswith((".ts", ".tsx")) for target in targets):
        missing.append("*.ts")
    if not _pm_target_files_include_tests(targets):
        missing.append("executable_test_target")
    return missing


def _pm_verification_command_contract_issues(
    contracts: list[dict[str, Any]],
    directive: str,
) -> list[str]:
    """Validate PM-owned structured command authority before CE dispatch.

    The PM quality loop, rather than CE, owns repair/retry for an omitted or
    malformed verifier declaration.  Natural-language acceptance text never
    becomes command authority.
    """

    if _pm_verification_command_diagnostics(contracts):
        return ["verification_commands_invalid"]
    if not str(directive or "").strip():
        return []
    modalities: set[str] = set()
    for contract in contracts:
        rows = contract.get("verification_commands")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            modality = row.get("modality")
            if isinstance(modality, str) and modality in _PM_VERIFICATION_MODALITIES:
                modalities.add(modality)

    issues: list[str] = []
    if not modalities:
        return ["verification_commands_missing"]
    if "environment_prep" not in modalities:
        issues.append("verification_environment_prep_missing")
    if not modalities.intersection({"build", "test", "lint"}):
        issues.append("verification_delivery_gate_missing")

    lower_targets = {path.lower() for path in _pm_contract_target_files(contracts)}
    conventional_entrypoint = any(
        path
        in {
            "index.html",
            "main.py",
            "app.py",
            "main.go",
            "src/main.py",
            "src/main.rs",
            "src/main.cpp",
            "src/index.ts",
            "src/index.js",
            "src/main.ts",
            "src/main.js",
            "src/main/java/polaris/factory/main.java",
        }
        for path in lower_targets
    )
    if conventional_entrypoint and "entrypoint" not in modalities:
        issues.append("verification_entrypoint_missing")
    return issues


def _pm_verification_command_diagnostics(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve original errors and also validate callers supplying raw rows."""
    diagnostics: list[dict[str, Any]] = []
    for index, contract in enumerate(contracts):
        metadata = contract.get("metadata")
        if isinstance(metadata, dict):
            stored = metadata.get("pm_verification_command_diagnostics")
            if isinstance(stored, list):
                diagnostics.extend(dict(item) for item in stored if isinstance(item, dict))
        if "verification_commands" in contract:
            _, row_errors = _validate_pm_verification_commands(
                contract["verification_commands"], path=f"$.tasks[{index}].verification_commands"
            )
            diagnostics.extend(row_errors)
    return diagnostics


def _pm_contract_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _pm_quality_contract_context_payload(context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}

    metadata = _pm_contract_mapping(context.get("metadata"))
    payload: dict[str, Any] = {}
    payload_metadata: dict[str, Any] = {}
    for key in (
        "factory_bench_level",
        "factory_bench_project_id",
        "language",
        "level_contract",
        "delivery_depth_contract",
    ):
        value = context.get(key)
        if value is None and key in metadata:
            value = metadata.get(key)
        if value is None:
            continue
        copied = dict(value) if isinstance(value, dict) else value
        payload[key] = copied
        payload_metadata[key] = dict(value) if isinstance(value, dict) else value
    if payload_metadata:
        payload["metadata"] = payload_metadata
    return payload


def _pm_scope_path_covers_target(scope_path: Any, target_file: Any) -> bool:
    scope = str(scope_path or "").replace("\\", "/").strip("/")
    target = str(target_file or "").replace("\\", "/").strip("/")
    return bool(scope and target) and (target == scope or target.startswith(f"{scope}/"))


def _pm_reconcile_task_target_scope(contract: dict[str, Any]) -> dict[str, Any]:
    """Keep every final task-local mutation target inside its capability scope."""

    raw_scope_paths = contract.get("scope_paths")
    scope_paths = list(raw_scope_paths) if isinstance(raw_scope_paths, list) else []
    raw_target_files = contract.get("target_files")
    target_files = raw_target_files if isinstance(raw_target_files, list) else []
    for target_file in target_files:
        target = str(target_file or "").strip()
        if target and not any(_pm_scope_path_covers_target(scope, target) for scope in scope_paths):
            scope_paths.append(target)
    contract["scope_paths"] = list(dict.fromkeys(scope_paths))
    return contract


class PMContractNormalizationMixin(_PMAdapterMixinBase):
    """PM 合同归一化 mixin：标题/路径/scope/projection 字段归一与结构化校验。"""

    def _normalize_task_contract(
        self,
        raw: dict[str, Any],
        index: int,
        directive: str,
    ) -> dict[str, Any]:
        if is_pm_task_intent(raw):
            normalized = deepcopy(raw)
            normalized["metadata"]["pm_verification_command_diagnostics"] = []
            return normalized

        title = _pm_title_fragment(raw.get("title") or raw.get("subject") or "")
        if _pm_is_placeholder_task_title(title):
            for candidate in (raw.get("goal"), raw.get("description"), raw.get("backlog_ref")):
                candidate_text = _pm_title_fragment(candidate)
                if candidate_text and not _pm_is_placeholder_task_title(candidate_text):
                    title = re.split(r"[:：。.;；\n]", candidate_text, maxsplit=1)[0].strip()
                    break
            else:
                title = _pm_extract_requirement_subject(directive) or f"项目交付任务 {index}"
        title_lower = title.lower()
        if not any(marker in title_lower for marker in _ACTION_MARKERS):
            title = f"实现{title}"

        description = str(raw.get("description") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            goal = description or f"完成任务: {title}"
            requirement_subject = _pm_extract_requirement_subject(directive)
            if requirement_subject and requirement_subject not in goal:
                goal = f"{goal}；满足需求: {requirement_subject}"

        inline_field_source = "\n".join(
            item
            for item in (
                str(raw.get("description") or ""),
                str(raw.get("goal") or ""),
                str(raw.get("scope") or ""),
            )
            if item.strip()
        )
        inline_target_files = _pm_extract_inline_list_field(inline_field_source, "target_files")
        inline_scope_paths = _pm_extract_inline_list_field(inline_field_source, "scope_paths")
        target_values = raw.get("target_files") or inline_target_files
        scope_values = raw.get("scope_paths") or inline_scope_paths or raw.get("scope")
        context_values = raw.get("context_files") or raw.get("context_paths") or []
        target_items = self._normalize_scope_path_list(self._normalize_list(target_values))
        scope_items = self._normalize_scope_path_list(self._normalize_list(scope_values))
        context_files = self._normalize_scope_path_list(self._normalize_list(context_values))
        target_files, target_directory_scopes = _pm_split_concrete_targets_and_scopes(target_items)
        scope_file_targets, _scope_directory_paths = _pm_split_concrete_targets_and_scopes(scope_items)
        if not target_items and scope_file_targets:
            target_files = [*target_files, *scope_file_targets]
        root_workspace_targets = _pm_root_workspace_target_files_from_context(
            title=title,
            goal=goal,
            description=description,
            directive=directive,
        )
        if root_workspace_targets and not target_files:
            target_files = [*target_files, *root_workspace_targets]

        steps = self._normalize_list(raw.get("steps") or raw.get("execution_checklist"))
        if len(steps) < 2:
            steps = [
                f"分析并定位 {title} 所需改动",
                f"实现 {title} 并补充必要测试",
                "运行验证命令并记录结果",
            ]

        acceptance = self._normalize_list(raw.get("acceptance") or raw.get("acceptance_criteria"))
        if len(acceptance) < 2:
            acceptance = [
                "相关测试命令执行通过（如 `pytest`/`npm test`）",
                "功能行为与预期一致并可复现验证",
            ]

        phase = str(raw.get("phase") or _DEFAULT_PHASE_SEQUENCE[(index - 1) % len(_DEFAULT_PHASE_SEQUENCE)]).strip()
        drop_generic_product_test_for_documentation = _pm_should_drop_generic_product_test_for_documentation_contract(
            title=title,
            goal=goal,
            description=description,
            steps=steps,
            acceptance=acceptance,
            phase=phase,
        )
        if drop_generic_product_test_for_documentation:
            target_files = [path for path in target_files if not _pm_is_generic_product_test_path(path)]
            scope_items = [path for path in scope_items if not _pm_is_generic_product_test_path(path)]
        # Native model contracts never acquire inferred test ownership.

        if target_items:
            merged_scope_items = [
                *target_files,
                *scope_items,
                *target_directory_scopes,
            ]
        else:
            merged_scope_items = [
                *scope_items,
                *target_directory_scopes,
                *target_files,
            ]
        scope_items = list(dict.fromkeys(item for item in merged_scope_items if item))
        if not scope_items:
            scope_items = self._infer_scope_from_title(title)
        scope_text = ", ".join(scope_items[:4]) if scope_items else "src/"
        if scope_items:
            # Scope paths include read/search context. Concrete writes are
            # authorized by JobToken target_files; retain those paths in the
            # readable scope while bounding supplementary context entries.
            declared_target_scope = list(dict.fromkeys(target_files))
            declared_target_set = set(declared_target_scope)
            supplementary_scope = [item for item in scope_items if item not in declared_target_set]
            supplementary_budget = max(0, _PM_CONTRACT_SCOPE_PATH_LIMIT - len(declared_target_scope))
            allowed_supplementary = set(supplementary_scope[:supplementary_budget])
            scope_paths = [item for item in scope_items if item in declared_target_set or item in allowed_supplementary]
            for target in declared_target_scope:
                if target not in scope_paths:
                    scope_paths.append(target)
        else:
            scope_paths = ["src/", "tests/"]

        depends_on = self._normalize_list(raw.get("depends_on") or raw.get("dependencies"))
        task_id = str(raw.get("id") or f"TASK-{index}").strip()
        assigned_to = str(raw.get("assigned_to") or "Director").strip() or "Director"

        _raw_meta = raw.get("metadata")
        metadata: dict[str, Any] = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}
        # The reserved mirror is a final graph projection, never raw model
        # metadata authority or evidence that an unvalidated build can run.
        metadata.pop("generated_outputs", None)
        # Model-authored metadata never supplies validator evidence.
        metadata["pm_html_entrypoint_declared"] = _pm_task_declares_html_entrypoint(raw)
        verifier_rows, verifier_diagnostics = _validate_pm_verification_commands(
            raw.get("verification_commands", []), path=f"$.tasks[{index - 1}].verification_commands"
        )
        metadata["pm_verification_command_diagnostics"] = verifier_diagnostics
        execution_backend = str(raw.get("execution_backend") or metadata.get("execution_backend") or "").strip().lower()
        if execution_backend == "projection_generate" and index != 1:
            execution_backend = "code_edit"
        if execution_backend:
            metadata["execution_backend"] = execution_backend
        _raw_proj = metadata.get("projection")
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        _raw_raw_proj = raw.get("projection")
        raw_projection: dict[str, Any] = dict(_raw_raw_proj) if isinstance(_raw_raw_proj, dict) else {}
        if raw_projection:
            projection.update(raw_projection)
        for source_key, target_key in (
            ("projection_scenario", "scenario_id"),
            ("scenario_id", "scenario_id"),
            ("project_slug", "project_slug"),
            ("experiment_id", "experiment_id"),
            ("projection_experiment_id", "experiment_id"),
            ("projection_requirement", "requirement"),
            ("requirement_delta", "requirement"),
            ("use_pm_llm", "use_pm_llm"),
            ("run_verification", "run_verification"),
            ("overwrite", "overwrite"),
        ):
            value = raw.get(source_key)
            if value is None:
                continue
            token = str(value).strip() if isinstance(value, str) else value
            if token == "":
                continue
            projection[target_key] = value
        if projection:
            metadata["projection"] = projection
        delivery_plan_document = _pm_contract_mapping(raw.get("delivery_plan_document")) or _pm_contract_mapping(
            metadata.get("delivery_plan_document")
        )
        delivery_depth_contract = _pm_contract_mapping(raw.get("delivery_depth_contract")) or _pm_contract_mapping(
            metadata.get("delivery_depth_contract")
        )
        behavior_contract = _pm_contract_mapping(raw.get("behavior_contract")) or _pm_contract_mapping(
            metadata.get("behavior_contract")
        )
        if delivery_plan_document:
            metadata["delivery_plan_document"] = delivery_plan_document
        if delivery_depth_contract:
            metadata["delivery_depth_contract"] = delivery_depth_contract
        if behavior_contract:
            metadata["behavior_contract"] = behavior_contract
        normalized = {
            "id": task_id,
            "title": title,
            "goal": goal,
            "description": description or f"实现 {title}，并满足验收标准。",
            "scope": scope_text,
            "scope_paths": scope_paths,
            "target_files": list(dict.fromkeys(target_files)),
            "context_files": list(dict.fromkeys(context_files)),
            "steps": steps,
            "acceptance": acceptance,
            "acceptance_criteria": acceptance,
            "verification_commands": verifier_rows,
            "phase": phase,
            "depends_on": depends_on,
            "assigned_to": assigned_to,
            "execution_checklist": steps,
            "backlog_ref": str(raw.get("backlog_ref") or task_id).strip() or task_id,
            "metadata": metadata,
        }
        if "generated_outputs" in raw:
            normalized["generated_outputs"] = deepcopy(raw["generated_outputs"])
        if delivery_plan_document:
            normalized["delivery_plan_document"] = delivery_plan_document
        if delivery_depth_contract:
            normalized["delivery_depth_contract"] = delivery_depth_contract
        if behavior_contract:
            normalized["behavior_contract"] = behavior_contract
        return normalized

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
        if isinstance(value, list):
            items = []
            for item in value:
                token = str(item).strip()
                if token:
                    items.append(token)
            return items
        return []

    @classmethod
    def _normalize_scope_path_list(cls, values: list[str]) -> list[str]:
        rows: list[str] = []
        for value in values:
            token = cls._normalize_scope_path_token(value)
            if token and token not in rows:
                rows.append(token)
        return rows

    @classmethod
    def _normalize_scope_path_token(cls, value: str) -> str:
        raw = str(value or "").strip().strip("'\"").replace("\\", "/")
        if not raw:
            return ""
        if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", raw):
            return ""
        if _PM_NON_PATH_SCOPE_RE.search(raw):
            return ""

        token = re.sub(r"/+", "/", raw.lstrip("./").strip("/"))
        if not token or token in {".", "*", "**"}:
            return ""
        parts = [part for part in token.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            return ""

        filename = parts[-1]
        if filename.lower() == "readme.md":
            return "/".join([*parts[:-1], "README.md"]) if parts[:-1] else "README.md"
        if token in _PM_SCOPE_PATH_FILENAMES or filename in _PM_SCOPE_PATH_FILENAMES:
            return token
        if Path(filename).suffix.lower() in _PM_SCOPE_PATH_SUFFIXES:
            return token
        if parts[0] in _PM_SCOPE_PATH_ROOTS and (len(parts) > 1 or raw.endswith("/")):
            return f"{token}/" if raw.endswith("/") else token
        if token.rstrip("/") in _PM_SCOPE_PATH_ROOTS:
            return f"{token.rstrip('/')}/"
        return ""

    def _infer_scope_from_title(self, title: str) -> list[str]:
        keyword_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", title.lower())
        normalized = [token for token in keyword_tokens if token not in _STOPWORDS]
        if not normalized:
            return ["src/", "tests/"]
        first = normalized[0]
        return [f"src/{first}", "tests/"]

    def _derive_domain_token(self, directive: str) -> str:
        workspace_name = Path(self.workspace).resolve().name.lower()
        workspace_tokens = [token.strip() for token in re.split(r"[^a-z0-9]+", workspace_name) if token.strip()]
        for token in workspace_tokens:
            if len(token) < 3:
                continue
            if token in _STOPWORDS:
                continue
            return token

        keyword_match = re.search(
            r"(?:关键词|keywords?)\s*[:：]\s*([^\n]+)",
            str(directive or ""),
            flags=re.IGNORECASE,
        )
        if keyword_match:
            keyword_tokens: list[str] = re.findall(
                r"[a-z][a-z0-9_-]{2,}",
                str(keyword_match.group(1) or "").lower(),
            )
            for token in keyword_tokens:
                if token in _STOPWORDS:
                    continue
                return token

        text = str(directive or "").lower()
        tokens: list[str] = re.findall(r"[a-z][a-z0-9_-]{3,}", text)
        for token in tokens:
            if token in _STOPWORDS:
                continue
            return token
        return "project"

    def _extract_domain_keywords(self, directive: str, *, limit: int = 4) -> list[str]:
        text = str(directive or "")
        tokens: list[str] = []

        keyword_hint_pattern = re.compile(r"(?:示例|关键词|keywords?)\s*[:：]\s*([^\n]+)", re.IGNORECASE)
        for match in keyword_hint_pattern.finditer(text):
            chunk = str(match.group(1) or "").lower()
            for token in re.findall(r"[a-z][a-z0-9_-]{2,}", chunk):
                if token in _STOPWORDS or token in tokens:
                    continue
                tokens.append(token)
                if len(tokens) >= limit:
                    return tokens

        for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()):
            if token in _STOPWORDS or token in tokens:
                continue
            tokens.append(token)
            if len(tokens) >= limit:
                return tokens

        fallback = self._derive_domain_token(directive)
        if fallback and fallback not in tokens:
            tokens.append(fallback)
        return tokens[:limit]

    @staticmethod
    def _normalize_projection_project_slug(value: Any, *, default_value: str = "projection_lab") -> str:
        token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
        token = re.sub(r"_+", "_", token).strip("_")
        return token or str(default_value or "projection_lab").strip()

    def _extract_projection_contract_hint(
        self,
        *,
        input_data: dict[str, Any],
        context: dict[str, Any],
        directive: str,
    ) -> dict[str, Any]:
        _raw_input_meta = input_data.get("metadata") if isinstance(input_data, dict) else None
        input_metadata: dict[str, Any] = dict(_raw_input_meta) if isinstance(_raw_input_meta, dict) else {}
        _raw_ctx_meta = context.get("metadata") if isinstance(context, dict) else None
        context_metadata: dict[str, Any] = dict(_raw_ctx_meta) if isinstance(_raw_ctx_meta, dict) else {}

        execution_backend = (
            str(
                input_data.get("execution_backend")
                or input_metadata.get("execution_backend")
                or context.get("execution_backend")
                or context_metadata.get("execution_backend")
                or ""
            )
            .strip()
            .lower()
        )
        if execution_backend != "projection_generate":
            return {}

        projection: dict[str, Any] = {}
        for payload in (
            context_metadata.get("projection"),
            context.get("projection"),
            input_metadata.get("projection"),
            input_data.get("projection"),
        ):
            if isinstance(payload, dict):
                projection.update(payload)

        for source in (input_data, input_metadata, context, context_metadata):
            if not isinstance(source, dict):
                continue
            mapping = (
                ("projection_scenario", "scenario_id"),
                ("scenario_id", "scenario_id"),
                ("project_slug", "project_slug"),
                ("projection_requirement", "requirement"),
                ("requirement_delta", "requirement"),
                ("use_pm_llm", "use_pm_llm"),
                ("run_verification", "run_verification"),
                ("overwrite", "overwrite"),
            )
            for source_key, target_key in mapping:
                value = source.get(source_key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                projection[target_key] = value

        scenario_value = projection.get("scenario_id")
        if not isinstance(scenario_value, str) or not scenario_value.strip():
            return {}
        scenario_id = scenario_value.strip()

        projection["scenario_id"] = scenario_id
        projection["project_slug"] = self._normalize_projection_project_slug(
            projection.get("project_slug"),
        )
        projection["requirement"] = str(projection.get("requirement") or directive or "").strip()
        projection["use_pm_llm"] = bool(projection.get("use_pm_llm", True))
        projection["run_verification"] = bool(projection.get("run_verification", True))
        projection["overwrite"] = bool(projection.get("overwrite", False))

        return {
            "execution_backend": execution_backend,
            "projection": projection,
        }

    def _apply_projection_contract_hint(
        self,
        contracts: list[dict[str, Any]],
        *,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        projection_hint = self._extract_projection_contract_hint(
            input_data=projection_hint or {}, context={}, directive=""
        )
        if not projection_hint:
            return contracts

        _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        has_projection_generate = any(
            str(item.get("execution_backend") or "").strip().lower() == "projection_generate"
            or (
                isinstance(item.get("metadata"), dict)
                and str(item["metadata"].get("execution_backend") or "").strip().lower() == "projection_generate"
            )
            for item in contracts
            if isinstance(item, dict)
        )
        normalized_contracts: list[dict[str, Any]] = []

        for index, contract in enumerate(contracts):
            if not isinstance(contract, dict):
                continue
            enriched = dict(contract)
            _raw_enr_meta = enriched.get("metadata")
            metadata: dict[str, Any] = dict(_raw_enr_meta) if isinstance(_raw_enr_meta, dict) else {}
            _raw_proj = metadata.get("projection")
            projection_payload: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
            if isinstance(enriched.get("projection"), dict):
                projection_payload.update(enriched.get("projection") or {})

            execution_backend = (
                str(enriched.get("execution_backend") or metadata.get("execution_backend") or "").strip().lower()
            )
            if index == 0 and not has_projection_generate:
                execution_backend = "projection_generate"
            if index > 0 and execution_backend == "projection_generate":
                execution_backend = "code_edit"
            if execution_backend == "projection_generate":
                projection_payload.update(projection)
                metadata["projection"] = projection_payload
                metadata["execution_backend"] = "projection_generate"
                enriched["execution_backend"] = "projection_generate"
            elif execution_backend == "code_edit" or not execution_backend:
                metadata["execution_backend"] = "code_edit"
                enriched["execution_backend"] = "code_edit"

            enriched["metadata"] = metadata
            normalized_contracts.append(enriched)

        return normalized_contracts

    def _evaluate_contract_quality(
        self,
        contracts: list[dict[str, Any]],
        *,
        directive: str = "",
        context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if any(is_pm_task_intent(item) for item in contracts):
            semantic_payload: dict[str, Any] = {"tasks": deepcopy(contracts)}
            quality = evaluate_pm_task_quality(semantic_payload, workspace_full=str(Path(self.workspace).resolve()))
            return semantic_payload["tasks"], quality

        # Capture original declarations before quality autofix can split or
        # remove tasks. Feedback describes input rows, not derived copies.
        verifier_diagnostics = _pm_verification_command_diagnostics(contracts)
        language_payload = _pm_quality_contract_context_payload(context)
        primary_language = str(language_payload.get("language") or "").strip().lower()
        if not primary_language:
            depth = language_payload.get("delivery_depth_contract", {})
            if isinstance(depth, dict):
                primary_language = str(depth.get("language") or "").strip().lower()
        if not primary_language and _directive_requires_typescript_package_contract(directive):
            primary_language = "typescript"
        artifact_diagnostics = list(
            validate_pm_artifact_contracts(contracts, primary_language=primary_language).diagnostics
        )
        payload = _pm_quality_contract_context_payload(context)
        if primary_language:
            payload.setdefault("language", primary_language)
        payload["tasks"] = [dict(item) for item in contracts if isinstance(item, dict)]
        if directive:
            payload["directive"] = directive
        autofix_pm_contract_for_quality(
            payload,
            workspace_full=str(Path(self.workspace).resolve()),
        )
        # Quality hints may enrich checklists, but they cannot manufacture
        # artifact ownership or promote read-only dependencies into writes.
        authored = {str(item.get("id")): item for item in contracts}
        fission = payload.get("_quality_gate_task_fission", {})
        for item in payload.get("tasks", []):
            original = authored.get(str(item.get("id")))
            if original is None:
                continue
            for field in ("target_files", "context_files", "generated_outputs"):
                if field == "generated_outputs" and fission:
                    # The staged graph validator proved these input ID bindings,
                    # including consumers that retained their original task ID.
                    continue
                if field in original:
                    item[field] = deepcopy(original[field])
                else:
                    item.pop(field, None)
        quality = evaluate_pm_task_quality(payload, docs_stage={}, workspace_full=str(Path(self.workspace).resolve()))
        if fission:
            quality["task_fission"] = deepcopy(fission)
        _raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
        tasks: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
        normalized = [_pm_reconcile_task_target_scope(item) for item in tasks if isinstance(item, dict)]
        derived_diagnostics = validate_pm_artifact_contracts(normalized, primary_language=primary_language).diagnostics
        for item in normalized:
            raw_metadata = item.get("metadata")
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            metadata.pop("generated_outputs", None)
            if not artifact_diagnostics and not derived_diagnostics and "generated_outputs" in item:
                metadata["generated_outputs"] = deepcopy(item["generated_outputs"])
            item["metadata"] = metadata
        for diagnostic in derived_diagnostics:
            if fission:
                for original_id, derived_ids in fission["original_to_derived"].items():
                    if diagnostic.get("task_id") in derived_ids:
                        diagnostic = {
                            **diagnostic,
                            "provenance": {
                                "original_task_id": original_id,
                                "derived_task_id": diagnostic["task_id"],
                                "stage": "quality_autofix",
                            },
                        }
                        break
            if diagnostic not in artifact_diagnostics:
                artifact_diagnostics.append(diagnostic)
        verification_command_issues = _pm_verification_command_contract_issues(normalized, directive)
        if verifier_diagnostics:
            verification_command_issues = ["verification_commands_invalid"]
        quality_diagnostics = quality.get("contract_diagnostics")
        contract_diagnostics = [
            *(quality_diagnostics if isinstance(quality_diagnostics, list) else []),
            *verifier_diagnostics,
        ]
        for diagnostic in artifact_diagnostics:
            if diagnostic not in contract_diagnostics:
                contract_diagnostics.append(diagnostic)
        artifact_issues = list(
            dict.fromkeys(
                d["code"]
                for d in artifact_diagnostics
                if d["code"].startswith(("artifact_", "generated_output_", "pm_plan_language_mismatch"))
            )
        )
        missing_typescript_contract = _pm_typescript_factory_contract_missing(normalized, directive)
        if missing_typescript_contract or verification_command_issues or artifact_issues:
            quality = dict(quality)
            raw_critical = quality.get("critical_issues")
            critical = list(raw_critical) if isinstance(raw_critical, list) else []
            if missing_typescript_contract:
                critical.append("factory_typescript_contract_missing:" + ",".join(missing_typescript_contract[:10]))
            critical.extend(verification_command_issues)
            critical.extend(artifact_issues)
            raw_warnings = quality.get("warnings")
            warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
            if missing_typescript_contract:
                warnings.append(
                    "factory TypeScript/npm directive requires package.json/tsconfig.json, README, TypeScript business "
                    "source and executable test targets; Chief Engineer chooses directories and entrypoints"
                )
            if verification_command_issues:
                warnings.append("PM contract requires exact structured verifier argv/cwd authority before CE dispatch")
            if artifact_issues:
                warnings.append("PM artifact/action contract requires model-authored correction before task creation")
            quality["ok"] = False
            quality["score"] = min(int(quality.get("score") or 0), 40)
            quality["critical_issues"] = critical
            quality["contract_diagnostics"] = contract_diagnostics
            quality["warnings"] = warnings
            summary = str(quality.get("summary") or "").strip()
            suffix_parts: list[str] = []
            if missing_typescript_contract:
                suffix_parts.append("factory_typescript_contract_missing=" + ",".join(missing_typescript_contract[:10]))
            if verification_command_issues:
                suffix_parts.append("verification_command_contract=" + ",".join(verification_command_issues))
            if artifact_issues:
                suffix_parts.append("artifact_contract=" + ",".join(artifact_issues))
            suffix = "; ".join(suffix_parts)
            quality["summary"] = f"{summary}; {suffix}" if summary else suffix
        return normalized, quality

    def _validate_task_contracts(self, task_contracts: list[dict[str, Any]]) -> PlanValidationResult:
        """Validate task contract dependencies using StructuralPlanValidator.

        Args:
            task_contracts: List of task contract dictionaries

        Returns:
            PlanValidationResult with is_valid and any violations found
        """
        if not task_contracts:
            return PlanValidationResult(
                is_valid=False,
                violations=(),
                suggestions=("At least one task is required",),
            )

        # Build PlanStep objects from task contracts
        plan_steps: list[PlanStep] = []
        for contract in task_contracts:
            task_id = str(contract.get("id") or contract.get("title") or "unknown").strip()
            depends_on = contract.get("depends_on") or []
            if isinstance(depends_on, str):
                depends_on = [d.strip() for d in depends_on.split(",") if d.strip()]
            plan_steps.append(
                PlanStep(
                    id=task_id,
                    description=str(contract.get("description") or contract.get("title") or ""),
                    depends_on=tuple(depends_on),
                    estimated_duration=None,
                    metadata={},
                )
            )

        # Build Plan and validate
        plan = Plan(
            steps=tuple(plan_steps),
            max_duration=None,
            metadata={},
        )

        validator = StructuralPlanValidator()
        return validator.validate(plan)
