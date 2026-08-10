"""core construction for AgentStressRunner (mixin)."""

# mypy: ignore-errors

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backend_bootstrap import (
    ManagedBackendSession,
)
from ..backend_context import resolve_backend_context
from ..engine import RoundResult
from ..project_pool import (
    ProjectCategory,
)
from ._constants import (
    DEFAULT_NON_LLM_TIMEOUT_SECONDS,
    MAX_NON_LLM_TIMEOUT_SECONDS,
)


class _AgentStressRunnerCoreMixin:
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
    ) -> None:
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
