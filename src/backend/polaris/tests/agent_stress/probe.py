"""角色可用性探针 - 压测前 LLM 绑定验证

验证所有角色（pm/director/qa/architect/chief_engineer）的 LLM 可用性，
确保压测结论不因 LLM 不可用而失真。

使用当前正式 API:
- GET /v2/role/{role}/chat/status (需要鉴权)
- 返回字段: ready, configured, role_config {provider_id, model}
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Self

from .paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()

import httpx

from .backend_bootstrap import (
    BackendBootstrapError,
    ensure_backend_session,
)
from .preflight import BackendPreflightProbe, BackendPreflightStatus

DEFAULT_GENERATION_CHECK_ATTEMPTS = 2
MAX_GENERATION_CHECK_ATTEMPTS = 3
DEFAULT_GENERATION_TIMEOUT_SECONDS = 30.0
MIN_GENERATION_TIMEOUT_SECONDS = 2.0
MAX_GENERATION_TIMEOUT_SECONDS = 60.0
_RETRYABLE_GENERATION_ERROR_TOKENS = (
    "timeout",
    "timed out",
    "readtimeout",
    "connecttimeout",
    "generation http 429",
    "generation http 500",
    "generation http 502",
    "generation http 503",
    "generation http 504",
)


def _write_report_output(output_path: str, content: str) -> None:
    """Persist probe output with UTF-8, creating parent dirs when needed."""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")


class ProbeStatus(Enum):
    """探针状态"""

    HEALTHY = "healthy"  # 健康可用
    DEGRADED = "degraded"  # 降级可用
    UNHEALTHY = "unhealthy"  # 不可用
    UNKNOWN = "unknown"  # 未知状态


@dataclass
class RoleProbeResult:
    """单个角色探针结果"""

    role: str
    status: ProbeStatus
    provider: str = ""
    model: str = ""
    ready: bool = False
    configured: bool = False
    latency_ms: int = 0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "status": self.status.value,
            "provider": self.provider,
            "model": self.model,
            "ready": self.ready,
            "configured": self.configured,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


@dataclass
class ProbeReport:
    """探针完整报告"""

    timestamp: str
    overall_status: ProbeStatus
    role_results: list[RoleProbeResult]
    backend_preflight: dict[str, Any] | None = None
    backend_context: dict[str, Any] = field(default_factory=dict)
    total_roles: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    unhealthy_count: int = 0

    def __post_init__(self) -> None:
        self.total_roles = len(self.role_results)
        self.healthy_count = sum(1 for r in self.role_results if r.status == ProbeStatus.HEALTHY)
        self.degraded_count = sum(1 for r in self.role_results if r.status == ProbeStatus.DEGRADED)
        self.unhealthy_count = sum(1 for r in self.role_results if r.status == ProbeStatus.UNHEALTHY)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status.value,
            "summary": {
                "total_roles": self.total_roles,
                "healthy": self.healthy_count,
                "degraded": self.degraded_count,
                "unhealthy": self.unhealthy_count,
            },
            "roles": [r.to_dict() for r in self.role_results],
        }
        if self.backend_preflight is not None:
            payload["backend_preflight"] = self.backend_preflight
        if self.backend_context:
            payload["backend_context"] = self.backend_context
        return payload

    def to_markdown(self) -> str:
        """生成 Markdown 报告"""
        lines = [
            "# AI Agent 角色可用性探针报告",
            "",
            f"**时间**: {self.timestamp}",
            f"**整体状态**: {self._status_emoji(self.overall_status)} {self.overall_status.value.upper()}",
            "",
            "## 摘要",
            "",
            f"- 总角色数: {self.total_roles}",
            f"- 健康: {self.healthy_count}",
            f"- 降级: {self.degraded_count}",
            f"- 不可用: {self.unhealthy_count}",
            "",
        ]

        if self.backend_preflight is not None:
            lines.extend(
                [
                    "## Backend 预检",
                    "",
                    f"- 状态: {self.backend_preflight.get('status', 'unknown')}",
                    f"- Backend 可达: {self.backend_preflight.get('backend_reachable', False)}",
                    f"- Settings 可访问: {self.backend_preflight.get('settings_accessible', False)}",
                    f"- WS runtime.v2 可用: {self.backend_preflight.get('ws_runtime_v2_accessible', False)}",
                    f"- JetStream 可用: {self.backend_preflight.get('jetstream_accessible', False)}",
                    f"- 投影传输: {self.backend_preflight.get('projection_transport', 'none')}",
                    "",
                ]
            )

        lines.extend(
            [
                "## 各角色详情",
                "",
                "| 角色 | 状态 | Provider | 模型 | 延迟 | 错误 |",
                "|------|------|----------|------|------|------|",
            ]
        )

        for r in self.role_results:
            emoji = self._status_emoji(r.status)
            error_short = (r.error[:30] + "...") if len(r.error) > 30 else r.error
            lines.append(
                f"| {r.role} | {emoji} {r.status.value} | {r.provider} | {r.model} | {r.latency_ms}ms | {error_short or '-'} |"
            )

        lines.extend(
            [
                "",
                "## 结论",
                "",
            ]
        )

        if self.overall_status == ProbeStatus.HEALTHY:
            lines.append("✅ 所有角色 LLM 绑定正常，可以进行压测。")
        elif self.overall_status == ProbeStatus.DEGRADED:
            lines.append("⚠️ 部分角色降级，压测结论可能受影响。")
        else:
            lines.append("❌ 存在不可用角色，压测会失真，请先修复 LLM 配置。")

        return "\n".join(lines)

    @staticmethod
    def _status_emoji(status: ProbeStatus) -> str:
        return {
            ProbeStatus.HEALTHY: "🟢",
            ProbeStatus.DEGRADED: "🟡",
            ProbeStatus.UNHEALTHY: "🔴",
            ProbeStatus.UNKNOWN: "⚪",
        }.get(status, "⚪")


class RoleAvailabilityProbe:
    """角色可用性探针

    验证 PM/Director/QA/Architect/Chief Engineer 的 LLM 可用性
    使用当前正式 API，需要鉴权 token
    """

    ROLES = ["pm", "architect", "chief_engineer", "director", "qa"]

    def __init__(
        self,
        backend_url: str = "",
        probe_timeout: int = 30,
        token: str = "",
        verify_generation: bool = True,
    ) -> None:
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.probe_timeout = probe_timeout
        self.token = str(token or "").strip()
        self.verify_generation = bool(verify_generation)

        # 创建带鉴权的客户端
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.client = httpx.AsyncClient(timeout=probe_timeout, headers=headers)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.client.aclose()

    async def probe_all(self) -> ProbeReport:
        """探测所有角色"""
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        results = []

        # 并行探测所有角色
        tasks = [self._probe_role(role) for role in self.ROLES]
        role_results = await asyncio.gather(*tasks, return_exceptions=True)

        for role, result in zip(self.ROLES, role_results, strict=False):
            if isinstance(result, Exception):
                results.append(
                    RoleProbeResult(
                        role=role,
                        status=ProbeStatus.UNHEALTHY,
                        error=str(result),
                    )
                )
            else:
                results.append(result)

        # 确定整体状态
        unhealthy_count = sum(1 for r in results if r.status == ProbeStatus.UNHEALTHY)
        degraded_count = sum(1 for r in results if r.status == ProbeStatus.DEGRADED)

        if unhealthy_count > 0:
            overall = ProbeStatus.UNHEALTHY
        elif degraded_count > 0:
            overall = ProbeStatus.DEGRADED
        else:
            overall = ProbeStatus.HEALTHY

        return ProbeReport(
            timestamp=timestamp,
            overall_status=overall,
            role_results=results,
        )

    async def _probe_role(self, role: str) -> RoleProbeResult:
        """探测单个角色"""
        start_time = time.time()

        try:
            # 获取角色状态 (正式 API)
            status_url = f"{self.backend_url}/v2/role/{role}/chat/status"
            response = await self.client.get(status_url)

            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 401:
                return RoleProbeResult(
                    role=role,
                    status=ProbeStatus.UNHEALTHY,
                    error="Unauthorized - token required",
                    latency_ms=latency_ms,
                )

            if response.status_code != 200:
                return RoleProbeResult(
                    role=role,
                    status=ProbeStatus.UNHEALTHY,
                    error=f"HTTP {response.status_code}",
                    latency_ms=latency_ms,
                )

            status_data = response.json()

            # 解析当前正式 API 返回结构
            ready = status_data.get("ready", False)
            configured = status_data.get("configured", False)
            role_config = status_data.get("role_config", {})
            provider = role_config.get("provider_id", "") if isinstance(role_config, dict) else ""
            model = role_config.get("model", "") if isinstance(role_config, dict) else ""

            # 判断状态
            if not configured:
                status = ProbeStatus.UNHEALTHY
                error = "Role not configured"
            elif not ready:
                status = ProbeStatus.DEGRADED
                error = "Role configured but not ready"
            else:
                status = ProbeStatus.HEALTHY
                error = ""

            generation_ok = True
            generation_error = ""
            generation_latency_ms = 0
            generation_attempts = 0
            if status == ProbeStatus.HEALTHY and self.verify_generation:
                (
                    generation_ok,
                    generation_error,
                    generation_latency_ms,
                    generation_attempts,
                ) = await self._probe_role_generation_with_retry(role)
                if not generation_ok:
                    status = ProbeStatus.UNHEALTHY
                    error = generation_error or "Role generation check failed"

            return RoleProbeResult(
                role=role,
                status=status,
                provider=provider,
                model=model,
                ready=ready,
                configured=configured,
                latency_ms=latency_ms,
                error=error,
                details={
                    "llm_test_ready": status_data.get("llm_test_ready", False),
                    "provider_type": status_data.get("provider_type"),
                    "generation_check_enabled": self.verify_generation,
                    "generation_ok": generation_ok,
                    "generation_attempts": generation_attempts,
                    "generation_latency_ms": generation_latency_ms,
                    "generation_error": generation_error,
                },
            )

        except httpx.TimeoutException:
            return RoleProbeResult(
                role=role,
                status=ProbeStatus.UNHEALTHY,
                error="Timeout",
                latency_ms=int((time.time() - start_time) * 1000),
            )
        except httpx.HTTPError as e:
            # httpx.HTTPError: network-level errors (connection, protocol, etc.)
            # We intentionally do NOT catch CancelledError so it propagates.
            return RoleProbeResult(
                role=role,
                status=ProbeStatus.UNHEALTHY,
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            )

    async def _probe_role_generation(self, role: str) -> tuple[bool, str, int]:
        """做一次轻量真实调用，避免仅配置探针造成假阳性。"""
        started = time.time()
        try:
            url = f"{self.backend_url}/v2/agent/turn"
            payload = {
                "role": role,
                "message": "健康检查：请简短回复OK",
                "stream": False,
            }
            timeout = self._generation_probe_timeout_seconds()
            response = await self.client.post(url, json=payload, timeout=timeout)
            latency_ms = int((time.time() - started) * 1000)
            if response.status_code != 200:
                return False, f"Generation HTTP {response.status_code}", latency_ms
            data = response.json() if response.content else {}
            if not isinstance(data, dict) or not bool(data.get("ok")):
                return False, f"Generation returned non-ok payload: {str(data)[:160]}", latency_ms
            reply = str(data.get("reply") or "").strip()
            if not reply:
                return False, "Generation reply is empty", latency_ms
            return True, "", latency_ms
        except asyncio.CancelledError:
            raise
        except json.JSONDecodeError:
            # Generation returned non-JSON response body
            latency_ms = int((time.time() - started) * 1000)
            return False, "Generation returned non-JSON response", latency_ms
        except httpx.HTTPError as exc:
            # httpx.HTTPError: network errors (connection, protocol, HTTP status, etc.)
            latency_ms = int((time.time() - started) * 1000)
            return False, f"Generation check error: {type(exc).__name__}: {exc}", latency_ms

    async def _probe_role_generation_with_retry(
        self,
        role: str,
    ) -> tuple[bool, str, int, int]:
        attempts = self._generation_probe_attempts()
        total_latency = 0
        errors: list[str] = []
        for attempt in range(1, attempts + 1):
            ok, error, latency_ms = await self._probe_role_generation(role)
            total_latency += max(int(latency_ms), 0)
            if ok:
                return True, "", total_latency, attempt
            normalized_error = str(error or "").strip() or "unknown_generation_error"
            errors.append(normalized_error)
            if attempt >= attempts:
                break
            if not self._is_retryable_generation_error(normalized_error):
                break
        return False, " | ".join(errors), total_latency, len(errors)

    @staticmethod
    def _is_retryable_generation_error(error: str) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        return any(token in text for token in _RETRYABLE_GENERATION_ERROR_TOKENS)

    @staticmethod
    def _generation_probe_attempts() -> int:
        raw = str(os.environ.get("KERNELONE_STRESS_PROBE_GENERATION_ATTEMPTS") or "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                # ValueError: invalid integer string
                value = DEFAULT_GENERATION_CHECK_ATTEMPTS
        else:
            value = DEFAULT_GENERATION_CHECK_ATTEMPTS
        return max(1, min(MAX_GENERATION_CHECK_ATTEMPTS, value))

    def _generation_probe_timeout_seconds(self) -> float:
        raw = str(os.environ.get("KERNELONE_STRESS_PROBE_GENERATION_TIMEOUT_SECONDS") or "").strip()
        if raw:
            try:
                value = float(raw)
            except ValueError:
                # ValueError: invalid float string
                value = float(self.probe_timeout or DEFAULT_GENERATION_TIMEOUT_SECONDS)
        else:
            value = float(self.probe_timeout or DEFAULT_GENERATION_TIMEOUT_SECONDS)
        return min(
            max(value, MIN_GENERATION_TIMEOUT_SECONDS),
            MAX_GENERATION_TIMEOUT_SECONDS,
        )

    async def probe_provider_health(self) -> dict[str, Any]:
        """探测 Provider 层健康状态"""
        try:
            url = f"{self.backend_url}/llm/status"
            response = await self.client.get(url)

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}"}
        except httpx.HTTPError as e:
            return {"error": str(e)}
        except Exception as e:
            # Unexpected error - catch-all to prevent crashes in health probe
            return {"error": f"Unexpected: {type(e).__name__}: {e}"}


def _build_backend_context_payload(session: Any) -> dict[str, Any]:
    """为探针输出补充 backend context 元数据。"""
    backend_context = getattr(session, "context", None)
    return {
        "source": str(getattr(backend_context, "source", "") or ""),
        "backend_url": str(getattr(backend_context, "backend_url", "") or ""),
        "desktop_info_path": str(getattr(backend_context, "desktop_info_path", "") or ""),
        "auto_bootstrapped": bool(getattr(session, "auto_bootstrapped", False)),
        "startup_workspace": str(getattr(session, "startup_workspace", "") or ""),
        "ramdisk_root": str(getattr(session, "ramdisk_root", "") or ""),
    }


def _build_preflight_blocked_report(
    *,
    timestamp: str,
    backend_context: dict[str, Any],
    preflight_report: dict[str, Any],
) -> dict[str, Any]:
    """统一构造 backend 预检失败时的阻塞报告。"""
    return {
        "timestamp": timestamp,
        "overall_status": ProbeStatus.UNHEALTHY.value,
        "blocked": True,
        "blocking_reason": "backend_preflight_failed",
        "summary": {
            "total_roles": 0,
            "healthy": 0,
            "degraded": 0,
            "unhealthy": 0,
        },
        "backend_context": backend_context,
        "backend_preflight": preflight_report,
        "message": (
            "Polaris backend preflight failed. Stop here and repair backend/runtime.v2/JetStream before role probing."
        ),
        "roles": [],
    }


async def main(argv: list[str] | None = None):
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="AI Agent 角色可用性探针")
    parser.add_argument("--backend-url", default="", help="留空时自动解析当前 Polaris backend")
    parser.add_argument("--token", default="", help="留空时自动解析当前 Polaris backend")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output", "-o", help="输出报告路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument(
        "--no-auto-bootstrap",
        action="store_true",
        help="禁用 backend context 自动自举（默认会在 context 缺失时用官方 server.py CLI 自动拉起本地 backend）",
    )
    args = parser.parse_args(argv)

    try:
        session = await ensure_backend_session(
            backend_url=args.backend_url,
            token=args.token,
            auto_bootstrap=not args.no_auto_bootstrap,
        )
    except BackendBootstrapError as exc:
        blocked_report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "overall_status": ProbeStatus.UNHEALTHY.value,
            "blocked": True,
            "blocking_reason": "backend_bootstrap_failed",
            "summary": {
                "total_roles": 0,
                "healthy": 0,
                "degraded": 0,
                "unhealthy": 0,
            },
            "backend_context": {
                "source": "bootstrap_failed",
                "details": exc.details,
            },
            "message": (
                "Polaris backend auto-bootstrap failed. "
                "Stop here and report environment blockage; do not guess ports "
                "and do not manually start backend outside the official runner flow."
            ),
            "roles": [],
        }
        print("=" * 80)
        print("AI Agent 角色可用性探针")
        print("=" * 80)
        print("Backend: ")
        print("Backend Context: bootstrap_failed")
        print("")
        output = (
            json.dumps(blocked_report, indent=2, ensure_ascii=False)
            if args.json
            else (
                "# AI Agent 角色可用性探针报告\n\n"
                f"**整体状态**: {ProbeStatus.UNHEALTHY.value.upper()}\n\n"
                "## 阻塞\n\n"
                "- 原因: backend_bootstrap_failed\n"
                f"- 详情: {json.dumps(exc.details, ensure_ascii=False)}\n\n"
                "结论: 当前环境无法通过官方 runner 自举 Polaris backend。"
            )
        )
        print(output)
        if args.output:
            _write_report_output(args.output, output)
            print(f"\n报告已保存: {args.output}")
        return 2

    async with session:
        backend_context = session.context
        backend_context_payload = _build_backend_context_payload(session)

        print("=" * 80)
        print("AI Agent 角色可用性探针")
        print("=" * 80)
        print(f"Backend: {backend_context.backend_url}")
        print(f"Backend Context: {backend_context.source}")
        if session.auto_bootstrapped:
            print(f"Backend Bootstrap Workspace: {session.startup_workspace}")
            print(f"Backend Bootstrap RamDisk: {session.ramdisk_root}")
        print("")

        if not str(backend_context.backend_url or "").strip():
            blocked_report = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "overall_status": ProbeStatus.UNHEALTHY.value,
                "blocked": True,
                "blocking_reason": "backend_context_unresolved",
                "summary": {
                    "total_roles": 0,
                    "healthy": 0,
                    "degraded": 0,
                    "unhealthy": 0,
                },
                "backend_context": {
                    "source": backend_context.source,
                    "desktop_info_path": backend_context.desktop_info_path,
                },
                "message": (
                    "Polaris backend context is unresolved even after the runner's "
                    "official auto-bootstrap path. Stop here and report environment blockage."
                ),
                "roles": [],
            }
            if args.json:
                output = json.dumps(blocked_report, indent=2, ensure_ascii=False)
            else:
                output = (
                    "# AI Agent 角色可用性探针报告\n\n"
                    f"**整体状态**: {ProbeStatus.UNHEALTHY.value.upper()}\n\n"
                    "## 阻塞\n\n"
                    "- 原因: backend_context_unresolved\n"
                    f"- 来源: {backend_context.source}\n"
                    f"- desktop-backend-info: {backend_context.desktop_info_path or '-'}\n\n"
                    "结论: 当前环境在官方 auto-bootstrap 后仍然无法解析 backend context。"
                )
            print(output)
            if args.output:
                _write_report_output(args.output, output)
                print(f"\n报告已保存: {args.output}")
            return 2

        print("执行 Backend 预检...")
        async with BackendPreflightProbe(
            backend_url=backend_context.backend_url,
            token=backend_context.token,
            timeout=min(max(float(args.timeout), 5.0), 15.0),
        ) as preflight_probe:
            backend_preflight = await preflight_probe.run()
        backend_preflight_payload = backend_preflight.to_dict()
        print(f"  - 状态: {backend_preflight_payload.get('status', 'unknown')}")
        print(f"  - Backend 可达: {backend_preflight_payload.get('backend_reachable', False)}")
        print(f"  - 鉴权有效: {backend_preflight_payload.get('auth_valid', False)}")
        print(f"  - Settings 可访问: {backend_preflight_payload.get('settings_accessible', False)}")
        print(f"  - WS runtime.v2 可用: {backend_preflight_payload.get('ws_runtime_v2_accessible', False)}")
        print(f"  - JetStream 可用: {backend_preflight_payload.get('jetstream_accessible', False)}")
        print(f"  - 投影传输: {backend_preflight_payload.get('projection_transport', 'none')}")
        print("")

        if backend_preflight.status != BackendPreflightStatus.HEALTHY:
            blocked_report = _build_preflight_blocked_report(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                backend_context=backend_context_payload,
                preflight_report=backend_preflight_payload,
            )
            if args.json:
                output = json.dumps(blocked_report, indent=2, ensure_ascii=False)
            else:
                output = (
                    "# AI Agent 角色可用性探针报告\n\n"
                    f"**整体状态**: {ProbeStatus.UNHEALTHY.value.upper()}\n\n"
                    "## 阻塞\n\n"
                    "- 原因: backend_preflight_failed\n"
                    f"- 预检状态: {backend_preflight_payload.get('status', 'unknown')}\n"
                    f"- WS runtime.v2 可用: {backend_preflight_payload.get('ws_runtime_v2_accessible', False)}\n"
                    f"- JetStream 可用: {backend_preflight_payload.get('jetstream_accessible', False)}\n"
                    f"- 投影传输: {backend_preflight_payload.get('projection_transport', 'none')}\n\n"
                    "结论: 先修复 backend / runtime.v2 / JetStream，再继续角色探针。"
                )
            print(output)
            if args.output:
                _write_report_output(args.output, output)
                print(f"\n报告已保存: {args.output}")
            return 2

        async with RoleAvailabilityProbe(
            backend_url=backend_context.backend_url,
            probe_timeout=args.timeout,
            token=backend_context.token,
        ) as probe:
            # 探测 Provider 健康
            print("探测 Provider 状态...")
            provider_health = await probe.probe_provider_health()

            if "error" in provider_health:
                print(f"⚠️ Provider 探测失败: {provider_health['error']}")
            else:
                providers = provider_health.get("providers", {})
                print(f"发现 {len(providers)} 个 Provider")
                for pid, pinfo in providers.items():
                    status = "✅" if pinfo.get("healthy") else "❌"
                    print(f"  {status} {pid}: {pinfo.get('type', 'unknown')}")
            print("")

            # 探测角色
            print("探测角色 LLM 绑定...")
            print("-" * 80)
            report = await probe.probe_all()
            report.backend_preflight = backend_preflight_payload
            report.backend_context = backend_context_payload

            # 输出报告
            output = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) if args.json else report.to_markdown()

            print(output)

            # 保存报告
            if args.output:
                _write_report_output(args.output, output)
                print(f"\n报告已保存: {args.output}")

            # 返回码
            if report.overall_status == ProbeStatus.HEALTHY:
                return 0
            if report.overall_status == ProbeStatus.DEGRADED:
                return 1
            return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
