"""execution methods for StressEngine (mixin)."""

# mypy: ignore-errors

import asyncio
import contextlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..contracts import (
    factory_failure_evidence,
    factory_failure_info,
)
from ..project_pool import ProjectDefinition
from ..stress_path_policy import (
    ensure_stress_workspace_path,
    runtime_layout_policy_violations,
)
from ._models import RoundResult


class _StressEngineExecutionMixin:
    async def run_round(
        self,
        round_number: int,
        project: ProjectDefinition,
        remediation_notes: str = "",
        start_from_override: str = "",
    ) -> RoundResult:
        """执行单轮压测 - 通过 Factory API 端到端驱动"""
        self.workspace = self._resolve_round_workspace(round_number, project)
        self.workspace.mkdir(parents=True, exist_ok=True)
        requested_entry_stage = self._normalize_entry_stage(start_from_override)
        if not str(start_from_override or "").strip():
            requested_entry_stage = "architect" if self.run_architect_stage else "pm"

        print(f"\n{'=' * 80}")
        print(f"压测轮次 #{round_number}: {project.name}")
        print(f"类别: {project.category.value} | 复杂度: {project.complexity_level}/5")
        print(f"增强特性: {[e.value for e in project.enhancements]}")
        print(f"主链入口: {requested_entry_stage}")
        print(f"项目工作区: {self.workspace}")
        print("=" * 80)

        result = RoundResult(
            round_number=round_number,
            project=project,
            start_time=datetime.now().isoformat(),
            entry_stage=requested_entry_stage,
        )
        baseline_snapshot = self._collect_workspace_code_files()
        self._current_round_path_fallback_before = int(self.path_fallback_count)

        # Step 1: 配置 workspace
        if not await self._configure_workspace():
            result.overall_result = "FAIL"
            result.failure_point = "engine"
            result.root_cause = "无法配置 workspace"
            return await self._finalize_round(result)

        # Step 2: 创建 Factory 运行
        factory_run = await self._create_factory_run(
            project,
            remediation_notes=remediation_notes,
            start_from=result.entry_stage,
        )
        if not factory_run:
            result.overall_result = "FAIL"
            result.failure_point = "engine"
            result.root_cause = "无法创建 Factory 运行"
            return await self._finalize_round(result)

        result.factory_run_id = factory_run.get("run_id")
        print(f"[Factory] Run ID: {result.factory_run_id}")

        # Step 3: 启动追踪和可观测性收集
        self.tracer.start_round(
            round_number=round_number,
            project_id=project.id,
            project_name=project.name,
            factory_run_id=result.factory_run_id,
        )
        if self.collector:
            self.collector.start_collection(round_number, result.factory_run_id)

        # Step 4: 轮询 Factory 运行直到完成
        final_status = await self._poll_factory_run(result.factory_run_id, result)

        # Step 5: 根据 Factory 结果设置整体结果
        if final_status == "completed":
            result.overall_result = "PASS"
        elif final_status == "completed_with_warnings":
            result.overall_result = "PARTIAL"
        else:
            result.overall_result = "FAIL"

        self._enforce_project_output_gate(result, baseline_snapshot)
        return await self._finalize_round(result)

    async def _configure_workspace(self) -> bool:
        """通过 API 配置 workspace"""
        try:
            workspace = ensure_stress_workspace_path(self.workspace)
            url = f"{self.backend_url}/settings"

            # 先获取当前设置
            response = await self._request_with_retry(
                "GET",
                url,
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                print(f"[settings] 获取设置失败: HTTP {response.status_code}")
                return False

            def _path_equals(left: str, right: str) -> bool:
                if not left or not right:
                    return False
                try:
                    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
                except (OSError, ValueError):
                    # 路径解析失败时降级为字符串比较
                    return str(left).strip().lower() == str(right).strip().lower()

            expected_workspace = str(workspace)
            expected_ramdisk = str(self.ramdisk_root)
            payload = {
                "workspace": expected_workspace,
                "ramdisk_root": expected_ramdisk,
            }
            layout_url = f"{self.backend_url}/v2/runtime/storage/layout"

            last_issue = ""
            for attempt in range(1, 4):
                response = await self._request_with_retry(
                    "POST",
                    url,
                    json_body=payload,
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    last_issue = f"update_settings_http_{response.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 更新 workspace 失败: HTTP {response.status_code}")
                    return False

                verify_settings = await self._request_with_retry(
                    "GET",
                    url,
                    timeout=self.request_timeout,
                )
                if verify_settings.status_code != 200:
                    last_issue = f"verify_settings_http_{verify_settings.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 校验 settings 失败: HTTP {verify_settings.status_code}")
                    return False
                settings_payload = verify_settings.json()
                effective_workspace = str(settings_payload.get("workspace") or "").strip()
                effective_ramdisk = str(settings_payload.get("ramdisk_root") or "").strip()
                if not _path_equals(effective_workspace, expected_workspace):
                    last_issue = f"workspace_not_applied:expected={expected_workspace}, actual={effective_workspace}"
                    if attempt < 3:
                        continue
                if not _path_equals(effective_ramdisk, expected_ramdisk):
                    last_issue = f"ramdisk_not_applied:expected={expected_ramdisk}, actual={effective_ramdisk}"
                    if attempt < 3:
                        continue

                layout_response = await self._request_with_retry(
                    "GET",
                    layout_url,
                    timeout=self.request_timeout,
                )
                if layout_response.status_code != 200:
                    last_issue = f"runtime_storage_layout_http_{layout_response.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 获取 v2/runtime/storage/layout 失败: HTTP {layout_response.status_code}")
                    return False

                layout = layout_response.json()
                violations = runtime_layout_policy_violations(layout)
                if violations:
                    last_issue = "; ".join(violations)
                    if attempt < 3:
                        continue
                    print("[settings] v2 runtime storage layout does not satisfy stress path policy: " + last_issue)
                    return False

                self.workspace = workspace
                print(
                    f"[settings] Workspace 已配置: {self.workspace} | "
                    f"Ramdisk: {self.ramdisk_root} | Runtime Root: {layout.get('runtime_root', '')}"
                )
                return True

            print(f"[settings] 配置 workspace 未生效: {last_issue or 'unknown'}")
            return False

        except ValueError as e:
            print(f"[settings] 路径策略校验失败: {e}")
            return False
        except (OSError, httpx.HTTPError) as e:
            print(f"[settings] 配置 workspace 失败: {e}")
            return False

    async def _create_factory_run(
        self,
        project: ProjectDefinition,
        *,
        remediation_notes: str = "",
        start_from: str = "",
    ) -> dict[str, Any] | None:
        """通过 API 创建 Factory 运行"""
        try:
            url = f"{self.backend_url}/v2/factory/runs"

            # 构建 directive
            directive = self._build_directive(
                project,
                remediation_notes=remediation_notes,
            )

            payload = {
                "workspace": str(self.workspace),
                "directive": directive,
                "start_from": self._normalize_entry_stage(start_from)
                if str(start_from or "").strip()
                else ("architect" if self.run_architect_stage else "pm"),
                "run_director": True,
                "run_chief_engineer": self.run_chief_engineer_stage,
                "director_iterations": 1,
                "loop": False,
            }

            response = await self._request_with_retry(
                "POST",
                url,
                json_body=payload,
                timeout=self.request_timeout,
            )

            if response.status_code != 200:
                print(f"[factory] 创建运行失败: HTTP {response.status_code}")
                print(f"[factory] 响应: {response.text[:500]}")
                return None

            return response.json()

        except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
            print(f"[factory] 创建运行异常: {e}")
            return None

    def _build_directive(self, project: ProjectDefinition, remediation_notes: str = "") -> str:
        """构建 Factory 运行的 directive"""
        enhancements_desc = "\n".join([f"- {e.value}" for e in project.enhancements])
        focus_desc = "\n".join([f"- {item}" for item in project.stress_focus])
        domain_keywords = self._build_project_domain_keywords(project)
        domain_keyword_hint = ", ".join(domain_keywords[:8]) if domain_keywords else project.id
        ascii_domain_keywords = [token for token in domain_keywords if re.fullmatch(r"[a-z0-9_-]+", token)]
        path_keyword_hint = (
            ", ".join(ascii_domain_keywords[:3]) if ascii_domain_keywords else project.id.replace("-", "_")
        )

        tech_requirements = [
            f"- 复杂度等级: {project.complexity_level}/5",
        ]
        if project.requires_backend:
            tech_requirements.append("- 需要后端 API 支持")
        if project.requires_websocket:
            tech_requirements.append("- 需要 WebSocket / SSE 实时通信")
        if project.requires_encryption:
            tech_requirements.append("- 需要加密/安全处理")

        delivery_baseline = "\n".join(
            [
                (
                    f"- 至少 {self.min_new_code_files} 个代码文件，总代码行数不少于 "
                    f"{self.min_new_code_lines} 行（含测试/脚本/配置）。"
                ),
                "- 至少包含: 一个核心模块目录、一个测试目录、一个配置文件、一个可运行入口或脚本。",
                "- 必须包含单元测试；若涉及后端/接口，请补充集成测试。",
                "- 输出必须是可运行代码，不得只输出计划/说明。",
                "- 若当前工作区已有内容，请在其基础上新增模块/测试以满足基线。",
                "- 严禁占位实现（TODO/FIXME/NotImplemented/stub/空壳 main+helpers 模板）；若出现视为失败。",
                f"- 代码命名与核心逻辑必须体现项目领域关键词（示例: {domain_keyword_hint}）。",
                f"- 至少一个核心代码文件路径或模块名必须包含项目关键词（示例: {path_keyword_hint}）。",
            ]
        )
        remediation = str(remediation_notes or "").strip()
        remediation_section = ""
        if remediation:
            remediation_section = (
                "## 上轮失败复盘（必须修复）\n"
                f"{remediation}\n\n"
                "你必须先逐条修复上述失败证据，再补充新功能，禁止重复提交相同模板代码。\n\n"
            )

        return f"""# {project.name}

## 需求描述
{project.description}

## 增强特性
{enhancements_desc}

## 压测重点
{focus_desc}

## 技术要求
{chr(10).join(tech_requirements)}

## 交付基线（硬性）
{delivery_baseline}

{remediation_section}## 验收标准
1. 核心功能完整可用
2. 增强特性全部落地
3. 代码与测试通过基础质量检查
4. 交付基线全部满足

请使用 Polaris 的标准流程完成此项目。
"""

    async def _poll_factory_run(
        self,
        run_id: str,
        result: RoundResult,
    ) -> str:
        """轮询 Factory 运行状态"""
        start_time = time.time()
        last_observed_success_at = start_time
        last_progress_signal_at = start_time
        last_progress_signature = ""
        last_runtime_activity_at = start_time
        last_runtime_activity_signature = ""
        last_stall_deferral_notice_at = 0.0
        last_phase = None
        snapshot_counter = 0
        rate_limit_cooldown = self.poll_interval

        print(f"[factory] 开始轮询运行状态 (timeout: {self.factory_timeout}s)")
        runtime_signature, _ = self._collect_runtime_activity_signature(run_id)
        if runtime_signature:
            last_runtime_activity_signature = runtime_signature
            last_runtime_activity_at = time.time()

        while time.time() - start_time < self.factory_timeout:
            sleep_seconds = self.poll_interval
            request_budget_seconds = max(self.request_timeout + 1.0, 2.0)
            try:
                url = f"{self.backend_url}/v2/factory/runs/{run_id}"
                response = await asyncio.wait_for(
                    self._request_with_retry(
                        "GET",
                        url,
                        timeout=self.request_timeout,
                        max_attempts=1,
                    ),
                    timeout=request_budget_seconds,
                )

                if response.status_code != 200:
                    if response.status_code == 429:
                        retry_after_raw = str(response.headers.get("Retry-After") or "").strip()
                        retry_after = 0.0
                        if retry_after_raw:
                            try:
                                retry_after = max(float(retry_after_raw), 0.0)
                            except (TypeError, ValueError):
                                retry_after = 0.0
                        rate_limit_cooldown = min(
                            max(rate_limit_cooldown * 1.8, self.poll_interval, retry_after),
                            30.0,
                        )
                        sleep_seconds = rate_limit_cooldown
                        print(f"[factory] 查询状态被限流(429), cooldown={sleep_seconds:.1f}s")
                    else:
                        print(f"[factory] 查询状态失败: HTTP {response.status_code}")
                else:
                    status = response.json()
                    last_observed_success_at = time.time()
                    rate_limit_cooldown = self.poll_interval
                    phase = status.get("phase")
                    lifecycle = status.get("status")
                    progress = status.get("progress", 0)
                    progress_signature = json.dumps(
                        {
                            "phase": phase,
                            "status": lifecycle,
                            "progress": progress,
                            "updated_at": status.get("updated_at"),
                            "stages_completed": status.get("stages_completed"),
                            "current_stage_started_at": (
                                status.get("metadata", {}).get("current_stage_started_at")
                                if isinstance(status.get("metadata"), dict)
                                else None
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if progress_signature != last_progress_signature:
                        last_progress_signature = progress_signature
                        last_progress_signal_at = time.time()
                    runtime_signature, runtime_last_mtime = self._collect_runtime_activity_signature(run_id)
                    if runtime_signature and runtime_signature != last_runtime_activity_signature:
                        last_runtime_activity_signature = runtime_signature
                        last_runtime_activity_at = time.time()
                    elif runtime_last_mtime > 0:
                        # 文件系统时间戳可用于补偿 signature 哈希偶发相同的场景。
                        last_runtime_activity_at = max(last_runtime_activity_at, runtime_last_mtime)

                    # 只在阶段变化时打印
                    if phase != last_phase:
                        print(f"[factory] Phase: {phase} | Status: {lifecycle} | Progress: {progress}%")
                        last_phase = phase

                    # 更新各阶段执行记录
                    self._update_stage_executions(status, result)

                    # 定期捕获可观测性快照
                    snapshot_counter += 1
                    if snapshot_counter % 6 == 0 and self.collector:  # 每 6 次轮询 (约 30s) 捕获一次
                        await self._capture_snapshot_with_budget("periodic")

                    # 检查是否完成
                    if lifecycle in ("completed", "failed", "cancelled"):
                        print(f"[factory] 运行结束: {lifecycle}")

                        # 最终快照
                        if self.collector:
                            await self._capture_snapshot_with_budget("final")

                        # 获取失败信息
                        if lifecycle == "failed":
                            failure = factory_failure_info(status)
                            result.failure_point = str(
                                failure.get("failure_point")
                                or failure.get("stage")
                                or failure.get("phase")
                                or "unknown"
                            )
                            result.failure_evidence = factory_failure_evidence(status)
                            result.root_cause = failure.get("detail", "Factory 运行失败")

                            # 生成诊断报告
                            if self.collector:
                                diagnostic = self.collector.analyze_failure(status)
                                result.diagnostic_report = diagnostic
                                result.root_cause = diagnostic.root_cause_analysis

                        return lifecycle

                    if (
                        lifecycle not in ("completed", "failed", "cancelled")
                        and time.time() - last_progress_signal_at >= self.control_plane_stall_timeout
                    ):
                        now_ts = time.time()
                        stagnant_seconds = int(now_ts - last_progress_signal_at)
                        runtime_inactive_seconds = int(now_ts - last_runtime_activity_at)
                        if runtime_inactive_seconds < int(self.control_plane_stall_timeout):
                            if now_ts - last_stall_deferral_notice_at >= max(self.poll_interval, 5.0):
                                print(
                                    "[factory] 阶段状态静止但运行时仍有活动，继续等待: "
                                    f"phase={phase}, status={lifecycle}, progress={progress}%, "
                                    f"status_stagnant={stagnant_seconds}s, runtime_inactive={runtime_inactive_seconds}s"
                                )
                                last_stall_deferral_notice_at = now_ts
                        else:
                            print(
                                "[factory] 非 LLM 控制面阻塞: "
                                f"阶段状态 {stagnant_seconds}s 无进展且运行时 {runtime_inactive_seconds}s 无活动 "
                                f"(phase={phase}, status={lifecycle}, progress={progress}%)"
                            )
                            cancel_reason = (
                                f"agent_stress_non_llm_blocked: phase={phase}, status={lifecycle}, progress={progress}"
                            )
                            await self._cancel_factory_run(run_id, reason=cancel_reason)
                            result.failure_point = "factory_stage_stalled"
                            result.root_cause = (
                                f"Factory phase '{phase}' remained unchanged for {stagnant_seconds}s and runtime "
                                f"activity was idle for {runtime_inactive_seconds}s (status={lifecycle}, "
                                f"progress={progress}%), exceeding non-LLM budget "
                                f"{self.control_plane_stall_timeout:.0f}s"
                            )
                            result.failure_evidence = (
                                "No progress change in phase/status/progress/updated_at and no runtime "
                                f"activity within {self.control_plane_stall_timeout:.0f}s"
                            )
                            return "blocked"

            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
                print(f"[factory] 轮询异常: {e}")

            if time.time() - last_observed_success_at >= self.control_plane_stall_timeout:
                print(f"[factory] 非 LLM 控制面阻塞: {self.control_plane_stall_timeout:.0f}s 内未获得有效状态响应")
                await self._cancel_factory_run(
                    run_id,
                    reason=(
                        "agent_stress_status_observation_timeout:"
                        f" no successful status response for {self.control_plane_stall_timeout:.0f}s"
                    ),
                )
                result.failure_point = "factory_status_observation_blocked"
                result.root_cause = (
                    "Factory status observation exceeded the non-LLM control-plane "
                    f"budget of {self.control_plane_stall_timeout:.0f}s"
                )
                result.failure_evidence = (
                    f"No successful GET /v2/factory/runs/{{id}} response within {self.control_plane_stall_timeout:.0f}s"
                )
                return "blocked"

            await asyncio.sleep(sleep_seconds)

        # 超时
        print(f"[factory] 运行超时 ({self.factory_timeout}s)")
        result.failure_point = "factory_timeout"
        result.root_cause = f"Factory 运行超时 ({self.factory_timeout}s)"
        return "timeout"

    async def _cancel_factory_run(self, run_id: str, *, reason: str) -> bool:
        """在阻塞时取消 Factory run，避免僵尸任务影响后续轮次。"""
        run_token = str(run_id or "").strip()
        if not run_token:
            return False
        try:
            url = f"{self.backend_url}/v2/factory/runs/{run_token}/control"
            response = await self._request_with_retry(
                "POST",
                url,
                json_body={
                    "action": "cancel",
                    "reason": str(reason or "").strip()[:240],
                },
                timeout=self.request_timeout,
                max_attempts=1,
            )
            if response.status_code == 200:
                print(f"[factory] 已取消阻塞运行: {run_token}")
                return True
            print(
                f"[factory] 取消阻塞运行失败: run={run_token}, http={response.status_code}, body={response.text[:240]}"
            )
            return False
        except (httpx.HTTPError, json.JSONDecodeError, OSError) as exc:
            print(f"[factory] 取消阻塞运行异常: run={run_token}, error={exc}")
            return False

    def _collect_runtime_activity_signature(self, run_id: str) -> tuple[str, float]:
        """收集运行时活动签名，用于区分“真阻塞”和“仍有执行活动”。

        返回:
            (signature, latest_mtime_epoch)
        """
        run_token = str(run_id or "").strip()
        if not run_token:
            return "", 0.0

        hp_root = Path(self.workspace) / ".polaris"
        if not hp_root.exists():
            return "", 0.0

        candidates: list[Path] = [
            hp_root / "factory" / run_token / "run.json",
            hp_root / "factory" / run_token / "events" / "events.jsonl",
        ]

        role_logs = hp_root / "runtime" / "roles"
        if role_logs.exists():
            with contextlib.suppress(OSError):
                candidates.extend(role_logs.glob("*/logs/*.jsonl"))

        runtime_events = hp_root / "runtime" / "events"
        if runtime_events.exists():
            with contextlib.suppress(OSError):
                candidates.extend(runtime_events.glob("*.jsonl"))

        signature_rows: list[list[Any]] = []
        latest_mtime = 0.0
        for path in candidates:
            try:
                if not path.exists() or not path.is_file():
                    continue
                stat = path.stat()
            except (OSError, PermissionError):
                continue
            latest_mtime = max(latest_mtime, float(stat.st_mtime))
            try:
                rel = path.relative_to(hp_root).as_posix()
            except ValueError:
                rel = str(path)
            signature_rows.append([rel, int(stat.st_mtime_ns), int(stat.st_size)])

        if not signature_rows:
            return "", latest_mtime

        signature_rows.sort(key=lambda row: str(row[0]))
        return json.dumps(signature_rows, ensure_ascii=False, separators=(",", ":")), latest_mtime

    async def _fetch_factory_events(self, run_id: str) -> list[dict[str, Any]]:
        url = f"{self.backend_url}/v2/factory/runs/{run_id}/events"
        response = await self._request_with_retry(
            "GET",
            url,
            timeout=self.request_timeout,
            params={"limit": 500},
        )
        if response.status_code != 200:
            print(f"[factory] 获取运行事件失败: HTTP {response.status_code}")
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []
