from __future__ import annotations

import time
from pathlib import Path

import pytest
from polaris.cells.director.execution.internal.code_generation_engine import (
    CODE_WRITING_FORBIDDEN_WARNING,
    CodeGenerationPolicyViolationError,
)
from polaris.cells.director.execution.internal.worker_executor import WorkerExecutor
from polaris.domain.entities import Task


def test_extract_tech_stack_rust_not_misdetected_as_typescript() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-1",
        subject="Requirements bootstrap (Rust Api)",
        description="Create initial project files derived from requirements. Use Rust conventions.",
    )
    tech_stack = executor._extract_tech_stack(task)
    assert tech_stack.get("language") == "rust"


def test_extract_tech_stack_go_from_language_context() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-2",
        subject="Requirements implementation (Go Api)",
        description="Implement core module files. Use Go conventions and run go test.",
    )
    tech_stack = executor._extract_tech_stack(task)
    assert tech_stack.get("language") == "go"


def test_extract_tech_stack_prefers_metadata_when_present() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-3",
        subject="Requirements bootstrap (Rust Api)",
        description="Use Rust conventions.",
        metadata={
            "detected_language": "python",
            "detected_framework": "fastapi",
            "project_type": "api",
        },
    )
    tech_stack = executor._extract_tech_stack(task)
    assert tech_stack.get("language") == "python"
    assert tech_stack.get("framework") == "fastapi"


def test_extract_files_from_response_supports_file_header_blocks() -> None:
    executor = WorkerExecutor(workspace=".")
    response = (
        "File: src/service.rs\n"
        "```rust\n"
        "pub fn ping() -> &'static str { \"ok\" }\n"
        "```\n"
    )
    files = executor._extract_files_from_response(response)
    assert len(files) == 1
    assert files[0]["path"] == "src/service.rs"
    assert "ping" in files[0]["content"]


def test_fallback_code_files_from_target_metadata() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-4",
        subject="Implement service",
        description="Implement core module",
        metadata={
            "detected_language": "rust",
            "target_files": ["src/service.rs"],
        },
    )
    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_files(task)


def test_fallback_code_files_javascript_are_syntax_safe_and_have_valid_package_json() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-js-fallback",
        subject="Build JavaScript API server",
        description="Implement service logic and tests in JavaScript.",
        metadata={
            "detected_language": "javascript",
            "target_files": [
                "package.json",
                "src/index.js",
                "src/service.js",
                "src/store.js",
                "tests/service.test.js",
            ],
        },
    )
    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_files(task)


def test_fallback_code_content_generates_dependency_files() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-dep-fallback",
        subject="Bootstrap dependencies",
        description="Create module metadata files.",
        metadata={"detected_language": "go"},
    )

    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_content("go.mod", "go", task)
    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_content("requirements.txt", "python", task)


def test_fallback_code_content_generates_valid_pyproject_toml() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-pyproject-fallback",
        subject="Bootstrap Python project",
        description="Create valid Python dependency metadata.",
        metadata={"detected_language": "python"},
    )

    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_content("pyproject.toml", "python", task)


def test_fallback_code_content_generates_safe_python_package_init() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-python-init-fallback",
        subject="Refresh package exports",
        description="Keep package imports safe after a fallback rewrite.",
        metadata={"detected_language": "python"},
    )

    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_content("src/__init__.py", "python", task)


def test_fallback_code_content_prefers_python_test_templates_over_basename_matches() -> None:
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-python-test-fallback",
        subject="Refresh tests",
        description="Keep Python tests executable after fallback rewrites.",
        metadata={"detected_language": "python"},
    )

    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._fallback_code_content("tests/test_service.py", "python", task)


def test_deterministic_repair_disabled_when_require_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_STRESS_STRICT", "1")
    monkeypatch.setenv("KERNELONE_STRESS_REQUIRE_LLM", "1")
    monkeypatch.setenv("KERNELONE_WORKER_ALLOW_DETERMINISTIC_REPAIR", "0")
    executor = WorkerExecutor(workspace=".")
    assert executor._deterministic_repair_enabled() is False


def test_build_code_generation_rounds_splits_construction_plan(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_CE_ROUND_FILE_CHUNK", "1")
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="task-5",
        subject="Multi-round build",
        description="Apply ChiefEngineer blueprint",
        metadata={
            "construction_plan": {
                "file_plans": [
                    {"path": "src/a.rs"},
                    {"path": "src/b.rs"},
                ]
            }
        },
    )
    rounds = executor._build_code_generation_rounds(task)
    assert len(rounds) == 2
    assert len(rounds[0]) == 1
    assert len(rounds[1]) == 1


