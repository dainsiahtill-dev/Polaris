"""Characterization tests for the prompt-builder cluster (G7 step 8).

These tests pin the *current* behavior of the code-generation prompt assembly on
``WorkerExecutor`` BEFORE the cluster is extracted into a sibling collaborator
module. The Chinese architecture-hint literals, the module-order truncation, the
construction-hint membership filter / cap, and the three-way ``target_scope_rule``
branch are encoding-sensitive and must stay byte-identical; these tests guard
that drift.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor


def _task(metadata: dict | None = None, *, subject: str = "", description: str = "") -> MagicMock:
    task = MagicMock()
    task.subject = subject
    task.description = description
    task.metadata = metadata if metadata is not None else {}
    return task


# --------------------------------------------------------------------------
# _extract_architecture_context / _get_module_for_task
# --------------------------------------------------------------------------


def test_extract_architecture_context_present() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    arch = {"module_order": ["a", "b"]}
    task = _task({"architecture_context": arch})
    assert executor._extract_architecture_context(task) == arch


def test_extract_architecture_context_default_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    assert executor._extract_architecture_context(_task({})) == {}


def test_extract_architecture_context_non_dict_metadata() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task()
    task.metadata = None
    assert executor._extract_architecture_context(task) == {}


def test_get_module_for_task_present() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"current_module": "renderer"})
    assert executor._get_module_for_task(task) == "renderer"


def test_get_module_for_task_default_unknown() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    assert executor._get_module_for_task(_task({})) == "unknown"
    bad = _task()
    bad.metadata = None
    assert executor._get_module_for_task(bad) == "unknown"


# --------------------------------------------------------------------------
# Architecture hints assembly
# --------------------------------------------------------------------------


def test_prompt_module_order_truncation_and_overflow_note() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "architecture_context": {
                "module_order": ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"],
            },
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "模块构建顺序（底层优先）: m1 -> m2 -> m3 -> m4 -> m5 -> m6" in prompt
    assert "... 及其他 2 个模块" in prompt


def test_prompt_module_arch_layer_deps_stability() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "current_module": "core",
            "architecture_context": {
                "module_arch": {
                    "core": {
                        "layer": 2,
                        "dependencies": ["util", "io"],
                        "stability_score": 0.42,
                    }
                }
            },
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "当前模块: 'core' (层级 L2)" in prompt
    assert "依赖模块: util, io" in prompt
    assert "稳定性 42%" in prompt


def test_prompt_module_arch_low_stability_omits_warning() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "current_module": "core",
            "architecture_context": {"module_arch": {"core": {"layer": 1, "dependencies": [], "stability_score": 0.2}}},
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "当前模块: 'core' (层级 L1)" in prompt
    assert "请设计稳定接口" not in prompt
    assert "依赖模块" not in prompt


def test_prompt_violation_constraints_capped_at_two() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "architecture_context": {
                "constraints": [
                    "❌ no circular deps",
                    "⚠️ keep layers thin",
                    "❌ third constraint ignored",
                    "plain note not flagged",
                ]
            },
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "架构警告: ❌ no circular deps" in prompt
    assert "架构警告: ⚠️ keep layers thin" in prompt
    assert "third constraint ignored" not in prompt
    assert "plain note not flagged" not in prompt


def test_prompt_no_arch_context_default_text() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task)
    assert "- 无全局架构上下文" in prompt


# --------------------------------------------------------------------------
# Construction hints
# --------------------------------------------------------------------------


def test_prompt_construction_hints_steps_join() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "construction_plan": {
                "files": [
                    {
                        "path": "src/a.ts",
                        "implementation_steps": ["step one", "step two", "step three ignored"],
                    }
                ]
            },
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "- src/a.ts: step one; step two" in prompt
    assert "step three ignored" not in prompt


def test_prompt_construction_hints_fallback_when_no_steps() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "construction_plan": {"files": [{"path": "src/a.ts"}]},
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "- src/a.ts: follow ChiefEngineer file plan" in prompt


def test_prompt_construction_hints_membership_filter() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "construction_plan": {
                "files": [
                    {"path": "src/a.ts", "implementation_steps": ["keep me"]},
                    {"path": "src/other.ts", "implementation_steps": ["drop me"]},
                ]
            },
        }
    )
    prompt = executor._build_code_generation_prompt(task)
    assert "- src/a.ts: keep me" in prompt
    assert "src/other.ts" not in prompt


def test_prompt_construction_hints_none_when_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task)
    assert "- no explicit file hints" in prompt


# --------------------------------------------------------------------------
# target_scope_rule three-way branch
# --------------------------------------------------------------------------


def test_target_scope_rule_concrete_targets() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task)
    assert "Concrete target files are declared for this round." in prompt


def test_target_scope_rule_no_targets(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": [], "scope_paths": []})
    prompt = executor._build_code_generation_prompt(task)
    assert "No concrete target files were declared." in prompt
    assert "- (model may decide)" in prompt


def test_target_scope_rule_verification_repair(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/integration/task.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": ["tests/integration/task.test.ts: ../../src/app"],
            },
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    round_paths = [item["path"] for item in rounds[0]]
    prompt = executor._build_code_generation_prompt(task, round_files=round_paths)
    assert "This is a verification repair round." in prompt


# --------------------------------------------------------------------------
# Round header + output contract literals
# --------------------------------------------------------------------------


def test_prompt_round_header_present_when_indexed() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task, round_index=2, round_total=4)
    assert "Build Round: 2/4" in prompt


def test_prompt_round_header_absent_when_zero() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task)
    assert "Build Round:" not in prompt


def test_prompt_output_contract_literals() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": ["src/a.ts"]})
    prompt = executor._build_code_generation_prompt(task)
    assert "You are a software developer implementing a task." in prompt
    assert "Do not output `Command:`" in prompt
    assert "Output contract:" in prompt
    assert "IMPORTANT ARCHITECTURE GUIDELINES:" in prompt


def test_factory_bench_prompt_cap_escalates_from_minimal_env(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_WORKER_PROMPT_MAX_CHARS", "2400")
    executor = WorkerExecutor(workspace="/tmp")
    low_level = _task(
        {
            "factory_bench_project_id": "L1-01",
            "factory_bench_level": 1,
            "target_files": ["src/a.ts"],
        }
    )
    high_level = _task(
        {
            "factory_bench_project_id": "L8-01",
            "factory_bench_level": 8,
            "target_files": ["src/a.ts"],
        }
    )

    assert executor._prompt_builder._resolve_prompt_max_chars(low_level) == 24000
    assert executor._prompt_builder._resolve_prompt_max_chars(high_level) == 40000


def test_prompt_includes_pm_ce_contract_context() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "factory_bench_project_id": "L1-01",
            "factory_bench_level": 1,
            "factory_bench_title": "Firefly Garden Simulator",
            "factory_bench_project_workspace": "/tmp/factory/L1-01",
            "target_files": ["src/a.ts"],
            "acceptance_criteria": ["build passes", "web entrypoint starts"],
            "constraints": ["do not mock success"],
            "quality_gates": ["npm test"],
            "verification_commands": ["npm run build"],
            "entrypoints": ["npm run dev"],
        }
    )

    prompt = executor._build_code_generation_prompt(task)

    assert "=== PM/CE Contract Context ===" in prompt
    assert "Factory bench: L1-01 (L1) - Firefly Garden Simulator" in prompt
    assert "Acceptance criteria: build passes; web entrypoint starts" in prompt
    assert "Constraints: do not mock success" in prompt
    assert "Quality gates: npm test" in prompt
    assert "Verification commands: npm run build" in prompt
    assert "Entrypoints: npm run dev" in prompt


# --------------------------------------------------------------------------
# Dead-but-carried §8 helpers (preserve exact behavior)
# --------------------------------------------------------------------------


def test_extract_functional_requirements_dead_helper() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    # Only ONE leading verb is stripped per line (single re.sub), so "Must" is
    # removed but the following "implement" survives; the "- short" line is
    # dropped for being <=10 chars after cleanup.
    desc = "1. Must implement a clear user login flow with validation\n- short\n* Add a robust session token rotation handler"
    result = executor._extract_functional_requirements(desc)
    assert result == [
        "implement a clear user login flow with validation",
        "a robust session token rotation handler",
    ]


def test_get_framework_guidance_fastapi() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    out = executor._get_framework_guidance("python", "fastapi")
    assert "FastAPI Specific Requirements" in out
    assert "Use Pydantic models" in out


def test_get_framework_guidance_flask() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    out = executor._get_framework_guidance("python", "flask")
    assert "Flask Specific Requirements" in out


def test_get_framework_guidance_non_python_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    assert executor._get_framework_guidance("typescript", "fastapi") == ""
    assert executor._get_framework_guidance("python", None) == ""
    assert executor._get_framework_guidance("python", "django") == ""
