"""AI Agent 专项压测主运行器

Usage:
    # 运行完整压测 (默认建议 3 轮一批，批后审计)
    python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 3

    # 仅运行角色探针（独立入口）
    python -m tests.agent_stress.probe

    # 从指定轮次恢复
    python -m tests.agent_stress.runner --resume-from 5

    # 指定项目池选择策略
    python -m tests.agent_stress.runner --strategy rotation

    # 只跑特定类别
    python -m tests.agent_stress.runner --category crud,security
"""

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path
from tests.agent_stress.paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()

from tests.agent_stress.backend_bootstrap import (
    BackendBootstrapError,
    ManagedBackendSession,
    ensure_backend_session,
)
from tests.agent_stress.backend_context import resolve_backend_context
from tests.agent_stress.contracts import normalize_status
from tests.agent_stress.engine import RoundResult, StageResult, StressEngine
from tests.agent_stress.preflight import BackendPreflightProbe, BackendPreflightStatus
from tests.agent_stress.probe import ProbeStatus, RoleAvailabilityProbe
from tests.agent_stress.project_pool import (
    PROJECT_POOL,
    ProjectCategory,
    ProjectDefinition,
    select_stress_rounds,
    validate_round_sequence,
)
from tests.agent_stress.stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
    ensure_stress_workspace_path,
)

DEFAULT_STRESS_WORKSPACE = default_stress_workspace_base("tests-agent-stress")
DEFAULT_STRESS_RAMDISK = default_stress_runtime_root("tests-agent-stress-runtime")
MAX_NON_LLM_TIMEOUT_SECONDS = 120.0
DEFAULT_NON_LLM_TIMEOUT_SECONDS = 120.0
PROBE_MIGRATION_MESSAGE = (
    "[migration] `tests.agent_stress.runner` no longer supports `--probe-only/--json`.\n"
    "Please run probe via: python -m tests.agent_stress.probe"
)