def test_apply_response_operations_supports_patch_format(tmp_path: Path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    target = tmp_path / "src" / "role_agent_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def ready() -> bool:\n    return False\n", encoding="utf-8")

    response = (
        "PATCH_FILE: src/role_agent_service.py\n"
        "<<<<<<< SEARCH\n"
        "return False\n"
        "=======\n"
        "return True\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    files_created, errors = executor._apply_response_operations(response)
    assert files_created
    assert errors == []
    assert "return True" in target.read_text(encoding="utf-8")


def test_worker_spin_guard_blocks_identical_loop(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_WORKER_SPIN_MAX_REPEAT", "2")
    executor = WorkerExecutor(workspace=".")
    tracker: dict[str, dict[str, object]] = {}
    executor._register_spin_guard(
        tracker,
        scope="codegen:1/1",
        prompt="same prompt",
        output="same output",
    )
    with pytest.raises(RuntimeError):
        executor._register_spin_guard(
            tracker,
            scope="codegen:1/1",
            prompt="same prompt",
            output="same output",
        )


def test_build_prompt_respects_prompt_size_limit(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_WORKER_PROMPT_MAX_CHARS", "2400")
    executor = WorkerExecutor(workspace=".")
    long_desc = "A" * 12000
    task = Task(
        id="task-6",
        subject="Compact prompt",
        description=long_desc,
        metadata={"target_files": ["src/fastapi_entrypoint.py"]},
    )
    prompt = executor._build_code_generation_prompt(task)
    assert len(prompt) <= 2600


def test_invoke_generation_with_retries_uses_deterministic_repair_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_STRESS_STRICT", "1")
    monkeypatch.setenv("KERNELONE_WORKER_PATCH_RETRIES", "1")
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = Task(
        id="PM-0001-R1",
        subject="Requirements round 1",
        description="Build bootstrap structure for service layer.",
        metadata={
            "phase": "bootstrap",
            "tech_stack": {"language": "python"},
            "construction_plan": {
                "file_plans": [
                    {"path": "src/monolith_service.py", "method_names": ["add_task"]}
                ]
            },
        },
    )

    monkeypatch.setattr(
        executor,
        "_invoke_ollama",
        lambda **_: "need more context to complete this request",
    )

    files, warnings = executor._invoke_generation_with_retries(
        task=task,
        prompt="implement",
        model="dummy",
        per_call_timeout=30,
        deadline_ts=time.time() + 60,
        round_label="1/1",
        round_files=["src/monolith_service.py"],
        spin_tracker={},
    )
    assert files == []
    assert any(CODE_WRITING_FORBIDDEN_WARNING in warning for warning in warnings)


def test_deterministic_repair_uses_phase_hint_for_modify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_WORKER_DETERMINISTIC_REPAIR", "1")
    executor = WorkerExecutor(workspace=".")
    task = Task(
        id="PM-0001-R2",
        subject="Requirements round 2",
        description="Enhance task operations.",
        metadata={
            "phase": "implementation",
            "tech_stack": {"language": "python"},
            "target_files": ["src/monolith_service.py"],
        },
    )
    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._deterministic_repair_files(task, ["src/monolith_service.py"])


def test_deterministic_repair_generates_tests_for_test_only_round(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_STRESS_STRICT", "1")
    executor = WorkerExecutor(workspace=str(tmp_path))
    (tmp_path / "commands.py").write_text(
        "def help_command() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )
    task = Task(
        id="PM-0001-F3",
        subject="Requirements tests",
        description="Create tests for command behavior.",
        metadata={
            "phase": "verification",
            "tech_stack": {"language": "python"},
            "target_files": ["tests/test_commands.py"],
        },
    )

    with pytest.raises(CodeGenerationPolicyViolationError):
        executor._deterministic_repair_files(task, ["tests/test_commands.py"])


def test_invoke_generation_with_retries_uses_rust_deterministic_repair_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_STRESS_STRICT", "1")
    monkeypatch.setenv("KERNELONE_WORKER_PATCH_RETRIES", "1")
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = Task(
        id="PM-0001-F1",
        subject="Requirements bootstrap (Rust Api)",
        description="Create initial project files derived from requirements. Use Rust conventions.",
        metadata={
            "phase": "bootstrap",
            "tech_stack": {"language": "rust"},
            "construction_plan": {
                "file_plans": [
                    {"path": "src/main.rs"},
                    {"path": "src/service.rs"},
                ]
            },
        },
    )

    monkeypatch.setattr(
        executor,
        "_invoke_ollama",
        lambda **_: "need more context to complete this request",
    )

    files, warnings = executor._invoke_generation_with_retries(
        task=task,
        prompt="implement rust bootstrap",
        model="dummy",
        per_call_timeout=30,
        deadline_ts=time.time() + 60,
        round_label="1/1",
        round_files=["src/main.rs", "src/service.rs"],
        spin_tracker={},
    )

    assert files == []
    assert any(CODE_WRITING_FORBIDDEN_WARNING in warning for warning in warnings)
