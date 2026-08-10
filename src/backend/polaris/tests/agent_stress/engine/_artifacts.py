"""artifacts methods for StressEngine (mixin)."""

# mypy: ignore-errors

import json
from pathlib import Path
from typing import Any

import httpx
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots

from ..contracts import (
    normalize_status,
)
from ..tracer import RoundTrace, TaskLineage
from ._constants import (
    STAGE_NAME_TO_CHAIN_ROLE,
)
from ._models import RoundResult


class _StressEngineArtifactsMixin:
    @staticmethod
    def _normalize_entry_stage(entry_stage: str | None) -> str:
        token = str(entry_stage or "").strip().lower()
        if token in {"architect", "pm", "director"}:
            return token
        return "architect"

    def _resolve_round_entry_stage(self, result: RoundResult) -> str:
        stage_token = self._normalize_entry_stage(getattr(result, "entry_stage", "architect"))
        if stage_token != "architect":
            return stage_token
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_policy = (
            workspace_artifacts.get("chain_policy") if isinstance(workspace_artifacts.get("chain_policy"), dict) else {}
        )
        candidate = str(chain_policy.get("entry_stage") or workspace_artifacts.get("entry_stage") or "").strip()
        normalized = self._normalize_entry_stage(candidate)
        return normalized or "architect"

    def _expected_chain_roles(self, *, entry_stage: str | None = None) -> list[str]:
        normalized_entry = self._normalize_entry_stage(entry_stage)

        base_roles: list[str] = []
        if self.run_architect_stage or self.require_architect_stage:
            base_roles.append("architect")
        base_roles.extend(["pm", "director", "qa"])

        if normalized_entry not in base_roles:
            return base_roles
        start_index = base_roles.index(normalized_entry)
        return base_roles[start_index:]

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
        return ordered

    @staticmethod
    def _dedupe_resolved_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            artifact = str(item.get("artifact") or "").strip()
            resolved_by = str(item.get("resolved_by") or "").strip()
            resolved_path = str(item.get("resolved_path") or "").strip()
            if not artifact or not resolved_path:
                continue
            key = f"{artifact}|{resolved_by}|{resolved_path}"
            if key in seen:
                continue
            seen.add(key)
            ordered.append(
                {
                    "artifact": artifact,
                    "resolved_by": resolved_by,
                    "resolved_path": resolved_path,
                }
            )
        return ordered

    def _is_path_in_trusted_root(self, path: Path, run_id: str) -> bool:
        """绝对路径受信根目录校验

        受信根：
        - {workspace}/.polaris
        - {workspace}/.polaris/factory/{run_id}
        - runtime project root 及其 runtime 根目录
        """
        try:
            resolved = path.resolve()
            workspace_resolved = self.workspace.resolve()
            roots = resolve_storage_roots(
                str(workspace_resolved),
                ramdisk_root=str(self.ramdisk_root),
            )
            runtime_root = Path(roots.runtime_project_root).resolve()
            runtime_project_root = runtime_root.parent.resolve()

            # 受信任路径前缀
            trusted_roots = [
                workspace_resolved / ".polaris",
                workspace_resolved / ".polaris" / "factory" / run_id,
                runtime_project_root,
                runtime_root,
            ]

            for trusted in trusted_roots:
                try:
                    resolved.relative_to(trusted)
                    return True
                except ValueError:
                    continue
            return False
        except (OSError, TypeError):
            # 路径解析失败（无效路径或系统错误）
            return False

    def _resolve_stage_artifact_path(self, run_id: str, relative_path: str) -> dict[str, Any] | None:
        """解析阶段 artifact 路径

        返回包含元数据的字典：
        {
            "path": Path,
            "resolved_by": "logical_path" | ".polaris_factory" | ".polaris_artifacts",
            "resolved_path": str
        }
        """
        rel = str(relative_path or "").strip().replace("\\", "/")
        if not rel:
            return None
        normalized_rel = rel.lstrip("/")
        candidates: list[tuple[Path, str]] = []

        # Task A2: 绝对路径受信根目录校验
        if Path(rel).is_absolute():
            abs_path = Path(rel)
            if self._is_path_in_trusted_root(abs_path, run_id):
                candidates.append((abs_path, "absolute_trusted"))
            else:
                # 不受信的绝对路径直接拒绝
                return None
        else:
            # Task A1: 收紧路径解析候选列表（仅逻辑路径 + .polaris 受限路径）
            # 尝试逻辑路径
            try:
                logical_path = Path(
                    resolve_logical_path(
                        str(self.workspace),
                        normalized_rel,
                        ramdisk_root=str(self.ramdisk_root),
                    )
                )
                candidates.append((logical_path, "logical_path"))
            except (OSError, ValueError):
                # 逻辑路径解析失败：忽略此候选
                pass

            # .polaris/factory/{run_id}
            candidates.append(
                (
                    self.workspace / ".polaris" / "factory" / run_id / Path(normalized_rel),
                    ".polaris_factory",
                )
            )
            # .polaris/factory/{run_id}/artifacts
            candidates.append(
                (
                    self.workspace / ".polaris" / "factory" / run_id / "artifacts" / Path(normalized_rel),
                    ".polaris_artifacts",
                )
            )
            # .polaris（仅此受限路径）
            candidates.append((self.workspace / ".polaris" / Path(normalized_rel), ".polaris"))
            # 注意：已删除 self.workspace / Path(normalized_rel) 不受控兜底

        seen: set[str] = set()
        fallback_count = 0
        # 以第一个候选的类型作为基准类型（期望的类型）
        first_resolve_type = candidates[0][1] if candidates else None
        for candidate, resolve_type in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)

            # Task A3: 统计回退次数（与基准类型不同的路径都算回退）
            if first_resolve_type and resolve_type != first_resolve_type:
                fallback_count += 1

            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    self.path_fallback_count += fallback_count
                    return {
                        "path": candidate,
                        "resolved_by": resolve_type,
                        "resolved_path": str(candidate),
                    }
                if candidate.is_dir() and any(
                    path.is_file() and path.stat().st_size > 0 for path in candidate.rglob("*")
                ):
                    self.path_fallback_count += fallback_count
                    return {
                        "path": candidate,
                        "resolved_by": resolve_type,
                        "resolved_path": str(candidate),
                    }
            except (OSError, PermissionError):
                # 文件系统错误：跳过此候选路径
                continue
        return None

    def _extract_chain_stage_evidence(
        self,
        events: list[dict[str, Any]],
        *,
        run_id: str,
        expected_role_order: list[str] | None = None,
    ) -> dict[str, Any]:
        observed_role_sequence: list[str] = []
        stages: dict[str, dict[str, Any]] = {}

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            result_payload = event.get("result") if isinstance(event.get("result"), dict) else {}
            stage_name = str(event.get("stage") or "").strip()
            if not stage_name:
                stage_name = str(result_payload.get("stage") or "").strip()
            role = STAGE_NAME_TO_CHAIN_ROLE.get(stage_name)
            if not role:
                continue
            stage = stages.setdefault(
                role,
                {
                    "stage_names": [],
                    "statuses": [],
                    "declared_artifacts": [],
                    "existing_artifacts": [],
                    "resolved_artifacts": [],
                    "missing_artifacts": [],
                },
            )
            stage["stage_names"].append(stage_name)
            if event_type == "stage_started":
                observed_role_sequence.append(role)
            if event_type != "stage_completed":
                continue
            status = normalize_status(result_payload.get("status") or event.get("status"))
            if status:
                stage["statuses"].append(status)
            artifacts_raw = result_payload.get("artifacts")
            artifacts = artifacts_raw if isinstance(artifacts_raw, list) else []
            for artifact in artifacts:
                rel = str(artifact or "").strip()
                if not rel:
                    continue
                stage["declared_artifacts"].append(rel)
                resolved = self._resolve_stage_artifact_path(run_id, rel)
                if resolved is None:
                    stage["missing_artifacts"].append(rel)
                else:
                    stage["existing_artifacts"].append(resolved["resolved_path"])
                    stage["resolved_artifacts"].append(
                        {
                            "artifact": rel,
                            "resolved_by": resolved["resolved_by"],
                            "resolved_path": resolved["resolved_path"],
                        }
                    )

        for payload in stages.values():
            payload["stage_names"] = self._dedupe_preserve_order(payload["stage_names"])
            payload["statuses"] = self._dedupe_preserve_order(payload["statuses"])
            payload["declared_artifacts"] = self._dedupe_preserve_order(payload["declared_artifacts"])
            payload["existing_artifacts"] = self._dedupe_preserve_order(payload["existing_artifacts"])
            payload["resolved_artifacts"] = self._dedupe_resolved_artifacts(payload["resolved_artifacts"])
            payload["missing_artifacts"] = self._dedupe_preserve_order(payload["missing_artifacts"])

        return {
            "expected_role_order": (
                self._dedupe_preserve_order(expected_role_order or []) or self._expected_chain_roles()
            ),
            "observed_role_order": self._dedupe_preserve_order(observed_role_sequence),
            "stages": stages,
        }

    async def _capture_chain_stage_evidence(self, result: RoundResult) -> None:
        run_id = str(result.factory_run_id or "").strip()
        if not run_id:
            return
        try:
            events = await self._fetch_factory_events(run_id)
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            workspace_artifacts["chain_stage_evidence"] = {
                "error": f"fetch_factory_events_failed: {type(exc).__name__}: {exc}",
            }
            result.workspace_artifacts = workspace_artifacts
            return

        expected_roles = self._expected_chain_roles(entry_stage=self._resolve_round_entry_stage(result))
        chain_evidence = self._extract_chain_stage_evidence(
            events,
            run_id=run_id,
            expected_role_order=expected_roles,
        )
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_evidence["path_contract"] = {
            "path_fallback_count": int(workspace_artifacts.get("path_fallback_count") or 0),
            "pass": bool(workspace_artifacts.get("path_contract_ok") is True),
        }
        workspace_artifacts["chain_stage_evidence"] = chain_evidence
        result.workspace_artifacts = workspace_artifacts

    def _validate_pm_task_contract(self, result: RoundResult, stage_evidence: dict[str, Any]) -> str | None:
        stages = stage_evidence.get("stages") if isinstance(stage_evidence.get("stages"), dict) else {}
        pm_stage = stages.get("pm") if isinstance(stages.get("pm"), dict) else {}
        existing_artifacts = (
            pm_stage.get("existing_artifacts") if isinstance(pm_stage.get("existing_artifacts"), list) else []
        )
        plan_candidates = [
            Path(path)
            for path in existing_artifacts
            if str(path).replace("\\", "/").lower().endswith("tasks/plan.json")
        ]
        if not plan_candidates:
            return "pm_plan_missing_artifact"

        plan_path = plan_candidates[0]
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"pm_plan_invalid_json:{plan_path}"

        tasks: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            raw_tasks = payload.get("tasks")
            if isinstance(raw_tasks, list):
                tasks = [item for item in raw_tasks if isinstance(item, dict)]
            elif isinstance(payload.get("plan"), dict):
                nested_tasks = payload.get("plan", {}).get("tasks")
                if isinstance(nested_tasks, list):
                    tasks = [item for item in nested_tasks if isinstance(item, dict)]
        if not tasks:
            return f"pm_plan_empty_tasks:{plan_path}"

        def has_field(task: dict[str, Any], keys: tuple[str, ...], *, require_list: bool = False) -> bool:
            for key in keys:
                value = task.get(key)
                if require_list and isinstance(value, list) and len(value) > 0:
                    return True
                if not require_list and str(value or "").strip():
                    return True
            return False

        invalid_tasks = 0
        for task in tasks:
            has_goal = has_field(task, ("goal", "title", "objective", "目标"))
            has_scope = has_field(task, ("scope", "范围", "作用域"))
            has_steps = has_field(task, ("steps", "implementation_steps", "执行步骤"), require_list=True)
            has_acceptance = has_field(
                task,
                ("acceptance", "acceptance_criteria", "验收标准", "可测验收"),
                require_list=True,
            )
            if not (has_goal and has_scope and has_steps and has_acceptance):
                invalid_tasks += 1
        if invalid_tasks > 0:
            return f"pm_plan_incomplete_tasks:{invalid_tasks}/{len(tasks)}"
        return None

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(parsed, 0)

    @staticmethod
    def _parse_json_file(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _extract_dispatch_metrics(self, chain_stage_evidence: dict[str, Any]) -> dict[str, Any]:
        stages = chain_stage_evidence.get("stages") if isinstance(chain_stage_evidence.get("stages"), dict) else {}
        director_stage = stages.get("director") if isinstance(stages.get("director"), dict) else {}
        existing_artifacts = (
            director_stage.get("existing_artifacts")
            if isinstance(director_stage.get("existing_artifacts"), list)
            else []
        )

        completed_statuses = {"completed", "success", "done"}
        failed_statuses = {"failed", "error", "cancelled", "blocked", "timeout"}
        best: dict[str, Any] = {
            "task_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "task_status_counts": {},
            "dispatch_log": "",
            "failed_tasks": [],
        }

        for candidate_raw in existing_artifacts:
            candidate_path = Path(str(candidate_raw or "").strip())
            if not candidate_path.exists() or not candidate_path.is_file():
                continue
            normalized = candidate_path.as_posix().lower()
            if not normalized.endswith("/dispatch/log.json"):
                continue
            payload = self._parse_json_file(candidate_path)
            if not payload:
                continue

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            task_status_counts = (
                metadata.get("task_status_counts")
                if isinstance(metadata.get("task_status_counts"), dict)
                else (payload.get("task_status_counts") if isinstance(payload.get("task_status_counts"), dict) else {})
            )
            summarized_total = sum(self._safe_int(v) for v in task_status_counts.values())
            explicit_total = self._safe_int(
                metadata.get("task_count") if "task_count" in metadata else payload.get("task_count")
            )
            task_count = max(explicit_total, summarized_total)

            completed_count = 0
            failed_count = 0
            for status_name, status_count in task_status_counts.items():
                normalized_status = normalize_status(status_name)
                count = self._safe_int(status_count)
                if normalized_status in completed_statuses:
                    completed_count += count
                elif normalized_status in failed_statuses:
                    failed_count += count

            failed_tasks = metadata.get("failed_tasks") if isinstance(metadata.get("failed_tasks"), list) else []
            if failed_tasks and failed_count <= 0:
                failed_count = len([item for item in failed_tasks if isinstance(item, dict)])
            if task_count <= 0 and failed_count > 0:
                task_count = failed_count

            if task_count > int(best.get("task_count") or 0):
                best = {
                    "task_count": task_count,
                    "completed_count": min(completed_count, task_count),
                    "failed_count": min(failed_count, task_count),
                    "task_status_counts": dict(task_status_counts),
                    "dispatch_log": str(candidate_path),
                    "failed_tasks": [item for item in failed_tasks if isinstance(item, dict)],
                }

        return best

    def _backfill_trace_from_dispatch_artifact(
        self,
        result: RoundResult,
        chain_stage_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = self._extract_dispatch_metrics(chain_stage_evidence)
        task_count = self._safe_int(metrics.get("task_count"))
        if task_count <= 0:
            return {}

        completed_count = min(self._safe_int(metrics.get("completed_count")), task_count)
        failed_count = min(self._safe_int(metrics.get("failed_count")), task_count)

        if result.trace is None:
            result.trace = RoundTrace(
                round_number=result.round_number,
                project_id=result.project.id,
                project_name=result.project.name,
                start_time=result.start_time,
                factory_run_id=result.factory_run_id,
            )
        trace_obj = result.trace
        trace_obj.total_tasks = max(self._safe_int(trace_obj.total_tasks), task_count)
        trace_obj.completed_tasks = max(self._safe_int(trace_obj.completed_tasks), completed_count)
        trace_obj.failed_tasks = max(self._safe_int(trace_obj.failed_tasks), failed_count)

        if not isinstance(trace_obj.tasks, dict):
            trace_obj.tasks = {}

        if len(trace_obj.tasks) == 0:
            failed_tasks = metrics.get("failed_tasks") if isinstance(metrics.get("failed_tasks"), list) else []
            for item in failed_tasks:
                task_id = str(item.get("task_id") or "").strip()
                if not task_id:
                    continue
                trace_obj.tasks[task_id] = TaskLineage(
                    task_id=task_id,
                    subject=str(item.get("subject") or "director task").strip() or "director task",
                    status=normalize_status(item.get("status") or "failed"),
                    created_by=str(item.get("role_id") or "director").strip() or "director",
                    created_at=str(item.get("updated_at") or result.end_time or result.start_time).strip(),
                    result_summary=str(item.get("error_message") or "").strip(),
                    pm_task_id=str(item.get("pm_task_id") or "").strip() or None,
                )
            synthetic_needed = max(trace_obj.total_tasks - len(trace_obj.tasks), 0)
            for index in range(synthetic_needed):
                synthetic_id = f"dispatch-task-{index + 1}"
                if synthetic_id in trace_obj.tasks:
                    continue
                synthetic_status = "completed" if index < trace_obj.completed_tasks else "failed"
                trace_obj.tasks[synthetic_id] = TaskLineage(
                    task_id=synthetic_id,
                    subject="director dispatch synthesized task",
                    status=synthetic_status,
                    created_by="director",
                    created_at=result.end_time or result.start_time,
                    result_summary="backfilled from dispatch/log.json metadata",
                )

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        workspace_artifacts["trace_backfill"] = {
            "source": "dispatch_log_metadata",
            "dispatch_log": str(metrics.get("dispatch_log") or ""),
            "task_count": trace_obj.total_tasks,
            "completed_tasks": trace_obj.completed_tasks,
            "failed_tasks": trace_obj.failed_tasks,
        }
        result.workspace_artifacts = workspace_artifacts
        return {
            "task_count": trace_obj.total_tasks,
            "completed_tasks": trace_obj.completed_tasks,
            "failed_tasks": trace_obj.failed_tasks,
        }

    def _extract_tool_executions_from_adapter_logs(self) -> list[dict[str, Any]]:
        log_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/roles/director/logs"))
        if not log_dir.exists() or not log_dir.is_dir():
            return []

        candidates = sorted(
            [path for path in log_dir.glob("adapter_debug_*.jsonl") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return []

        extracted: list[dict[str, Any]] = []
        seen: set[str] = set()

        def append_tool_item(timestamp: str, item: dict[str, Any]) -> None:
            tool_name = str(item.get("tool") or item.get("source_tool") or "").strip()
            if not tool_name:
                return
            file_path = str(item.get("file") or "").strip()
            success = bool(item.get("success", False))
            error_message = str(item.get("error") or "").strip()
            dedupe_key = "|".join([timestamp, tool_name, file_path, str(success), error_message])
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            extracted.append(
                {
                    "tool_name": tool_name,
                    "timestamp": timestamp,
                    "success": success,
                    "error_message": error_message,
                    "duration_ms": self._safe_int(item.get("duration_ms")),
                }
            )

        for path in candidates:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, PermissionError):
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                event_name = str(entry.get("event") or "").strip().lower()
                if event_name not in {"first_tool_results", "retry_tool_results", "workspace_diff"}:
                    continue
                payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                timestamp = str(entry.get("timestamp") or "").strip()
                items = payload.get("items") if isinstance(payload.get("items"), list) else []
                for item in items:
                    if isinstance(item, dict):
                        append_tool_item(timestamp, item)
                summary_items = payload.get("tool_summary") if isinstance(payload.get("tool_summary"), list) else []
                for item in summary_items:
                    if isinstance(item, dict):
                        append_tool_item(timestamp, item)

        return extracted

    def _backfill_observability_from_director_logs(self, result: RoundResult) -> int:
        extracted = self._extract_tool_executions_from_adapter_logs()
        if not extracted:
            return 0

        observability = result.observability_data if isinstance(result.observability_data, dict) else {}
        existing = (
            observability.get("tool_executions") if isinstance(observability.get("tool_executions"), list) else []
        )
        merged: list[dict[str, Any]] = [item for item in existing if isinstance(item, dict)]
        seen_keys: set[str] = set()
        for item in merged:
            key = "|".join(
                [
                    str(item.get("timestamp") or ""),
                    str(item.get("tool_name") or ""),
                    str(item.get("success") or ""),
                    str(item.get("error_message") or ""),
                ]
            )
            seen_keys.add(key)
        for item in extracted:
            key = "|".join(
                [
                    str(item.get("timestamp") or ""),
                    str(item.get("tool_name") or ""),
                    str(item.get("success") or ""),
                    str(item.get("error_message") or ""),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(item)

        observability["tool_executions"] = merged
        stats = observability.get("statistics") if isinstance(observability.get("statistics"), dict) else {}
        stats["total_tool_executions"] = len(merged)
        stats["failed_tool_executions"] = sum(1 for item in merged if not bool(item.get("success", False)))
        observability["statistics"] = stats

        warnings = (
            observability.get("collection_warnings")
            if isinstance(observability.get("collection_warnings"), list)
            else []
        )
        warnings.append("tool_executions backfilled from director adapter_debug logs")
        observability["collection_warnings"] = warnings[-50:]
        result.observability_data = observability

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        workspace_artifacts["observability_backfill"] = {
            "source": "director_adapter_debug_logs",
            "tool_execution_count": len(merged),
        }
        result.workspace_artifacts = workspace_artifacts
        return len(merged)
