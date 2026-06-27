"""Code-generation prompt builder collaborator for the Director worker.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 8). ``PromptBuilder`` assembles the single LLM prompt the worker sends per
code-generation round: target/scope sections, architecture hints, ChiefEngineer
blueprint hints, the verification-repair context, the output contract, and the
prompt-size clamp. Factory bench tasks are intentionally allowed a larger prompt
cap because they validate whole-project generation, not a single narrow edit.

The Chinese architecture-guideline literals, the module-order truncation
(``... 及其他 N 个模块``), the ``stability_score > 0.3`` warning, the
``violation_constraints[:2]`` cap, the construction-hint membership filter / 12
cap, the three-way ``target_scope_rule`` branch, and the base
``KERNELONE_WORKER_PROMPT_MAX_CHARS`` clamp (``min(max(value, 2000), 40000)``)
MUST stay byte-identical to the original implementation; the bodies below are
moved verbatim except for the factory-bench-specific cap escalation and PM/CE
contract context section.

``_compact_prompt_fragment`` is NOT relocated here: it stays an instance method on
``WorkerExecutor`` (test contract) and is injected into this collaborator as a
callable so the token-service truncation behavior is shared, not duplicated.

This module depends only on the standard library + the pure ``path_predicates``
module + the ``TargetFileResolver`` / ``VerificationRepair`` / ``CodegenRoundPlanner``
collaborators + domain (``Task``). It MUST NOT import ``code_generation_engine`` /
``file_apply_service`` at module top (lazy circular-import contract documented in
``worker_executor``).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.director.tasking.internal import path_predicates
from polaris.domain.entities import Task

_DEFAULT_PROMPT_MAX_CHARS = 9000
_MIN_PROMPT_MAX_CHARS = 2000
_MAX_PROMPT_MAX_CHARS = 40000
_FACTORY_BENCH_L1_PROMPT_MIN_CHARS = 24000
_FACTORY_BENCH_L4_PROMPT_MIN_CHARS = 32000
_FACTORY_BENCH_L8_PROMPT_MIN_CHARS = 40000

if TYPE_CHECKING:
    from polaris.cells.director.tasking.internal.codegen_rounds import CodegenRoundPlanner
    from polaris.cells.director.tasking.internal.target_file_resolver import TargetFileResolver
    from polaris.cells.director.tasking.internal.verification_repair import VerificationRepair
    from polaris.cells.director.tasking.public.contracts import TaskExecutionStrategyV1


class _CompactFragment(Protocol):
    """Callable contract for ``WorkerExecutor._compact_prompt_fragment``."""

    def __call__(self, text: str, *, max_chars: int) -> str: ...


class PromptBuilder:
    """Assemble the Director code-generation prompt for one round."""

    def __init__(
        self,
        target_resolver: TargetFileResolver,
        verification_repair: VerificationRepair,
        codegen_rounds: CodegenRoundPlanner,
        compact_fragment: _CompactFragment,
    ) -> None:
        self._target_resolver = target_resolver
        self._verification_repair = verification_repair
        self._codegen_rounds = codegen_rounds
        self._compact_prompt_fragment = compact_fragment
        self._workspace = str(getattr(target_resolver, "workspace", "") or "")

    def extract_architecture_context(self, task: Task) -> dict[str, Any]:
        """Extract architecture context from task metadata."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        arch_context = metadata.get("architecture_context", {})
        return arch_context if isinstance(arch_context, dict) else {}

    def get_module_for_task(self, task: Task) -> str:
        """Get current module name for task."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        current_module = metadata.get("current_module", "unknown")
        return current_module if isinstance(current_module, str) else "unknown"

    def _resolve_prompt_max_chars(
        self,
        task: Task,
        strategy: TaskExecutionStrategyV1 | None = None,
    ) -> int:
        """Resolve the codegen prompt cap, escalating factory bench tasks."""
        raw_max_chars = os.environ.get("KERNELONE_WORKER_PROMPT_MAX_CHARS", str(_DEFAULT_PROMPT_MAX_CHARS))
        try:
            max_chars = int(raw_max_chars)
        except ValueError:
            max_chars = _DEFAULT_PROMPT_MAX_CHARS
        max_chars = min(max(max_chars, _MIN_PROMPT_MAX_CHARS), _MAX_PROMPT_MAX_CHARS)
        if strategy is not None:
            max_chars = max(max_chars, int(strategy.prompt_max_chars))

        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if not self._is_factory_bench_task(metadata):
            return max_chars

        level = self._factory_bench_level(metadata)
        if level >= 8:
            factory_floor = _FACTORY_BENCH_L8_PROMPT_MIN_CHARS
        elif level >= 4:
            factory_floor = _FACTORY_BENCH_L4_PROMPT_MIN_CHARS
        else:
            factory_floor = _FACTORY_BENCH_L1_PROMPT_MIN_CHARS
        return max(max_chars, factory_floor)

    @staticmethod
    def _is_factory_bench_task(metadata: dict[str, Any]) -> bool:
        return (
            any(
                str(metadata.get(key) or "").strip()
                for key in (
                    "factory_bench_session_id",
                    "factory_bench_project_id",
                    "factory_bench_project_workspace",
                )
            )
            or metadata.get("factory_bench_level") is not None
        )

    @staticmethod
    def _factory_bench_level(metadata: dict[str, Any]) -> int:
        try:
            return int(metadata.get("factory_bench_level") or 0)
        except (TypeError, ValueError):
            return 0

    def _metadata_items(self, value: Any, *, limit: int = 8, max_chars: int = 260) -> list[str]:
        if isinstance(value, str):
            raw_items: list[Any] = [item.strip() for item in re.split(r"[\n;]+", value) if item.strip()]
        elif isinstance(value, list | tuple):
            raw_items = list(value)
        else:
            return []

        items: list[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                text = str(item.get("description") or item.get("name") or item.get("command") or item.get("path") or "")
            else:
                text = str(item or "")
            text = self._compact_prompt_fragment(text.strip(), max_chars=max_chars)
            if text:
                items.append(text)
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _metadata_mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _contract_mapping(self, metadata: dict[str, Any], key: str) -> dict[str, Any]:
        direct = self._metadata_mapping(metadata.get(key))
        if direct:
            return direct
        task_payload = self._metadata_mapping(metadata.get("task"))
        nested = self._metadata_mapping(task_payload.get(key))
        if nested:
            return nested
        task_metadata = self._metadata_mapping(task_payload.get("metadata"))
        return self._metadata_mapping(task_metadata.get(key))

    def _contract_text_items(
        self,
        value: Any,
        *,
        limit: int = 6,
        max_chars: int = 240,
    ) -> list[str]:
        if isinstance(value, dict):
            raw_items: list[Any] = []
            for key, item in value.items():
                if isinstance(item, (list, tuple, dict)):
                    raw_items.append({str(key): item})
                else:
                    raw_items.append(f"{key}: {item}")
        elif isinstance(value, (list, tuple)):
            raw_items = list(value)
        elif isinstance(value, str):
            raw_items = [item.strip() for item in re.split(r"[\n;]+", value) if item.strip()]
        else:
            return []

        items: list[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                parts: list[str] = []
                for key, val in item.items():
                    if isinstance(val, (list, tuple)):
                        text_value = ", ".join(str(entry) for entry in val[:4])
                    elif isinstance(val, dict):
                        text_value = "; ".join(
                            f"{child_key}={child_val}" for child_key, child_val in list(val.items())[:4]
                        )
                    else:
                        text_value = str(val or "")
                    if text_value.strip():
                        parts.append(f"{key}: {text_value}")
                text = "; ".join(parts)
            else:
                text = str(item or "")
            text = self._compact_prompt_fragment(text.strip(), max_chars=max_chars)
            if text:
                items.append(text)
            if len(items) >= limit:
                break
        return items

    def _delivery_plan_rows(self, metadata: dict[str, Any]) -> list[str]:
        document = self._contract_mapping(metadata, "delivery_plan_document")
        if not document:
            return []
        rows: list[str] = []
        product_summary = self._metadata_mapping(document.get("product_summary"))
        intent = str(product_summary.get("intent") or document.get("intent") or "").strip()
        if intent:
            rows.append("Intent: " + self._compact_prompt_fragment(intent, max_chars=300))
        for label, key, limit in (
            ("User journey", "user_journey", 5),
            ("Capability plan", "capability_plan", 6),
            ("Behavior plan", "behavior_plan", 6),
            ("Verification plan", "verification_plan", 6),
            ("Evolution notes", "evolution_notes", 4),
        ):
            items = self._contract_text_items(document.get(key), limit=limit, max_chars=260)
            if items:
                rows.append(f"{label}: " + " | ".join(items))
        return rows

    def _delivery_depth_rows(self, metadata: dict[str, Any]) -> list[str]:
        depth_contract = self._contract_mapping(metadata, "delivery_depth_contract")
        behavior_contract = self._contract_mapping(metadata, "behavior_contract")
        if not behavior_contract:
            behavior_contract = self._metadata_mapping(depth_contract.get("behavior_contract"))
        rows: list[str] = []
        product_intent = self._metadata_mapping(depth_contract.get("product_intent"))
        for label, key, limit in (
            ("Core user journey", "core_user_journey", 4),
            ("Primary entities", "primary_entities", 6),
        ):
            items = self._contract_text_items(product_intent.get(key), limit=limit, max_chars=220)
            if items:
                rows.append(f"{label}: " + " | ".join(items))
        if behavior_contract:
            for label, key, limit in (
                ("Rule matrix", "rule_matrix", 8),
                ("Sample dataset", "sample_dataset", 6),
                ("Edge cases", "edge_cases", 8),
                ("Required behavior tests", "required_behavior_tests", 8),
                ("Minimum depth signals", "minimum_depth_signals", 6),
                ("Anti-hollow delivery", "anti_hollow_delivery", 6),
            ):
                items = self._contract_text_items(behavior_contract.get(key), limit=limit, max_chars=260)
                if items:
                    rows.append(f"{label}: " + " | ".join(items))
        acceptance_contract = self._metadata_mapping(depth_contract.get("acceptance_contract"))
        for label, key, limit in (
            ("Depth acceptance tests", "required_behavior_tests", 6),
            ("Deterministic checks", "deterministic_checks", 8),
            ("Minimum depth signals", "minimum_depth_signals", 6),
            ("Anti-hollow delivery", "anti_hollow_delivery", 6),
        ):
            items = self._contract_text_items(acceptance_contract.get(key), limit=limit, max_chars=240)
            if items and not any(row.startswith(label + ":") for row in rows):
                rows.append(f"{label}: " + " | ".join(items))
        return rows

    def _architecture_decision_items(
        self,
        value: Any,
        *,
        limit: int = 6,
        max_chars: int = 420,
    ) -> list[str]:
        if not isinstance(value, list | tuple):
            return []
        rows: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            concern = str(item.get("concern") or item.get("area") or "").strip()
            decision = str(item.get("decision") or item.get("selected") or "").strip()
            raw_evidence = item.get("evidence")
            evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
            kind = str(item.get("decision_status") or "").strip()
            if not kind:
                kind = "guidance" if evidence.get("guidance_only") is True else "decision"
            source = str(item.get("source") or "").strip()
            libraries = self._metadata_items(
                item.get("selected_libraries") or item.get("libraries"),
                limit=4,
                max_chars=80,
            )
            options = self._metadata_items(
                item.get("options_considered") or item.get("options"),
                limit=5,
                max_chars=120,
            )
            constraints = self._metadata_items(item.get("constraints"), limit=2, max_chars=120)
            rationale = str(item.get("rationale") or item.get("reason") or "").strip()
            parts: list[str] = [kind]
            if source:
                parts.append("source=" + source)
            if concern:
                parts.append(concern)
            if decision:
                parts.append(decision)
            if libraries:
                parts.append("libraries=" + ", ".join(libraries))
            if options:
                parts.append("options=" + " | ".join(options))
            if constraints:
                parts.append("constraints=" + " | ".join(constraints))
            if rationale:
                parts.append("rationale=" + self._compact_prompt_fragment(rationale, max_chars=160))
            text = self._compact_prompt_fragment("; ".join(parts), max_chars=max_chars)
            if text:
                rows.append(text)
            if len(rows) >= limit:
                break
        return rows

    def _contract_context_section(self, task: Task) -> str:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        lines: list[str] = []

        if self._is_factory_bench_task(metadata):
            project_id = str(metadata.get("factory_bench_project_id") or "").strip()
            title = str(metadata.get("factory_bench_title") or "").strip()
            level = self._factory_bench_level(metadata)
            workspace = str(metadata.get("factory_bench_project_workspace") or "").strip()
            bench_label = f"L{level}" if level > 0 else "level n/a"
            project_label = project_id or "project n/a"
            title_suffix = f" - {title}" if title else ""
            lines.append(f"- Factory bench: {project_label} ({bench_label}){title_suffix}")
            if workspace:
                lines.append(f"- Target workspace: {self._compact_prompt_fragment(workspace, max_chars=220)}")

        delivery_plan_rows = self._delivery_plan_rows(metadata)
        if delivery_plan_rows:
            lines.append("- Delivery plan document: " + " || ".join(delivery_plan_rows))

        delivery_depth_rows = self._delivery_depth_rows(metadata)
        if delivery_depth_rows:
            lines.append("- Delivery depth/behavior contract: " + " || ".join(delivery_depth_rows))

        section_specs = (
            ("Acceptance criteria", "acceptance_criteria", 10),
            ("Constraints", "constraints", 8),
            ("Quality gates", "quality_gates", 8),
            ("Verification commands", "verification_commands", 8),
            ("Entrypoints", "entrypoints", 8),
            ("Dependencies", "dependencies", 8),
            ("Selected libraries", "selected_libraries", 8),
        )
        for label, key, limit in section_specs:
            items = self._metadata_items(metadata.get(key), limit=limit)
            if items:
                lines.append(f"- {label}: " + "; ".join(items))

        architecture_decisions = self._architecture_decision_items(metadata.get("architecture_decisions"))
        if architecture_decisions:
            lines.append("- Architecture guidance/decisions: " + " || ".join(architecture_decisions))

        task_context = metadata.get("task_context")
        if isinstance(task_context, dict):
            for label, key in (
                ("Prior gate failures", "failed_gates"),
                ("Previous errors", "errors"),
                ("Required artifacts", "required_artifacts"),
            ):
                items = self._metadata_items(task_context.get(key), limit=6, max_chars=220)
                if items:
                    lines.append(f"- {label}: " + "; ".join(items))

        previous_verification = metadata.get("previous_verification_result")
        if isinstance(previous_verification, dict):
            failed_checks = self._metadata_items(previous_verification.get("failed_checks"), limit=6, max_chars=220)
            if failed_checks:
                lines.append("- Previous failed checks: " + "; ".join(failed_checks))
            error_text = str(previous_verification.get("error") or previous_verification.get("summary") or "").strip()
            if error_text:
                lines.append(
                    "- Previous verification summary: " + self._compact_prompt_fragment(error_text, max_chars=360)
                )

        return "\n".join(lines) if lines else "- no additional PM/CE contract context"

    def extract_functional_requirements(self, description: str) -> list[str]:
        """Extract functional requirements from description.

        Dead code carried verbatim from ``WorkerExecutor`` (zero callers); see the
        G7 blueprint and the separate §8 cleanup ADR. Retained for behavior parity,
        not for live use.
        """
        requirements: list[str] = []
        lines = str(description or "").split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Remove common prefixes
            cleaned = re.sub(r"^[\d]+[.)]\s*", "", line)
            cleaned = re.sub(r"^[-*•]\s*", "", cleaned)
            cleaned = re.sub(
                r"^(must|should|need|require|implement|create|add)\s+",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            if len(cleaned) > 10 and len(cleaned) < 500:
                requirements.append(cleaned)
        return requirements[:10]

    def get_framework_guidance(self, language: str, framework: str | None) -> str:
        """Get framework-specific guidance.

        Dead §8 code carried verbatim from ``WorkerExecutor`` (zero callers); the
        FastAPI/Flask templates are flagged for removal in a separate §8 cleanup
        ADR. Retained here only for behavior parity, not for live use.
        """
        if language != "python":
            return ""
        if framework == "fastapi":
            return """