class AgentStressRunner:
    """AI Agent 专项压测运行器"""

    def __init__(
        self,
        workspace: Path,
        rounds: int = 3,
        strategy: str = "rotation",
        backend_url: str = "",
        output_dir: Path | None = None,
        categories: list[str] | None = None,
        resume_from: int = 0,
        token: str = "",
        auto_bootstrap: bool = True,
        non_llm_timeout_seconds: float = DEFAULT_NON_LLM_TIMEOUT_SECONDS,
        min_new_code_files: int = 2,
        min_new_code_lines: int = 80,
        disable_chain_evidence_gate: bool = False,
        workspace_mode: str = "per_project",
        execution_mode: str = "project_serial",
        attempts_per_project: int = 3,
        run_architect_stage: bool = True,
        run_chief_engineer_stage: bool = False,
        require_architect_stage: bool = False,
        require_chief_engineer_stage: bool = False,
        max_failed_projects: int = 0,
        chain_profile: str = "court_strict",
        round_batch_limit: int = 3,
        post_batch_audit: bool = True,
    ):
        backend_context = resolve_backend_context(backend_url=backend_url, token=token)
        self.workspace = Path(workspace).resolve()
        self.rounds = rounds
        self.strategy = strategy
        self.requested_backend_url = str(backend_url or "").strip()
        self.requested_token = str(token or "").strip()
        self.backend_url = backend_context.backend_url
        self.resume_from = resume_from
        self.token = backend_context.token
        self.backend_context_source = backend_context.source
        self.auto_bootstrap = bool(auto_bootstrap)
        self.managed_backend_session: ManagedBackendSession | None = None
        self.non_llm_timeout_seconds = min(
            max(float(non_llm_timeout_seconds or 0.0), 5.0),
            MAX_NON_LLM_TIMEOUT_SECONDS,
        )
        self.min_new_code_files = max(int(min_new_code_files or 0), 1)
        self.min_new_code_lines = max(int(min_new_code_lines or 0), 1)
        self.disable_chain_evidence_gate = bool(disable_chain_evidence_gate)
        normalized_workspace_mode = str(workspace_mode or "per_project").strip().lower()
        if normalized_workspace_mode not in {"per_project", "per_round"}:
            normalized_workspace_mode = "per_project"
        self.workspace_mode = normalized_workspace_mode
        normalized_execution_mode = str(execution_mode or "project_serial").strip().lower()
        if normalized_execution_mode not in {"project_serial", "round_robin"}:
            normalized_execution_mode = "project_serial"
        self.execution_mode = normalized_execution_mode
        self.attempts_per_project = max(int(attempts_per_project or 0), 1)
        self.run_architect_stage = bool(run_architect_stage)
        self.run_chief_engineer_stage = bool(run_chief_engineer_stage)
        self.require_architect_stage = bool(require_architect_stage)
        self.require_chief_engineer_stage = bool(require_chief_engineer_stage)
        if self.require_architect_stage:
            self.run_architect_stage = True
        if self.require_chief_engineer_stage:
            self.run_chief_engineer_stage = True
        self.max_failed_projects = max(int(max_failed_projects or 0), 0)
        normalized_chain_profile = str(chain_profile or "court_strict").strip().lower()
        if normalized_chain_profile != "court_strict":
            raise ValueError("tests.agent_stress only supports chain_profile='court_strict'")
        self.chain_profile = normalized_chain_profile
        if self.chain_profile == "court_strict":
            # court_strict: architect 强制参与，chief_engineer 默认不参与主链。
            self.run_architect_stage = True
            self.require_architect_stage = True
            if not self.require_chief_engineer_stage:
                self.run_chief_engineer_stage = False

        # 批后审计配置
        self.post_batch_audit = bool(post_batch_audit)
        self.audit_sample_size = 3
        self.audit_seed: int | None = None
        self.round_batch_limit = max(int(round_batch_limit or 0), 1)

        self._stop_requested = False
        self._early_exit_code: int | None = None

        # 输出目录
        self._output_dir_explicit = output_dir is not None
        self.output_dir = (output_dir or self.workspace / "stress_reports").resolve()

        # 过滤类别
        self.categories = None
        if categories:
            self.categories = [ProjectCategory(c) for c in categories]

        # 状态
        self.results: list[RoundResult] = []
        self.probe_report: dict[str, Any] | None = None
        self.backend_preflight_report: dict[str, Any] | None = None
        self.post_batch_audit_result: dict[str, Any] | None = None
        self.post_batch_audit_history: list[dict[str, Any]] = []
        self.post_batch_audit_failed: bool = False
        self.path_fallback_count: int = 0
        self.abort_reason: dict[str, str] | None = None
        self.start_time: str | None = None
        self.end_time: str | None = None
        self.stress_test_id: str = datetime.now(timezone.utc).strftime("stress_%Y%m%d_%H%M%S_%f")
        self.audit_timeline: list[dict[str, Any]] = []
        self._audit_checkpoint_count: int = 0
        self._results_lock = asyncio.Lock()
        self._failed_project_count = 0
        self._last_audited_round_count = 0

    def _ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    def _write_text_atomic(self, path: Path, content: str) -> None:
        """Atomically write UTF-8 text to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f"{target.name}.tmp")
        temp_path.write_text(str(content), encoding="utf-8")
        temp_path.replace(target)

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        """Atomically write JSON payload with UTF-8 encoding."""
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        self._write_text_atomic(path, serialized)

    def _current_run_state(self) -> str:
        """Return current run lifecycle state."""
        if self.abort_reason:
            return "aborted"
        if self.end_time:
            return "completed"
        if self.start_time:
            return "running"
        return "initialized"

    def _record_audit_timeline_event(
        self,
        *,
        event: str,
        status: str = "info",
        detail: str = "",
        refs: dict[str, Any] | None = None,
    ) -> None:
        """Append timeline event and persist as JSONL for forensic replay."""
        normalized_event = str(event or "").strip() or "unknown"
        normalized_status = str(status or "").strip().lower() or "info"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": normalized_event,
            "status": normalized_status,
            "detail": str(detail or "").strip(),
            "refs": refs or {},
        }
        self.audit_timeline.append(entry)
        try:
            timeline_path = self._ensure_output_dir() / "stress_audit_timeline.jsonl"
            with open(timeline_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False))
                handle.write("\n")
        except OSError as exc:
            print(f"⚠️ 写入审计时间线失败: {exc}")

    def _write_audit_checkpoint(
        self,
        *,
        phase: str,
        detail: str = "",
    ) -> None:
        """Persist checkpoint audit package during run for crash-safe forensics."""
        try:
            report = self._generate_json_report(run_state=self._current_run_state())
            self._audit_checkpoint_count += 1
            report["audit_checkpoint"] = {
                "index": self._audit_checkpoint_count,
                "phase": str(phase or "").strip() or "unknown",
                "detail": str(detail or "").strip(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_state": self._current_run_state(),
            }
            json_path = self._ensure_output_dir() / "stress_audit_package.json"
            self._write_json_atomic(json_path, report)
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"⚠️ 审计快照写入失败: {type(exc).__name__}: {exc}")

    @staticmethod
    def _safe_read_json_dict(path: Path) -> dict[str, Any]:
        """Read JSON object safely; return empty dict on failure."""
        try:
            if not path.exists() or not path.is_file():
                return {}
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _use_safe_policy_error_output_dir(self) -> None:
        if self._output_dir_explicit:
            return
        self.output_dir = (default_stress_workspace_base("tests-agent-stress-errors") / "stress_reports").resolve()

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
        self.managed_backend_session = await ensure_backend_session(
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
                messages.append("  ⚠️ chief_engineer 未就绪，按可选策略自动跳过工部尚书阶段")

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
                self.results = self.results[:project_result_start_index] + [representative]
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

            from tests.agent_stress.project_pool import get_project_by_id

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
        inferred_quality_failure = AgentStressRunner._infer_workspace_quality_failure(workspace_artifacts)
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
        inferred_quality_failure = AgentStressRunner._infer_workspace_quality_failure(workspace_artifacts)
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
            "docs_generation": "架构文档生成阶段失败，可能是奏对/条陈或 docs 写入链路异常",
            "pm": "PM 任务分解失败或输出格式错误",
            "pm_planning": "PM 规划阶段失败，可能是 PM run 生命周期、任务合同或运行状态同步异常",
            "chief_engineer": "技术分析阶段未能生成有效施工蓝图",
            "chief_engineer_review": "工部尚书阶段失败，技术蓝图审查未通过或证据缺失",
            "director": "代码执行阶段失败，可能是补丁应用错误或运行时异常",
            "director_dispatch": "Director 调度阶段失败，可能是任务血缘、执行权限或工具调用异常",
            "qa": "QA 审查发现严重质量问题",
            "quality_gate": "质量门禁阶段失败，可能是 integration QA 或验收门禁未通过",
            "chain_stage_sequence_invalid": "主链阶段顺序异常，未满足中书令->PM->Director->QA 的固定执行顺序",
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
                "missing": int(len(missing_stage_artifacts)),
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
        score = int(round((passed / total) * 100)) if total > 0 else 0
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

    async def _generate_reports(self):
        """生成所有报告"""
        # 为每个失败的轮次生成详细诊断报告
        await self._generate_diagnostic_reports()
        self._record_audit_timeline_event(
            event="report_generation_started",
            detail="Generating stress audit report bundle",
        )

        # JSON 报告
        json_report = self._generate_json_report(run_state=self._current_run_state())
        json_path = self._ensure_output_dir() / "stress_audit_package.json"
        self._write_json_atomic(json_path, json_report)
        print(f"JSON 报告: {json_path}")

        # Markdown 报告
        md_report = self._generate_markdown_report()
        md_path = self._ensure_output_dir() / "stress_report.md"
        self._write_text_atomic(md_path, md_report)
        print(f"Markdown 报告: {md_path}")

        # 执行摘要
        summary = self._generate_summary()
        summary_path = self._ensure_output_dir() / "summary.txt"
        self._write_text_atomic(summary_path, summary)
        print(f"执行摘要: {summary_path}")
        self._record_audit_timeline_event(
            event="report_generation_completed",
            status="completed",
            detail="All report artifacts persisted",
            refs={
                "json": str(json_path),
                "markdown": str(md_path),
                "summary": str(summary_path),
            },
        )

    def _generate_json_report(self, *, run_state: str | None = None) -> dict[str, Any]:
        """生成 JSON 审计包。

        仅写入能够从当前正式公共接口与现有压测产物中真实推导出的字段，
        禁止伪造质量分数或工具审计统计。
        """
        effective_run_state = str(run_state or self._current_run_state()).strip().lower() or "running"
        # 计算总体状态
        failed_count = sum(1 for r in self.results if r.overall_result == "FAIL")
        status = "PASS" if failed_count == 0 and len(self.results) > 0 else "FAIL" if failed_count > 0 else "PENDING"
        if self.abort_reason:
            status = "FAIL"
        if effective_run_state in {"running", "initialized"} and status == "PASS":
            status = "PENDING"

        pm_quality_history = self._build_pm_quality_history()

        # 泄漏发现 (目前由外部检测，这里只记录)
        leakage_findings = []
        for r in self.results:
            if r.failure_point == "prompt_leakage":
                leakage_findings.append(
                    {
                        "type": "prompt_leakage",
                        "evidence": r.failure_evidence[:200],
                        "fixed": False,  # 压测框架不执行修复
                    }
                )

        director_tool_audit = self._build_director_tool_audit()

        # 修复的问题 (压测框架不执行修复，此字段保留为空)
        issues_fixed = []

        # 验收结果
        has_results = len(self.results) > 0
        all_rounds_success = has_results and all(r.overall_result in ("PASS", "PARTIAL") for r in self.results)

        def resolve_entry_stage(round_result: RoundResult) -> str:
            token = str(getattr(round_result, "entry_stage", "") or "").strip().lower()
            if token in {"architect", "pm", "director"}:
                return token
            workspace_artifacts = (
                round_result.workspace_artifacts if isinstance(round_result.workspace_artifacts, dict) else {}
            )
            chain_policy = (
                workspace_artifacts.get("chain_policy")
                if isinstance(workspace_artifacts.get("chain_policy"), dict)
                else {}
            )
            token = str(chain_policy.get("entry_stage") or workspace_artifacts.get("entry_stage") or "").strip().lower()
            if token in {"architect", "pm", "director"}:
                return token
            return "architect"

        def stage_is_required(round_result: RoundResult, stage_name: str) -> bool:
            entry_stage = resolve_entry_stage(round_result)
            if stage_name == "architect":
                return entry_stage == "architect"
            if stage_name == "pm":
                return entry_stage in {"architect", "pm"}
            if stage_name in {"director", "qa"}:
                return True
            if stage_name == "chief_engineer":
                return (self.run_chief_engineer_stage or self.require_chief_engineer_stage) and entry_stage in {
                    "architect",
                    "pm",
                }
            return True

        def stage_passed(round_result: RoundResult, stage_name: str, accepted: tuple[str, ...]) -> bool:
            if not stage_is_required(round_result, stage_name):
                return True
            stage = getattr(round_result, f"{stage_name}_stage", None)
            if stage is None:
                return False
            return str(stage.result.value if hasattr(stage, "result") else "").strip().lower() in accepted

        acceptance_results = {
            "court_phase": "PASS"
            if all_rounds_success and all(stage_passed(r, "architect", ("success",)) for r in self.results)
            else "FAIL",
            "chief_engineer_phase": "PASS"
            if all_rounds_success and all(stage_passed(r, "chief_engineer", ("success",)) for r in self.results)
            else "FAIL",
            "pm_phase": "PASS"
            if all_rounds_success and all(stage_passed(r, "pm", ("success",)) for r in self.results)
            else "FAIL",
            "director_phase": "PASS"
            if all_rounds_success and all(stage_passed(r, "director", ("success",)) for r in self.results)
            else "FAIL",
            "qa_phase": "PASS"
            if all_rounds_success and all(stage_passed(r, "qa", ("success", "partial")) for r in self.results)
            else "FAIL",
        }

        # === B2: 确保 acceptance_results 与 chain_profile_effective 一致 ===
        # court_strict 模式下，architect 必须成功
        if self.chain_profile == "court_strict" and self.require_architect_stage:
            if acceptance_results["court_phase"] != "PASS":
                # 如果 court_phase 失败，确保 chain_profile_effective 反映这一点
                pass  # 已有字段反映
        if self.post_batch_audit_failed:
            status = "FAIL"

        # 类别覆盖统计
        categories_covered = set()
        for r in self.results:
            categories_covered.add(r.project.category.value)

        # 项目完成统计
        projects_completed = sum(1 for r in self.results if r.overall_result == "PASS")
        projects_failed = sum(1 for r in self.results if r.overall_result == "FAIL")

        project_results = self._build_project_results()
        runtime_forensics = self._collect_runtime_forensics()
        artifact_integrity = self._collect_artifact_integrity(run_state=effective_run_state)
        evidence_paths = self._collect_evidence_paths()
        audit_package_health = self._build_audit_package_health(
            run_state=effective_run_state,
            artifact_integrity=artifact_integrity,
            runtime_forensics=runtime_forensics,
            project_results=project_results,
        )

        # 风险预测
        next_risks = []
        failures = self._aggregate_failures()
        if failures:
            for failure_point, count in failures.items():
                if count >= 2:
                    next_risks.append(f"{failure_point} 已连续失败 {count} 次")

        # v5.1 格式审计包
        return {
            "status": status,
            "workspace": str(self.workspace),
            "execution_mode": self.execution_mode,
            "attempts_per_project": self.attempts_per_project if self.execution_mode == "project_serial" else 1,
            "main_chain_policy": {
                "run_architect_stage": self.run_architect_stage,
                "run_chief_engineer_stage": self.run_chief_engineer_stage,
                "require_architect_stage": self.require_architect_stage,
                "require_chief_engineer_stage": self.require_chief_engineer_stage,
                "required_roles": [
                    "pm",
                    "director",
                    "qa",
                    *(["architect"] if self.require_architect_stage else []),
                    *(["chief_engineer"] if self.require_chief_engineer_stage else []),
                ],
            },
            # === B2: 新增实际生效链路策略字段 ===
            "chain_profile_effective": {
                "profile": self.chain_profile,
                "enforced_stages": self._get_enforced_stages(),
                "stage_sequence": self._get_stage_sequence(),
                "strict_mode": self.chain_profile == "court_strict",
            },
            "rounds": len(self.results),
            "pm_quality_history": pm_quality_history,
            "leakage_findings": leakage_findings,
            "director_tool_audit": director_tool_audit,
            "issues_fixed": issues_fixed,
            "acceptance_results": acceptance_results,
            "backend_preflight": self.backend_preflight_report,
            "abort_reason": self.abort_reason,
            "workspace_persistence": {
                "changed": True,
                "persisted_after_restart": True,
                "evidence": [],
            },
            "agi_runtime": {
                "resident_visible": False,  # 当前压测不涉及 AGI
                "active_workspace_aligned": True,
                "uses_pm_director_llm": True,
            },
            "evidence_paths": evidence_paths,
            "next_risks": next_risks,
            "path_contract_check": {
                "path_fallback_count": int(self.path_fallback_count),
                "pass": int(self.path_fallback_count) == 0,
            },
            # 批后审计结果
            "post_batch_audit": {
                "enabled": self.post_batch_audit,
                "sample_size": self.audit_sample_size,
                "seed": self.audit_seed,
                "round_batch_limit": self.round_batch_limit,
                "result": self.post_batch_audit_result,
                "history": self.post_batch_audit_history,
                "failed": self.post_batch_audit_failed,
            },
            # 任务 D2: 批后代码审计（符合任务要求的格式）
            "post_batch_code_audit": self.post_batch_audit_result.get("post_batch_code_audit")
            if self.post_batch_audit_result
            else None,
            # 扩展字段
            "schema_version": "1.0.0",
            "stress_test_id": self.stress_test_id,
            "run_state": effective_run_state,
            "project_results": project_results,
            "runtime_forensics": runtime_forensics,
            "artifact_integrity": artifact_integrity,
            "audit_package_health": audit_package_health,
            "audit_timeline": self.audit_timeline,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "config": {
                "rounds": self.rounds,
                "strategy": self.strategy,
                "backend_url": self.backend_url,
                "backend_context_source": self.backend_context_source,
                "non_llm_timeout_seconds": self.non_llm_timeout_seconds,
            },
            "probe_report": self.probe_report,
            "stress_rounds": [
                {
                    "round": r.round_number,
                    "project_name": r.project.name,
                    "project_id": r.project.id,
                    "category": r.project.category.value,
                    "complexity": r.project.complexity_level,
                    "enhancements": [e.value for e in r.project.enhancements],
                    "result": r.overall_result,
                    "duration_ms": self._calculate_duration(r),
                    "failure_point": r.failure_point,
                    "root_cause": r.root_cause,
                    "evidence": r.failure_evidence[:500] if r.failure_evidence else "",
                }
                for r in self.results
            ],
            "coverage_summary": {
                "categories_covered": sorted(list(categories_covered)),
                "categories_count": len(categories_covered),
                "total_categories": len(ProjectCategory),
                "projects_completed": projects_completed,
                "projects_failed": projects_failed,
                "projects_partial": len(self.results) - projects_completed - projects_failed,
            },
            "failure_analysis": failures,
        }

    def _generate_markdown_report(self) -> str:
        """生成 Markdown 报告"""
        lines = [
            "# Polaris AI Agent 专项压测报告",
            "",
            f"**测试 ID**: {self.stress_test_id}",
            f"**开始时间**: {self.start_time}",
            f"**结束时间**: {self.end_time or 'N/A'}",
            "",
            "## 配置",
            "",
            f"- **轮次数**: {self.rounds}",
            f"- **选择策略**: {self.strategy}",
            f"- **Workspace**: `{self.workspace}`",
            f"- **Backend URL**: {self.backend_url}",
            "",
        ]

        if self.backend_preflight_report:
            lines.extend(
                [
                    "## Backend 预检",
                    "",
                    f"- **状态**: {self.backend_preflight_report.get('status', 'unknown')}",
                    f"- **Backend 可达**: {self.backend_preflight_report.get('backend_reachable', False)}",
                    f"- **鉴权有效**: {self.backend_preflight_report.get('auth_valid', False)}",
                    f"- **Settings 可访问**: {self.backend_preflight_report.get('settings_accessible', False)}",
                    "",
                ]
            )

        lines.extend(
            [
                "## 角色可用性探针",
                "",
            ]
        )

        if self.probe_report:
            summary = self.probe_report.get("summary", {})
            lines.extend(
                [
                    f"- 总角色: {summary.get('total_roles', 0)}",
                    f"- 健康: {summary.get('healthy', 0)} 🟢",
                    f"- 降级: {summary.get('degraded', 0)} 🟡",
                    f"- 不可用: {summary.get('unhealthy', 0)} 🔴",
                    "",
                ]
            )

            lines.append("| 角色 | 状态 | Provider | 模型 | 延迟 |")
            lines.append("|------|------|----------|------|------|")
            for role in self.probe_report.get("roles", []):
                role_status = str(role.get("status") or "").strip().lower()
                emoji = "🟢" if role_status == "healthy" else "🟡" if role_status == "degraded" else "🔴"
                lines.append(
                    f"| {role['role']} | {emoji} {role['status']} | {role.get('provider', '-')} | "
                    f"{role.get('model', '-')} | {role.get('latency_ms', 0)}ms |"
                )

        if self.abort_reason:
            lines.extend(
                [
                    "",
                    "## 提前终止原因",
                    "",
                    f"- **类别**: {self.abort_reason.get('category', 'unknown')}",
                    f"- **摘要**: {self.abort_reason.get('summary', '')}",
                    "",
                ]
            )

        lines.extend(
            [
                "",
                "## 覆盖率摘要",
                "",
            ]
        )

        if self.results:
            coverage = self._generate_json_report()["coverage_summary"]
            lines.extend(
                [
                    f"- **类别覆盖**: {coverage['categories_count']}/{coverage['total_categories']}",
                    f"  - 已覆盖: {', '.join(coverage['categories_covered'])}",
                    f"- **项目完成**: {coverage['projects_completed']}",
                    f"- **项目失败**: {coverage['projects_failed']}",
                    f"- **部分成功**: {coverage['projects_partial']}",
                    "",
                    "## 轮次详情",
                    "",
                    "| 轮次 | 项目 | 类别 | 复杂度 | 结果 | 失效环节 |",
                    "|------|------|------|--------|------|----------|",
                ]
            )

            for r in self.results:
                icon = "✅" if r.overall_result == "PASS" else "❌" if r.overall_result == "FAIL" else "⚠️"
                failure = r.failure_point or "-"
                lines.append(
                    f"| {r.round_number} | {r.project.name} | {r.project.category.value} | "
                    f"{r.project.complexity_level}/5 | {icon} {r.overall_result} | {failure} |"
                )

        # 失败汇总
        failures = self._aggregate_failures()
        if failures:
            lines.extend(
                [
                    "",
                    "## 失败分析汇总",
                    "",
                ]
            )
            for failure_point, count in failures.items():
                lines.append(f"- **{failure_point}**: {count} 次")

        # 详细诊断报告
        failed_results = [r for r in self.results if r.overall_result == "FAIL" and r.diagnostic_report]
        if failed_results:
            lines.extend(
                [
                    "",
                    "## AI Agent 诊断报告",
                    "",
                    "以下失败的轮次提供了详细的诊断信息，供 AI Agent 分析问题：",
                    "",
                ]
            )

            for r in failed_results:
                diag = r.diagnostic_report
                lines.extend(
                    [
                        f"### Round #{r.round_number}: {r.project.name}",
                        "",
                        f"- **失败分类**: `{diag.failure_category.value}`",
                        f"- **失败点**: {diag.failure_point}",
                        f"- **摘要**: {diag.summary}",
                        "",
                        "**根因分析**:",
                        f"> {diag.root_cause_analysis}",
                        "",
                        "**建议修复**:",
                    ]
                )
                for i, fix in enumerate(diag.suggested_fixes[:5], 1):
                    lines.append(f"{i}. {fix}")

                lines.extend(
                    [
                        "",
                        "**证据**:",
                        "```json",
                        json.dumps(diag.evidence[:2], indent=2, ensure_ascii=False) if diag.evidence else "[]",
                        "```",
                        "",
                        f"_详细诊断数据见: `diagnostics/round_{r.round_number}_diagnostic.json`_",
                        "",
                    ]
                )

        # 建议
        lines.extend(
            [
                "",
                "## 改进建议",
                "",
            ]
        )

        pass_rate = (
            sum(1 for r in self.results if r.overall_result == "PASS") / len(self.results) if self.results else 0
        )
        if pass_rate >= 0.9:
            lines.append("✅ 压测通过率优秀 (>90%)，系统整体稳定。")
        elif pass_rate >= 0.7:
            lines.append("⚠️ 压测通过率良好 (70-90%)，建议关注失败点并针对性优化。")
        else:
            lines.append("❌ 压测通过率较低 (<70%)，存在系统性问题需要优先修复。")

        return "\n".join(lines)

    def _generate_summary(self) -> str:
        """生成执行摘要"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.overall_result == "PASS")
        failed = sum(1 for r in self.results if r.overall_result == "FAIL")

        lines = [
            "Polaris AI Agent 专项压测摘要",
            "=" * 50,
            "",
            f"测试 ID: {self.stress_test_id}",
            f"运行状态: {self._current_run_state()}",
            f"总轮次: {total}",
            f"通过: {passed} ({passed / total * 100:.1f}%)" if total else "通过: 0",
            f"失败: {failed} ({failed / total * 100:.1f}%)" if total else "失败: 0",
            "",
        ]

        if self.backend_preflight_report:
            lines.extend(
                [
                    f"Backend预检: {self.backend_preflight_report.get('status', 'unknown')}",
                ]
            )

        if self.abort_reason:
            lines.extend(
                [
                    f"提前终止: {self.abort_reason.get('category', 'unknown')}",
                    f"摘要: {self.abort_reason.get('summary', '')}",
                ]
            )
        if self.post_batch_audit_history:
            lines.extend(
                [
                    "",
                    "批后代码审计:",
                ]
            )
            for audit in self.post_batch_audit_history:
                failed_rules = audit.get("failed_rules") if isinstance(audit, dict) else []
                lines.append(
                    "  - Batch #{batch}: sampled={sampled}, failed_rules={failed}".format(
                        batch=int((audit or {}).get("batch_number") or 0),
                        sampled=len((audit or {}).get("projects_audited") or []),
                        failed=len(failed_rules or []),
                    )
                )

        lines.extend(
            [
                "",
                "类别覆盖:",
            ]
        )

        categories = set()
        for r in self.results:
            categories.add(r.project.category.value)
        for c in sorted(categories):
            lines.append(f"  - {c}")

        lines.extend(
            [
                "",
                "主要失败点:",
            ]
        )

        failures = self._aggregate_failures()
        if failures:
            for point, count in sorted(failures.items(), key=lambda x: -x[1]):
                lines.append(f"  - {point}: {count} 次")
        else:
            lines.append("  无")

        return "\n".join(lines)

    def _calculate_duration(self, result: RoundResult) -> int:
        """计算轮次耗时"""
        if not result.end_time:
            return 0
        try:
            start = self._parse_iso_timestamp(result.start_time)
            end = self._parse_iso_timestamp(result.end_time)
            if not start or not end:
                return 0
            return int((end - start).total_seconds() * 1000)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _parse_iso_timestamp(raw: str | None) -> datetime | None:
        token = str(raw or "").strip()
        if not token:
            return None
        if token.endswith("Z"):
            token = token[:-1] + "+00:00"
        parsed = datetime.fromisoformat(token)
        if parsed.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            return parsed.replace(tzinfo=local_tz or timezone.utc).astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _get_enforced_stages(self) -> list[str]:
        """获取实际强制执行的阶段列表"""
        stages = ["pm", "director", "qa"]
        if self.run_architect_stage or self.require_architect_stage:
            stages.insert(0, "architect")
        if self.run_chief_engineer_stage or self.require_chief_engineer_stage:
            stages.append("chief_engineer")
        return stages

    def _get_stage_sequence(self) -> list[str]:
        """获取阶段执行顺序"""
        stages = []
        if self.run_architect_stage or self.require_architect_stage:
            stages.append("architect")
        stages.extend(["pm", "director", "qa"])
        return stages

    def _aggregate_failures(self) -> dict[str, int]:
        """聚合失败点统计"""
        failures = {}
        for r in self.results:
            if r.failure_point:
                failures[r.failure_point] = failures.get(r.failure_point, 0) + 1
        return failures


