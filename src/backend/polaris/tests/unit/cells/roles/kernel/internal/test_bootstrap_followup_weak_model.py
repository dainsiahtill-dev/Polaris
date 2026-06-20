"""Phase-2 wave-2: bootstrap follow-up must be transcribable, durable, and non-destructive.

Covers the django-11630 live findings (2026-06-10):
- bootstrap read content was truncated to a 1200-char JSON fragment, making correct
  SEARCH/REPLACE transcription impossible by construction;
- bootstrap read results never reached the session event stream, so later turns had
  no trace the file was ever read (models then hallucinate SEARCH text from memory);
- the deterministic write_file fallback planted scaffolding stubs (main.py) for
  targets the user never named, reinforcing weak-model task drift.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    RetryOrchestrator,
    _should_force_leaf_bootstrap_followup_write_file,
    build_deterministic_bootstrap_followup_write_decision,
    build_retry_write_after_bootstrap_context,
    merge_bootstrap_receipt_into_result,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _recent_edit_failure_in_context,
)

# ---------------------------------------------------------------------------
# build_retry_write_after_bootstrap_context — real content must be transcribable
# ---------------------------------------------------------------------------


def _receipt_with_content(content: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "django/core/checks/model_checks.py", "content": content},
                "arguments": {"file": "django/core/checks/model_checks.py"},
            }
        ]
    }


def test_bootstrap_context_carries_real_file_content_beyond_old_fragment_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Transcription fidelity is tested with a generous budget (a large-window
    # provider); the I3-r22 window-sized default is exercised separately below.
    monkeypatch.setenv("KERNELONE_BOOTSTRAP_READ_TOTAL_CHARS", "16000")
    monkeypatch.setenv("KERNELONE_BOOTSTRAP_READ_MAX_CHARS", "9000")
    content = "\n".join(f"line {i}: db_table_models[model._meta.db_table].append(x)" for i in range(120))
    assert len(content) > 1200  # would have been truncated to a fragment before
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix models.E028 in the checks module"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "line 119:" in rendered  # full content present, not the 1200-char fragment
    assert "exact file content follows" in rendered


def test_bootstrap_content_window_sized_default_caps_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """I3-r22 (F10): the default read-content budget is small (sized for a 16k
    local-Director window) so a large transcription payload cannot collapse the
    output budget. 120 lines (~6k chars) is truncated under the default."""
    monkeypatch.delenv("KERNELONE_BOOTSTRAP_READ_TOTAL_CHARS", raising=False)
    monkeypatch.delenv("KERNELONE_BOOTSTRAP_READ_MAX_CHARS", raising=False)
    content = "\n".join(f"line {i}: db_table_models[model._meta.db_table].append(x)" for i in range(120))
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "[content truncated]" in rendered  # capped under the small default
    assert "line 0:" in rendered  # the head is still transcribable
    assert "line 119:" not in rendered  # tail dropped to protect the output budget


def test_bootstrap_context_unwraps_executor_envelope() -> None:
    content = "def real_function():\n    return 42\n"
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                # executor envelope shape: {"ok": ..., "result": {...}}
                "result": {"ok": True, "result": {"file": "a.py", "content": content}},
                "arguments": {"file": "a.py"},
            }
        ]
    }
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix a.py"}],
        bootstrap_receipt=receipt,
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "def real_function():" in rendered


def test_bootstrap_context_caps_giant_content() -> None:
    content = "x" * 50_000
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "[content truncated]" in rendered
    assert len(rendered) < 30_000


def test_bootstrap_context_non_content_results_keep_fragment_form() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "repo_rg",
                "status": "success",
                "result": {"returned_count": 3, "results": ["a", "b", "c"]},
                "arguments": {"pattern": "E028"},
            }
        ]
    }
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=receipt,
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "returned_count" in rendered
    assert "exact file content follows" not in rendered


# ---------------------------------------------------------------------------
# C3 (2026-06-16 deliberation): successful-files write-steer must be suppressed
# on a from-scratch CREATE. The real target cannot have been read (it does not
# exist on disk yet), so steering the weak Director to "write only to the files
# you successfully read" points it at adjacent context files instead of the new
# target -> 0 correct-file output (write-convergence wall). The fix is guard-only
# + default-False, so the edit-existing path is byte-for-byte unchanged.
# ---------------------------------------------------------------------------


def _receipt_adjacent_read_plus_failed_target(adjacent: str, target: str) -> dict[str, Any]:
    """A bootstrap that successfully read an ADJACENT context file and failed to
    read the (not-yet-existing) TARGET file — the exact from-scratch-create shape.
    """
    return {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": adjacent, "content": "export const helper = 1;\n"},
                "arguments": {"file": adjacent},
            },
            {
                "tool_name": "read_file",
                "status": "error",
                "result": {"error": f"File not found: {target}"},
                "arguments": {"file": target},
            },
        ]
    }


def test_successful_files_steer_present_on_edit_existing_default() -> None:
    """Default (from_scratch_create omitted == False) keeps the read-existing
    steer byte-for-byte: this is the legitimate edit-existing turn where the
    write target IS among the successfully-read files."""
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "edit helper.ts to fix the bug"}],
        bootstrap_receipt=_receipt_adjacent_read_plus_failed_target("helper.ts", "missing.ts"),
        forced_write_tool_name="write_file",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "selected from successfully-read files only" in rendered
    assert "helper.ts" in rendered


def test_successful_files_steer_suppressed_on_from_scratch_create() -> None:
    """from_scratch_create=True drops the read-existing steer (which would point
    at the adjacent helper.ts instead of the new game.ts), while KEEPING the
    failed-file create guidance, which is correct for a create."""
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "create game.ts implementing the loop"}],
        bootstrap_receipt=_receipt_adjacent_read_plus_failed_target("helper.ts", "game.ts"),
        forced_write_tool_name="write_file",
        from_scratch_create=True,
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    # The read-existing steer (and the adjacent file it would name) must be gone.
    assert "selected from successfully-read files only" not in rendered
    assert "helper.ts" not in rendered
    # The create guidance for the failed/not-yet-existing target is still emitted.
    assert "game.ts" in rendered
    assert "use write_file/create_file/append_to_file" in rendered


def test_from_scratch_create_with_no_successful_reads_is_unaffected() -> None:
    """When the bootstrap read nothing successfully, the steer was already absent;
    the guard must be a no-op there (no spurious change to the create path)."""
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "create app.ts"}],
        bootstrap_receipt=_failed_read_receipt("app.ts"),
        forced_write_tool_name="write_file",
        from_scratch_create=True,
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "selected from successfully-read files only" not in rendered
    assert "app.ts" in rendered  # still steered to create the failed target


# ---------------------------------------------------------------------------
# deterministic write_file fallback — never stomp, never invent
# ---------------------------------------------------------------------------


def _failed_read_receipt(path: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "tool_name": "read_file",
                "status": "error",
                "result": {"error": f"File not found: {path}"},
                "arguments": {"file": path},
            }
        ]
    }


def test_deterministic_fallback_skips_target_not_named_by_user(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-1",
        original_context=[{"role": "user", "content": "fix the db_table checks bug"}],
        bootstrap_receipt=_failed_read_receipt("main.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_deterministic_fallback_skips_existing_file(tmp_path: Path) -> None:
    (tmp_path / "demo_app.py").write_text("real_source = True\n", encoding="utf-8")
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-2",
        original_context=[{"role": "user", "content": "please create demo_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("demo_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_deterministic_fallback_suppresses_unknown_user_named_new_file(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-3",
        original_context=[{"role": "user", "content": "please create demo_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("demo_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_deterministic_fallback_prefers_structured_scope_over_design_mentions(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-structured-scope",
        original_context=[
            {
                "role": "user",
                "content": (
                    "任务: 需求对齐与实现设计\n"
                    "范围: docs/design.md, docs/\n"
                    "执行步骤:\n"
                    "- 定义模块划分：main.py（入口）、parser.py（解析）、evaluator.py（计算）、errors.py（异常）\n"
                    "验收标准:\n"
                    "- design.md 存在且包含模块接口定义\n"
                ),
            }
        ],
        bootstrap_receipt={"results": []},
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is not None
    assert decision.metadata.get("target_file") == "docs/design.md"


# ---------------------------------------------------------------------------
# I3-r21 root fix: leaf-construction suppression (rank 2) + refuse-to-guess (rank 1)
# ---------------------------------------------------------------------------


def _leaf_step_context(target_file: str, *, verify: str, named_files: str) -> list[dict[str, Any]]:
    """A turn context carrying a CE construction-step card (a leaf step execution)."""
    return [
        {"role": "user", "content": f"build the game across {named_files}"},
        {
            "role": "user",
            "content": "execute the construction step",
            "context_override": {
                "construction_step": {
                    "step_id": "PM-0001-1-S4",
                    "target_file": target_file,
                    "verify": verify,
                }
            },
        },
    ]


def test_leaf_construction_step_suppresses_write_fallback(tmp_path: Path) -> None:
    """A leaf step naming many files must NOT plant a stub for ANY of them — the
    placeholder can never satisfy the real verify and only poisons the owner.
    This is the live r21 main.js clobber: S4(readme.md) executing wrote main.js.
    """
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "readme.md",
            verify="test -f main.js && node --check main.js && grep -q 'class Paddle' main.js",
            named_files="index.html style.css main.js readme.md",
        ),
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_leaf_test_target_uses_safe_calculator_test_fallback(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "tests/test_calculator.py",
            verify="python tests/test_calculator.py && python calculator.py",
            named_files="calculator.py README.md tests/test_calculator.py expression calculator",
        ),
        bootstrap_receipt=_failed_read_receipt("tests/test_calculator.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_leaf_test_write_file"
    assert decision.tool_batch is not None
    invocation = decision.tool_batch.invocations[0]
    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"]["file"] == "tests/test_calculator.py"
    content = invocation["arguments"]["content"]
    assert "class CalculatorBehaviorTests" in content
    assert "parse_and_evaluate" in content
    assert "evaluate_expression('2+3*4')" in content
    assert "unittest.main()" in content
    assert "index.html" not in content
    assert "styles.css" not in content


def test_leaf_unknown_typescript_test_target_suppresses_placeholder_fallback(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "src/tests/core.test.ts",
            verify="npx jest src/tests/core.test.ts --testNamePattern='Flower emotion|MoonPhase progression'",
            named_files="src/models/flower.ts src/models/moonphase.ts src/tests/core.test.ts",
        ),
        bootstrap_receipt=_failed_read_receipt("src/tests/core.test.ts"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is None


def test_failed_existing_calculator_test_repair_rewrites_safe_test_target(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# CLI Calculator\n", encoding="utf-8")
    (tmp_path / "calculator.py").write_text("def evaluate(expression: str) -> int:\n    return 1\n", encoding="utf-8")
    test_path = tmp_path / "tests" / "test_calculator.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_bad_generated_assertion():\n    assert False\n", encoding="utf-8")

    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=[
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n"
                    "任务: 实现README 文档与运行验证\n"
                    "范围: README.md, calculator.py, tests/test_calculator.py\n"
                    "MATERIALIZATION QUALITY REPAIR MODE:\n"
                    "EXISTING FAILED TARGET FILES — rewrite these exact paths NOW:\n"
                    "- tests/test_calculator.py\n"
                    "SINGLE FAILED TARGET REPAIR:\n"
                    "[director_quality_repair:write_only_single_target]\n"
                    "- Target path: tests/test_calculator.py\n"
                    "Quality errors:\n"
                    "- Artifact quality scan failed: python runtime smoke crashed for 'tests/test_calculator.py'\n"
                    "The calculator expression tests cover 计算器 表达式 四则 运算.\n"
                ),
            }
        ],
        bootstrap_receipt={"results": []},
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_write_file"
    assert decision.tool_batch is not None
    invocation = decision.tool_batch.invocations[0]
    assert invocation["arguments"]["file"] == "tests/test_calculator.py"
    content = invocation["arguments"]["content"]
    assert "class CalculatorBehaviorTests" in content
    assert "unittest.main()" in content
    assert "assert False" not in content


def test_calculator_unittest_fallback_accepts_string_returning_calculator(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "def evaluate(expression: str) -> str:\n"
        "    compact = expression.replace(' ', '')\n"
        "    if compact == '2+3*4':\n"
        "        return '14'\n"
        "    if compact == '(2+3)*4':\n"
        "        return '20'\n"
        "    if compact == '7/2':\n"
        "        return '3.5'\n"
        "    return '错误: invalid expression'\n",
        encoding="utf-8",
    )
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "tests/test_calculator.py",
            verify="python -m unittest discover -s tests -p 'test_*.py' -v",
            named_files="calculator.py tests/test_calculator.py expression calculator",
        ),
        bootstrap_receipt=_failed_read_receipt("tests/test_calculator.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.tool_batch is not None
    invocation = decision.tool_batch.invocations[0]
    test_path = tmp_path / invocation["arguments"]["file"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(invocation["arguments"]["content"], encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_leaf_calculator_source_target_uses_runnable_cli_fallback(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "calculator.py",
            verify="python -m py_compile calculator.py && python calculator.py '2+3*4'",
            named_files="calculator.py README.md tests/test_calculator.py expression calculator",
        ),
        bootstrap_receipt=_failed_read_receipt("calculator.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_calculator_source_write_file"
    assert decision.tool_batch is not None
    invocation = decision.tool_batch.invocations[0]
    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"]["file"] == "calculator.py"
    content = invocation["arguments"]["content"]
    assert "def parse_and_evaluate" in content
    assert "class CalculatorError" in content
    assert "workspace_artifact_ready" not in content


def test_non_leaf_calculator_source_target_uses_runnable_cli_fallback(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=[
            {
                "role": "user",
                "content": (
                    "任务: 核心计算器实现与 README\n"
                    "范围: calculator.py, README.md\n"
                    "MATERIALIZATION QUALITY REPAIR MODE:\n"
                    "MISSING TARGET FILES — create these exact paths NOW:\n"
                    "- calculator.py\n"
                    "Target path: calculator.py\n"
                    "需求: CLI calculator expression arithmetic 计算器 表达式 四则 运算\n"
                ),
            }
        ],
        bootstrap_receipt={"results": []},
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.tool_batch is not None
    by_file = {
        invocation["arguments"]["file"]: invocation["arguments"]["content"]
        for invocation in decision.tool_batch.invocations
    }
    assert set(by_file) == {"calculator.py", "README.md"}
    assert "def parse_and_evaluate" in by_file["calculator.py"]
    assert "class CalculatorError" in by_file["calculator.py"]
    assert "workspace_artifact_ready" not in by_file["calculator.py"]
    assert "python calculator.py" in by_file["README.md"]


def test_non_leaf_calculator_verification_targets_use_safe_fallbacks(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=[
            {
                "role": "user",
                "content": (
                    "任务: 实现功能验证与 QA 闭环\n"
                    "范围: tests/test_calculator.py, tests/qa_report.md\n"
                    "MATERIALIZATION QUALITY REPAIR MODE:\n"
                    "MISSING TARGET FILES — create these exact paths NOW:\n"
                    "- tests/test_calculator.py\n"
                    "- tests/qa_report.md\n"
                    "需求: CLI calculator expression arithmetic 计算器 表达式 四则 运算\n"
                ),
            }
        ],
        bootstrap_receipt={"results": []},
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.tool_batch is not None
    by_file = {
        invocation["arguments"]["file"]: invocation["arguments"]["content"]
        for invocation in decision.tool_batch.invocations
    }
    assert set(by_file) == {"tests/test_calculator.py", "tests/qa_report.md"}
    assert "class CalculatorBehaviorTests" in by_file["tests/test_calculator.py"]
    assert "python -m unittest discover" in by_file["tests/qa_report.md"]


def test_leaf_small_bootstrap_target_forces_write_file_followup() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {
                    "file": "calculator.py",
                    "content": "def main():\n    print('placeholder')\n",
                },
                "arguments": {"file": "calculator.py"},
            }
        ]
    }

    assert (
        _should_force_leaf_bootstrap_followup_write_file(
            original_context=_leaf_step_context(
                "calculator.py",
                verify="python calculator.py",
                named_files="calculator.py README.md",
            ),
            bootstrap_receipt=receipt,
            allowed_tool_names={"edit_blocks", "write_file"},
        )
        is True
    )


def test_declared_target_line_without_step_forces_small_leaf_write_file_followup() -> None:
    """Director prompts may carry only text fields such as ``范围``/``目标文件``.

    The weak-model bootstrap follow-up still needs the small-leaf write_file
    path in that shape; otherwise it remains locked onto edit_blocks and can
    circuit-break on repeated no-op edit blocks.
    """
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {
                    "file": "README.md",
                    "content": "# 项目运行说明\n\n运行 `python main.py`。\n",
                },
                "arguments": {"file": "README.md"},
            }
        ]
    }

    assert (
        _should_force_leaf_bootstrap_followup_write_file(
            original_context=[
                {
                    "role": "user",
                    "content": (
                        "[mode:materialize]\n"
                        "任务: 编写运行说明文档\n"
                        "范围: README.md\n"
                        "目标文件: README.md\n"
                        "验收标准:\n"
                        "- grep -Fq 'python main.py' README.md\n"
                    ),
                }
            ],
            bootstrap_receipt=receipt,
            allowed_tool_names={"edit_blocks", "write_file"},
        )
        is True
    )


def test_leaf_existing_bootstrap_target_uses_current_content_write_fence(tmp_path: Path) -> None:
    """When a weak Director reads an existing leaf target and then fails to emit
    any write tool, the deterministic fallback may replay the exact current
    content through write_file. This satisfies the transaction write fence
    without synthesizing business code or planting a placeholder.
    """
    current_content = "def main() -> None:\n    print('ready')\n"
    (tmp_path / "main.py").write_text(current_content, encoding="utf-8")
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001--PM-0001-3-S2",
        original_context=_leaf_step_context(
            "main.py",
            verify="python -m py_compile main.py",
            named_files="main.py README.md",
        ),
        bootstrap_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"file": "main.py", "content": current_content},
                    "arguments": {"file": "main.py"},
                }
            ]
        },
        allowed_tool_names={"edit_blocks", "write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("target_file") == "main.py"
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_existing_file_write_file_fence"
    tool_batch = decision.tool_batch
    assert tool_batch is not None
    invocation = tool_batch.invocations[0]
    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "main.py", "content": current_content}


def test_leaf_existing_large_bootstrap_target_keeps_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS", "20")
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001--PM-0001-3-S2",
        original_context=_leaf_step_context(
            "main.py",
            verify="python -m py_compile main.py",
            named_files="main.py README.md",
        ),
        bootstrap_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"file": "main.py", "content": "print('too large for the fence')\n"},
                    "arguments": {"file": "main.py"},
                }
            ]
        },
        allowed_tool_names={"edit_blocks", "write_file"},
        workspace=".",
    )
    assert decision is None


def test_leaf_large_bootstrap_target_keeps_edit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS", "100")
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {
                    "file": "large_module.py",
                    "content": "print('real code')\n" * 20,
                },
                "arguments": {"file": "large_module.py"},
            }
        ]
    }

    assert (
        _should_force_leaf_bootstrap_followup_write_file(
            original_context=_leaf_step_context(
                "large_module.py",
                verify="python -m py_compile large_module.py",
                named_files="large_module.py",
            ),
            bootstrap_receipt=receipt,
            allowed_tool_names={"edit_blocks", "write_file"},
        )
        is False
    )


def test_non_leaf_bootstrap_target_does_not_force_write_file() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "calculator.py", "content": "def main():\n    pass\n"},
                "arguments": {"file": "calculator.py"},
            }
        ]
    }

    assert (
        _should_force_leaf_bootstrap_followup_write_file(
            original_context=[{"role": "user", "content": "edit calculator.py"}],
            bootstrap_receipt=receipt,
            allowed_tool_names={"edit_blocks", "write_file"},
        )
        is False
    )


def test_non_leaf_refuses_to_guess_among_multiple_targets(tmp_path: Path) -> None:
    """Without a declared single target, >1 user-named new file is ambiguous;
    picking viable_targets[0] is the wrong-file bug. Refuse rather than guess."""
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-multi",
        original_context=[{"role": "user", "content": "please create index.html and main.js and style.css for me"}],
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_non_leaf_support_files_multitarget_fallback_writes_readme_and_tests(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<html><body><main><section><article></article></section></main></body></html>", encoding="utf-8"
    )
    (tmp_path / "styles.css").write_text(
        "main { display: grid; }\nsection { display: flex; }\n@media (max-width: 768px) {}\n@media print {}\n",
        encoding="utf-8",
    )
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-support-files",
        original_context=[
            {
                "role": "user",
                "content": (
                    "范围: README.md, index.html, styles.css, tests/test_product.py\n"
                    "编写 README.md 并创建 tests/test_product.py 验证 HTML/CSS 产物"
                ),
            }
        ],
        bootstrap_receipt=_failed_read_receipt("README.md"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_support_files_write_file"
    assert decision.metadata.get("target_files") == ["README.md", "tests/test_product.py"]
    assert decision.tool_batch is not None
    invocations = decision.tool_batch.invocations
    assert [item["arguments"]["file"] for item in invocations] == ["README.md", "tests/test_product.py"]
    assert "python -m pytest tests/test_product.py" in invocations[0]["arguments"]["content"]
    assert 'read_text(encoding="utf-8")' in invocations[1]["arguments"]["content"]


def test_non_leaf_static_web_multitarget_fallback_writes_resume_files(tmp_path: Path) -> None:
    (tmp_path / "requirements.md").write_text(
        "# Product Requirements\n\n用纯 HTML5/CSS3 制作个人简历静态页面。\n",
        encoding="utf-8",
    )

    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-static-web",
        original_context=[
            {
                "role": "user",
                "content": (
                    "范围: requirements.md, index.html, styles.css, README.md\n"
                    "完整可运行的实现落盘到工作区根，并附 README.md 说明如何运行。"
                ),
            }
        ],
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.metadata.get("deterministic_recovery") == "bootstrap_followup_support_files_write_file"
    assert decision.metadata.get("target_files") == ["index.html", "styles.css", "README.md"]
    assert decision.tool_batch is not None
    invocations = decision.tool_batch.invocations
    assert [item["arguments"]["file"] for item in invocations] == ["index.html", "styles.css", "README.md"]
    html = invocations[0]["arguments"]["content"]
    css = invocations[1]["arguments"]["content"]
    readme = invocations[2]["arguments"]["content"]
    assert "<main" in html
    assert '<link rel="stylesheet" href="styles.css">' in html
    assert "display: flex" in css
    assert "display: grid" in css
    assert css.count("@media") >= 2
    assert "python -m http.server 8000" in readme


def test_static_web_fallback_does_not_rewrite_requirements_input_doc(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-static-web-input-doc",
        original_context=[
            {
                "role": "user",
                "content": (
                    "Mutation target files detected from user request: requirements.md, index.html, styles.css. "
                    "Ensure the write step touches at least one target file."
                ),
            }
        ],
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )

    assert decision is not None
    assert decision.tool_batch is not None
    invocations = decision.tool_batch.invocations
    assert [item["arguments"]["file"] for item in invocations] == ["index.html", "styles.css"]
    assert all(item["arguments"]["file"] != "requirements.md" for item in invocations)


def test_non_leaf_unknown_single_target_suppressed(tmp_path: Path) -> None:
    """A single unknown source target must still fail closed instead of getting
    a generic workspace_artifact_ready placeholder."""

    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-single",
        original_context=[{"role": "user", "content": "please create config_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("config_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


# ---------------------------------------------------------------------------
# verification-read exemption helper
# ---------------------------------------------------------------------------


def test_recent_edit_failure_detected_in_context_tail() -> None:
    context = [
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": "**edit_blocks**: Error - Validation failed for 1 block(s)."},
        {"role": "user", "content": "continue"},
    ]
    assert _recent_edit_failure_in_context(context) is True


def test_recent_edit_failure_respects_lookback() -> None:
    context = [{"role": "assistant", "content": "No valid edit blocks found in input"}] + [
        {"role": "user", "content": f"turn {i}"} for i in range(10)
    ]
    assert _recent_edit_failure_in_context(context, lookback=8) is False


def test_recent_edit_failure_handles_non_list_context() -> None:
    assert _recent_edit_failure_in_context(None) is False
    assert _recent_edit_failure_in_context(123) is False
    assert _recent_edit_failure_in_context([{"role": "user", "content": "all good"}]) is False


# ---------------------------------------------------------------------------
# execute_read_bootstrap_batch — receipts must reach the event stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_reads_emit_tool_result_events() -> None:
    emitted: list[Any] = []
    orchestrator = RetryOrchestrator(
        tool_runtime=MagicMock(),
        config=MagicMock(max_tool_execution_time_ms=60000, max_retry_attempts=4),
        decoder=MagicMock(),
        call_llm_for_decision=AsyncMock(),
        call_llm_for_decision_stream=None,
        execute_tool_batch=AsyncMock(),
        guard_assert_single_tool_batch=MagicMock(),
        emit_event=emitted.append,
    )

    receipt = {
        "batch_id": "b-1",
        "turn_id": "t-boot",
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "a.py", "content": "x = 1\n"},
                "arguments": {"file": "a.py"},
            }
        ],
        "success_count": 1,
        "failure_count": 0,
    }
    runtime = MagicMock()
    runtime.execute_batch = AsyncMock(return_value=[receipt])
    orchestrator._build_tool_batch_runtime = MagicMock(return_value=runtime)  # type: ignore[method-assign]

    merged = await orchestrator.execute_read_bootstrap_batch(
        turn_id="t-boot",
        workspace=".",
        tool_batch={
            "invocations": [
                {
                    "call_id": "c1",
                    "tool_name": "read_file",
                    "arguments": {"file": "a.py"},
                }
            ]
        },
        ledger=TurnLedger(turn_id="t-boot"),
    )

    assert merged is not None
    tool_result_events = [e for e in emitted if isinstance(e, dict) and e.get("type") == "tool_result"]
    assert len(tool_result_events) == 1
    data = tool_result_events[0]["data"]
    assert data["tool"] == "read_file"
    assert data["bootstrap_read"] is True
    assert data["result"]["content"] == "x = 1\n"


# ---------------------------------------------------------------------------
# merge_bootstrap_receipt_into_result — bootstrap reads must reach the envelope
# ---------------------------------------------------------------------------


def _bootstrap_receipt() -> dict[str, Any]:
    return {
        "batch_id": "b-boot",
        "turn_id": "t-merge",
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "a.py", "content": "x = 1\n"},
                "arguments": {"file": "a.py"},
            }
        ],
        "success_count": 1,
        "failure_count": 0,
    }


def test_merge_prepends_bootstrap_reads_into_existing_receipt() -> None:
    result = {
        "kind": "tool_batch_with_receipt",
        "batch_receipt": {
            "results": [{"tool_name": "write_file", "status": "success", "result": {"file": "a.py"}}],
            "success_count": 1,
        },
    }
    merged = merge_bootstrap_receipt_into_result(result, _bootstrap_receipt())
    tools = [item["tool_name"] for item in merged["batch_receipt"]["results"]]
    assert tools == ["read_file", "write_file"]  # reads first, then the write
    assert merged["batch_receipt"]["success_count"] == 2


def test_merge_sets_receipt_when_result_has_none() -> None:
    result = {"kind": "tool_batch_with_receipt", "batch_receipt": None}
    merged = merge_bootstrap_receipt_into_result(result, _bootstrap_receipt())
    assert merged["batch_receipt"]["results"][0]["tool_name"] == "read_file"


def test_merge_leaves_non_dict_result_untouched() -> None:
    sentinel = object()
    assert merge_bootstrap_receipt_into_result(sentinel, _bootstrap_receipt()) is sentinel
    plain = {"kind": "x"}
    assert merge_bootstrap_receipt_into_result(plain, None) is plain
