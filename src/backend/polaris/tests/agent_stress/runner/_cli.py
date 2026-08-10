"""CLI entry for agent stress runner."""

# mypy: ignore-errors

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ._constants import (
    DEFAULT_NON_LLM_TIMEOUT_SECONDS,
    DEFAULT_STRESS_WORKSPACE,
    PROBE_MIGRATION_MESSAGE,
)
from ._runner import AgentStressRunner


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
        help="主链从 PM 起跑（跳过可选Architect阶段）",
    )
    parser.add_argument(
        "--run-chief-engineer-stage",
        action="store_true",
        help="启用可选Chief Engineer阶段（仅基于公开 API 证据判定）",
    )
    parser.add_argument(
        "--require-architect-stage",
        action="store_true",
        help="将Architect阶段设为必需（未观测到成功即失败）",
    )
    parser.add_argument(
        "--require-chief-engineer-stage",
        action="store_true",
        help="将Chief Engineer阶段设为必需（未观测到成功即失败）",
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
        from ..observer import observe_runner

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
