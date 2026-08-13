"""Characterization tests for text-shaping, delivery-target, bool/env, contract-metadata helpers."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _executor,
)


class TestTextShapingHelpers:
    def test_compact_text_under_limit_returns_stripped(self) -> None:
        assert OrchestrationStageExecutor._compact_text_for_prompt("  hello  ", max_chars=100) == "hello"

    def test_compact_text_over_limit_inserts_omission_marker(self) -> None:
        text = "A" * 60 + "B" * 60
        result = OrchestrationStageExecutor._compact_text_for_prompt(text, max_chars=30)
        assert "[... omitted" in result
        assert "chars for PM planning context ...]" in result
        # head=20 (30*2//3), tail=10, omitted=120-20-10=90
        assert "omitted 90 chars" in result
        assert result.startswith("A" * 20)
        assert result.endswith("B" * 10)

    def test_compact_text_handles_none(self) -> None:
        assert OrchestrationStageExecutor._compact_text_for_prompt(None, max_chars=10) == ""  # type: ignore[arg-type]

    def test_compact_workspace_quality_evidence_for_qa_preserves_parseable_failure(self) -> None:
        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "source": "factory_stage_executor",
            "factory_run_id": "factory-run-1",
            "workspace": "/tmp/project",
            "passed": False,
            "commands": [
                {
                    "command": ["npm", "install"],
                    "phase": "prepare",
                    "passed": True,
                    "exit_code": 0,
                    "stdout_tail": "ok\n" + ("x" * 10_000),
                },
                {
                    "command": ["npm", "run", "build"],
                    "phase": "check",
                    "passed": False,
                    "exit_code": 2,
                    "stdout_tail": "src/app.ts(1,1): error TS1005\n" + ("y" * 10_000),
                },
            ],
            "repair": {
                "attempted": True,
                "success": False,
                "source_tools": ["deterministic_ts"],
                "evidence": ["repair failed " + ("z" * 1000)],
            },
        }

        compact = OrchestrationStageExecutor._compact_workspace_quality_evidence_for_qa(
            json.dumps(payload, ensure_ascii=False)
        )
        summary = extract_workspace_quality_summary(compact)

        assert summary is not None
        assert summary["passed"] is False
        assert summary["command_count"] == 2
        assert summary["prepare_passed_count"] == 1
        assert summary["check_passed_count"] == 0
        assert summary["repair_attempted"] is True
        assert summary["repair_success"] is False

    def test_workspace_quality_repair_original_message_uses_data_plane_blueprint_summary(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build canvas flight entrypoint",
                        "goal": "Render a paper plane flight canvas.",
                        "scope": "index.html, src/web.ts",
                        "target_files": ["index.html", "src/web.ts"],
                        "steps": ["Create browser bootstrap", "Draw a non-empty first frame"],
                        "acceptance": ["npm run build passes", "canvas paints pixels"],
                        "metadata": {"internal": "not prompt data"},
                    }
                ]
            },
        )
        executor._write_json_artifact(
            "runtime/state/blueprints/factory-run.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "source": "factory_stage_executor",
                "factory_run_id": "factory-run",
                "generated_blueprints": 1,
                "total_tasks": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "status": "generated",
                        "blueprint_id": "ce_TASK-1_test",
                        "blueprint_path": "runtime/blueprints/ce_TASK-1_test.json",
                        "summary": "Use src/web.ts as the browser bootstrap for index.html.",
                        "recommendations": ["Keep DOM canvas code out of the Node CLI entrypoint."],
                        "risks": ["API drift between renderer and models."],
                    }
                ],
                "metadata": {"internal": "not prompt data"},
            },
        )

        message = executor._workspace_quality_repair_original_message(
            run_id="factory-run",
            target_files=["index.html", "src/web.ts"],
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "ce_TASK-1_test" in message
        assert "Build canvas flight entrypoint" in message
        assert "factory_run_id" not in message
        assert '"metadata"' not in message
        assert '"source"' not in message

    def test_workspace_quality_repair_original_message_uses_workspace_local_blueprint_summary(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build package entrypoint",
                        "goal": "Deliver a runnable npm project.",
                        "target_files": ["package.json", "src/index.js"],
                    }
                ]
            },
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        (blueprint_dir / "latest.review.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory.chief_engineer_review.v1",
                    "generated_blueprints": 1,
                    "blueprints": [
                        {
                            "task_id": "TASK-1",
                            "status": "generated",
                            "blueprint_id": "ce_TASK-1_workspace_local",
                            "summary": "Keep the package entrypoint aligned with declared exports.",
                            "recommendations": ["Do not shrink the manifest around missing targets."],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        message = executor._workspace_quality_repair_original_message(
            run_id="factory-run",
            target_files=["package.json", "src/index.js"],
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "workspace-local:.polaris/blueprints/latest.review.json" in message
        assert "ce_TASK-1_workspace_local" in message
        assert "Do not shrink the manifest" in message

    def test_workspace_quality_runtime_repair_task_carries_workspace_blueprint_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build package entrypoint",
                        "goal": "Deliver a runnable npm project.",
                        "target_files": ["package.json", "src/index.js"],
                    }
                ]
            },
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        (blueprint_dir / "factory-run.review.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory.chief_engineer_review.v1",
                    "generated_blueprints": 1,
                    "blueprints": [
                        {
                            "task_id": "TASK-1",
                            "status": "generated",
                            "blueprint_id": "ce_TASK-1_runtime_schedule",
                            "summary": "Runtime repair must retain CE handoff evidence.",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        captured: dict[str, Any] = {}

        def fake_run_schedule(
            adapter: Any,
            *,
            task: dict[str, Any],
            task_id: str,
            artifact_quality_errors: list[str],
            execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        ):
            del adapter, task_id, artifact_quality_errors, execution_attempt
            captured["task"] = task
            return [], {"attempted": False}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair_schedule",
            fake_run_schedule,
        )

        executor._apply_workspace_quality_repairs(
            run_id="factory-run",
            artifact_quality_errors=["Artifact quality scan failed: workspace validation command failed"],
        )

        metadata = captured["task"]["metadata"]
        assert metadata["ce_blueprint"]["artifact"] == "workspace-local:.polaris/blueprints/factory-run.review.json"
        assert "ce_TASK-1_runtime_schedule" in metadata["ce_blueprint"]["evidence"]
        assert metadata["factory_workspace_quality_repair"]["target_files"] == ["package.json", "src/index.js"]

    def test_quality_repair_prompt_compaction_preserves_blueprint_after_four_goals(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        original_message = "\n".join(
            [
                "Factory workspace quality repair contract:",
                "goal: deliver manifest",
                "goal: deliver core engine",
                "goal: deliver supporting modules",
                "goal: deliver runtime entrypoint",
                "- Chief Engineer blueprint evidence:",
                "  artifact: workspace-local:.polaris/blueprints/latest.review.json",
                '  "blueprint_id": "ce_TASK-1_preserve_blueprint"',
                '  "summary": "Keep manifest scripts aligned with declared targets."',
                '  "recommendations": ["Create missing entrypoint instead of shrinking scripts."]',
            ]
        )

        message = build_director_materialization_quality_repair_message(
            original_message=original_message,
            artifact_quality_errors=["Artifact quality scan failed: npm run build references missing src/index.js"],
            changed_files=["package.json"],
            repair_target_files=["package.json"],
            workspace_full=str(tmp_path),
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "ce_TASK-1_preserve_blueprint" in message
        assert "Create missing entrypoint instead of shrinking scripts" in message

    def test_strip_prompt_meta_lines_removes_matching_lines(self) -> None:
        text = "keep this\n这是提示词内容\nalso keep\nsystem prompt here\nfinal"
        result = OrchestrationStageExecutor._strip_prompt_meta_lines(text)
        assert result == "keep this\nalso keep\nfinal"

    def test_strip_prompt_meta_lines_empty(self) -> None:
        assert OrchestrationStageExecutor._strip_prompt_meta_lines("") == ""

    def test_is_substantive_doc_text_requires_two_headings_and_min_chars(self) -> None:
        good = "# Title\n" + ("body " * 60) + "\n## Section\nmore"
        assert OrchestrationStageExecutor._is_substantive_doc_text(good) is True
        assert OrchestrationStageExecutor._is_substantive_doc_text("# one\nshort") is False
        assert OrchestrationStageExecutor._is_substantive_doc_text("x" * 300) is False


class TestDeliveryTargetNormalization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("./src/app.py", "src/app.py"),
            ("`workspace/src/app.py`", "src/app.py"),
            ("https://example.com", ""),
            ("#anchor", ""),
            ("runtime/x.py", ""),
            (".git/config", ""),
            (".polaris/state", ""),
            ("src/", ""),
            ("", ""),
            ("../escape.py", ""),
            ("/abs/path.py", "abs/path.py"),
            ('func main() {\n\tprintln("not a path")\n}', ""),
            (" SortedExhibits returns exhibits ordered by Position ascending.", ""),
            ("src/" + ("x" * 241) + ".go", ""),
        ],
    )
    def test_normalize_declared_delivery_target(self, value: str, expected: str) -> None:
        assert OrchestrationStageExecutor._normalize_declared_delivery_target(value) == expected

    def test_collect_declared_delivery_targets_rejects_code_fragments(self, tmp_path: Path) -> None:
        source_fragment = (
            " SortedExhibits returns exhibits ordered by Position ascending.\n"
            "func (g *Gallery) SortedExhibits() []*Exhibit {\n"
            "\treturn nil\n"
            "}\n"
        )
        executor = _executor(tmp_path)

        targets = executor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["models/gallery.go", source_fragment],
                    "steps": [source_fragment, "run go test ./..."],
                }
            ]
        )

        assert targets == ["models/gallery.go"]

    def test_missing_declared_delivery_targets_handles_invalid_pathlike_input(self, tmp_path: Path) -> None:
        source_fragment = " SortedExhibits returns exhibits ordered by Position ascending.\nfunc broken() {}"
        executor = _executor(tmp_path)

        missing = executor._missing_declared_delivery_targets(
            [{"target_files": ["models/gallery.go", source_fragment]}]
        )

        assert missing == ["models/gallery.go"]

    def test_extend_artifacts_dedupes_and_normalizes(self) -> None:
        artifacts: list[str] = ["a/b.py"]
        OrchestrationStageExecutor._extend_artifacts(artifacts, "a\\b.py", "c.py", "", "c.py")
        assert artifacts == ["a/b.py", "c.py"]


class TestBoolFromContextOrEnv:
    def test_context_value_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "false")
        assert (
            OrchestrationStageExecutor._bool_from_context_or_env(
                {"flag": True}, "flag", env_var="MY_ENV", default=False
            )
            is True
        )

    def test_env_fallback_truthy_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "on")
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", env_var="MY_ENV", default=False) is True

    def test_env_fallback_falsy_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "disabled")
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", env_var="MY_ENV", default=True) is False

    def test_default_when_unset(self) -> None:
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", default=True) is True

    def test_unrecognized_token_returns_default(self) -> None:
        assert OrchestrationStageExecutor._bool_from_context_or_env({"flag": "maybe"}, "flag", default=True) is True


class TestPMDeterministicContractMetadata:
    def test_no_metadata_keeps_pm_llm_path(self) -> None:
        run = FactoryRun(
            id="factory-no-bench",
            config=FactoryConfig(name="regular-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        assert OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(run, {}) == {}

    def test_factory_bench_metadata_enables_preemptive_deterministic_pm(self) -> None:
        run = FactoryRun(
            id="factory-bench",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
            metadata={
                "factory_start_request": {
                    "metadata": {
                        "factory_bench_project_id": "L1-06",
                        "factory_bench_level": 1,
                    }
                }
            },
        )

        metadata = OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(run, {})

        assert metadata["deterministic_pm_contracts"] is True
        assert metadata["factory_bench_project_id"] == "L1-06"
        assert metadata["factory_bench_deterministic_pm"] is True
        assert metadata["pm_route_audit_probe"] is True
        assert metadata["factory_recovery"] == "bench_preemptive_deterministic_contracts"

    def test_explicit_context_flag_enables_deterministic_pm_without_bench_semantics(self) -> None:
        run = FactoryRun(
            id="factory-explicit",
            config=FactoryConfig(name="explicit-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        metadata = OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(
            run,
            {"deterministic_pm_contracts": "yes"},
        )

        assert metadata == {
            "deterministic_pm_contracts": True,
            "factory_recovery": "explicit_deterministic_contracts",
        }


class TestTrimCommandOutput:
    def test_under_limit_unchanged(self) -> None:
        assert OrchestrationStageExecutor._trim_command_output("short", limit=100) == "short"

    def test_over_limit_keeps_tail(self) -> None:
        assert OrchestrationStageExecutor._trim_command_output("abcdef", limit=3) == "def"
