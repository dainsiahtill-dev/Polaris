"""PM 合同归一化 mixin：标题/路径/scope/projection 字段归一与结构化校验。

本 mixin 由 :class:`PMAdapter` 组合；方法体与原 ``pm_adapter.py`` 100% 一致（无损迁移）。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
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
    _pm_append_unique_path,
    _pm_extract_inline_list_field,
    _pm_extract_requirement_subject,
    _pm_infer_test_target_file_for_contract,
    _pm_is_generic_product_test_path,
    _pm_is_placeholder_task_title,
    _pm_root_workspace_target_files_from_context,
    _pm_should_drop_generic_product_test_for_documentation_contract,
    _pm_split_concrete_targets_and_scopes,
    _pm_title_fragment,
)

_PM_VERIFICATION_MODALITIES = frozenset({"environment_prep", "build", "test", "lint", "entrypoint"})
_PM_SHELL_LAUNCHERS = frozenset({"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"})


def _normalize_pm_verification_commands(value: Any) -> list[dict[str, Any]]:
    """Keep only exact argv-based verifier authority rows.

    PM contract normalization never turns prose or a shell command string into
    execution authority.  Malformed rows are dropped; the downstream
    completion-contract preflight then fails closed when no valid authority is
    available.
    """

    if type(value) is not list:
        return []
    normalized: list[dict[str, Any]] = []
    for raw_row in value:
        if type(raw_row) is not dict or set(raw_row) != {"modality", "argv", "cwd"}:
            continue
        modality = raw_row.get("modality")
        argv_value = raw_row.get("argv")
        cwd_value = raw_row.get("cwd")
        if type(modality) is not str or modality not in _PM_VERIFICATION_MODALITIES:
            continue
        if type(argv_value) is not list or not argv_value or len(argv_value) > 128:
            continue
        argv: list[str] = []
        invalid_argv = False
        for item in argv_value:
            if type(item) is not str or not item or item != item.strip() or "\x00" in item:
                invalid_argv = True
                break
            argv.append(item)
        if invalid_argv:
            continue
        executable = PurePosixPath(argv[0].replace("\\", "/")).name.lower()
        if executable in _PM_SHELL_LAUNCHERS and any(item in {"-c", "-lc", "/c"} for item in argv[1:]):
            continue
        if type(cwd_value) is not str or not cwd_value or cwd_value != cwd_value.strip():
            continue
        if cwd_value != ".":
            windows_path = PureWindowsPath(cwd_value)
            posix_path = PurePosixPath(cwd_value)
            if (
                "\\" in cwd_value
                or windows_path.drive
                or windows_path.root
                or posix_path.is_absolute()
                or any(part in {"", ".", ".."} for part in cwd_value.split("/"))
                or posix_path.as_posix() != cwd_value
            ):
                continue
        normalized.append({"modality": modality, "argv": argv, "cwd": cwd_value})
    return normalized


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


def _pm_typescript_factory_contract_missing(contracts: list[dict[str, Any]], directive: str) -> list[str]:
    if not _directive_requires_typescript_package_contract(directive):
        return []

    targets = _pm_contract_target_files(contracts)
    lower_targets = {target.lower() for target in targets}
    missing: list[str] = []
    if len(contracts) < 2:
        missing.append("task_count>=2")
    for required in ("package.json", "tsconfig.json", "index.html", "README.md"):
        if required.lower() not in lower_targets:
            missing.append(required)
    if not any(target.endswith((".ts", ".tsx")) for target in targets):
        missing.append("*.ts")
    if not any(target.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")) for target in targets):
        missing.append("*.test.ts")
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
        raw_metadata_value = raw.get("metadata")
        raw_metadata_for_test_inference: dict[str, Any] = (
            raw_metadata_value if isinstance(raw_metadata_value, dict) else {}
        )
        inferred_test_target = ""
        if (
            not raw_metadata_for_test_inference.get("qa_rework_reason")
            and not drop_generic_product_test_for_documentation
        ):
            inferred_test_target = _pm_infer_test_target_file_for_contract(
                title=title,
                goal=goal,
                description=description,
                steps=steps,
                acceptance=acceptance,
                phase=phase,
                target_files=target_files,
                directive=directive,
            )
        if inferred_test_target:
            _pm_append_unique_path(target_files, inferred_test_target)

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
            # ``scope_paths`` becomes the Director/DEO write capability.  The
            # compact scope limit may bound supplementary directory/context
            # entries, but it must never remove a concrete mutation target:
            # doing so leaves a task that commands a write while withholding
            # the matching JobToken authority.  Keep least privilege by
            # retaining only task-local targets plus as many supplementary
            # scopes as fit inside the original compact budget.
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
            "verification_commands": _normalize_pm_verification_commands(raw.get("verification_commands")),
            "phase": phase,
            "depends_on": depends_on,
            "assigned_to": assigned_to,
            "execution_checklist": steps,
            "backlog_ref": str(raw.get("backlog_ref") or task_id).strip() or task_id,
            "metadata": metadata,
        }
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

        scenario_id = str(projection.get("scenario_id") or "").strip()
        if not scenario_id:
            return {}

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
        if (
            not projection_hint
            or str(projection_hint.get("execution_backend") or "").strip().lower() != "projection_generate"
        ):
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
        payload = _pm_quality_contract_context_payload(context)
        payload["tasks"] = [dict(item) for item in contracts if isinstance(item, dict)]
        if directive:
            payload["directive"] = directive
        autofix_pm_contract_for_quality(
            payload,
            workspace_full=str(Path(self.workspace).resolve()),
        )
        quality = evaluate_pm_task_quality(payload, docs_stage={})
        _raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
        tasks: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
        normalized = [_pm_reconcile_task_target_scope(item) for item in tasks if isinstance(item, dict)]
        verification_command_issues = _pm_verification_command_contract_issues(normalized, directive)
        missing_typescript_contract = _pm_typescript_factory_contract_missing(normalized, directive)
        if missing_typescript_contract or verification_command_issues:
            quality = dict(quality)
            raw_critical = quality.get("critical_issues")
            critical = list(raw_critical) if isinstance(raw_critical, list) else []
            if missing_typescript_contract:
                critical.append("factory_typescript_contract_missing:" + ",".join(missing_typescript_contract[:10]))
            critical.extend(verification_command_issues)
            raw_warnings = quality.get("warnings")
            warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
            if missing_typescript_contract:
                warnings.append(
                    "factory TypeScript/npm directive requires package, src, engine, test, and README targets"
                )
            if verification_command_issues:
                warnings.append("PM contract requires exact structured verifier argv/cwd authority before CE dispatch")
            quality["ok"] = False
            quality["score"] = min(int(quality.get("score") or 0), 40)
            quality["critical_issues"] = critical
            quality["warnings"] = warnings
            summary = str(quality.get("summary") or "").strip()
            suffix_parts: list[str] = []
            if missing_typescript_contract:
                suffix_parts.append("factory_typescript_contract_missing=" + ",".join(missing_typescript_contract[:10]))
            if verification_command_issues:
                suffix_parts.append("verification_command_contract=" + ",".join(verification_command_issues))
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
