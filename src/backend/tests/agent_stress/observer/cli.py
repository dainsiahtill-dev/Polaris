"""CLI 辅助函数。

命令行参数处理和命令构建。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _redact_command_for_log(command: Sequence[str]) -> str:
    """将命令中的敏感信息（如 token）脱敏。"""
    redacted: list[str] = []
    hide_next = False
    for token in command:
        current = str(token)
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        if current == "--token":
            redacted.append(current)
            hide_next = True
            continue
        if current.startswith("--token="):
            redacted.append("--token=***")
            continue
        redacted.append(current)
    return " ".join(redacted)


def _clone_namespace(args: argparse.Namespace) -> argparse.Namespace:
    """克隆命令行参数命名空间。"""
    return argparse.Namespace(**vars(args))


def _build_runner_command(args: argparse.Namespace) -> list[str]:
    """构建 runner 子进程的命令行参数。"""
    command = [sys.executable, "-u", "-B", "-m", "tests.agent_stress.runner"]
    if args.workspace:
        command.extend(["--workspace", str(args.workspace)])
    if getattr(args, "rounds", None) is not None:
        command.extend(["--rounds", str(args.rounds)])
    if getattr(args, "strategy", None):
        command.extend(["--strategy", str(args.strategy)])
    if getattr(args, "backend_url", None):
        command.extend(["--backend-url", str(args.backend_url)])
    if getattr(args, "output_dir", None):
        command.extend(["--output-dir", str(args.output_dir)])
    if getattr(args, "category", None):
        command.extend(["--category", str(args.category)])
    if getattr(args, "resume_from", None):
        command.extend(["--resume-from", str(args.resume_from)])
    if getattr(args, "token", None):
        command.extend(["--token", str(args.token)])
    if bool(getattr(args, "no_auto_bootstrap", False)):
        command.append("--no-auto-bootstrap")

    optional_defaults: dict[str, object] = {
        "non_llm_timeout_seconds": 120.0,
        "execution_mode": "project_serial",
        "attempts_per_project": 3,
        "workspace_mode": "per_project",
        "min_new_code_files": 2,
        "min_new_code_lines": 80,
        "max_failed_projects": 0,
        "chain_profile": "court_strict",
        "round_batch_limit": 3,
        "audit_sample_size": 3,
        "audit_seed": None,
        "projection_transport": "ws",
        "projection_focus": "all",
    }

    # 可选参数
    for attr, flag in [
        ("non_llm_timeout_seconds", "--non-llm-timeout-seconds"),
        ("execution_mode", "--execution-mode"),
        ("attempts_per_project", "--attempts-per-project"),
        ("workspace_mode", "--workspace-mode"),
        ("min_new_code_files", "--min-new-code-files"),
        ("min_new_code_lines", "--min-new-code-lines"),
        ("max_failed_projects", "--max-failed-projects"),
        ("chain_profile", "--chain-profile"),
        ("round_batch_limit", "--round-batch-limit"),
        ("audit_sample_size", "--audit-sample-size"),
        ("audit_seed", "--audit-seed"),
        ("projection_transport", "--projection-transport"),
        ("projection_focus", "--projection-focus"),
    ]:
        value = getattr(args, attr, None)
        if value is not None:
            default_value = optional_defaults.get(attr)
            if default_value is not None and str(value) == str(default_value):
                continue
            command.extend([flag, str(value)])

    # 布尔标志
    for attr, flag in [
        ("skip_architect_stage", "--skip-architect-stage"),
        ("run_chief_engineer_stage", "--run-chief-engineer-stage"),
        ("require_architect_stage", "--require-architect-stage"),
        ("require_chief_engineer_stage", "--require-chief-engineer-stage"),
        ("disable_chain_evidence_gate", "--disable-chain-evidence-gate"),
        ("no_post_batch_audit", "--no-post-batch-audit"),
        ("no_projection", "--no-projection"),
    ]:
        if bool(getattr(args, attr, False)):
            command.append(flag)

    # 内部标记：避免 runner 再次进入 observer 包装导致递归。
    command.append("--observer-child")

    return command


def _build_observer_command(args: argparse.Namespace) -> list[str]:
    """构建 observer 子进程的命令行参数（用于弹出新窗口）。"""
    command = [sys.executable, "-u", "-B", "-m", "tests.agent_stress.observer"]
    if args.workspace:
        command.extend(["--workspace", str(args.workspace)])
    if getattr(args, "rounds", None) is not None:
        command.extend(["--rounds", str(args.rounds)])
    if getattr(args, "strategy", None):
        command.extend(["--strategy", str(args.strategy)])
    if getattr(args, "backend_url", None):
        command.extend(["--backend-url", str(args.backend_url)])
    if getattr(args, "output_dir", None):
        command.extend(["--output-dir", str(args.output_dir)])
    if getattr(args, "category", None):
        command.extend(["--category", str(args.category)])
    if getattr(args, "resume_from", None):
        command.extend(["--resume-from", str(args.resume_from)])
    if getattr(args, "token", None):
        command.extend(["--token", str(args.token)])
    if bool(getattr(args, "no_auto_bootstrap", False)):
        command.append("--no-auto-bootstrap")

    optional_defaults: dict[str, object] = {
        "non_llm_timeout_seconds": 120.0,
        "execution_mode": "project_serial",
        "attempts_per_project": 3,
        "workspace_mode": "per_project",
        "min_new_code_files": 2,
        "min_new_code_lines": 80,
        "max_failed_projects": 0,
        "chain_profile": "court_strict",
        "round_batch_limit": 3,
        "audit_sample_size": 3,
        "audit_seed": None,
        "projection_transport": "ws",
        "projection_focus": "all",
    }

    # 可选参数（简化版）
    for attr, flag in [
        ("non_llm_timeout_seconds", "--non-llm-timeout-seconds"),
        ("execution_mode", "--execution-mode"),
        ("attempts_per_project", "--attempts-per-project"),
        ("workspace_mode", "--workspace-mode"),
        ("min_new_code_files", "--min-new-code-files"),
        ("min_new_code_lines", "--min-new-code-lines"),
        ("max_failed_projects", "--max-failed-projects"),
        ("chain_profile", "--chain-profile"),
        ("round_batch_limit", "--round-batch-limit"),
        ("audit_sample_size", "--audit-sample-size"),
        ("audit_seed", "--audit-seed"),
        ("projection_transport", "--projection-transport"),
        ("projection_focus", "--projection-focus"),
    ]:
        value = getattr(args, attr, None)
        if value is not None:
            default_value = optional_defaults.get(attr)
            if default_value is not None and str(value) == str(default_value):
                continue
            command.extend([flag, str(value)])

    # 布尔标志
    for attr, flag in [
        ("skip_architect_stage", "--skip-architect-stage"),
        ("run_chief_engineer_stage", "--run-chief-engineer-stage"),
        ("require_architect_stage", "--require-architect-stage"),
        ("require_chief_engineer_stage", "--require-chief-engineer-stage"),
        ("disable_chain_evidence_gate", "--disable-chain-evidence-gate"),
        ("no_post_batch_audit", "--no-post-batch-audit"),
        ("no_projection", "--no-projection"),
    ]:
        if bool(getattr(args, attr, False)):
            command.append(flag)

    return command
