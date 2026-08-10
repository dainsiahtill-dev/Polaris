"""run lifecycle / probe / execution for AgentStressRunner (mixin)."""

# mypy: ignore-errors

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backend_bootstrap import (
    BackendBootstrapError,
)
from ..engine import RoundResult, StageResult, StressEngine
from ..preflight import BackendPreflightProbe, BackendPreflightStatus
from ..probe import ProbeStatus, RoleAvailabilityProbe
from ..project_pool import (
    PROJECT_POOL,
    ProjectDefinition,
    select_stress_rounds,
    validate_round_sequence,
)
from ..stress_path_policy import (
    ensure_stress_workspace_path,
)
from ._constants import (
    DEFAULT_STRESS_RAMDISK,
)


class _AgentStressRunnerLifecycleMixin:
    async def run(self) -> int:
        """运行完整压测流程"""
        self.start_time = datetime.now().isoformat()
        self._record_audit_timeline_event(
            event="run_started",
            detail="Agent stress runner started",
            refs={"workspace": str(self.workspace)},
        )

        try:
            # === 任务 D1: 批次闸门检查 ===
            # 当 rounds > round_batch_limit 时直接拒绝，要求分批执行
            if self.rounds > self.round_batch_limit:
                raise ValueError(
                    f"Rounds {self.rounds} exceeds batch limit {self.round_batch_limit}. Please run in batches."
                )

            self.workspace = ensure_stress_workspace_path(self.workspace)
        except ValueError as exc:
            self._use_safe_policy_error_output_dir()
            self.abort_reason = {
                "category": "workspace_policy_violation",
                "summary": str(exc),
                "detail": str(exc),
            }
            self.end_time = datetime.now().isoformat()
            self._record_audit_timeline_event(
                event="workspace_policy_violation",
                status="failed",
                detail=str(exc),
            )
            print(f"\n❌ Workspace 路径策略失败: {exc}")
            await self._generate_reports()
            return 2

        try:
            await self._ensure_backend_session()
        except BackendBootstrapError as exc:
            self.abort_reason = {
                "category": "backend_bootstrap_failed",
                "summary": str(exc),
                "detail": json.dumps(exc.details, ensure_ascii=False),
            }
            self.end_time = datetime.now().isoformat()
            self._record_audit_timeline_event(
                event="backend_bootstrap_failed",
                status="failed",
                detail=str(exc),
                refs={"details": exc.details},
            )
            print(f"\n❌ Backend 自动自举失败: {exc}")
            await self._generate_reports()
            return 2

        try:
            print("=" * 80)
            print("Polaris AI Agent 专项压测")
            print("=" * 80)
            print(f"Workspace: {self.workspace}")
            print("Round Execution: sequential (one project per round)")
            print(f"Execution Mode: {self.execution_mode}")
            print(f"Rounds: {self.rounds}")
            print(f"Strategy: {self.strategy}")
            print(f"Workspace Mode: {self.workspace_mode}")
            if self.execution_mode == "project_serial":
                print(f"Attempts Per Project: {self.attempts_per_project}")
            print(
                "Main Chain Policy: "
                f"architect={'on' if self.run_architect_stage else 'off'}"
                f"{' (required)' if self.require_architect_stage else ' (optional)'}, "
                "pm=required, "
                f"chief_engineer={'on' if self.run_chief_engineer_stage else 'off'}"
                f"{' (required)' if self.require_chief_engineer_stage else ' (optional)'}, "
                "director=required, qa=required"
            )
            print(f"Backend: {self.backend_url}")
            print(f"Backend Context: {self.backend_context_source}")
            print(f"Non-LLM Control Plane Budget: {self.non_llm_timeout_seconds:.0f}s")
            print(
                f"Quality Gate: min_new_code_files={self.min_new_code_files}, "
                f"min_new_code_lines={self.min_new_code_lines}, "
                f"chain_evidence_gate={'off' if self.disable_chain_evidence_gate else 'on'}"
            )
            if self.managed_backend_session and self.managed_backend_session.auto_bootstrapped:
                print(f"Backend Bootstrap Workspace: {self.managed_backend_session.startup_workspace}")
                print(f"Backend Bootstrap RamDisk: {self.managed_backend_session.ramdisk_root}")
            print("=" * 80)
            self._record_audit_timeline_event(
                event="run_context_ready",
                detail="Workspace policy and backend bootstrap passed",
                refs={
                    "backend_url": self.backend_url,
                    "execution_mode": self.execution_mode,
                    "rounds": self.rounds,
                },
            )
            self._write_audit_checkpoint(phase="run_context_ready")

            # Step 1: Backend 预检
            print("\n## Step 1: Backend 预检")
            self._record_audit_timeline_event(event="step_preflight_started", detail="Running backend preflight")
            if not await self._run_backend_preflight():
                return await self._abort_run(2)
            self._record_audit_timeline_event(event="step_preflight_completed", detail="Backend preflight passed")

            # Step 2: 角色可用性探针
            print("\n## Step 2: 角色可用性探针")
            self._record_audit_timeline_event(event="step_probe_started", detail="Running role readiness probe")
            if not await self._run_probe():
                return await self._abort_run(2)
            self._record_audit_timeline_event(event="step_probe_completed", detail="Role probe passed")

            # Step 3: 选择项目
            print("\n## Step 3: 选择压测项目")
            selected_projects = self._select_projects()
            if not selected_projects:
                print("❌ 没有可用的项目")
                return 1

            print(f"已选择 {len(selected_projects)} 个项目:")
            for i, p in enumerate(selected_projects, 1):
                print(f"  {i}. [{p.category.value}] {p.name} (复杂度 {p.complexity_level}/5)")

            # Step 4: 验证轮次序列
            print("\n## Step 4: 验证轮次序列")
            violations = validate_round_sequence(selected_projects)
            if violations:
                print("⚠️ 发现违规项:")
                for v in violations:
                    print(f"  - {v['message']}")
            else:
                print("✅ 轮次序列符合规则")

            # Step 5: 执行压测
            print("\n## Step 5: 执行压测")
            print("-" * 80)

            # 恢复之前的进度
            if self.resume_from >= 1:
                await self._load_previous_results()
                print(f"已从第 {self.resume_from} 轮恢复，跳过前 {self.resume_from - 1} 轮")

            async with StressEngine(
                workspace=self.workspace,
                backend_url=self.backend_url,
                token=self.token,
                ramdisk_root=DEFAULT_STRESS_RAMDISK,
                factory_timeout=3600,
                poll_interval=5.0,
                control_plane_stall_timeout=self.non_llm_timeout_seconds,
                min_new_code_files=self.min_new_code_files,
                min_new_code_lines=self.min_new_code_lines,
                require_full_chain_evidence=not self.disable_chain_evidence_gate,
                workspace_mode=self.workspace_mode,
                run_architect_stage=self.run_architect_stage,
                run_chief_engineer_stage=self.run_chief_engineer_stage,
                require_architect_stage=self.require_architect_stage,
                require_chief_engineer_stage=self.require_chief_engineer_stage,
                chain_profile=self.chain_profile,
            ) as engine:
                if self.execution_mode == "project_serial":
                    await self._run_project_serial(engine, selected_projects)
                else:
                    await self._run_round_robin(engine, selected_projects)
                self.path_fallback_count = int(getattr(engine, "path_fallback_count", 0) or 0)
                if self.post_batch_audit and self.results and len(self.results) > self._last_audited_round_count:
                    batch_number = max(1, (len(self.results) + self.round_batch_limit - 1) // self.round_batch_limit)
                    await self._run_batch_audit_and_pause(engine, batch_number)

            if self._stop_requested and self._early_exit_code is not None:
                return await self._abort_run(self._early_exit_code)

            self.end_time = datetime.now().isoformat()
            self._record_audit_timeline_event(
                event="run_completed",
                status="completed",
                detail="Stress execution loop completed",
                refs={"completed_rounds": len(self.results)},
            )

            # Step 6: 生成报告
            print("\n## Step 6: 生成报告")
            await self._generate_reports()

            # 返回码
            failed_count = sum(1 for r in self.results if r.overall_result == "FAIL")
            if self.post_batch_audit_failed:
                print("\n❌ 批后代码审计未通过")
                return 2
            if failed_count == 0:
                print("\n✅ 所有轮次通过")
                return 0
            if failed_count <= len(self.results) * 0.2:
                print(f"\n⚠️ {failed_count}/{len(self.results)} 轮次失败")
                return 1
            print(f"\n❌ {failed_count}/{len(self.results)} 轮次失败")
            return 2
        except asyncio.CancelledError:
            if not self.abort_reason:
                self.abort_reason = {
                    "category": "runner_cancelled",
                    "summary": "Runner cancelled before completion",
                    "detail": "asyncio.CancelledError",
                }
            self.end_time = datetime.now().isoformat()
            self._record_audit_timeline_event(
                event="run_cancelled",
                status="failed",
                detail="Runner cancelled before completion",
            )
            await self._generate_reports()
            return 2
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if not self.abort_reason:
                self.abort_reason = {
                    "category": "runner_unhandled_exception",
                    "summary": f"{type(exc).__name__}: {exc}",
                    "detail": traceback.format_exc(),
                }
            self.end_time = datetime.now().isoformat()
            self._record_audit_timeline_event(
                event="run_unhandled_exception",
                status="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )
            await self._generate_reports()
            return 2
        finally:
            if self.managed_backend_session is not None:
                await self.managed_backend_session.aclose()

    async def _ensure_backend_session(self) -> None:
        # Resolve via package surface so monkeypatch of
        # ``runner.ensure_backend_session`` keeps working after the
        # module→package split (same binding as the former single-file module).
        import sys

        _runner_pkg = sys.modules[__name__.rsplit(".", 1)[0]]  # polaris.tests.agent_stress.runner

        self.managed_backend_session = await _runner_pkg.ensure_backend_session(
            backend_url=self.requested_backend_url,
            token=self.requested_token,
            auto_bootstrap=self.auto_bootstrap,
            startup_workspace=self.workspace,
            ramdisk_root=Path(DEFAULT_STRESS_RAMDISK),
        )
        self.backend_url = self.managed_backend_session.context.backend_url
        self.token = self.managed_backend_session.context.token
        self.backend_context_source = self.managed_backend_session.context.source

    async def _run_probe(self) -> bool:
        """运行角色探针"""
        async with RoleAvailabilityProbe(
            backend_url=self.backend_url,
            probe_timeout=30,
            token=self.token,
        ) as probe:
            report = await probe.probe_all()
            self.probe_report = report.to_dict()

            # 保存探针报告
            probe_path = self._ensure_output_dir() / "probe_report.json"
            self._write_json_atomic(probe_path, self.probe_report)
            print(f"探针报告已保存: {probe_path}")
            self._record_audit_timeline_event(
                event="probe_report_written",
                detail="Role probe report persisted",
                refs={"path": str(probe_path)},
            )
            self._write_audit_checkpoint(phase="probe_report_written")

            # 打印摘要
            summary = self.probe_report.get("summary", {})
            print("\n探针结果:")
            print(f"  健康: {summary.get('healthy', 0)}/{summary.get('total_roles', 0)}")
            print(f"  降级: {summary.get('degraded', 0)}")
            print(f"  不可用: {summary.get('unhealthy', 0)}")

            policy_ok, policy_messages = self._apply_chain_probe_policy(report.to_dict())
            for message in policy_messages:
                print(message)
            if policy_ok:
                return True

            self.abort_reason = self._classify_probe_failure(report.to_dict())
            print(f"\n❌ 角色探针失败: {self.abort_reason['summary']}")
            self._record_audit_timeline_event(
                event="probe_failed",
                status="failed",
                detail=self.abort_reason.get("summary", ""),
            )
            return False

    async def _run_backend_preflight(self) -> bool:
        """运行 backend 预检。"""
        async with BackendPreflightProbe(
            backend_url=self.backend_url,
            token=self.token,
            timeout=5.0,
        ) as preflight:
            report = await preflight.run()
            self.backend_preflight_report = report.to_dict()

        preflight_path = self._ensure_output_dir() / "backend_preflight.json"
        self._write_json_atomic(preflight_path, self.backend_preflight_report)
        print(f"预检报告已保存: {preflight_path}")
        self._record_audit_timeline_event(
            event="backend_preflight_written",
            detail="Backend preflight report persisted",
            refs={"path": str(preflight_path), "status": report.status.value},
        )
        self._write_audit_checkpoint(phase="backend_preflight_written")
        print(f"  状态: {report.status.value}")
        print(f"  Backend 可达: {report.backend_reachable}")
        print(f"  鉴权有效: {report.auth_valid}")
        print(f"  Settings 可访问: {report.settings_accessible}")
        print(f"  WS runtime.v2 可用: {report.ws_runtime_v2_accessible}")
        print(f"  JetStream 可用: {report.jetstream_accessible}")
        print(f"  投影传输: {report.projection_transport}")

        if report.status == BackendPreflightStatus.HEALTHY:
            return True

        self.abort_reason = {
            "category": report.status.value,
            "summary": self._backend_preflight_summary(report),
            "detail": json.dumps(self.backend_preflight_report, ensure_ascii=False),
        }
        print(f"\n❌ Backend 预检失败: {self.abort_reason['summary']}")
        self._record_audit_timeline_event(
            event="backend_preflight_failed",
            status="failed",
            detail=self.abort_reason.get("summary", ""),
        )
        return False

    async def _run_post_batch_audit(
        self,
        engine: StressEngine,
        *,
        batch_number: int,
    ) -> None:
        """执行批后随机抽查审计"""
        if not self.results:
            print("  无结果可审计")
            return

        # 使用默认 seed（可复现）
        seed = self.audit_seed if self.audit_seed is not None else 42

        print(f"  批后审计配置: sample_size={self.audit_sample_size}, seed={seed}")

        # 执行审计
        audit_result = engine._post_batch_code_audit(
            projects=self.results,
            sample_size=self.audit_sample_size,
            seed=seed,
        )

        # 保存审计结果
        self.post_batch_audit_result = audit_result

        # 打印摘要
        sample_audits = audit_result.get("sample_audits", [])
        failed_rules = audit_result.get("failed_rules_hit", [])
        self.post_batch_audit_failed = bool(failed_rules)

        timestamp = datetime.now(timezone.utc).isoformat()
        post_batch_code_audit = {
            "timestamp": timestamp,
            "batch_number": int(batch_number),
            "sample_size": int(self.audit_sample_size),
            "projects_audited": [
                {
                    "project_id": str(audit.get("project_id") or "").strip(),
                    "project_name": str(audit.get("project_name") or "").strip(),
                    "workspace": str(audit.get("workspace") or "").strip(),
                }
                for audit in sample_audits
            ],
            "issues_found": list(audit_result.get("issues_found") or []),
            "failed_rules": list(failed_rules),
            "evidence_paths": list(audit_result.get("evidence_paths") or []),
        }
        self.post_batch_audit_result["post_batch_code_audit"] = post_batch_code_audit
        self.post_batch_audit_history.append(post_batch_code_audit)
        self._record_audit_timeline_event(
            event="post_batch_audit_completed",
            status="failed" if failed_rules else "completed",
            detail="Post-batch audit finished",
            refs={
                "batch_number": batch_number,
                "sampled_projects": len(sample_audits),
                "failed_rules": len(failed_rules),
            },
        )
        self._write_audit_checkpoint(phase="post_batch_audit_completed")

        print(f"  审计完成: 抽查 {len(sample_audits)} 个项目")
        print(f"  失败规则命中: {len(failed_rules)} 个")

        # 打印失败的规则详情
        if failed_rules:
            print("  失败规则详情:")
            for rule in failed_rules[:10]:
                print(f"    - [{rule['severity']}] {rule['rule']}: {rule['project_id']}/{rule['file']}")

    async def _run_batch_audit_and_pause(self, engine: StressEngine, batch_number: int) -> None:
        """每批结束后执行审计（无人值守模式下不阻塞输入）。"""
        print(f"\n{'=' * 60}")
        print(f"### Batch #{batch_number} Complete - Running Audit ###")
        print(f"{'=' * 60}")

        await self._run_post_batch_audit(engine, batch_number=batch_number)
        self._last_audited_round_count = len(self.results)
        print(f"\n{'=' * 60}")
        print(f"### Batch #{batch_number} Audit Complete (non-blocking) ###")
        print(f"{'=' * 60}")

    def _write_audit_report(self, batch_number: int) -> None:
        """写入审计报告到 stress_audit_package.json"""
        if not self.post_batch_audit_result:
            return

        audit_data = self.post_batch_audit_result
        timestamp = datetime.now(timezone.utc).isoformat()

        # 构建符合任务要求的审计报告格式
        post_batch_code_audit = {
            "timestamp": timestamp,
            "batch_number": batch_number,
            "sample_size": self.audit_sample_size,
            "projects_audited": [
                {
                    "project_id": audit.get("project_id"),
                    "project_name": audit.get("project_name"),
                    "workspace": audit.get("workspace"),
                }
                for audit in audit_data.get("sample_audits", [])
            ],
            "issues_found": audit_data.get("issues_found", []),
            "failed_rules": audit_data.get("failed_rules_hit", []),
            "evidence_paths": audit_data.get("evidence_paths", []),
        }

        # 更新 JSON 报告中的批后审计字段
        self.post_batch_audit_result["post_batch_code_audit"] = post_batch_code_audit

    def _write_summary_audit(self, batch_number: int) -> None:
        """将批后审计摘要写入 summary.txt"""
        if not self.post_batch_audit_result:
            return

        audit_data = self.post_batch_audit_result
        timestamp = datetime.now(timezone.utc).isoformat()

        sample_audits = audit_data.get("sample_audits", [])
        failed_rules = audit_data.get("failed_rules_hit", [])

        # 计算总批次数（基于 round_batch_limit）
        total_batches = (self.rounds + self.round_batch_limit - 1) // self.round_batch_limit

        summary_lines = [
            "",
            "=" * 50,
            "=== Batch Audit Summary ===",
            f"Batch: {batch_number}/{total_batches}",
            f"Timestamp: {timestamp}",
            f"Projects Audited: {len(sample_audits)}",
            f"Issues Found: {len(failed_rules)}",
        ]

        if failed_rules:
            summary_lines.append("Failed Rules:")
            for rule in failed_rules[:10]:
                summary_lines.append(
                    f"  - [{rule.get('severity', 'unknown')}] {rule.get('rule', 'unknown')}: {rule.get('project_id', 'unknown')}"
                )

        summary_lines.append("=" * 50)

        # 追加到 summary.txt
        summary_path = self._ensure_output_dir() / "summary.txt"
        existing_content = ""
        if summary_path.exists():
            existing_content = summary_path.read_text(encoding="utf-8")

        new_content = existing_content + "\n" + "\n".join(summary_lines)
        summary_path.write_text(new_content, encoding="utf-8")
        print(f"  批后审计摘要已写入: {summary_path}")

    @staticmethod
    def _backend_preflight_summary(report: object) -> str:
        status = getattr(report, "status", None)
        if status == BackendPreflightStatus.BACKEND_CONTEXT_MISSING:
            return "Unable to resolve Polaris backend context"
        if status == BackendPreflightStatus.BACKEND_UNAVAILABLE:
            return "Polaris backend is unreachable"
        if status == BackendPreflightStatus.AUTH_INVALID:
            return "Polaris backend authentication is invalid"
        if status == BackendPreflightStatus.SETTINGS_UNAVAILABLE:
            return "Polaris backend settings endpoint is unavailable"
        if status == BackendPreflightStatus.RUNTIME_V2_UNAVAILABLE:
            return "Polaris runtime.v2 WebSocket or JetStream preflight failed"
        error = str(getattr(report, "error", "") or "").strip()
        return error or str(getattr(status, "value", status) or "backend_preflight_failed")

    def _classify_probe_failure(self, report: dict[str, Any]) -> dict[str, str]:
        """区分角色未配置、未就绪和鉴权类失败。"""
        roles = report.get("roles", []) if isinstance(report.get("roles"), list) else []
        errors = [str(role.get("error") or "") for role in roles if isinstance(role, dict)]
        configured_flags = [bool(role.get("configured")) for role in roles if isinstance(role, dict)]
        ready_flags = [bool(role.get("ready")) for role in roles if isinstance(role, dict)]

        if errors and all("unauthorized" in error.lower() or "401" in error for error in errors if error):
            return {
                "category": "auth_invalid",
                "summary": "Role probe unauthorized; backend token is invalid or missing",
                "detail": json.dumps(report, ensure_ascii=False),
            }

        if configured_flags and not any(configured_flags):
            return {
                "category": "roles_unconfigured",
                "summary": "All required Polaris roles are currently unconfigured",
                "detail": json.dumps(report, ensure_ascii=False),
            }

        if configured_flags and any(configured_flags) and not all(ready_flags):
            return {
                "category": "roles_not_ready",
                "summary": "Some Polaris roles are configured but not ready",
                "detail": json.dumps(report, ensure_ascii=False),
            }

        return {
            "category": "roles_unhealthy",
            "summary": "Role probe found unhealthy role bindings",
            "detail": json.dumps(report, ensure_ascii=False),
        }

    @staticmethod
    def _is_role_probe_ready(role_payload: dict[str, Any]) -> bool:
        status = str(role_payload.get("status") or "").strip().lower()
        configured = bool(role_payload.get("configured"))
        ready = bool(role_payload.get("ready"))
        return configured and ready and status == ProbeStatus.HEALTHY.value

    def _apply_chain_probe_policy(self, report: dict[str, Any]) -> tuple[bool, list[str]]:
        roles = report.get("roles", []) if isinstance(report.get("roles"), list) else []
        role_map: dict[str, dict[str, Any]] = {
            str(item.get("role") or "").strip(): item for item in roles if isinstance(item, dict)
        }
        messages: list[str] = []

        required_roles = {"pm", "director", "qa"}
        if self.chain_profile == "court_strict":
            required_roles.add("architect")
        if self.require_architect_stage:
            required_roles.add("architect")
        if self.require_chief_engineer_stage:
            required_roles.add("chief_engineer")

        missing_required: list[str] = []
        for role in sorted(required_roles):
            payload = role_map.get(role)
            if not payload or not self._is_role_probe_ready(payload):
                missing_required.append(role)
        if missing_required:
            messages.append("  ❌ 必需角色未就绪: " + ", ".join(missing_required))
            return False, messages

        if self.chain_profile == "court_strict":
            self.run_architect_stage = True
            self.require_architect_stage = True
            if self.run_chief_engineer_stage and not self.require_chief_engineer_stage:
                self.run_chief_engineer_stage = False
                messages.append("  ⚠️ court_strict 已忽略可选 chief_engineer 阶段")
        elif self.run_architect_stage and not self.require_architect_stage:
            architect_ready = self._is_role_probe_ready(role_map.get("architect") or {})
            if not architect_ready:
                self.run_architect_stage = False
                messages.append("  ⚠️ architect 未就绪，按可选策略自动降级为 PM 起跑")

        if self.run_chief_engineer_stage and not self.require_chief_engineer_stage:
            chief_ready = self._is_role_probe_ready(role_map.get("chief_engineer") or {})
            if not chief_ready:
                self.run_chief_engineer_stage = False
                messages.append("  ⚠️ chief_engineer 未就绪，按可选策略自动跳过Chief Engineer阶段")

        if not messages:
            messages.append("  ✅ 链路角色策略检查通过")
        return True, messages

    async def _abort_run(self, code: int) -> int:
        """带报告地提前终止。"""
        self.end_time = datetime.now().isoformat()
        self._record_audit_timeline_event(
            event="run_aborted",
            status="failed",
            detail="Run aborted with report generation",
            refs={"exit_code": code, "abort_reason": self.abort_reason or {}},
        )
        print("\n## 提前终止并生成报告")
        await self._generate_reports()
        return code

    def _select_projects(self) -> list[ProjectDefinition]:
        """选择压测项目"""
        # 过滤类别
        pool = PROJECT_POOL
        if self.categories:
            pool = [p for p in pool if p.category in self.categories]
        if self.execution_mode == "project_serial":
            if not pool:
                return []
            ordered_unique = select_stress_rounds(
                total_rounds=len(pool),
                strategy=self.strategy,
                pool=pool,
            )
            project_count = min(max(self.rounds, 0), len(ordered_unique))
            return ordered_unique[:project_count]
        return select_stress_rounds(
            total_rounds=self.rounds,
            strategy=self.strategy,
            pool=pool,
        )

    async def _run_round_robin(self, engine: StressEngine, selected_projects: list[ProjectDefinition]) -> None:
        round_index = max(len(self.results), self.resume_from - 1)
        batch_number = 0
        for project in selected_projects:
            if self._stop_requested:
                return
            round_index += 1
            result = await engine.run_round(round_index, project)
            self.results.append(result)
            project_workspace = str((result.workspace_artifacts or {}).get("workspace") or "").strip()
            if project_workspace:
                print(f"[round {round_index}] Project workspace: {project_workspace}")
            await self._save_intermediate_results()
            if result.overall_result == "FAIL":
                print(f"\n⚠️ Round #{round_index} 失败，记录失败分析...")
                await self._analyze_failure(result)
                self._record_failed_project(
                    project_name=project.name,
                    round_index=round_index,
                )
                if self._stop_requested:
                    return

            # === 任务 D2: 每批结束后自动执行审计 ===
            current_batch = round_index // self.round_batch_limit
            if self.post_batch_audit and round_index % self.round_batch_limit == 0 and current_batch > batch_number:
                batch_number = current_batch
                await self._run_batch_audit_and_pause(engine, batch_number)

    async def _run_project_serial(self, engine: StressEngine, selected_projects: list[ProjectDefinition]) -> None:
        round_index = max(len(self.results), self.resume_from - 1)
        total_projects = len(selected_projects)
        batch_number = 0
        for project_number, project in enumerate(selected_projects, 1):
            if self._stop_requested:
                return
            print(
                f"\n[project {project_number}/{total_projects}] {project.name} "
                f"(max_attempts={self.attempts_per_project})"
            )
            project_result_start_index = len(self.results)
            project_passed = False
            retry_guidance = ""
            retry_start_from = "architect" if self.run_architect_stage else "pm"
            architect_stage_ready = False
            pm_stage_ready = False
            for attempt in range(1, self.attempts_per_project + 1):
                round_index += 1
                print(
                    f"[project {project_number}] attempt {attempt}/{self.attempts_per_project} "
                    f"(start_from={retry_start_from})"
                )
                result = await engine.run_round(
                    round_index,
                    project,
                    remediation_notes=retry_guidance,
                    start_from_override=retry_start_from,
                )
                if isinstance(result.workspace_artifacts, dict):
                    result.workspace_artifacts["project_attempt"] = attempt
                    result.workspace_artifacts["project_attempt_budget"] = self.attempts_per_project
                    result.workspace_artifacts["project_index"] = project_number
                if result.architect_stage and result.architect_stage.result == StageResult.SUCCESS:
                    architect_stage_ready = True
                if result.pm_stage and result.pm_stage.result == StageResult.SUCCESS:
                    pm_stage_ready = True
                self.results.append(result)
                project_workspace = str((result.workspace_artifacts or {}).get("workspace") or "").strip()
                if project_workspace:
                    print(f"[round {round_index}] Project workspace: {project_workspace}")
                await self._save_intermediate_results()
                if result.overall_result == "FAIL":
                    print(f"\n⚠️ Round #{round_index} 失败，记录失败分析...")
                    await self._analyze_failure(result)
                    retry_guidance = self._build_retry_guidance(result)
                    retry_start_from = self._select_retry_start_from(
                        result,
                        architect_ready=architect_stage_ready,
                        pm_ready=pm_stage_ready,
                    )
                    print(f"[project {project_number}] retry strategy: next_start_from={retry_start_from}")
                    continue
                project_passed = True
                print(f"[project {project_number}] ✅ converged at attempt {attempt}")
                break

            project_attempt_results = self.results[project_result_start_index:]
            if project_attempt_results:
                representative = project_attempt_results[-1]
                if isinstance(representative.workspace_artifacts, dict):
                    representative.workspace_artifacts["attempt_count"] = len(project_attempt_results)
                    representative.workspace_artifacts["attempt_converged"] = bool(project_passed)
                    representative.workspace_artifacts["attempt_history"] = [
                        {
                            "round_number": int(item.round_number),
                            "entry_stage": str(getattr(item, "entry_stage", "") or ""),
                            "overall_result": str(item.overall_result or ""),
                            "failure_point": str(item.failure_point or ""),
                        }
                        for item in project_attempt_results
                    ]
                # project_serial 的统计按“项目收敛结果”计，不把中间 attempt 计入最终轮次。
                self.results = [*self.results[:project_result_start_index], representative]
                await self._save_intermediate_results()

            if not project_passed:
                print(f"[project {project_number}] ❌ did not converge within {self.attempts_per_project} attempt(s)")
                representative_round = self.results[-1].round_number if self.results else round_index
                self._record_failed_project(
                    project_name=project.name,
                    round_index=representative_round,
                )
                if self._stop_requested:
                    return

            # === 任务 D2: 每批结束后自动执行审计 ===
            committed_rounds = len(self.results)
            if (
                self.post_batch_audit
                and self.round_batch_limit > 0
                and committed_rounds > 0
                and committed_rounds % self.round_batch_limit == 0
            ):
                batch_number += 1
                await self._run_batch_audit_and_pause(engine, batch_number)

            if not self.post_batch_audit and committed_rounds > 0:
                self._last_audited_round_count = committed_rounds

    async def _load_previous_results(self):
        """加载之前的压测结果"""
        results_path = self._ensure_output_dir() / "stress_results.json"
        if not results_path.exists():
            return
        try:
            data = json.loads(results_path.read_text(encoding="utf-8"))
            results_data = data.get("results", [])

            from ..project_pool import get_project_by_id

            loaded_results: list[RoundResult] = []
            for r_data in results_data:
                # 查找项目定义
                project_id = r_data.get("project", {}).get("id")
                project = get_project_by_id(project_id) if project_id else None
                if project:
                    loaded_results.append(RoundResult.from_dict(r_data, project=project))

            async with self._results_lock:
                self.results = loaded_results
            self._failed_project_count = self._count_failed_projects_from_results(loaded_results)
            print(f"已加载 {len(self.results)} 轮之前的结果: {results_path}")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ 加载之前结果失败: {e}")

    def _count_failed_projects_from_results(self, results: list[RoundResult]) -> int:
        if not results:
            return 0
        if self.execution_mode == "round_robin":
            return sum(1 for r in results if r.overall_result == "FAIL")

        project_states: dict[str, dict[str, Any]] = {}
        for result in results:
            project_id = str((result.project.id if result.project else "") or "").strip()
            if not project_id:
                continue
            state = project_states.setdefault(
                project_id,
                {"has_pass": False, "max_attempt": 0, "budget": self.attempts_per_project},
            )
            if result.overall_result == "PASS":
                state["has_pass"] = True
            if isinstance(result.workspace_artifacts, dict):
                attempt = int(result.workspace_artifacts.get("project_attempt") or 0)
                budget = int(result.workspace_artifacts.get("project_attempt_budget") or 0)
                if attempt > state["max_attempt"]:
                    state["max_attempt"] = attempt
                if budget > 0:
                    state["budget"] = budget
            else:
                state["max_attempt"] = max(state["max_attempt"], 1)

        failed = 0
        for state in project_states.values():
            if state["has_pass"]:
                continue
            if state["max_attempt"] >= state["budget"]:
                failed += 1
        return failed

    async def _save_intermediate_results(self):
        """保存中间结果"""
        output_dir = self._ensure_output_dir()
        async with self._results_lock:
            data = {
                "start_time": self.start_time,
                "last_update": datetime.now().isoformat(),
                "completed_rounds": len(self.results),
                "results": [r.to_dict() for r in self.results],
            }
            results_path = output_dir / "stress_results.json"
            self._write_json_atomic(results_path, data)
        self._record_audit_timeline_event(
            event="intermediate_results_saved",
            detail="Saved stress_results checkpoint",
            refs={"completed_rounds": len(self.results), "path": str(results_path)},
        )
        self._write_audit_checkpoint(phase="intermediate_results_saved")

    def _record_failed_project(self, *, project_name: str, round_index: int) -> None:
        self._failed_project_count += 1
        if self.max_failed_projects <= 0:
            return
        if self._failed_project_count < self.max_failed_projects:
            return

        self.abort_reason = {
            "category": "failure_threshold_reached",
            "summary": (f"Failed project threshold reached: {self._failed_project_count}/{self.max_failed_projects}"),
            "detail": (
                f"latest_project={project_name}; "
                f"round_index={round_index}; "
                f"failed_projects={self._failed_project_count}"
            ),
        }
        print("\n⛔ 失败项目数量触达阈值，暂停压测并等待修复。")
        self._stop_requested = True
        self._early_exit_code = 2