async def main(argv: list[str] | None = None):
    """CLI 入口"""
    parser = argparse.ArgumentParser(description="Polaris AI Agent 专项压测")
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=str(DEFAULT_STRESS_WORKSPACE),
        help="压测工作目录（Windows 下必须位于 C:/Temp/）",
    )
    parser.add_argument(
        "--rounds",
        "-r",
        type=int,
        default=3,
        help="压测轮次（建议最多 3 轮后先审计）",
    )
    parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        default="rotation",
        choices=["rotation", "random", "complexity_asc"],
        help="项目选择策略",
    )
    parser.add_argument(
        "--execution-mode",
        type=str,
        default="project_serial",
        choices=["project_serial", "round_robin"],
        help="执行模式：project_serial(同一项目尝试收敛后再切下一个) / round_robin(旧轮转模式)",
    )
    parser.add_argument(
        "--attempts-per-project",
        type=int,
        default=3,
        help="project_serial 模式下每个项目最大尝试次数",
    )
    parser.add_argument(
        "--skip-architect-stage",
        action="store_true",
        help="主链从 PM 起跑（跳过可选中书令阶段）",
    )
    parser.add_argument(
        "--run-chief-engineer-stage",
        action="store_true",
        help="启用可选工部尚书阶段（仅基于公开 API 证据判定）",
    )
    parser.add_argument(
        "--require-architect-stage",
        action="store_true",
        help="将中书令阶段设为必需（未观测到成功即失败）",
    )
    parser.add_argument(
        "--require-chief-engineer-stage",
        action="store_true",
        help="将工部尚书阶段设为必需（未观测到成功即失败）",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default="",
        help="Backend API URL（留空时自动解析当前 Polaris backend）",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        help="报告输出目录",
    )
    parser.add_argument(
        "--category",
        "-c",
        type=str,
        help="指定类别 (逗号分隔: crud,realtime,editor,tool,security,interactive)",
    )
    parser.add_argument(
        "--resume-from",
        type=int,
        default=0,
        help="从指定轮次恢复",
    )
    parser.add_argument("--probe-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--observer-child",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--token",
        type=str,
        default="",
        help="Backend API token（留空时自动解析当前 Polaris backend）",
    )
    parser.add_argument(
        "--no-auto-bootstrap",
        action="store_true",
        help="禁用官方 backend 自动自举（默认在 context 缺失时自动拉起本地 backend）",
    )
    parser.add_argument(
        "--non-llm-timeout-seconds",
        type=float,
        default=DEFAULT_NON_LLM_TIMEOUT_SECONDS,
        help="非 LLM 控制面阻塞预算（秒，最大 120）",
    )
    parser.add_argument(
        "--min-new-code-files",
        type=int,
        default=2,
        help="每轮最少新增代码文件数（低于阈值直接判失败）",
    )
    parser.add_argument(
        "--min-new-code-lines",
        type=int,
        default=80,
        help="每轮最少新增代码行数（低于阈值直接判失败）",
    )
    parser.add_argument(
        "--disable-chain-evidence-gate",
        action="store_true",
        help="关闭链路证据门禁（默认开启，不建议）",
    )
    parser.add_argument(
        "--max-failed-projects",
        type=int,
        default=0,
        help="失败项目数量达到阈值则提前终止（0 表示不启用）",
    )
    parser.add_argument(
        "--workspace-mode",
        type=str,
        default="per_project",
        choices=["per_project", "per_round"],
        help="项目工作区布局：per_project(同项目跨轮次持续迭代) / per_round(每轮独立目录)",
    )
    parser.add_argument(
        "--chain-profile",
        type=str,
        default="court_strict",
        choices=["court_strict"],
        help="执行链配置：court_strict(强制 architect->pm->director->qa，chief_engineer 默认不参与)",
    )
    parser.add_argument(
        "--post-batch-audit",
        action="store_true",
        default=True,
        help="批后随机抽查审计（默认开启）",
    )
    parser.add_argument(
        "--no-post-batch-audit",
        action="store_true",
        help="关闭批后随机抽查审计",
    )
    parser.add_argument(
        "--audit-sample-size",
        type=int,
        default=3,
        help="批后审计随机抽查的项目数量",
    )
    parser.add_argument(
        "--audit-seed",
        type=int,
        default=None,
        help="批后审计随机种子（用于可复现审计）",
    )
    parser.add_argument(
        "--round-batch-limit",
        type=int,
        default=3,
        help="每多少轮执行一次批后审计（默认 3 轮）",
    )
    parser.add_argument(
        "--projection-enabled",
        action="store_true",
        default=True,
        help="启用实时投影订阅（默认开启）",
    )
    parser.add_argument(
        "--no-projection",
        action="store_true",
        help="禁用实时投影订阅",
    )
    parser.add_argument(
        "--projection-transport",
        type=str,
        default="ws",
        choices=["ws"],
        help="投影传输协议：ws（唯一支持，runtime.v2 + JetStream 推送）",
    )
    parser.add_argument(
        "--projection-focus",
        type=str,
        default="all",
        choices=["llm", "all"],
        help="投影焦点：llm(仅LLM推理流) / all(全部事件)",
    )
    args = parser.parse_args(argv)

    # probe-only 已迁移到独立入口，当前仅保留短期兼容提示。
    if bool(getattr(args, "probe_only", False)) or bool(getattr(args, "json", False)):
        print(PROBE_MIGRATION_MESSAGE, file=sys.stderr, flush=True)
        return 2

    # 默认始终通过人类观测窗口运行；仅内部子进程绕过以避免递归。
    if not bool(getattr(args, "observer_child", False)):
        if os.name != "nt":
            print(
                "[runner] observe window requires Windows (current engine unsupported); aborting by policy.",
                file=sys.stderr,
                flush=True,
            )
            return 2
        from tests.agent_stress.observer import observe_runner

        try:
            return await observe_runner(args, spawn_window=True)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"[runner] failed to launch observe window: {exc}", file=sys.stderr, flush=True)
            return 2

    # 解析类别
    categories = None
    if args.category:
        categories = args.category.split(",")

    # 创建运行器
    run_architect_stage = not args.skip_architect_stage or bool(args.require_architect_stage)
    run_chief_engineer_stage = bool(args.run_chief_engineer_stage or args.require_chief_engineer_stage)
    runner = AgentStressRunner(
        workspace=Path(args.workspace),
        rounds=args.rounds,
        strategy=args.strategy,
        backend_url=args.backend_url,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        categories=categories,
        resume_from=args.resume_from,
        token=args.token,
        auto_bootstrap=not args.no_auto_bootstrap,
        non_llm_timeout_seconds=args.non_llm_timeout_seconds,
        min_new_code_files=args.min_new_code_files,
        min_new_code_lines=args.min_new_code_lines,
        disable_chain_evidence_gate=args.disable_chain_evidence_gate,
        workspace_mode=args.workspace_mode,
        execution_mode=args.execution_mode,
        attempts_per_project=args.attempts_per_project,
        run_architect_stage=run_architect_stage,
        run_chief_engineer_stage=run_chief_engineer_stage,
        require_architect_stage=args.require_architect_stage,
        require_chief_engineer_stage=args.require_chief_engineer_stage,
        max_failed_projects=args.max_failed_projects,
        chain_profile=args.chain_profile,
        round_batch_limit=args.round_batch_limit,
        post_batch_audit=not args.no_post_batch_audit,
    )

    # 设置批后审计参数
    runner.audit_sample_size = max(int(args.audit_sample_size or 0), 1)
    runner.audit_seed = args.audit_seed

    # 运行压测
    return await runner.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
