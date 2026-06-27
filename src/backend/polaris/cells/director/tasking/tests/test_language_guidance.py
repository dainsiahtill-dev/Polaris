"""Tests for composable Director language guidance profiles.

All text file operations in these tests use explicit UTF-8 encoding.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from polaris.cells.director.tasking.internal.execution_profile import (
    resolve_director_execution_profile,
)
from polaris.cells.director.tasking.internal.execution_strategy import (
    resolve_director_execution_strategy,
)
from polaris.cells.director.tasking.internal.language_guidance import (
    build_language_section,
    detect_primary_language,
    get_language_guidance,
    get_role_identity,
)
from polaris.cells.director.tasking.internal.task_classifier import (
    classify_task,
    extract_tech_stack,
)


def test_metadata_language_overrides_target_extension() -> None:
    identity, section = build_language_section(
        ["src/app.js"],
        metadata={
            "tech_stack": {
                "language": "python",
                "framework": "fastapi",
            },
            "project_type": "api",
        },
        subject="Implement FastAPI endpoint",
        description="Build a typed API handler with request validation",
    )

    assert "资深 Python 架构师" in identity
    assert "Primary language: Python" in section
    assert "=== Python Language Best Practices ===" in section
    assert "=== FastAPI Framework Best Practices ===" in section
    assert "=== Task Type Best Practices ===" in section
    assert "FastAPI" in section
    assert "Pydantic model" in section
    assert "API/backend service" in section


def test_task_and_file_role_guidance_are_composed() -> None:
    _, section = build_language_section(
        ["tests/test_cli_config.py", "pyproject.toml", "scripts/run_checks.sh"],
        metadata={"project_type": "cli"},
        subject="Fix pytest regression in CLI configuration",
        description="Repair failing tests and keep config syntax valid",
    )

    assert "Write tests / verification" in section
    assert "Bug fix / production repair" in section
    assert "CLI/tooling" in section
    assert "Test/spec file" in section
    assert "Config/manifest file" in section
    assert "Script/automation" in section


def test_go_profile_contains_specific_best_practices() -> None:
    identity, section = build_language_section(
        ["cmd/server/main.go", "internal/service/service.go"],
        metadata={"detected_language": "go", "project_type": "service"},
        subject="Implement concurrent Go service",
        description="Use context cancellation and table driven tests",
    )

    assert "精通 Go" in identity
    assert "=== Go (Golang) Language Best Practices ===" in section
    assert "Effective Go" in identity
    assert "gofmt/goimports" in section
    assert "fmt.Errorf" in section
    assert "context.Context" in section
    assert "goroutine" in section
    assert "table-driven tests" in section


def test_explicit_go_contract_beats_generic_typescript_example_paths() -> None:
    identity, section = build_language_section(
        ["src/engine/renderer.ts"],
        metadata={"project_type": "interactive_visual"},
        subject="实现 情绪涂鸦色轮",
        description=(
            "Project Metadata: 主语言: go. Deterministic Checks: go_compile; "
            "source_target_coverage:**/*.go. 示例路径如 src/engine/renderer.ts 仅用于说明可渲染场景。"
        ),
    )

    assert "精通 Go" in identity
    assert "Primary language: Go (Golang)" in section
    assert "builtin.language.typescript" not in section
    assert "gofmt/goimports" in section


def test_language_profiles_name_language_specific_standards() -> None:
    cases = [
        ("src/app.py", {"detected_language": "python"}, "PEP 8"),
        ("src/app.ts", {"detected_language": "typescript"}, "TypeScript Handbook"),
        ("src/lib.rs", {"detected_language": "rust"}, "Rust API Guidelines"),
        ("src/App.java", {"detected_language": "java"}, "Google Java Style"),
        ("src/Main.kt", {"detected_language": "kotlin"}, "Kotlin Coding Conventions"),
        ("src/Service.cs", {"detected_language": "csharp"}, "Microsoft C# Coding Conventions"),
        ("src/index.php", {"detected_language": "php"}, "PSR-12"),
        ("scripts/deploy.sh", {"detected_language": "shell"}, "Google Shell Style Guide"),
    ]

    for path, metadata, expected_standard in cases:
        identity, section = build_language_section(
            [path],
            metadata=metadata,
            subject="Implement production change",
            description="Follow the language standard",
        )
        combined = f"{identity}\n{section}"
        assert expected_standard in combined


def test_core_task_type_best_practices_are_detected() -> None:
    _, section = build_language_section(
        ["src/reviewer.py", "tests/test_reviewer.py"],
        metadata={"task_type": "review"},
        subject="Review and refactor bug-prone Python code",
        description="Find hidden bug risk, repair the regression, and add pytest coverage",
    )

    assert "Write code / implement feature" in section
    assert "Code review / audit" in section
    assert "Refactor code" in section
    assert "Bug fix / production repair" in section
    assert "Write tests / verification" in section


def test_detect_primary_language_falls_back_to_workspace(tmp_path) -> None:
    source = tmp_path / "main.rs"
    source.write_text("fn main() {}\n", encoding="utf-8")

    assert detect_primary_language([], tmp_path) == "rust"


def test_unknown_language_uses_generic_identity() -> None:
    assert get_language_guidance("unknown-lang") == ""
    assert "资深代码架构师" in get_role_identity("unknown-lang")
    assert "软件工程师" not in get_role_identity("unknown-lang")


def test_role_identity_composes_language_framework_task_and_file_role() -> None:
    identity, section = build_language_section(
        ["src/components/ProfileCard.tsx", "src/components/ProfileCard.css"],
        metadata={"project_type": "frontend", "framework": "react"},
        subject="Refactor React profile card UI",
        description="Improve accessibility, layout stability, and visual states",
    )

    assert "资深 TypeScript 前端/全栈架构师" in identity
    assert "TypeScript 前端工程师" in identity
    assert "UI 用户体验工程师" in identity
    assert "遗留系统重构专家" in identity
    assert "Frontend/UI" in section


def test_execution_profile_is_single_source_for_dispatch_guidance_and_temperature() -> None:
    profile = resolve_director_execution_profile(
        subject="Fix Go API regression",
        description="Repair the gin handler bug and add table driven tests",
        metadata={"project_type": "api"},
        target_files=["internal/http/handler.go", "internal/http/handler_test.go"],
        scope_paths=["internal/http"],
    )

    assert profile.schema_version == "task.execution_profile.v1"
    assert profile.dispatch_type == "code_generation"
    assert profile.task_type == "bugfix"
    assert profile.phase == "repair"
    assert profile.project_type == "api"
    assert profile.language == "go"
    assert profile.temperature_phase == "repair"
    assert profile.temperature == 0.05
    assert "source" in profile.file_roles
    assert "test" in profile.file_roles


def test_quality_repair_marker_overrides_review_words_for_precise_repair_profile() -> None:
    profile = resolve_director_execution_profile(
        subject="Artifact quality scan review failed",
        description=(
            "MATERIALIZATION QUALITY REPAIR MODE: previous write failed Polaris artifact quality gates. "
            "MISSING TARGET FILES - create src/main.py. Quality errors: declared target file missing."
        ),
        metadata={"phase": "code_review"},
        target_files=["src/main.py"],
        scope_paths=[],
    )

    assert profile.task_type == "bugfix"
    assert profile.phase == "repair"
    assert profile.temperature_phase == "repair"
    assert profile.temperature == 0.05
    assert profile.signal_evidence["task_type_source"] == "quality_repair_marker"


def test_execution_strategy_derives_large_budget_from_profile() -> None:
    profile = resolve_director_execution_profile(
        subject="Implement TypeScript dashboard feature",
        description="Create React UI, state management, tests, and repair build failures",
        metadata={"project_type": "frontend", "framework": "react"},
        target_files=[
            "src/App.tsx",
            "src/state/store.ts",
            "src/components/Panel.tsx",
            "tests/App.test.tsx",
            "package.json",
        ],
        scope_paths=["src", "tests"],
    )

    strategy = resolve_director_execution_strategy(profile, metadata={"quality_gates": ["npm test"]})

    assert strategy.schema_version == "task.execution_strategy.v1"
    assert strategy.temperature == profile.temperature
    assert strategy.output_budget_tokens == 128_000
    assert strategy.input_budget_tokens >= 128_000
    assert strategy.prompt_max_chars >= strategy.input_budget_tokens * 4
    assert "architecture_or_file_plan" in strategy.evidence_requirements


def test_execution_strategy_overrides_project_to_context_gateway_controls() -> None:
    from polaris.cells.director.tasking.internal.execution_strategy import apply_execution_strategy_overrides

    profile = resolve_director_execution_profile(
        subject="Fix Python service regression",
        description="Repair failing pytest coverage in the API handler",
        metadata={"project_type": "api"},
        target_files=["src/api.py", "tests/test_api.py"],
        scope_paths=["src", "tests"],
    )
    strategy = resolve_director_execution_strategy(profile)
    context: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    apply_execution_strategy_overrides(
        context=context,
        metadata=metadata,
        profile=profile,
        strategy=strategy,
    )

    assert context["llm_max_tokens"] == strategy.output_budget_tokens
    assert context["_transaction_kernel_temperature_override"] == profile.temperature
    assert context["task_execution_contract"]["schema_version"] == "task.execution_contract.v1"
    assert context["task_execution_contract"]["task_type"] == profile.task_type
    assert context["task_execution_contract"]["sampling"]["temperature"] == strategy.temperature
    assert context["task_execution_contract"]["context_budget"]["output_budget_tokens"] == strategy.output_budget_tokens
    assert (
        context["task_execution_contract"]["prompt_protocol"]["language_guidance_source"]
        == "director.tasking.language_guidance.select_guidance"
    )
    assert context["task_execution_contract"]["prompt_protocol"]["language"] == profile.language
    assert context["task_execution_contract"]["quality_contract"]["evidence_requirements"] == list(
        strategy.evidence_requirements
    )
    assert metadata["task_execution_contract"] == context["task_execution_contract"]
    assert context["cognitive_strategy_override"]["cognitive_runtime"]["applied"] is True
    assert context["cognitive_strategy_override"]["read_escalation"]["full_read_allowed"] is True
    assert metadata["cognitive_strategy_override"]["task_execution"]["schema_version"] == strategy.schema_version


def test_execution_strategy_overrides_publish_execution_envelope_to_context_and_metadata() -> None:
    from polaris.cells.director.tasking.internal.execution_strategy import apply_execution_strategy_overrides

    profile = resolve_director_execution_profile(
        subject="Implement Python CLI",
        description="Build the scoped CLI and tests",
        metadata={"project_type": "cli", "language": "python"},
        target_files=["src/main.py"],
        scope_paths=["src"],
    )
    strategy = resolve_director_execution_strategy(profile)
    strict_handoff_decision = {
        "schema_version": "polaris.ce_handoff_decision.v1",
        "decision_id": "ce-handoff-1",
        "task_id": "TASK-1",
        "blueprint_id": "ce_TASK-1",
        "allowed": True,
        "decision_hash": "handoff-hash",
        "bindings": {
            "pm_contract_ref": "tasks/plan.json",
            "pm_contract_hash": "pm-hash",
            "blueprint_ref": "runtime/blueprints/ce_TASK-1.json",
            "blueprint_hash": "blueprint-hash",
            "execution_profile_ref": "runtime/contracts/profile.json",
            "execution_profile_hash": "profile-hash",
        },
    }
    context: dict[str, Any] = {
        "workspace": "/workspace",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "ce_handoff_decision": strict_handoff_decision,
    }
    metadata: dict[str, Any] = {
        "task_id": "TASK-1",
        "job_token": {
            "token_id": "job-1",
            "allowed_paths": ["src/main.py"],
            "target_files": ["src/main.py"],
            "allowed_commands": ["python --version"],
        },
    }

    apply_execution_strategy_overrides(
        context=context,
        metadata=metadata,
        profile=profile,
        strategy=strategy,
    )

    envelope = context["director_execution_envelope"]
    assert envelope == context["task_execution_envelope"]
    assert envelope == metadata["director_execution_envelope"]
    assert context["execution_envelope_hash"] == envelope["envelope_hash"]
    assert metadata["execution_envelope_hash"] == envelope["envelope_hash"]
    assert envelope["pm_contract"] == {"ref": "tasks/plan.json", "hash": "pm-hash"}
    assert envelope["ce_blueprint"] == {
        "ref": "runtime/blueprints/ce_TASK-1.json",
        "hash": "blueprint-hash",
    }
    assert envelope["handoff_decision"] == {"ref": "", "hash": "handoff-hash", "allowed": True}
    assert envelope["execution_profile"] == {
        "ref": "runtime/contracts/profile.json",
        "hash": "profile-hash",
    }
    assert envelope["authorization"]["capability_token_ref"] == "job-1"
    assert envelope["authorization"]["allowed_write_paths"] == ["src/main.py"]
    assert envelope["authorization"]["allowed_commands"] == ["python --version"]


def test_legacy_task_classifier_delegates_to_execution_profile() -> None:
    task = MagicMock()
    task.subject = "Build service"
    task.description = "Create a golang microservice"
    task.metadata = {}

    assert classify_task(task) == "code_generation"
    assert extract_tech_stack(task) == {
        "language": "go",
        "project_type": "service",
    }
