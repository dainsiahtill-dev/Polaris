"""failure diagnostics and evidence for AgentStressRunner (mixin)."""

# mypy: ignore-errors

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

from ..contracts import normalize_status
from ..engine import RoundResult, StageResult


class _AgentStressRunnerDiagnosticsMixin:
    @staticmethod
    def _infer_workspace_quality_failure(
        workspace_artifacts: dict[str, Any],
    ) -> dict[str, str] | None:
        if not isinstance(workspace_artifacts, dict):
            return None

        quality_gate = workspace_artifacts.get("quality_gate")
        quality_gate = quality_gate if isinstance(quality_gate, dict) else {}
        has_gate_metrics = any(
            key in workspace_artifacts
            for key in (
                "code_file_count",
                "new_code_file_count",
                "new_code_line_count",
                "fallback_scaffold_detected",
                "placeholder_markers",
                "generic_scaffold_markers",
                "domain_keywords",
                "domain_keyword_hits",
            )
        )
        if not has_gate_metrics:
            return None

        def _as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        code_file_count = _as_int(workspace_artifacts.get("code_file_count"))
        new_code_file_count = _as_int(workspace_artifacts.get("new_code_file_count"))
        new_code_line_count = _as_int(workspace_artifacts.get("new_code_line_count"))
        min_new_code_files = _as_int(quality_gate.get("min_new_code_files"))
        min_new_code_lines = _as_int(quality_gate.get("min_new_code_lines"))
        min_generic_markers = _as_int(quality_gate.get("min_generic_scaffold_markers"), 2)

        placeholder_markers = (
            workspace_artifacts.get("placeholder_markers")
            if isinstance(workspace_artifacts.get("placeholder_markers"), list)
            else []
        )
        generic_scaffold_markers = (
            workspace_artifacts.get("generic_scaffold_markers")
            if isinstance(workspace_artifacts.get("generic_scaffold_markers"), list)
            else []
        )
        domain_keywords = (
            workspace_artifacts.get("domain_keywords")
            if isinstance(workspace_artifacts.get("domain_keywords"), list)
            else []
        )
        domain_keyword_hits = (
            workspace_artifacts.get("domain_keyword_hits")
            if isinstance(workspace_artifacts.get("domain_keyword_hits"), list)
            else []
        )

        if "code_file_count" in workspace_artifacts and code_file_count <= 0:
            return {
                "failure_point": "project_output_missing",
                "root_cause": "Factory lifecycle completed but workspace contains no generated project code files",
                "failure_evidence": "code_file_count=0",
            }
        if bool(workspace_artifacts.get("fallback_scaffold_detected")):
            return {
                "failure_point": "project_output_fallback_scaffold",
                "root_cause": "Director fallback scaffold was detected; this round did not produce authentic project code",
                "failure_evidence": f"fallback_scaffold_files={workspace_artifacts.get('fallback_scaffold_files')}",
            }
        if placeholder_markers:
            return {
                "failure_point": "project_output_placeholder_code",
                "root_cause": "Generated project output contains placeholder markers instead of completed business logic",
                "failure_evidence": f"placeholder_markers={placeholder_markers[:10]}",
            }
        if len(generic_scaffold_markers) >= max(min_generic_markers, 1):
            return {
                "failure_point": "project_output_generic_scaffold",
                "root_cause": "Generated project output matches known generic scaffold patterns",
                "failure_evidence": f"generic_scaffold_markers={generic_scaffold_markers[:10]}",
            }
        if domain_keywords and not domain_keyword_hits:
            return {
                "failure_point": "project_output_not_project_specific",
                "root_cause": "Generated project output does not match expected project-domain keywords",
                "failure_evidence": f"expected_keywords={domain_keywords[:12]} matched_keywords=[]",
            }
        if "new_code_file_count" in workspace_artifacts and new_code_file_count <= 0:
            return {
                "failure_point": "project_output_stagnant",
                "root_cause": "No new or modified project code files were produced in this attempt",
                "failure_evidence": "new_or_modified_code_files=0",
            }
        if min_new_code_files > 0 and new_code_file_count < min_new_code_files:
            return {
                "failure_point": "project_output_too_sparse",
                "root_cause": "Generated project output is too sparse for the configured quality baseline",
                "failure_evidence": (
                    f"new_or_modified_code_files={new_code_file_count} required_min_new_code_files={min_new_code_files}"
                ),
            }
        if min_new_code_lines > 0 and new_code_line_count < min_new_code_lines:
            return {
                "failure_point": "project_output_too_small",
                "root_cause": "Generated project code size is below the configured quality baseline",
                "failure_evidence": (
                    f"new_code_line_count={new_code_line_count} required_min_new_code_lines={min_new_code_lines}"
                ),
            }
        return None

    @staticmethod
    def _select_retry_start_from(
        result: RoundResult,
        *,
        architect_ready: bool,
        pm_ready: bool,
    ) -> str:
        """按失败环节选择下一次 attempt 的主链入口。"""
        failure_point = normalize_status(result.failure_point)
        root_cause = normalize_status(result.root_cause)
        evidence = normalize_status(result.failure_evidence)
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        inferred_quality_failure = _AgentStressRunnerDiagnosticsMixin._infer_workspace_quality_failure(
            workspace_artifacts
        )
        if inferred_quality_failure and failure_point in {"", "quality_gate", "qa"}:
            signal_text = " ".join(
                [
                    failure_point,
                    root_cause,
                    evidence,
                    str(inferred_quality_failure.get("failure_point") or ""),
                    str(inferred_quality_failure.get("root_cause") or ""),
                    str(inferred_quality_failure.get("failure_evidence") or ""),
                ]
            ).strip()
        else:
            signal_text = " ".join([failure_point, root_cause, evidence]).strip()

        def _contains(tokens: tuple[str, ...]) -> bool:
            return any(token in signal_text for token in tokens)

        # 规划层失败：需要回到 PM（若尚无可用 architect 产物则回到 architect）。
        if _contains(("pm_", "pm ", "pm_planning", "contract", "tasks_plan", "pm_contract")):
            return "pm" if architect_ready else "architect"

        # 架构层失败：必须回到 architect。
        if _contains(("architect", "docs_generation", "court_phase", "plan.md", "architecture.md")):
            return "architect"

        # 项目产物质量问题（语义不匹配/产物停滞）通常需要重新下发执行合同，
        # 从 PM 重启可以保持架构文档不变，同时给 Director 新任务，避免 Director 空跑。
        if _contains(
            (
                "project_output_",
                "chain_trace_missing_tasks",
                "chain_observability_missing_tools",
            )
        ):
            if architect_ready:
                return "pm"
            return "architect"

        # 代码执行 / QA 门禁问题：优先从 director 续跑，避免无谓回退到 architect。
        if _contains(
            (
                "director",
                "qa",
                "quality_gate",
            )
        ):
            if pm_ready:
                return "director"
            if architect_ready:
                return "pm"
            return "architect"

        # 未知失败：采用保守策略，优先从 PM 续跑。
        if architect_ready:
            return "pm"
        return "architect"

    @staticmethod
    def _build_retry_guidance(result: RoundResult) -> str:
        failure_point = str(result.failure_point or "").strip()
        root_cause = str(result.root_cause or "").strip()
        evidence = str(result.failure_evidence or "").strip()
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        inferred_quality_failure = _AgentStressRunnerDiagnosticsMixin._infer_workspace_quality_failure(
            workspace_artifacts
        )
        quality_gate = workspace_artifacts.get("quality_gate")
        quality_gate = quality_gate if isinstance(quality_gate, dict) else {}
        min_new_code_files = int(quality_gate.get("min_new_code_files") or 0)
        min_new_code_lines = int(quality_gate.get("min_new_code_lines") or 0)
        new_code_file_count = int(workspace_artifacts.get("new_code_file_count") or 0)
        new_code_line_count = int(workspace_artifacts.get("new_code_line_count") or 0)
        raw_domain_keywords = workspace_artifacts.get("domain_keywords")
        domain_keywords = raw_domain_keywords if isinstance(raw_domain_keywords, list) else []
        ascii_keywords = [
            str(keyword).strip()
            for keyword in domain_keywords
            if re.fullmatch(r"[a-z0-9_-]+", str(keyword or "").strip().lower())
        ]
        guidance_lines = [
            f"- 上轮失败点: {failure_point or 'unknown'}",
        ]
        if root_cause:
            guidance_lines.append(f"- 根因: {root_cause}")
        if evidence:
            guidance_lines.append(f"- 证据: {evidence[:300]}")
        if inferred_quality_failure:
            guidance_lines.append(f"- 质量门禁诊断: {inferred_quality_failure.get('failure_point')}")
            guidance_lines.append(f"- 质量诊断证据: {inferred_quality_failure.get('failure_evidence')}")
        if min_new_code_files > 0 or min_new_code_lines > 0:
            guidance_lines.append(
                "- 下轮产物门禁必须满足: "
                f"new_or_modified_code_files >= {max(min_new_code_files, 0)}, "
                f"new_code_line_count >= {max(min_new_code_lines, 0)}。"
            )
            guidance_lines.append(
                "- 上轮产物统计: "
                f"new_or_modified_code_files={new_code_file_count}, "
                f"new_code_line_count={new_code_line_count}。"
            )
        if ascii_keywords:
            guidance_lines.append(
                "- 下轮至少新增一个核心代码文件路径或模块名包含关键词: " + ", ".join(ascii_keywords[:3])
            )
        guidance_lines.extend(
            [
                "- 必须直接修改已有项目代码，补齐真实业务逻辑与测试，不得再次提交模板化占位实现。",
                "- 禁止保留 TODO/FIXME/NotImplemented/stub 或空壳主流程。",
                "- 输出前请自检：代码命名与实现需体现当前项目语义，不可复用通用脚手架。",
            ]
        )
        return "\n".join(guidance_lines)

    async def _analyze_failure(self, result: RoundResult):
        """分析失败原因"""
        # 获取追踪数据中的失败分析
        if result.trace and not result.failure_evidence:
            failures = result.trace.get_failure_analysis()
            if failures:
                result.failure_evidence = json.dumps(failures[0], indent=2, ensure_ascii=False)

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        inferred_quality_failure = self._infer_workspace_quality_failure(workspace_artifacts)
        normalized_failure_point = normalize_status(result.failure_point)
        if inferred_quality_failure and normalized_failure_point in {"", "quality_gate", "qa"}:
            result.failure_point = inferred_quality_failure.get("failure_point")
            if not normalize_status(result.root_cause):
                result.root_cause = inferred_quality_failure.get("root_cause")
            if not normalize_status(result.failure_evidence):
                result.failure_evidence = inferred_quality_failure.get("failure_evidence")

        # 根据失败点推断根因
        failure_analysis_map = {
            "architect": "架构设计阶段 LLM 输出格式不符合预期",
            "docs_generation": "Architecture document generation failed; possible Dialogue/Plan or docs write chain exception",
            "pm": "PM 任务分解失败或输出格式错误",
            "pm_planning": "PM 规划阶段失败，可能是 PM run 生命周期、任务合同或运行状态同步异常",
            "chief_engineer": "技术分析阶段未能生成有效施工蓝图",
            "chief_engineer_review": "Chief Engineer阶段失败，技术蓝图审查未通过或证据缺失",
            "director": "代码执行阶段失败，可能是补丁应用错误或运行时异常",
            "director_dispatch": "Director 调度阶段失败，可能是任务血缘、执行权限或工具调用异常",
            "qa": "QA 审查发现严重质量问题",
            "quality_gate": "质量门禁阶段失败，可能是 integration QA 或验收门禁未通过",
            "chain_stage_sequence_invalid": "主链阶段顺序异常，未满足Architect->PM->Chief Engineer->Director->QA 的固定执行顺序",
            "chain_stage_artifacts_missing": "主链阶段声称完成但未产出可审计产物，链路证据缺失",
            "pm_contract_incomplete": "PM 任务合同缺少目标/作用域/执行步骤/可测验收，无法指导有效执行",
            "project_output_placeholder_code": "项目产物包含 TODO/FIXME/stub 等占位实现，未形成可交付业务逻辑",
            "project_output_generic_scaffold": "项目产物命中通用脚手架特征，未体现项目特定实现",
            "project_output_not_project_specific": "项目产物缺少领域关键词命中，需求落地与项目语义绑定不足",
            "project_output_cross_project_duplication": "项目产物与其他项目代码高度重复，存在模板化复用风险",
            "project_output_missing": "项目目录没有有效代码产物，生成链路未落地产出",
            "project_output_stagnant": "本次 attempt 未产出新增或修改代码文件，执行链路停滞",
            "project_output_too_sparse": "项目产物文件数量不足，未达到质量门禁要求",
            "project_output_too_small": "项目新增代码行数不足，未达到质量门禁要求",
            "project_output_fallback_scaffold": "命中回退脚手架，说明未产出真实业务代码",
            "llm_failure": "LLM 调用失败，可能是模型不可用或超时",
            "runtime_error": "运行时异常，可能是系统资源不足或配置错误",
        }

        if result.failure_point in failure_analysis_map:
            result.root_cause = failure_analysis_map[result.failure_point]

    async def _generate_diagnostic_reports(self):
        """为失败的轮次生成详细诊断报告"""
        diagnostics_dir = self._ensure_output_dir() / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)

        for result in self.results:
            if result.overall_result == "FAIL" and result.diagnostic_report:
                # 保存诊断报告
                diag_path = diagnostics_dir / f"round_{result.round_number}_diagnostic.json"
                diag_data = {
                    "round_number": result.round_number,
                    "project_name": result.project.name,
                    "project_id": result.project.id,
                    "factory_run_id": result.factory_run_id,
                    "failure_category": result.diagnostic_report.failure_category.value,
                    "failure_point": result.diagnostic_report.failure_point,
                    "summary": result.diagnostic_report.summary,
                    "root_cause_analysis": result.diagnostic_report.root_cause_analysis,
                    "suggested_fixes": result.diagnostic_report.suggested_fixes,
                    "evidence": result.diagnostic_report.evidence,
                    "related_logs": result.diagnostic_report.related_logs,
                    "raw_api_responses": result.diagnostic_report.raw_api_responses,
                }
                self._write_json_atomic(diag_path, diag_data)
                print(f"诊断报告 (Round #{result.round_number}): {diag_path}")

            # 保存可观测性数据
            if result.observability_data:
                obs_path = diagnostics_dir / f"round_{result.round_number}_observability.json"
                self._write_json_atomic(obs_path, result.observability_data)

    @staticmethod
    def _is_unauthorized_signal(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(
            marker in lowered
            for marker in (
                "unauthorized",
                "not allowed",
                "permission denied",
                "blocked by policy",
                "forbidden",
                "越权",
                "未授权",
            )
        )

    @staticmethod
    def _is_dangerous_command_text(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(
            re.search(pattern, lowered) is not None
            for pattern in (
                r"\brm\s+-rf\b",
                r"\bgit\s+reset\s+--hard\b",
                r"\bdel\s+/[a-z]*\s+/f\b",
                r"\bformat\s+[a-z]:\b",
                r"\bshutdown\b",
                r"\breboot\b",
            )
        )

    def _build_pm_quality_history(self) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for result in self.results:
            stage = result.pm_stage
            issues: list[str] = []
            stage_result = stage.result.value if stage else "missing"
            if stage is None:
                issues.append("pm_stage_not_observed")
            elif stage.result != StageResult.SUCCESS:
                issues.append(f"pm_stage_{stage.result.value}")
            if result.failure_point == "prompt_leakage":
                issues.append("prompt_leakage_detected")
            history.append(
                {
                    "round": result.round_number,
                    "score": None,
                    "issues": issues,
                    "stage_result": stage_result,
                    "passed": bool(stage and stage.result == StageResult.SUCCESS),
                    "source": "public_api_only",
                }
            )
        return history

    def _build_director_tool_audit(self) -> dict[str, Any]:
        total_calls = 0
        unauthorized_blocked = 0
        dangerous_commands = 0
        findings: list[dict[str, Any]] = []
        seen_findings: set[str] = set()

        for result in self.results:
            observability = result.observability_data if isinstance(result.observability_data, dict) else {}
            tool_rows = (
                observability.get("tool_executions") if isinstance(observability.get("tool_executions"), list) else []
            )
            error_rows = (
                observability.get("error_events") if isinstance(observability.get("error_events"), list) else []
            )
            total_calls += len(tool_rows)

            for tool_row in tool_rows:
                serialized = json.dumps(tool_row, ensure_ascii=False)
                if self._is_unauthorized_signal(serialized):
                    unauthorized_blocked += 1
                    key = f"unauthorized:{result.round_number}:{serialized[:120]}"
                    if key not in seen_findings:
                        seen_findings.add(key)
                        findings.append(
                            {
                                "round": result.round_number,
                                "type": "unauthorized_blocked",
                                "evidence": serialized[:500],
                            }
                        )
                if self._is_dangerous_command_text(serialized):
                    dangerous_commands += 1
                    key = f"dangerous:{result.round_number}:{serialized[:120]}"
                    if key not in seen_findings:
                        seen_findings.add(key)
                        findings.append(
                            {
                                "round": result.round_number,
                                "type": "dangerous_command",
                                "evidence": serialized[:500],
                            }
                        )

            for error_row in error_rows:
                serialized = json.dumps(error_row, ensure_ascii=False)
                if self._is_unauthorized_signal(serialized):
                    key = f"error-unauthorized:{result.round_number}:{serialized[:120]}"
                    if key not in seen_findings:
                        seen_findings.add(key)
                        findings.append(
                            {
                                "round": result.round_number,
                                "type": "runtime_unauthorized_signal",
                                "evidence": serialized[:500],
                            }
                        )

        return {
            "total_calls": total_calls,
            "unauthorized_blocked": unauthorized_blocked,
            "dangerous_commands": dangerous_commands,
            "findings": findings,
        }

    def _build_project_results(self) -> list[dict[str, Any]]:
        """Build project-level results for audit package consumers."""
        project_results: list[dict[str, Any]] = []
        for result in self.results:
            stage_status = {
                "architect": result.architect_stage.result.value if result.architect_stage else "missing",
                "pm": result.pm_stage.result.value if result.pm_stage else "missing",
                "chief_engineer": result.chief_engineer_stage.result.value
                if result.chief_engineer_stage
                else "missing",
                "director": result.director_stage.result.value if result.director_stage else "missing",
                "qa": result.qa_stage.result.value if result.qa_stage else "missing",
            }
            observability = result.observability_data if isinstance(result.observability_data, dict) else {}
            stats = observability.get("statistics") if isinstance(observability.get("statistics"), dict) else {}
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            project_results.append(
                {
                    "round": int(result.round_number),
                    "project_id": str(result.project.id),
                    "project_name": str(result.project.name),
                    "category": str(result.project.category.value),
                    "complexity": int(result.project.complexity_level),
                    "overall_result": str(result.overall_result),
                    "entry_stage": str(result.entry_stage or ""),
                    "factory_run_id": str(result.factory_run_id or ""),
                    "workspace": str(workspace_artifacts.get("workspace") or ""),
                    "duration_ms": self._calculate_duration(result),
                    "stages": stage_status,
                    "workspace_artifacts": workspace_artifacts,
                    "observability_statistics": stats,
                    "failure_point": str(result.failure_point or ""),
                    "root_cause": str(result.root_cause or ""),
                    "evidence_excerpt": str(result.failure_evidence or "")[:300],
                }
            )
        return project_results

    def _iter_workspace_factory_run_jsons(self) -> list[Path]:
        """Collect all discoverable factory run.json files under stress workspace."""
        candidates: list[Path] = []
        seen: set[str] = set()
        for result in self.results:
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            workspace_token = str(workspace_artifacts.get("workspace") or "").strip()
            if not workspace_token:
                continue
            workspace_path = Path(workspace_token)
            if not workspace_path.exists():
                continue
            for run_json in workspace_path.glob(".polaris/factory/*/run.json"):
                key = str(run_json.resolve())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(run_json)

        projects_root = self.workspace / "projects"
        if projects_root.exists():
            for run_json in projects_root.glob("**/.polaris/factory/*/run.json"):
                key = str(run_json.resolve())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(run_json)
        return sorted(candidates, key=lambda p: str(p))

    def _collect_runtime_forensics(self) -> dict[str, Any]:
        """Collect forensic status of factory runs for partial/aborted diagnostics."""
        factory_runs: list[dict[str, Any]] = []
        in_progress_runs: list[dict[str, Any]] = []
        result_run_ids = {
            str(result.factory_run_id or "").strip()
            for result in self.results
            if str(result.factory_run_id or "").strip()
        }

        for run_json in self._iter_workspace_factory_run_jsons():
            payload = self._safe_read_json_dict(run_json)
            run_id = str(payload.get("id") or run_json.parent.name or "").strip()
            status = str(payload.get("status") or "unknown").strip().lower()
            completed_at = str(payload.get("completed_at") or "").strip()
            updated_at = str(payload.get("updated_at") or "").strip()
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

            workspace_path = run_json
            try:
                workspace_path = run_json.parents[3]
            except IndexError:
                workspace_path = run_json.parent

            checkpoints_dir = run_json.parent / "checkpoints"
            checkpoints_count = 0
            if checkpoints_dir.exists():
                checkpoints_count = sum(1 for _ in checkpoints_dir.glob("*.json"))

            events_path = run_json.parent / "events" / "events.jsonl"
            entry = {
                "run_id": run_id,
                "workspace": str(workspace_path),
                "source": "round_result" if run_id in result_run_ids else "workspace_scan",
                "status": status,
                "current_stage": str(metadata.get("current_stage") or ""),
                "last_successful_stage": str(metadata.get("last_successful_stage") or ""),
                "stages_completed": payload.get("stages_completed")
                if isinstance(payload.get("stages_completed"), list)
                else [],
                "updated_at": updated_at,
                "completed_at": completed_at,
                "run_json": str(run_json),
                "events_log": str(events_path),
                "events_log_exists": events_path.exists(),
                "checkpoints_count": int(checkpoints_count),
            }
            factory_runs.append(entry)
            if status in {"running", "pending"} and not completed_at:
                in_progress_runs.append(entry)

        return {
            "factory_runs": factory_runs,
            "in_progress_runs": in_progress_runs,
            "summary": {
                "total_factory_runs": len(factory_runs),
                "in_progress_runs": len(in_progress_runs),
                "completed_runs": sum(1 for item in factory_runs if item.get("status") == "completed"),
                "failed_runs": sum(1 for item in factory_runs if item.get("status") == "failed"),
            },
        }

    def _resolve_stage_artifact_candidates(self, workspace_path: Path, artifact: str) -> list[Path]:
        token = str(artifact or "").strip()
        if not token:
            return []
        raw_path = Path(token)
        candidates: list[Path] = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append(workspace_path / raw_path)
            candidates.append(workspace_path / ".polaris" / raw_path)

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _collect_artifact_integrity(self, *, run_state: str) -> dict[str, Any]:
        """Validate audit bundle artifacts and stage-declared artifact existence."""
        output_dir = self._ensure_output_dir()
        core_artifacts = [
            {
                "name": "stress_audit_package",
                "path": output_dir / "stress_audit_package.json",
                "required": True,
            },
            {
                "name": "stress_audit_timeline",
                "path": output_dir / "stress_audit_timeline.jsonl",
                "required": True,
            },
            {
                "name": "stress_results",
                "path": output_dir / "stress_results.json",
                "required": bool(self.results),
            },
            {
                "name": "backend_preflight",
                "path": output_dir / "backend_preflight.json",
                "required": self.backend_preflight_report is not None,
            },
            {
                "name": "probe_report",
                "path": output_dir / "probe_report.json",
                "required": self.probe_report is not None,
            },
            {
                "name": "stress_report_markdown",
                "path": output_dir / "stress_report.md",
                "required": run_state in {"completed", "aborted"},
            },
            {
                "name": "summary",
                "path": output_dir / "summary.txt",
                "required": run_state in {"completed", "aborted"},
            },
        ]

        core_inventory: list[dict[str, Any]] = []
        for item in core_artifacts:
            path = Path(item["path"])
            core_inventory.append(
                {
                    "name": item["name"],
                    "path": str(path),
                    "required": bool(item["required"]),
                    "exists": path.exists(),
                }
            )

        missing_required_core = [item for item in core_inventory if item.get("required") and not item.get("exists")]

        missing_stage_artifacts: list[dict[str, Any]] = []
        checked_stage_artifacts = 0
        for result in self.results:
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            workspace_token = str(workspace_artifacts.get("workspace") or "").strip()
            if not workspace_token:
                continue
            workspace_path = Path(workspace_token)
            stages = {
                "architect": result.architect_stage,
                "pm": result.pm_stage,
                "chief_engineer": result.chief_engineer_stage,
                "director": result.director_stage,
                "qa": result.qa_stage,
            }
            for stage_name, stage in stages.items():
                if stage is None:
                    continue
                for artifact in stage.artifacts:
                    token = str(artifact or "").strip()
                    if not token:
                        continue
                    checked_stage_artifacts += 1
                    candidates = self._resolve_stage_artifact_candidates(workspace_path, token)
                    resolved = next((path for path in candidates if path.exists()), None)
                    if resolved is not None:
                        continue
                    missing_stage_artifacts.append(
                        {
                            "round": int(result.round_number),
                            "project_id": str(result.project.id),
                            "stage": stage_name,
                            "artifact": token,
                            "candidate_paths": [str(path) for path in candidates],
                        }
                    )

        return {
            "core_artifacts": core_inventory,
            "missing_required_core_artifacts": missing_required_core,
            "stage_artifacts": {
                "checked": int(checked_stage_artifacts),
                "missing": len(missing_stage_artifacts),
                "missing_items": missing_stage_artifacts,
            },
        }

    def _build_audit_package_health(
        self,
        *,
        run_state: str,
        artifact_integrity: dict[str, Any],
        runtime_forensics: dict[str, Any],
        project_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build package health metrics for quick triage."""
        stage_artifacts = (
            artifact_integrity.get("stage_artifacts")
            if isinstance(artifact_integrity.get("stage_artifacts"), dict)
            else {}
        )
        checks = {
            "has_start_time": bool(self.start_time),
            "has_preflight_report": bool(self.backend_preflight_report),
            "has_probe_report": bool(self.probe_report),
            "project_results_present": bool(project_results) or not bool(self.results),
            "core_artifacts_complete": not bool(artifact_integrity.get("missing_required_core_artifacts")),
            "no_stage_artifact_missing": int(stage_artifacts.get("missing") or 0) == 0,
            "no_in_progress_factory_runs_after_completion": (
                run_state != "completed" or not bool(runtime_forensics.get("in_progress_runs"))
            ),
        }
        passed = sum(1 for value in checks.values() if bool(value))
        total = len(checks)
        score = round((passed / total) * 100) if total > 0 else 0
        issues = [name for name, ok in checks.items() if not ok]
        return {
            "run_state": run_state,
            "checks": checks,
            "score": score,
            "issues": issues,
        }

    def _collect_evidence_paths(self) -> dict[str, list[str]]:
        self._ensure_output_dir()
        diagnostics_dir = self.output_dir / "diagnostics"
        logs: list[str] = []
        snapshots: list[str] = []
        results_path = self._ensure_output_dir() / "stress_results.json"
        if results_path.exists():
            logs.append(str(results_path))
        if diagnostics_dir.exists():
            for item in sorted(diagnostics_dir.iterdir()):
                if not item.is_file():
                    continue
                if item.name.endswith("_observability.json"):
                    snapshots.append(str(item))
                else:
                    logs.append(str(item))
        probe_path = self.output_dir / "probe_report.json"
        if probe_path.exists():
            logs.append(str(probe_path))
        preflight_path = self.output_dir / "backend_preflight.json"
        if preflight_path.exists():
            logs.append(str(preflight_path))
        timeline_path = self.output_dir / "stress_audit_timeline.jsonl"
        if timeline_path.exists():
            logs.append(str(timeline_path))
        forensics = self._collect_runtime_forensics()
        for run_item in forensics.get("factory_runs", []):
            if not isinstance(run_item, dict):
                continue
            run_json = str(run_item.get("run_json") or "").strip()
            if run_json:
                logs.append(run_json)
            events_log = str(run_item.get("events_log") or "").strip()
            if events_log:
                logs.append(events_log)

        for result in self.results:
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            workspace_token = str(workspace_artifacts.get("workspace") or "").strip()
            if not workspace_token:
                continue
            workspace_path = Path(workspace_token)
            roles_root = Path(resolve_runtime_path(str(workspace_path), "runtime/roles"))
            if roles_root.exists():
                for log_path in roles_root.glob("**/logs/*.jsonl"):
                    logs.append(str(log_path))
            run_id = str(result.factory_run_id or "").strip()
            if run_id:
                checkpoints_dir = workspace_path / ".polaris" / "factory" / run_id / "checkpoints"
                if checkpoints_dir.exists():
                    for checkpoint in sorted(checkpoints_dir.glob("*.json")):
                        snapshots.append(str(checkpoint))

        logs = sorted(dict.fromkeys(logs))
        snapshots = sorted(dict.fromkeys(snapshots))
        return {
            "screenshots": [],
            "logs": logs,
            "snapshots": snapshots,
        }