## FastAPI Specific Requirements
- Use Pydantic models for request/response validation
- Include proper HTTP exception handling
- Add OpenAPI/Swagger documentation
- Use async/await for endpoint handlers
"""
        elif framework == "flask":
            return """
## Flask Specific Requirements
- Use Flask blueprints for route organization
- Include proper error handlers
- Add application factory pattern if appropriate
"""
        return ""

    def build_code_generation_prompt(
        self,
        task: Task,
        *,
        round_index: int = 0,
        round_total: int = 0,
        round_files: list[Any] | None = None,
    ) -> str:
        """Build LLM prompt for code generation."""
        raw_subject = str(task.subject or "")
        raw_description = str(task.description or "")
        task_subject = self._compact_prompt_fragment(raw_subject, max_chars=280)
        task_description = self._compact_prompt_fragment(
            raw_description,
            max_chars=2600,
        )
        normalized_round_files: list[str] = []
        for raw_round_file in round_files or []:
            raw_path = raw_round_file.get("path") if isinstance(raw_round_file, dict) else raw_round_file
            path = str(raw_path or "").strip().replace("\\", "/")
            if path and path_predicates.is_concrete_target_file_path(path) and path not in normalized_round_files:
                normalized_round_files.append(path)
        target_files = normalized_round_files or self._target_resolver.normalize_target_files(task)
        target_text = "\n".join(f"- {path}" for path in target_files[:16]) if target_files else "- (model may decide)"
        repair_records = self._verification_repair.unresolved_import_repair_records(task)
        target_scope_rule = (
            "- This is a verification repair round. Edit/create only the target files listed above, "
            "including derived repair files. Do not write outside declared scopes."
            if repair_records and target_files
            else (
                "- Concrete target files are declared for this round. Edit/create only those paths; "
                "do not add unrelated files."
                if target_files
                else "- No concrete target files were declared. Choose the smallest concrete file set under the declared scopes."
            )
        )
        scope_paths = self._target_resolver.normalize_scope_paths(task)
        scope_text = "\n".join(f"- {path}" for path in scope_paths[:16]) if scope_paths else "- (not declared)"
        construction_hints: list[str] = []
        for plan in self._codegen_rounds.construction_file_plans(task):
            path = str(plan.get("path") or "").strip()
            if not path:
                continue
            if target_files and path not in target_files:
                continue
            steps = plan.get("implementation_steps")
            step_text = (
                "; ".join(str(item).strip() for item in steps[:2] if str(item or "").strip())
                if isinstance(steps, list)
                else ""
            )
            step_text = self._compact_prompt_fragment(step_text, max_chars=180)
            if step_text:
                construction_hints.append(f"- {path}: {step_text}")
            else:
                construction_hints.append(f"- {path}: follow ChiefEngineer file plan")
            if len(construction_hints) >= 12:
                break
        round_text = ""
        if round_index > 0 and round_total > 0:
            round_text = f"\nBuild Round: {round_index}/{round_total}\n"

        # Extract architecture context
        arch_context = self.extract_architecture_context(task)
        current_module = self.get_module_for_task(task)

        # Build architecture hints
        arch_hints: list[str] = []
        module_order = arch_context.get("module_order", [])
        if module_order:
            arch_hints.append(f"模块构建顺序（底层优先）: {' -> '.join(module_order[:6])}")
            if len(module_order) > 6:
                arch_hints.append(f"  ... 及其他 {len(module_order) - 6} 个模块")

        module_arch = arch_context.get("module_arch", {})
        if current_module in module_arch:
            arch = module_arch[current_module]
            layer = arch.get("layer", 0)
            deps = arch.get("dependencies", [])
            stability = arch.get("stability_score", 0)
            arch_hints.append(f"当前模块: '{current_module}' (层级 L{layer})")
            if deps:
                arch_hints.append(f"  依赖模块: {', '.join(deps[:6])}")
            if stability > 0.3:
                arch_hints.append(f"  注意: 此模块被多个其他模块依赖（稳定性 {stability:.0%}），请设计稳定接口")

        constraints = arch_context.get("constraints", [])
        violation_constraints = [c for c in constraints if c.startswith(("❌", "⚠️"))]
        for vc in violation_constraints[:2]:
            arch_hints.append(f"架构警告: {vc}")

        arch_section = "\n".join(f"- {h}" for h in arch_hints) if arch_hints else "- 无全局架构上下文"
        contract_context = self._contract_context_section(task)
        metadata = task.metadata if isinstance(task.metadata, dict) else {}

        # Language-specific role identity + expert guidance
        from .execution_contract import build_task_execution_contract
        from .execution_profile import resolve_director_execution_profile
        from .execution_strategy import resolve_director_execution_strategy
        from .language_guidance import build_language_section

        execution_profile = resolve_director_execution_profile(
            subject=raw_subject,
            description=raw_description,
            metadata=metadata,
            target_files=target_files,
            scope_paths=scope_paths,
            workspace=self._workspace or str(getattr(task, "workspace", "") or ""),
        )
        guidance_metadata = dict(metadata)
        guidance_metadata.setdefault("detected_language", execution_profile.language)
        if execution_profile.framework:
            guidance_metadata.setdefault("detected_framework", execution_profile.framework)
        guidance_metadata.setdefault("project_type", execution_profile.project_type)
        guidance_metadata.setdefault("task_type", execution_profile.task_type)
        guidance_metadata.setdefault("phase", execution_profile.phase)
        role_identity, language_section = build_language_section(
            target_files,
            self._workspace or str(getattr(task, "workspace", "") or ""),
            metadata=guidance_metadata,
            subject=raw_subject,
            description=raw_description,
            scope_paths=scope_paths,
        )
        execution_profile_section = "\n".join(
            [
                f"- schema: {execution_profile.schema_version}",
                f"- dispatch_type: {execution_profile.dispatch_type}",
                f"- task_type: {execution_profile.task_type}",
                f"- phase: {execution_profile.phase}",
                f"- project_type: {execution_profile.project_type}",
                f"- language: {execution_profile.language_display_name}",
                f"- framework: {execution_profile.framework_display_name or '(none)'}",
                f"- temperature_phase: {execution_profile.temperature_phase}",
                f"- temperature: {execution_profile.temperature:.2f}",
                f"- output_contract_id: {execution_profile.output_contract_id}",
            ]
        )
        execution_strategy = resolve_director_execution_strategy(
            execution_profile,
            metadata=metadata,
        )
        execution_contract = build_task_execution_contract(
            execution_profile,
            execution_strategy,
            metadata=metadata,
        )
        execution_contract_payload = execution_contract.to_dict()
        delivery_contract = execution_contract_payload["delivery_contract"]
        quality_contract = execution_contract_payload["quality_contract"]
        audit_contract = execution_contract_payload["audit_contract"]
        execution_strategy_section = "\n".join(
            [
                f"- schema: {execution_strategy.schema_version}",
                f"- output_budget_tokens: {execution_strategy.output_budget_tokens}",
                f"- input_budget_tokens: {execution_strategy.input_budget_tokens}",
                f"- prompt_max_chars: {execution_strategy.prompt_max_chars}",
                f"- min_context_utilization: {execution_strategy.min_context_utilization:.2f}",
                f"- underutilized_policy: {execution_strategy.context_underutilized_policy}",
                "- evidence_requirements: " + ", ".join(execution_strategy.evidence_requirements),
            ]
        )
        execution_contract_section = "\n".join(
            [
                f"- schema: {execution_contract.schema_version}",
                f"- contract_hash: {audit_contract.get('contract_hash', '')}",
                "- primary_entities: " + ", ".join(delivery_contract.get("primary_entities") or ["(not declared)"]),
                f"- rule_count: {delivery_contract.get('rule_count', 0)}",
                f"- edge_case_count: {delivery_contract.get('edge_case_count', 0)}",
                f"- level: {delivery_contract.get('level') or '(not declared)'}",
                "- quality_gates: " + ", ".join(quality_contract.get("quality_gates") or ["(not declared)"]),
                "- verification_commands: "
                + ", ".join(quality_contract.get("verification_commands") or ["(not declared)"]),
                "- deterministic_checks: "
                + ", ".join(quality_contract.get("deterministic_checks") or ["(not declared)"]),
            ]
        )

        prompt_body = f"""{role_identity}

