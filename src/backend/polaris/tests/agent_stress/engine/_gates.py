"""gates methods for StressEngine (mixin)."""

# mypy: ignore-errors

import asyncio
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import (
    is_generic_failure_point,
    normalize_status,
)
from ..project_pool import ProjectDefinition
from ._constants import (
    DOMAIN_KEYWORD_STOPWORDS,
    FALLBACK_SCAFFOLD_SIGNATURES,
    GENERIC_SCAFFOLD_MARKERS,
    IGNORED_WORKSPACE_ROOTS,
    MIN_CROSS_PROJECT_DUPLICATE_FILES,
    MIN_CROSS_PROJECT_DUPLICATE_RATIO,
    MIN_GENERIC_SCAFFOLD_MARKERS,
    PLACEHOLDER_CODE_SIGNATURES,
    PROJECT_CODE_EXTENSIONS,
)
from ._models import CodeFileSnapshot, RoundResult, StageExecution, StageResult


class _StressEngineGatesMixin:
    def _collect_workspace_code_files(self, root: Path | None = None) -> dict[str, CodeFileSnapshot]:
        root = Path(root or self.workspace)
        if not root.exists() or not root.is_dir():
            return {}
        code_files: dict[str, CodeFileSnapshot] = {}
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root)
                if not rel.parts:
                    continue
                if rel.parts[0] in IGNORED_WORKSPACE_ROOTS:
                    continue
                if any(part in IGNORED_WORKSPACE_ROOTS for part in rel.parts):
                    continue
                if path.suffix.lower() in PROJECT_CODE_EXTENSIONS:
                    try:
                        content = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError, PermissionError):
                        # 文件读取失败（权限、编码、IO错误）跳过该文件
                        continue
                    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    line_count = len(content.splitlines())
                    code_files[rel.as_posix()] = CodeFileSnapshot(
                        digest=digest,
                        line_count=line_count,
                    )
        except (OSError, PermissionError) as e:
            # 文件系统错误：记录日志后返回空字典
            print(f"[engine] Failed to collect workspace files: {type(e).__name__}: {e}")
            return {}
        return code_files

    @staticmethod
    def _build_project_domain_keywords(project: ProjectDefinition) -> list[str]:
        keywords: set[str] = set()
        sources = [
            project.id.replace("-", " "),
            project.name,
            project.description,
            *project.stress_focus,
        ]
        for source in sources:
            lowered = str(source or "").strip().lower()
            if not lowered:
                continue
            for token in re.findall(r"[a-zA-Z]{3,}", lowered):
                normalized = token.lower()
                if normalized in DOMAIN_KEYWORD_STOPWORDS:
                    continue
                keywords.add(normalized)
            for token in re.findall(r"[\u4e00-\u9fff]{3,}", lowered):
                normalized = token.lower()
                if normalized in DOMAIN_KEYWORD_STOPWORDS:
                    continue
                keywords.add(normalized)
        return sorted(keywords)

    def _detect_cross_project_duplicate_files(
        self,
        *,
        effective_files: list[str],
        current_snapshot: dict[str, CodeFileSnapshot],
    ) -> list[dict[str, Any]]:
        if self.workspace_mode != "per_project":
            return []
        if len(effective_files) < MIN_CROSS_PROJECT_DUPLICATE_FILES:
            return []
        projects_root = self.root_workspace / "projects"
        if not projects_root.exists() or not projects_root.is_dir():
            return []

        current_workspace = self.workspace.resolve()
        findings: list[dict[str, Any]] = []
        min_duplicate_files = min(MIN_CROSS_PROJECT_DUPLICATE_FILES, len(effective_files))

        for sibling in projects_root.iterdir():
            if not sibling.is_dir():
                continue
            sibling_resolved = sibling.resolve()
            if sibling_resolved == current_workspace:
                continue
            sibling_snapshot = self._collect_workspace_code_files(root=sibling_resolved)
            if not sibling_snapshot:
                continue
            matched_files = [
                rel_path
                for rel_path in effective_files
                if rel_path in current_snapshot
                and rel_path in sibling_snapshot
                and current_snapshot[rel_path].digest == sibling_snapshot[rel_path].digest
            ]
            if len(matched_files) < min_duplicate_files:
                continue
            match_ratio = len(matched_files) / len(effective_files)
            if match_ratio < MIN_CROSS_PROJECT_DUPLICATE_RATIO:
                continue
            findings.append(
                {
                    "project": sibling_resolved.name,
                    "matched_file_count": len(matched_files),
                    "match_ratio": round(match_ratio, 3),
                    "matched_files": matched_files[:20],
                }
            )

        findings.sort(
            key=lambda item: (
                float(item.get("match_ratio") or 0.0),
                int(item.get("matched_file_count") or 0),
            ),
            reverse=True,
        )
        return findings

    def _enforce_project_output_gate(
        self,
        result: RoundResult,
        baseline_snapshot: dict[str, CodeFileSnapshot],
    ) -> None:
        baseline_snapshot = baseline_snapshot if isinstance(baseline_snapshot, dict) else {}
        current_snapshot = self._collect_workspace_code_files()
        current_files = set(current_snapshot.keys())
        baseline_files = set(baseline_snapshot.keys())
        new_files = sorted(current_files - baseline_files)
        modified_files = sorted(
            path
            for path in current_files
            if path in baseline_snapshot and current_snapshot[path].digest != baseline_snapshot[path].digest
        )
        effective_files = sorted(set(new_files + modified_files))
        (
            new_code_line_count,
            fallback_files,
            placeholder_markers,
            generic_scaffold_markers,
            domain_keywords,
            domain_keyword_hits,
        ) = self._inspect_new_code_files(
            effective_files,
            project=result.project,
        )
        cross_project_duplicates = self._detect_cross_project_duplicate_files(
            effective_files=effective_files,
            current_snapshot=current_snapshot,
        )
        result.workspace_artifacts = {
            "workspace": str(self.workspace),
            "baseline_code_file_count": len(baseline_snapshot),
            "code_file_count": len(current_snapshot),
            "new_code_file_count": len(effective_files),
            "truly_new_code_file_count": len(new_files),
            "modified_code_file_count": len(modified_files),
            "new_code_line_count": new_code_line_count,
            "new_code_files_sample": effective_files[:30],
            "truly_new_code_files_sample": new_files[:30],
            "modified_code_files_sample": modified_files[:30],
            "fallback_scaffold_detected": bool(fallback_files),
            "fallback_scaffold_files": fallback_files[:30],
            "placeholder_markers": placeholder_markers[:30],
            "generic_scaffold_markers": generic_scaffold_markers[:30],
            "domain_keywords": domain_keywords[:30],
            "domain_keyword_hits": domain_keyword_hits[:30],
            "cross_project_duplicate_projects": cross_project_duplicates[:10],
            "quality_gate": {
                "min_new_code_files": self.min_new_code_files,
                "min_new_code_lines": self.min_new_code_lines,
                "min_generic_scaffold_markers": MIN_GENERIC_SCAFFOLD_MARKERS,
                "min_cross_project_duplicate_files": MIN_CROSS_PROJECT_DUPLICATE_FILES,
                "min_cross_project_duplicate_ratio": MIN_CROSS_PROJECT_DUPLICATE_RATIO,
            },
            "chain_policy": {
                "run_architect_stage": self.run_architect_stage,
                "run_chief_engineer_stage": self.run_chief_engineer_stage,
                "require_architect_stage": self.require_architect_stage,
                "require_chief_engineer_stage": self.require_chief_engineer_stage,
                "entry_stage": self._resolve_round_entry_stage(result),
            },
        }

        if result.overall_result not in {"PASS", "PARTIAL"}:
            return
        if len(current_snapshot) == 0:
            self._set_quality_failure(
                result,
                failure_point="project_output_missing",
                root_cause=("Factory lifecycle completed but workspace contains no generated project code files"),
                failure_evidence=(
                    f"workspace={self.workspace} baseline_code_files={len(baseline_snapshot)} current_code_files=0"
                ),
            )
            return
        if len(effective_files) == 0:
            self._set_quality_failure(
                result,
                failure_point="project_output_stagnant",
                root_cause=("Factory lifecycle completed but this round did not produce any new project code files"),
                failure_evidence=(
                    f"workspace={self.workspace} baseline_code_files={len(baseline_snapshot)} "
                    f"current_code_files={len(current_snapshot)} new_or_modified_code_files=0"
                ),
            )
            return
        if fallback_files:
            self._set_quality_failure(
                result,
                failure_point="project_output_fallback_scaffold",
                root_cause=(
                    "Director fallback scaffold was detected; this round did not produce authentic project code"
                ),
                failure_evidence=(f"workspace={self.workspace} fallback_scaffold_files={fallback_files[:10]}"),
            )
            return
        if cross_project_duplicates:
            duplicate_summary = cross_project_duplicates[0]
            self._set_quality_failure(
                result,
                failure_point="project_output_cross_project_duplication",
                root_cause=(
                    "Generated project code is substantially duplicated from another project workspace, "
                    "indicating template-style output instead of project-specific implementation"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} duplicate_project={duplicate_summary.get('project')} "
                    f"matched_file_count={duplicate_summary.get('matched_file_count')} "
                    f"match_ratio={duplicate_summary.get('match_ratio')}"
                ),
            )
            return
        if len(generic_scaffold_markers) >= MIN_GENERIC_SCAFFOLD_MARKERS:
            self._set_quality_failure(
                result,
                failure_point="project_output_generic_scaffold",
                root_cause=(
                    "Generated project output matches a known generic scaffold pattern and lacks "
                    "project-specific implementation depth"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} generic_scaffold_markers={generic_scaffold_markers[:10]}"
                ),
            )
            return
        if placeholder_markers:
            self._set_quality_failure(
                result,
                failure_point="project_output_placeholder_code",
                root_cause=(
                    "Generated project output contains placeholder markers (TODO/FIXME/stub) "
                    "instead of completed business logic"
                ),
                failure_evidence=(f"workspace={self.workspace} placeholder_markers={placeholder_markers[:10]}"),
            )
            return
        if domain_keywords and not domain_keyword_hits:
            self._set_quality_failure(
                result,
                failure_point="project_output_not_project_specific",
                root_cause=(
                    "Generated project output does not contain detectable domain keywords "
                    "for the requested project, indicating weak requirement grounding"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} expected_keywords={domain_keywords[:12]} matched_keywords=[]"
                ),
            )
            return
        if len(effective_files) < self.min_new_code_files:
            self._set_quality_failure(
                result,
                failure_point="project_output_too_sparse",
                root_cause=("Generated project output is too sparse and does not satisfy the stress quality baseline"),
                failure_evidence=(
                    f"workspace={self.workspace} new_or_modified_code_files={len(effective_files)} "
                    f"required_min_new_code_files={self.min_new_code_files}"
                ),
            )
            return
        if new_code_line_count < self.min_new_code_lines:
            self._set_quality_failure(
                result,
                failure_point="project_output_too_small",
                root_cause=("Generated project code size is below the minimum quality threshold"),
                failure_evidence=(
                    f"workspace={self.workspace} new_code_line_count={new_code_line_count} "
                    f"required_min_new_code_lines={self.min_new_code_lines}"
                ),
            )

    def _inspect_new_code_files(
        self,
        new_code_files: list[str],
        *,
        project: ProjectDefinition,
    ) -> tuple[int, list[str], list[str], list[str], list[str], list[str]]:
        line_count = 0
        fallback_hits: list[str] = []
        placeholder_markers: set[str] = set()
        generic_scaffold_markers: set[str] = set()
        domain_keywords = self._build_project_domain_keywords(project)
        domain_keyword_hits: set[str] = set()
        for rel_path in new_code_files:
            file_path = self.workspace / Path(rel_path)
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, PermissionError, UnicodeDecodeError):
                continue
            line_count += len(content.splitlines())
            if any(signature in content for signature in FALLBACK_SCAFFOLD_SIGNATURES):
                fallback_hits.append(rel_path)
            lowered_content = content.lower()
            searchable_text = f"{rel_path.lower()}\n{lowered_content}"
            for label, pattern in PLACEHOLDER_CODE_SIGNATURES:
                if pattern.search(content):
                    placeholder_markers.add(f"{rel_path}:{label}")
            for marker in GENERIC_SCAFFOLD_MARKERS:
                if marker.lower() in lowered_content:
                    generic_scaffold_markers.add(f"{rel_path}:{marker}")
            for keyword in domain_keywords:
                if keyword and keyword in searchable_text:
                    domain_keyword_hits.add(keyword)
        return (
            line_count,
            sorted(fallback_hits),
            sorted(placeholder_markers),
            sorted(generic_scaffold_markers),
            domain_keywords,
            sorted(domain_keyword_hits),
        )

    @staticmethod
    def _set_quality_failure(
        result: RoundResult,
        *,
        failure_point: str,
        root_cause: str,
        failure_evidence: str,
    ) -> None:
        result.overall_result = "FAIL"
        result.failure_point = failure_point
        result.root_cause = root_cause
        result.failure_evidence = failure_evidence

    def _normalize_optional_chain_stages(self, result: RoundResult) -> None:
        now = result.end_time or datetime.now().isoformat()
        entry_stage = self._resolve_round_entry_stage(result)
        if result.architect_stage is None and entry_stage != "architect":
            result.architect_stage = StageExecution(
                stage_name="architect",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error=f"architect stage skipped by retry policy (entry_stage={entry_stage})",
            )
        elif result.architect_stage is None and (not self.run_architect_stage or not self.require_architect_stage):
            reason = (
                "architect stage disabled by stress chain policy"
                if not self.run_architect_stage
                else "architect stage optional and no direct factory-stage evidence was observed"
            )
            result.architect_stage = StageExecution(
                stage_name="architect",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error=reason,
            )
        if result.pm_stage is None and entry_stage == "director":
            result.pm_stage = StageExecution(
                stage_name="pm",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error="pm stage skipped by retry policy (entry_stage=director)",
            )
        if result.chief_engineer_stage is None:
            if self.run_chief_engineer_stage:
                result.chief_engineer_stage = StageExecution(
                    stage_name="chief_engineer",
                    result=StageResult.SKIPPED,
                    start_time=result.start_time,
                    end_time=now,
                    duration_ms=0,
                    error=(
                        "chief_engineer stage requested but no direct factory-stage "
                        "evidence was observed from public API"
                    ),
                )
            else:
                result.chief_engineer_stage = StageExecution(
                    stage_name="chief_engineer",
                    result=StageResult.SKIPPED,
                    start_time=result.start_time,
                    end_time=now,
                    duration_ms=0,
                    error="chief_engineer stage disabled by stress chain policy",
                )

    def _enforce_chain_evidence_gate(self, result: RoundResult) -> None:
        """强化链路证据门禁 - 校验阶段顺序、产物、耗时"""
        if not self.require_full_chain_evidence:
            return
        existing_failure = normalize_status(result.failure_point)
        if result.overall_result == "FAIL" and existing_failure and not is_generic_failure_point(existing_failure):
            return
        if str(result.failure_point or "").strip() in {
            "engine",
            "factory_timeout",
            "factory_status_observation_blocked",
        }:
            return

        # === B2: court_strict 模式硬化 ===
        # 1. 顺序校验：实际顺序必须匹配 chain_profile 定义的顺序
        # 2. 阶段产物校验：每个阶段必须有 artifact 产出
        # 3. 阶段耗时校验：单阶段超时触发告警

        entry_stage = self._resolve_round_entry_stage(result)
        expected_roles = self._expected_chain_roles(entry_stage=entry_stage)
        architect_required = "architect" in expected_roles
        pm_required = "pm" in expected_roles
        chief_required = "chief_engineer" in expected_roles
        director_required = "director" in expected_roles
        qa_required = "qa" in expected_roles

        # === B2 强化：court_strict 模式下缺少 architect 阶段直接 FAIL ===
        if self.chain_profile == "court_strict" and architect_required:
            if result.architect_stage is None:
                self._set_quality_failure(
                    result,
                    failure_point="chain_stage_sequence_invalid",
                    root_cause=("court_strict mode requires architect stage but no evidence was observed"),
                    failure_evidence=(
                        f"chain_profile={self.chain_profile}; architect_required=True; "
                        f"architect_stage=None; run_id={result.factory_run_id}"
                    ),
                )
                return
            if result.architect_stage.result != StageResult.SUCCESS:
                self._set_quality_failure(
                    result,
                    failure_point="chain_stage_sequence_invalid",
                    root_cause=("court_strict mode requires architect stage to complete successfully"),
                    failure_evidence=(
                        f"chain_profile={self.chain_profile}; architect_result={result.architect_stage.result.value}; "
                        f"run_id={result.factory_run_id}"
                    ),
                )
                return

        stage_requirements = [
            ("pm", result.pm_stage, {StageResult.SUCCESS}, pm_required),
            ("director", result.director_stage, {StageResult.SUCCESS}, director_required),
            ("qa", result.qa_stage, {StageResult.SUCCESS, StageResult.PARTIAL}, qa_required),
            (
                "architect",
                result.architect_stage,
                ({StageResult.SUCCESS} if architect_required else {StageResult.SUCCESS, StageResult.SKIPPED}),
                architect_required,
            ),
            (
                "chief_engineer",
                result.chief_engineer_stage,
                ({StageResult.SUCCESS} if chief_required else {StageResult.SUCCESS, StageResult.SKIPPED}),
                chief_required,
            ),
        ]
        missing_stage_evidence: list[str] = []
        for stage_name, stage, accepted_results, is_required in stage_requirements:
            if stage is None:
                if is_required:
                    missing_stage_evidence.append(f"{stage_name}=missing")
                continue
            if stage.result not in accepted_results:
                if is_required or stage.result != StageResult.SKIPPED:
                    missing_stage_evidence.append(f"{stage_name}={stage.result.value}")
                continue
            if stage.result == StageResult.SKIPPED:
                continue

            # === B2 强化：阶段耗时校验（单阶段超时告警）===
            # 单阶段超过 600s (10分钟) 触发告警
            stage_timeout_ms = 600000  # 10 minutes
            if stage.duration_ms > stage_timeout_ms:
                missing_stage_evidence.append(f"{stage_name}=timeout_exceeded({stage.duration_ms}ms)")

            if stage.duration_ms <= 0:
                missing_stage_evidence.append(f"{stage_name}=zero_duration")

        if missing_stage_evidence:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_evidence_missing",
                root_cause=(
                    "Factory lifecycle reported success but configured chain evidence is incomplete or inconsistent"
                ),
                failure_evidence="; ".join(missing_stage_evidence),
            )
            return

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_stage_evidence = (
            workspace_artifacts.get("chain_stage_evidence")
            if isinstance(workspace_artifacts.get("chain_stage_evidence"), dict)
            else {}
        )
        expected_order = (
            chain_stage_evidence.get("expected_role_order")
            if isinstance(chain_stage_evidence.get("expected_role_order"), list)
            else expected_roles
        )
        observed_order = (
            chain_stage_evidence.get("observed_role_order")
            if isinstance(chain_stage_evidence.get("observed_role_order"), list)
            else []
        )
        stages = chain_stage_evidence.get("stages") if isinstance(chain_stage_evidence.get("stages"), dict) else {}

        if not observed_order:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_sequence_invalid",
                root_cause=(
                    "Factory run lacks observable stage transition evidence, "
                    "cannot verify required chain order architect->pm->director->qa"
                ),
                failure_evidence=(
                    f"expected_order={expected_order}; observed_order=[]; run_id={result.factory_run_id}"
                ),
            )
            return

        if observed_order != expected_order:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_sequence_invalid",
                root_cause=("Observed execution stages do not match required main-chain order"),
                failure_evidence=(
                    f"expected_order={expected_order}; observed_order={observed_order}; run_id={result.factory_run_id}"
                ),
            )
            return

        path_fallback_count = int(workspace_artifacts.get("path_fallback_count") or 0)
        if path_fallback_count > 0:
            self._set_quality_failure(
                result,
                failure_point="path_contract_violation",
                root_cause=(
                    "Artifact resolution used fallback path candidates; "
                    "path contract requires logical path first-hit without fallback"
                ),
                failure_evidence=f"path_fallback_count={path_fallback_count}",
            )
            return

        # === B2 强化：阶段产物校验 ===
        artifact_issues: list[str] = []
        for role in expected_order:
            stage_payload = stages.get(role) if isinstance(stages.get(role), dict) else {}
            declared = (
                stage_payload.get("declared_artifacts")
                if isinstance(stage_payload.get("declared_artifacts"), list)
                else []
            )
            existing = (
                stage_payload.get("existing_artifacts")
                if isinstance(stage_payload.get("existing_artifacts"), list)
                else []
            )
            missing = (
                stage_payload.get("missing_artifacts")
                if isinstance(stage_payload.get("missing_artifacts"), list)
                else []
            )
            if not declared:
                artifact_issues.append(f"{role}=declared_artifacts_missing")
                continue
            if not existing:
                artifact_issues.append(f"{role}=existing_artifacts_missing")
            if missing:
                artifact_issues.append(f"{role}=missing_artifacts:{','.join(missing[:3])}")
        if artifact_issues:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_artifacts_missing",
                root_cause=(
                    "Factory stage completion claimed artifacts, but required chain artifacts were not materialized"
                ),
                failure_evidence="; ".join(artifact_issues),
            )
            return

        if pm_required:
            pm_contract_issue = self._validate_pm_task_contract(result, chain_stage_evidence)
            if pm_contract_issue:
                self._set_quality_failure(
                    result,
                    failure_point="pm_contract_incomplete",
                    root_cause=(
                        "PM stage artifacts do not provide executable task contracts "
                        "with goal/scope/steps/acceptance fields"
                    ),
                    failure_evidence=pm_contract_issue,
                )
                return

        trace_stats = result.trace.to_dict().get("statistics", {}) if result.trace else {}
        total_tasks = int(trace_stats.get("total_tasks") or 0)
        new_code_file_count = int(workspace_artifacts.get("new_code_file_count") or 0)
        new_code_line_count = int(workspace_artifacts.get("new_code_line_count") or 0)
        if total_tasks <= 0:
            backfilled_trace = self._backfill_trace_from_dispatch_artifact(result, chain_stage_evidence)
            if backfilled_trace:
                trace_stats = result.trace.to_dict().get("statistics", {}) if result.trace else {}
                total_tasks = int(trace_stats.get("total_tasks") or 0)
        if total_tasks <= 0:
            self._set_quality_failure(
                result,
                failure_point="chain_trace_missing_tasks",
                root_cause=("Round has no traced task lineage; cannot prove PM->Chief Engineer->Director task handoff"),
                failure_evidence=(
                    f"trace.statistics.total_tasks={total_tasks}; "
                    f"workspace.new_code_file_count={new_code_file_count}; "
                    f"workspace.new_code_line_count={new_code_line_count}"
                ),
            )
            return

        obs_stats: dict[str, Any] = {}
        if isinstance(result.observability_data, dict):
            obs_raw = result.observability_data.get("statistics")
            obs_stats = obs_raw if isinstance(obs_raw, dict) else {}
        total_tool_executions = int(obs_stats.get("total_tool_executions") or 0)
        if total_tool_executions <= 0:
            backfilled_tools = self._backfill_observability_from_director_logs(result)
            if backfilled_tools > 0 and isinstance(result.observability_data, dict):
                refreshed = result.observability_data.get("statistics")
                obs_stats = refreshed if isinstance(refreshed, dict) else {}
                total_tool_executions = int(obs_stats.get("total_tool_executions") or backfilled_tools)
        if total_tool_executions <= 0:
            self._set_quality_failure(
                result,
                failure_point="chain_observability_missing_tools",
                root_cause=(
                    "Round has no observable Director tool execution evidence; chain success cannot be trusted"
                ),
                failure_evidence=(
                    f"observability.statistics.total_tool_executions={total_tool_executions}; "
                    f"workspace.new_code_file_count={new_code_file_count}; "
                    f"workspace.new_code_line_count={new_code_line_count}"
                ),
            )

    async def _capture_snapshot_with_budget(self, label: str) -> None:
        """在预算内捕获可观测性快照，避免拖死整轮压测。"""
        if not self.collector:
            return
        timeout_budget = self.observability_snapshot_timeout + 1.0
        try:
            await asyncio.wait_for(
                self.collector.capture_full_snapshot(),
                timeout=timeout_budget,
            )
        except asyncio.TimeoutError:
            print(
                f"[observability] {label} snapshot timed out after {timeout_budget:.1f}s; continuing with partial data"
            )
        except (OSError, RuntimeError, ValueError) as e:
            print(f"[observability] {label} snapshot failed: {type(e).__name__}: {e}")
