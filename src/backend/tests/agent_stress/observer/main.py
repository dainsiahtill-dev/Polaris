"""主入口和运行逻辑。

负责命令解析、进程启动和主运行循环。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from tests.agent_stress.backend_bootstrap import (
    BackendBootstrapError,
    ensure_backend_session,
)
from tests.agent_stress.stress_path_policy import (
    default_stress_workspace_base,
    ensure_stress_workspace_path,
)

from .cli import (
    _build_observer_command,
    _build_runner_command,
    _clone_namespace,
    _redact_command_for_log,
)
from .constants import (
    CREATE_NEW_CONSOLE_FLAG,
    DEFAULT_LOG_NAME,
    IS_WINDOWS,
    PROJECT_ROOT,
)
from .projection import RuntimeProjection
from .state import ObserverState


def _extract_workspace_from_settings_line(line: str) -> str | None:
    """从 settings 日志行提取 workspace 路径。"""
    if not line:
        return None

    patterns = (
        r"\[settings\]\s*Workspace 已配置:\s*(.+?)(?:\s+\||$)",
        r"\[settings\]\s*Workspace configured:\s*(.+?)(?:\s+\||$)",
        r"\[settings\]\s*workspace configured:\s*(.+?)(?:\s+\||$)",
        r"\[settings\]\s*Current workspace:\s*(.+?)(?:\s+\||$)",
    )
    for pattern in patterns:
        match = re.search(pattern, line)
        if not match:
            continue
        workspace_part = str(match.group(1) or "").strip().strip('"')
        if not workspace_part:
            continue
        try:
            return str(Path(workspace_part).resolve())
        except (OSError, RuntimeError, ValueError):
            return workspace_part
    return None


def _build_live_kwargs(console: Console) -> dict[str, Any]:
    """Build Rich Live kwargs that are compatible across Rich versions."""
    kwargs: dict[str, Any] = {
        "console": console,
        "refresh_per_second": 4,
        "screen": True,
        "transient": False,
    }
    parameters = inspect.signature(Live.__init__).parameters
    if "vertical_scroll" in parameters:
        kwargs["vertical_scroll"] = False
    elif "vertical_overflow" in parameters:
        kwargs["vertical_overflow"] = "ellipsis"
    return kwargs


async def _stream_lines(
    stream: asyncio.StreamReader,
    *,
    state: ObserverState,
    sink,
    prefix: str = "",
    projection: RuntimeProjection | None = None,
) -> None:
    """读取子进程输出流。"""
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        decoded = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
        line = f"{prefix}{decoded}" if prefix else decoded
        sink.write(f"{line}\n")
        sink.flush()
        state.consume_line(line)

        if projection is not None:
            new_workspace = _extract_workspace_from_settings_line(line)
            if new_workspace and new_workspace != projection.workspace:
                switched = await projection.retarget_workspace(new_workspace)
                if switched:
                    notice = f"[projection] workspace retargeted: {new_workspace}"
                    sink.write(f"{notice}\n")
                    sink.flush()
                    state.consume_line(notice)


def _resolve_observer_output_dir(args: argparse.Namespace) -> Path:
    """解析观测器输出目录。"""
    if args.output_dir:
        return Path(args.output_dir).resolve()
    try:
        workspace = ensure_stress_workspace_path(Path(args.workspace).resolve())
    except ValueError:
        return default_stress_workspace_base("tests-agent-stress-errors") / "stress_reports"
    return (workspace / "stress_reports").resolve()


def _should_spawn_new_console_window(env: dict[str, str] | None = None) -> bool:
    """Return whether the observer should pop out a dedicated Windows console."""
    if not IS_WINDOWS:
        return False

    env_map = env or dict(os.environ)
    force_inline = str(env_map.get("POLARIS_STRESS_OBSERVER_INLINE") or "").strip().lower()
    if force_inline in {"1", "true", "yes", "on"}:
        return False

    remote_or_headless_markers = (
        "SSH_CONNECTION",
        "CI",
    )
    if any(str(env_map.get(key) or "").strip() for key in remote_or_headless_markers):
        return False

    session_name = str(env_map.get("SESSIONNAME") or "").strip().lower()
    if session_name and session_name != "console":
        return False

    return True


def _spawn_new_console(args: argparse.Namespace) -> int:
    """在 Windows 上弹出新控制台窗口运行观测器。"""
    if not IS_WINDOWS:
        raise RuntimeError("Observer pop-out console is currently supported on Windows only")
    observer_command = _build_observer_command(args)
    process = subprocess.Popen(
        observer_command,
        cwd=str(PROJECT_ROOT),
        creationflags=CREATE_NEW_CONSOLE_FLAG,
    )
    print(
        f"[observer] spawned window process pid={process.pid} "
        f"workspace={Path(args.workspace).resolve()} | waiting for window run to finish",
        flush=True,
    )
    exit_code = process.wait()
    print(f"[observer] window process exited with code={exit_code}", flush=True)
    return int(exit_code or 0)


async def _run_observer(args: argparse.Namespace) -> int:
    """运行观测器主循环。"""
    workspace_path = Path(args.workspace).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    output_dir = _resolve_observer_output_dir(args)
    output_dir.mkdir(parents=True, exist_ok=True)
    observer_log_path = output_dir / DEFAULT_LOG_NAME
    observer_log_path.parent.mkdir(parents=True, exist_ok=True)

    state = ObserverState(
        workspace=str(workspace_path),
        rounds=int(getattr(args, "rounds", 0) or 0),
        strategy=str(getattr(args, "strategy", "") or ""),
        backend_url=str(getattr(args, "backend_url", "") or ""),
        output_dir=str(output_dir),
        projection_enabled=bool(getattr(args, "projection_enabled", True) and not getattr(args, "no_projection", False)),
        projection_transport=str(getattr(args, "projection_transport", "ws")),
        projection_focus=str(getattr(args, "projection_focus", "all")),
    )

    console = Console()
    projection: RuntimeProjection | None = None
    backend_session = None

    command_args = _clone_namespace(args)
    projection_requested = bool(state.projection_enabled)

    if projection_requested:
        try:
            backend_session = await ensure_backend_session(
                backend_url=str(getattr(args, "backend_url", "") or ""),
                token=str(getattr(args, "token", "") or ""),
                auto_bootstrap=not bool(getattr(args, "no_auto_bootstrap", False)),
                startup_workspace=workspace_path,
            )
        except (BackendBootstrapError, ValueError) as exc:
            state.projection_error = f"projection_context_unavailable:{type(exc).__name__}"
        except (OSError, RuntimeError, TypeError) as exc:
            state.projection_error = f"projection_context_error:{type(exc).__name__}"
        else:
            context = backend_session.context
            if not str(getattr(command_args, "backend_url", "") or "").strip():
                command_args.backend_url = context.backend_url
            if not str(getattr(command_args, "token", "") or "").strip():
                command_args.token = context.token
            if context.backend_url:
                state.backend_url = context.backend_url
            if context.backend_url and context.token:
                projection = RuntimeProjection(
                    backend_url=context.backend_url,
                    token=context.token,
                    workspace=str(workspace_path),
                    transport=state.projection_transport,
                    focus=state.projection_focus,
                )
            else:
                state.projection_error = "projection_context_missing"

    runner_command = _build_runner_command(command_args)
    try:
        with observer_log_path.open("w", encoding="utf-8", newline="\n") as sink:
            sink.write("# Polaris Agent Stress Observer\n")
            sink.write(f"command={_redact_command_for_log(runner_command)}\n")
            sink.flush()

            if projection is not None:
                await projection.start()

            process = await asyncio.create_subprocess_exec(
                *runner_command,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_task = asyncio.create_task(
                _stream_lines(
                    process.stdout,
                    state=state,
                    sink=sink,
                    projection=projection,
                ),
            )
            stderr_task = asyncio.create_task(
                _stream_lines(
                    process.stderr,
                    state=state,
                    sink=sink,
                    prefix="[stderr] ",
                    projection=projection,
                ),
            )

            live_kwargs = _build_live_kwargs(console)
            with Live(state.render(), **live_kwargs) as live:
                last_render_time = 0.0
                while True:
                    if stdout_task.done() and stderr_task.done():
                        break
                    if projection is not None:
                        state.update_projection(
                            connected=projection.connected,
                            transport_used=projection.transport_used,
                            error=projection.connection_error,
                            panels=projection.get_panels(),
                        )
                    now = time.monotonic()
                    if now - last_render_time >= 0.5:
                        live.update(state.render())
                        last_render_time = now
                    await asyncio.sleep(0.05)

                await asyncio.gather(stdout_task, stderr_task)
                exit_code = await process.wait()
                state.attach_exit_code(exit_code)
                if projection is not None:
                    state.update_projection(
                        connected=projection.connected,
                        transport_used=projection.transport_used,
                        error=projection.connection_error,
                        panels=projection.get_panels(),
                    )
                live.update(state.render())
    finally:
        if projection is not None:
            await projection.stop()
        if backend_session is not None:
            await backend_session.aclose()

    console.print()
    console.print(
        Panel(
            f"[bold cyan]Observer log[/]: {observer_log_path}\n"
            f"[bold cyan]Runner exit code[/]: {state.exit_code}",
            title="[bold]Observer Finished[/bold]",
            border_style="green" if state.exit_code == 0 else "red",
        )
    )
    return int(state.exit_code or 0)


def build_observer_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="observer",
        description="Polaris Agent Stress Observer - 人类观测终端",
    )
    parser.add_argument("--workspace", required=True, help="工作空间路径")
    parser.add_argument("--rounds", type=int, default=1, help="压测轮数")
    parser.add_argument(
        "--strategy",
        default="rotation",
        choices=["rotation", "random", "complexity_asc"],
        help="执行策略",
    )
    parser.add_argument("--backend-url", help="后端 URL")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--category", help="项目类别筛选")
    parser.add_argument("--resume-from", help="从指定轮次恢复")
    parser.add_argument("--token", help="认证令牌")
    parser.add_argument("--no-auto-bootstrap", action="store_true", help="禁用自动引导")
    parser.add_argument(
        "--non-llm-timeout-seconds",
        type=float,
        default=120.0,
        help="非 LLM 控制面阻塞预算（秒）",
    )
    parser.add_argument(
        "--execution-mode",
        type=str,
        default="project_serial",
        choices=["project_serial", "round_robin"],
        help="执行模式",
    )
    parser.add_argument(
        "--attempts-per-project",
        type=int,
        default=3,
        help="project_serial 模式下每个项目最大尝试次数",
    )
    parser.add_argument(
        "--workspace-mode",
        type=str,
        default="per_project",
        choices=["per_project", "per_round"],
        help="项目工作区布局",
    )
    parser.add_argument(
        "--min-new-code-files",
        type=int,
        default=2,
        help="每轮最少新增代码文件数",
    )
    parser.add_argument(
        "--min-new-code-lines",
        type=int,
        default=80,
        help="每轮最少新增代码行数",
    )
    parser.add_argument(
        "--max-failed-projects",
        type=int,
        default=0,
        help="失败项目数量阈值",
    )
    parser.add_argument(
        "--chain-profile",
        type=str,
        default="court_strict",
        choices=["court_strict"],
        help="执行链配置",
    )
    parser.add_argument(
        "--round-batch-limit",
        type=int,
        default=3,
        help="每批最大轮次",
    )
    parser.add_argument(
        "--audit-sample-size",
        type=int,
        default=3,
        help="批后审计抽样数",
    )
    parser.add_argument(
        "--audit-seed",
        type=int,
        default=None,
        help="批后审计随机种子",
    )
    parser.add_argument("--skip-architect-stage", action="store_true", help="跳过 architect 阶段")
    parser.add_argument("--run-chief-engineer-stage", action="store_true", help="启用 chief_engineer 阶段")
    parser.add_argument("--require-architect-stage", action="store_true", help="强制 architect 阶段")
    parser.add_argument("--require-chief-engineer-stage", action="store_true", help="强制 chief_engineer 阶段")
    parser.add_argument("--disable-chain-evidence-gate", action="store_true", help="关闭链路证据门禁")
    parser.add_argument("--post-batch-audit", action="store_true", help="启用批后审计")
    parser.add_argument("--no-post-batch-audit", action="store_true", help="关闭批后审计")
    parser.add_argument("--projection-enabled", action="store_true", default=True, help="启用投影")
    parser.add_argument("--no-projection", action="store_true", help="禁用投影")
    parser.add_argument("--projection-transport", default="ws", choices=["ws"], help="投影传输方式（仅 ws）")
    parser.add_argument("--projection-focus", default="all", help="投影焦点")
    parser.add_argument("--pop-out", action="store_true", help="弹出新窗口")
    return parser


def main() -> int:
    """主入口。"""
    parser = build_observer_parser()
    args = parser.parse_args()

    if args.pop_out and _should_spawn_new_console_window():
        return _spawn_new_console(args)

    try:
        return asyncio.run(_run_observer(args))
    except KeyboardInterrupt:
        print("\n[observer] interrupted by user", flush=True)
        return 130
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        print(f"[observer] error: {e}", flush=True)
        return 1