你正在实现以下任务。
{language_section}

=== Director Execution Profile ===
{execution_profile_section}

=== Director Execution Strategy ===
{execution_strategy_section}

=== Shared Execution Contract ===
{execution_contract_section}

Task: {task_subject}
Description: {task_description}
{round_text}
Target files for this execution:
{target_text}

Declared directory/module scopes:
{scope_text}

=== PM/CE Contract Context ===
{contract_context}

=== Architecture Context (全局架构上下文) ===
{arch_section}

=== ChiefEngineer Blueprint Hints ===
{chr(10).join(construction_hints) if construction_hints else "- no explicit file hints"}

=== Verification Repair Context ===
{self._verification_repair.repair_prompt_section(task)}

IMPORTANT ARCHITECTURE GUIDELINES:
1. 遵循模块层级: 底层模块（L0）提供基础设施，上层模块（L1+）依赖底层
2. 保持接口稳定: 如果被依赖的模块（高稳定性），优先设计清晰的接口
3. 避免循环依赖: 不要让你的模块依赖关系形成环路
4. 按模块构建顺序施工: 优先实现底层模块，再实现依赖它们的上层模块
5. 架构演进: 如果模块健康状况不佳，优先重构而不是添加新功能
6. 模块规划: 如果任务涉及计划中的新模块，请按ADR(架构决策记录)的指引实现
7. 接口优先: 对于稳定模块，先定义清晰的公共接口，再实现内部逻辑

