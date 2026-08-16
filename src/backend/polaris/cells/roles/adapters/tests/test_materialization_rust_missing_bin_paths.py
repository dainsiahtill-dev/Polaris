"""Materialization quality must rescan Cargo.toml and allow declared bin creates."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
    _collect_materialization_rust_base_files,
    _declared_rust_binary_paths_from_cargo,
    _rust_missing_binary_paths_from_quality_issues,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _filter_missing_workspace_file_errors_to_task_write_scope,
    _materialization_quality_scan_paths_with_package_manifest,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1
from polaris.kernelone.quality import scan_workspace_artifact_quality


def test_materialization_scan_paths_include_cargo_toml_for_rust_sources(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "demo"',
                'version = "0.1.0"',
                'edition = "2021"',
                "[[bin]]",
                'name = "demo"',
                'path = "src/main.rs"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    paths = _materialization_quality_scan_paths_with_package_manifest(
        workspace_full=str(tmp_path),
        affected_files=["src/lib.rs"],
    )
    assert "Cargo.toml" in paths
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=paths)
    assert any("find bin" in error and "demo" in error for error in errors)


def test_declared_bin_paths_extend_allowed_paths_without_faking_base_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    cargo = "\n".join(
        [
            "[package]",
            'name = "demo"',
            'version = "0.1.0"',
            'edition = "2021"',
            "[[bin]]",
            'name = "demo"',
            'path = "src/main.rs"',
            "",
        ]
    )
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")

    base = _collect_materialization_rust_base_files(tmp_path)
    assert "src/main.rs" not in base
    declared = _declared_rust_binary_paths_from_cargo(base["Cargo.toml"])
    assert declared == ("src/main.rs",)
    allowed = tuple(dict.fromkeys((*base.keys(), *declared)))
    assert "src/main.rs" in allowed


def test_collect_rust_base_files_maps_lowercase_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    (tmp_path / "cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    base = _collect_materialization_rust_base_files(tmp_path)
    assert "Cargo.toml" in base
    assert 'name = "demo"' in base["Cargo.toml"]


def test_rust_missing_bin_paths_from_display_string_issues_include_main_rs() -> None:
    """Display-string rehydration must still allowlist src/main.rs for DEO commit."""

    from polaris.kernelone.quality import artifact_quality_issues_from_errors

    issues = artifact_quality_issues_from_errors(
        ["error: can't find bin `kitchen_flavor_palette` at path `/tmp/ws/src/main.rs`"]
    )
    paths = _rust_missing_binary_paths_from_quality_issues(issues)
    assert paths == ("src/main.rs",)


def test_rust_missing_bin_paths_reject_absolute_path_outside_src() -> None:
    paths = _rust_missing_binary_paths_from_quality_issues(
        (
            {
                "code": "rust_missing_binary_entrypoint",
                "path": "/tmp/elsewhere/escape.rs",
                "metadata": {"bin_path": "/tmp/elsewhere/escape.rs"},
            },
        )
    )
    assert paths == ()


def test_rust_task_defers_missing_bin_owned_by_explicit_downstream_task() -> None:
    err = "error: can't find bin `demo` at path `/tmp/ws/src/main.rs`"
    context = {
        "project_declared_target_files": [
            "Cargo.toml",
            "src/lib.rs",
            "src/models/flavor.rs",
            "src/main.rs",
        ]
    }
    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        [err],
        task={"target_files": ["Cargo.toml", "src/lib.rs", "src/models/flavor.rs"]},
        workspace_full="/tmp/ws",
        workspace_name="",
        context=context,
        issue_payloads=(
            {
                "code": "rust_missing_binary_entrypoint",
                "message": err,
                "path": "src/main.rs",
                "metadata": {"raw": err, "bin_path": "src/main.rs"},
            },
        ),
    )

    assert retained == []
    assert context["director_task_boundary_deferred_quality_errors"] == [
        {
            "schema_version": "director.task_boundary.deferred_quality_errors.v1",
            "reason": "missing_workspace_file_outside_current_task_target_files",
            "artifact_quality_errors": [err],
            "target_files": ["src/main.rs"],
            "artifact_quality_issues": [
                {
                    "code": "rust_missing_binary_entrypoint",
                    "message": err,
                    "path": "src/main.rs",
                    "metadata": {"raw": err, "bin_path": "src/main.rs"},
                }
            ],
        }
    ]


def test_rust_task_retains_missing_bin_without_explicit_project_owner() -> None:
    err = "error: can't find bin `demo` at path `/tmp/ws/src/main.rs`"
    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        [err],
        task={"target_files": ["Cargo.toml", "src/lib.rs", "src/models/flavor.rs"]},
        workspace_full="/tmp/ws",
        workspace_name="",
        context={},
        issue_payloads=(
            {
                "code": "rust_missing_binary_entrypoint",
                "message": err,
                "path": "src/main.rs",
                "metadata": {"raw": err, "bin_path": "src/main.rs"},
            },
        ),
    )

    assert err in retained


def test_rust_task_retains_missing_bin_inside_current_write_scope() -> None:
    err = "error: can't find bin `demo` at path `/tmp/ws/src/main.rs`"
    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        [err],
        task={"target_files": ["Cargo.toml", "src/main.rs"]},
        workspace_full="/tmp/ws",
        workspace_name="",
        context={"project_declared_target_files": ["Cargo.toml", "src/main.rs"]},
        issue_payloads=(
            {
                "code": "rust_missing_binary_entrypoint",
                "message": err,
                "path": "src/main.rs",
                "metadata": {"raw": err, "bin_path": "src/main.rs"},
            },
        ),
    )

    assert retained == [err]


def test_quality_issue_paths_extract_declared_bin() -> None:
    paths = _rust_missing_binary_paths_from_quality_issues(
        (
            {
                "code": "rust_missing_binary_entrypoint",
                "path": "src/main.rs",
                "metadata": {"bin_path": "src/main.rs"},
            },
        )
    )
    assert paths == ("src/main.rs",)


def test_missing_bin_plan_defers_and_synthesizes_write_file_without_bypass(
    tmp_path: Path,
) -> None:
    """Tier-A DEO wiring: quality → plan → deferred → synthesizer write_file; no adapter write."""

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "demo"',
                'version = "0.1.0"',
                'edition = "2021"',
                "[[bin]]",
                'name = "demo"',
                'path = "src/main.rs"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["Cargo.toml", "src/lib.rs"])
    assert any("find bin" in error.lower() for error in errors)

    base = _collect_materialization_rust_base_files(tmp_path)
    allowed = tuple(
        dict.fromkeys(
            (
                *base.keys(),
                *_declared_rust_binary_paths_from_cargo(base.get("Cargo.toml", "")),
            )
        )
    )
    assert "src/main.rs" in allowed
    assert "src/main.rs" not in base

    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=1,
        external_task_id="task-1",
        session_id="sess-missing-bin",
        attempt=1,
        role_id="director",
        worker_id="worker-missing-bin",
        run_id="run-missing-bin",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )

    class _Adapter:
        workspace = str(tmp_path)

    results = run_runtime_repair_with_director_tools(
        _Adapter(),
        workspace_path=tmp_path,
        task_id="task-1",
        source_tool="deterministic_rust_missing_binary_entrypoint_repair",
        execution_attempt=attempt,
        base_files=base,
        artifact_quality_errors=errors,
        allowed_paths=allowed,
    )
    assert len(results) == 1
    payload = results[0].get("result") or {}
    assert payload.get("status") == "deferred_repair_effects_pending"
    request = payload.get("deferred_request")
    assert request is not None
    assert "src/main.rs" in list(payload.get("allowed_paths") or [])

    forward_paths = {
        effect.target_path
        for effect in request.plan.effects
        if effect.contingency_kind == "forward" and effect.tool_name == "write_file"
    }
    assert "src/main.rs" in forward_paths
    # The adapter only emits the public deferred request; tool_batch followup owns commit.
    assert not (tmp_path / "src" / "main.rs").exists()


@pytest.mark.asyncio
async def test_materialization_deferred_commit_lands_main_rs_for_lib_only_runnable_gap(
    tmp_path: Path,
) -> None:
    """No-usable-bin (lib-only runnable) quality → plan → DEO commit physical main.rs."""

    from types import SimpleNamespace

    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        commit_materialization_deferred_repairs,
    )
    from polaris.cells.runtime.task_runtime.public import (
        TaskRuntimeService,
        create_task_runtime_execution_attempt_authority,
    )
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    ToolSpecRegistry._state_var.set(None)

    workspace = str(tmp_path.resolve())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    cargo = "\n".join(
        [
            "[package]",
            'name = "kitchen_flavor_palette"',
            'version = "0.1.0"',
            'edition = "2021"',
            'description = "厨房味觉配色器"',
            "publish = false",
            "",
            "[lib]",
            'name = "kitchen_flavor_palette"',
            'path = "src/lib.rs"',
            "",
        ]
    )
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    errors = scan_workspace_artifact_quality(workspace, relative_paths=["Cargo.toml", "src/lib.rs"])
    assert any("find bin" in error.lower() for error in errors), errors
    assert "src/main.rs" not in _collect_materialization_rust_base_files(tmp_path)

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="materialization-lib-only-deferred-commit-test",
        )
    )
    service = TaskRuntimeService(workspace)
    private_task_id = int(service.create_task_row(subject="lib only deo")["id"])
    attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            private_task_id,
            worker_id="worker-lib-only",
            role_id="director",
            run_id="run-lib-only-commit",
            external_task_id="task-1",
            selection_source="test",
        )["execution_attempt"]
    )
    authority = create_task_runtime_execution_attempt_authority(attempt)
    adapter = SimpleNamespace(workspace=workspace)
    results = run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=tmp_path,
        task_id="task-1",
        source_tool="deterministic_rust_missing_binary_entrypoint_repair",
        execution_attempt=attempt,
        base_files={
            "Cargo.toml": cargo,
            "src/lib.rs": "pub fn ok() {}\n",
        },
        artifact_quality_errors=tuple(errors),
        allowed_paths=("Cargo.toml", "src/lib.rs", "src/main.rs"),
    )
    assert results and results[0]["result"].get("status") == "deferred_repair_effects_pending"
    assert not (tmp_path / "src" / "main.rs").exists()

    followup = await commit_materialization_deferred_repairs(
        workspace=workspace,
        tool_results=results,
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="test-lib-only-deferred",
        context={
            "job_token": {
                "token_id": "job-lib-only",
                "capability_audit": {"ok": True},
                "execution_envelope_hash": "d" * 64,
            }
        },
    )
    assert followup, "DEO followup must return physical write receipts"
    assert (tmp_path / "src" / "main.rs").is_file()
    assert "fn main" in (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_materialization_deferred_commit_lands_main_rs_via_deo_followup(
    tmp_path: Path,
) -> None:
    """DEO followup must physically create declared bin entry without adapter bypass."""

    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        commit_materialization_deferred_repairs,
    )
    from polaris.cells.runtime.task_runtime.public import (
        TaskRuntimeService,
        create_task_runtime_execution_attempt_authority,
    )
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    ToolSpecRegistry._state_var.set(None)

    workspace = str(tmp_path.resolve())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    cargo = "\n".join(
        [
            "[package]",
            'name = "demo"',
            'version = "0.1.0"',
            'edition = "2021"',
            "[[bin]]",
            'name = "demo"',
            'path = "src/main.rs"',
            "",
        ]
    )
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    errors = scan_workspace_artifact_quality(workspace, relative_paths=["Cargo.toml", "src/lib.rs"])
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="materialization-deferred-commit-test",
        )
    )
    service = TaskRuntimeService(workspace)
    private_task_id = int(service.create_task_row(subject="missing bin deo")["id"])
    attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            private_task_id,
            worker_id="worker-missing-bin",
            role_id="director",
            run_id="run-missing-bin-commit",
            external_task_id="task-1",
            selection_source="test",
        )["execution_attempt"]
    )
    authority = create_task_runtime_execution_attempt_authority(attempt)

    from types import SimpleNamespace

    adapter = SimpleNamespace(workspace=workspace)

    results = run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=tmp_path,
        task_id="task-1",
        source_tool="deterministic_rust_missing_binary_entrypoint_repair",
        execution_attempt=attempt,
        base_files={
            "Cargo.toml": cargo,
            "src/lib.rs": "pub fn ok() {}\n",
        },
        artifact_quality_errors=tuple(errors),
        allowed_paths=("Cargo.toml", "src/lib.rs", "src/main.rs"),
    )
    assert results and results[0]["result"].get("status") == "deferred_repair_effects_pending"
    assert not (tmp_path / "src" / "main.rs").exists()

    followup = await commit_materialization_deferred_repairs(
        workspace=workspace,
        tool_results=results,
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="test-materialization-deferred",
        context={
            "job_token": {
                "token_id": "job-missing-bin",
                "capability_audit": {"ok": True},
                "execution_envelope_hash": "c" * 64,
            }
        },
    )
    assert followup, "DEO followup must return physical write receipts"
    assert (tmp_path / "src" / "main.rs").is_file()
    content = (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")
    assert "fn main" in content