Please generate the code to implement this task.
"""

        output_contract = f"""
Output contract: {execution_profile.output_contract_id}
{target_scope_rule}
- Return only `PATCH_FILE:` blocks or fenced ` ```file: path` file sections.
- Do not output `Command:`, shell commands, terminal logs, status narration, SESSION_PATCH, or tool-call wrappers.
- If target files are not concrete, choose concrete workspace-relative file paths under the declared scopes and create those files.

For each file you create, provide:
1. Prefer PATCH format for incremental edits on existing files:
   PATCH_FILE: path/to/file.py
   <<<<<<< SEARCH
   <old snippet>
   =======
   <new snippet>
   >>>>>>> REPLACE
2. For new files, use full-file block:
   FILE: path/to/file.py
   <complete content>
   END FILE
3. For deletions, use:
   DELETE_FILE: path/to/obsolete.py

Format your response as:

```file: path/to/file.py
<file content here>
```

```file: another/file.py
<content>
```

Requirements:
- Write complete, working code
- Include all necessary imports
- Add appropriate comments and docstrings
- Follow best practices for the target language/framework
- Ensure the code is syntactically correct
- Prioritize minimal patch edits over full-file rewrites when file already exists
- Prefer editing/creating the listed target files before creating new files
- Keep this generation response focused on source-file changes. Skip package
  installation, build/test commands, and dev-server startup; Polaris runs
  verification after file application.
"""

        max_chars = self._resolve_prompt_max_chars(task, execution_strategy)
        protected_suffix_budget = len(output_contract) + 2
        body_budget = max(500, max_chars - protected_suffix_budget)
        compact_body = self._compact_prompt_fragment(prompt_body, max_chars=body_budget)
        return f"{compact_body.rstrip()}\n{output_contract}"
