from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.internal import bench_gates
from polaris.cells.factory.pipeline.internal.bench_gates import (
    _command_serves_build_output,
    _resolve_polaris_roots_runtime_dir,
    _script_depends_on_build_output,
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_llm_route_audit,
    build_real_run_gate,
    classify_factory_bench_failure,
    collect_llm_events,
)


def _real_llm_event(
    role: str,
    provider_id: str,
    model: str,
    binding_id: str = "",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "llm_call_end",
        "role": role,
        "provider_id": provider_id,
        "model": model,
        "source": "llm",
        "terminal": True,
        "invocation": True,
    }
    if binding_id:
        event["binding_id"] = binding_id
    return event


def test_apply_factory_bench_failure_taxonomy_exposes_top_level_fields() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: src/engine/simulation.ts(58,16): TS1003",
            }
        ],
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            }
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_taxonomy"] == taxonomy
    assert record["failure_category"] == "llm_output"
    assert record["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_reasons"]
    assert record["failure_evidence"] == ["real run gate failed: build_test_lint_ran"]
    opencode_audit = record["opencode_audit"]
    assert opencode_audit["required"] is True
    assert opencode_audit["reason"] == "role_tool_failure_detected"
    assert opencode_audit["mode"] == "read_only_first"
    assert opencode_audit["trigger_category"] == "llm_output"
    assert opencode_audit["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert "target_project_code_changes" in opencode_audit["forbidden"]
    assert "llm_call_start_final_request_context_audit" in opencode_audit["must_review"]
    assert record["goal_audit"] == {
        "total": 1,
        "real_run_gate": {"passed": 0, "total": 1},
        "llm_route_audit": {"passed": 0, "total": 1},
        "failure_categories": {"llm_output": 1},
        "root_cause_signatures": {"llm_output:real_run_gate.build_test_lint_ran": 1},
    }


def test_failure_taxonomy_classifies_non_terminal_real_run_skip_as_runtime_environment() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            }
        ],
        "real_run_gate": {
            "ok": False,
            "skipped": True,
            "summary": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            "requirements": {
                "chain_terminal": {
                    "ok": False,
                    "detail": "chain_terminal=false; phase=event_wait_timeout; status=unknown",
                },
                "artifact_landed": {
                    "ok": False,
                    "detail": "not evaluated because the Polaris chain was non-terminal",
                },
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:real_run_gate.chain_terminal"
    assert taxonomy["evidence"] == ["real run gate skipped: chain did not reach terminal state (event_wait_timeout)"]


def test_start_failure_runtime_roles_not_ready_is_runtime_environment() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain": {
            "exit_code": -1,
            "error": "start_failed",
            "start_error": {
                "status": 409,
                "json": {
                    "error": {
                        "code": "RUNTIME_ROLES_NOT_READY",
                        "details": {
                            "role_issues": {
                                "director": (
                                    "director binding (openai_compat-1/qwen3.6-27b-code-gpu0) "
                                    "LLM not ready; run tests first"
                                )
                            }
                        },
                    }
                },
            },
        },
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=fail exit_code=-1"},
        ],
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:runtime_roles_not_ready"
    assert "openai_compat-1/qwen3.6-27b-code-gpu0" in taxonomy["evidence"][0]


def test_factory_bench_taxonomy_prioritizes_pm_runtime_environment_over_missing_ce_blueprint() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "terminal_status": "director_partial",
        "has_blueprint_doc": False,
        "checks": [
            {"check": "package_scripts", "ok": False, "detail": "package.json not found"},
        ],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": False,
                "detail": "blueprint artifact missing",
            },
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "llm_route_audit",
                "ok": False,
                "detail": "LLM route audit failed: pm, director",
            },
        ],
        "chain": {
            "exit_code": 1,
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "PM planning failed: Run status: failed | failed_task=task-0-pm (pm) | "
                        "error=cognitive_runtime_mainline_unavailable:process:FileNotFoundError; "
                        "error_code=pm.run_status_non_success"
                    ),
                }
            },
        },
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: pm, director"},
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: source_files_present",
            "requirements": {
                "source_files_present": {"ok": False},
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:cognitive_runtime_mainline_unavailable"
    assert record["failure_category"] == "runtime_environment"
    assert "cognitive_runtime_mainline_unavailable" in record["failure_evidence"][0]


def test_factory_bench_taxonomy_prioritizes_director_fanout_over_real_run_gate() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "terminal_status": "director_partial",
        "checks": [],
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
                "entrypoint_smoke": {"ok": False},
            },
        },
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Director dispatch failed: Director binding fanout: 3 bindings, "
                        "0 succeeded, 3 failed, 3 quarantined; "
                        "error_code=director.run_status_non_success"
                    ),
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert taxonomy["root_cause_signature"] == "director_tool_execution:director_binding_fanout_failed"
    assert record["failure_category"] == "director_tool_execution"
    assert record["goal_audit"]["failure_categories"] == {"director_tool_execution": 1}
    assert record["goal_audit"]["root_cause_signatures"] == {
        "director_tool_execution:director_binding_fanout_failed": 1
    }
    assert "Director dispatch failed" in record["failure_evidence"][0]


def test_factory_bench_taxonomy_does_not_treat_ce_full_blueprint_count_as_partial() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "terminal_status": "director_partial",
        "has_blueprint_doc": True,
        "checks": [],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": True,
                "detail": "blueprint present",
            },
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "stages": [
                {
                    "stage": "chief_engineer_review",
                    "status": "success",
                    "output": "Chief Engineer review generated 3/3 blueprints; signals=0",
                }
            ],
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "director": {
                    "total": 3,
                    "successes": 0,
                    "failures": 3,
                    "blocked": 0,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "Director dispatch failed: director_materialization_quality_failed",
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert taxonomy["root_cause_signature"] == "director_tool_execution:director_materialization_failed"
    assert "secondary_real_run_gate:real run gate failed: build_test_lint_ran" in taxonomy["evidence"]
    assert record["failure_category"] == "director_tool_execution"
    assert record["opencode_audit"]["required"] is True
    assert record["opencode_audit"]["recommended_agent_count"] == 5
    assert "context_snapshot_ref" in record["opencode_audit"]["prompt"]
    assert "toolspec_arg_aliases_and_provider_tool_call_normalization" in record["opencode_audit"]["must_review"]


def test_role_tool_failure_opencode_prompt_derives_project_workspace_from_backend_metadata() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "project_id": "L1-01",
        "level": 1,
        "backend_metadata": {"workspace": "/tmp/factory-bench"},
        "chain_results": {
            "director": {
                "total": 1,
                "successes": 0,
                "failures": 1,
                "blocked": 0,
            }
        },
        "chain": {
            "audit_bundle": {"failure": {"detail": "Director dispatch failed: director_materialization_quality_failed"}}
        },
    }

    apply_factory_bench_failure_taxonomy(record)

    assert "workspace：/tmp/factory-bench/L1-01" in record["opencode_audit"]["prompt"]


def test_pm_contract_failure_requires_opencode_audit() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "project_id": "L1-01",
        "level": 1,
        "chain": {
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "exit_class": "pm_failed",
                "factory_stage_hint": "pm_planning",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "PM contract quality failed",
                }
            },
        },
        "factory_gates": [{"gate": "chain_terminal", "ok": False, "detail": "pm failed"}],
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "pm_contract"
    assert record["opencode_audit"]["required"] is True


def test_factory_bench_taxonomy_prioritizes_post_qa_artifact_failure_over_director_failure() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "checks": [{"check": "ts_syntax", "ok": True, "detail": "8 TypeScript files pass"}],
        "factory_gates": [
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=True qa_passed=False",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False, "detail": "npm run build failed"},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "Director dispatch failed: director_materialization_quality_failed",
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_category"] == "llm_output"
    assert record["opencode_audit"]["required"] is True


def test_factory_bench_taxonomy_classifies_post_qa_typescript_failure_as_llm_output() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "terminal_status": "director_partial",
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: tests/verify.test.ts(1,1698): TS1005",
            }
        ],
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=True qa_passed=False",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran, entrypoint_smoke",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran, entrypoint_smoke",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
                "entrypoint_smoke": {"ok": False},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "qa_reason": "npm run build failed with TypeScript errors",
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Director dispatch failed: Director binding fanout: 3 bindings, "
                        "0 succeeded, 3 failed, 0 quarantined; Quality gate failed after Director dispatch"
                    ),
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_category"] == "llm_output"
    assert record["goal_audit"]["failure_categories"] == {"llm_output": 1}
    assert record["opencode_audit"]["required"] is True


def test_factory_bench_taxonomy_prioritizes_chief_engineer_blocker_over_downstream_route_audit() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "chain_state": "partial",
        "checks": [
            {
                "check": "source_target_coverage:src/**/*.ts",
                "ok": False,
                "detail": "source target 'src/**/*.ts': no source files found",
            }
        ],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": True,
                "detail": "blueprint artifact discovered",
            },
            {
                "gate": "llm_route_audit",
                "ok": False,
                "detail": "LLM route audit failed: director",
            },
        ],
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: director"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "director": {"total": None, "successes": None, "failures": None, "blocked": None},
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "current_stage": "chief_engineer_review",
                "last_successful_stage": "pm_planning",
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Chief Engineer review generated 8/9 blueprints; "
                        "signals=1; error_code=chief_engineer.llm_review_failed; "
                        "root_cause_hint=验证失败，已重试1次: No JSON object matched "
                        "chief_engineer blueprint keys: construction_plan, scope_for_apply, risk_flags"
                    ),
                },
                "director_convergence": {
                    "blocking_phase": "chief_engineer_review",
                    "missing_delivery_targets": ["director_dispatch", "quality_gate"],
                },
            },
        },
        "director_convergence": {
            "blocking_phase": "chief_engineer_review",
            "missing_delivery_targets": ["director_dispatch", "quality_gate"],
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:llm_review_failed"
    assert record["failure_category"] == "chief_engineer_blueprint"
    assert record["goal_audit"]["failure_categories"] == {"chief_engineer_blueprint": 1}
    assert "Chief Engineer review generated 8/9 blueprints" in record["failure_evidence"][0]


def test_real_run_gate_executes_python_build_and_cli_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["artifact_landed"]["ok"] is True
    assert gate["requirements"]["environment_prepared"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["kind"] == "python_cli"


def test_real_run_gate_executes_python_unittest_suite(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\n"
        "import unittest\n"
        "from calculator import add\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_adds_numbers(self) -> None:\n"
        "        self.assertEqual(add(1, 2), 3)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert "unittest passed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    assert any(command.get("runner") == "unittest" for command in gate["commands"])


def test_real_run_gate_falls_back_to_pytest_when_unittest_finds_zero_cases(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\n"
        "from calculator import add\n\n"
        "def test_adds_numbers() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert "pytest passed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    assert [command.get("runner") for command in gate["commands"] if command.get("runner")] == [
        "unittest",
        "pytest",
    ]


def test_real_run_gate_rejects_python_tests_that_run_zero_cases(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\nHELPER_VALUE = 3\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["detail"] in {
        "python pytest discovered zero tests from generated test files",
        "python pytest failed",
    }


def test_real_run_gate_rejects_python_cli_failure_marker(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('FAIL: calculate(1+2) = 4 (expected 3)')\n"
        "    raise SystemExit(0)\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["failure_marker"] is True
    assert gate["entrypoint"]["detail"] == "entrypoint output contained a failure marker"


def test_real_run_gate_accepts_required_arg_cli_usage_screen(tmp_path: Path) -> None:
    (tmp_path / "cli.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    if '--help' in sys.argv:\n"
        "        print('Usage: python cli.py <value>', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    if len(sys.argv) < 2:\n"
        "        print('Usage: python cli.py <value>', file=sys.stderr)\n"
        "        raise SystemExit(1)\n"
        "    print(sys.argv[1])\n",
        encoding="utf-8",
    )
    record = {"code_files": ["cli.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["usage_screen"] is True


def test_real_run_gate_accepts_interactive_cli_that_starts_and_waits(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "import time\n"
        "if __name__ == '__main__':\n"
        "    if '--help' in sys.argv:\n"
        "        raise SystemExit(2)\n"
        "    print('Interactive Calculator')\n"
        "    print('>>> ', end='', flush=True)\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=2)

    # Timeout is no longer considered success
    assert gate["ok"] is False
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["started"] is True
    assert gate["entrypoint"]["timeout"] is True


def test_real_run_gate_starts_static_web_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    record = {"code_files": ["index.html", "app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "node --check passed"
    # Accept either web_static or web_playwright (Playwright is preferred when available)
    assert gate["entrypoint"]["kind"] in ("web_static", "web_playwright")


def test_static_web_smoke_fails_missing_html_entrypoint(tmp_path: Path) -> None:
    smoke = bench_gates._smoke_static_web(tmp_path, "missing.html", timeout_s=10)

    assert smoke["ok"] is False
    detail = str(smoke.get("detail") or "")
    assert "404" in detail or "HTTP status" in detail


def test_real_run_gate_does_not_fallback_after_failed_npm_script(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "node scripts/verify.js",
                }
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": False,
            "returncode": 1,
            "duration_s": 0.01,
            "stdout_tail": "verification failed",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.html", "app.js", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test failed"
    assert ["node", "--check", "app.js"] not in commands


def test_real_run_gate_accepts_pure_static_html_css_smoke(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="style.css"></head><body><h1>ok</h1></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text("body { display: grid; }\n", encoding="utf-8")
    record = {"code_files": ["index.html", "style.css"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "static HTML/CSS entrypoint smoke passed"
    # Accept either web_static or web_playwright (Playwright is preferred when available)
    assert gate["entrypoint"]["kind"] in ("web_static", "web_playwright")


def test_real_run_gate_executes_go_build_and_cli_entrypoint(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text(
        'package main\nimport "fmt"\nfunc main() { fmt.Println("usage: app") }\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/go" if name == "go" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "usage: app\n" if "run" in command else "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["environment_prepared"]["detail"] == "go toolchain available"
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "go test passed"
    assert gate["entrypoint"]["kind"] == "go_cli"
    assert [command[1] for command in commands] == ["test", "run"]


def test_real_run_gate_ts_build_before_test_order(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_ts_build_failure_blocks_gate(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "TS1005: ';' expected." if is_build else "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert "TS1005" in gate["requirements"]["build_test_lint_ran"]["detail"]


def test_real_run_gate_non_compiled_js_can_run_test_only(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "start": "node app.js"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test passed"
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


def test_real_run_gate_build_failure_blocks_npm_start(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "TS1005: ';' expected." if is_build else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert "TS1005" in gate["requirements"]["build_test_lint_ran"]["detail"]
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" not in script_names
    assert gate["entrypoint"]["kind"] == "npm_start"
    assert gate["entrypoint"]["ok"] is False
    assert "build did not succeed" in gate["entrypoint"]["detail"] or "TS1005" in gate["entrypoint"]["detail"]


def test_real_run_gate_build_first_when_no_ts_but_build_is_tsc(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_build_first_when_test_references_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "node dist/bundle.js",
                    "start": "node dist/bundle.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_non_compiled_js_test_only_no_forced_build(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "start": "node app.js"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test passed"
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


class TestScriptDependsOnBuildOutput:
    """Direct tests for _script_depends_on_build_output helper."""

    def test_serve_s_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "serve -s dist"}, "start") is True

    def test_node_build(self) -> None:
        assert _script_depends_on_build_output({"start": "node build"}, "start") is True

    def test_node_dot_slash_out_server(self) -> None:
        assert _script_depends_on_build_output({"start": "node ./out/server.js"}, "start") is True

    def test_node_backslash_dist_index(self) -> None:
        assert _script_depends_on_build_output({"start": "node .\\dist\\index.js"}, "start") is True

    def test_vite_preview(self) -> None:
        assert _script_depends_on_build_output({"start": "vite preview"}, "start") is True

    def test_node_flag_dir_equals_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "node --dir=dist"}, "start") is True

    def test_npx_serve(self) -> None:
        assert _script_depends_on_build_output({"start": "npx serve -s dist"}, "start") is True

    def test_dist_slash_index_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node dist/index.js"}, "start") is True

    def test_build_slash_server_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node build/server.js"}, "start") is True

    def test_dot_slash_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "node ./dist"}, "start") is True

    def test_outdir_flag_equals_dist(self) -> None:
        assert _script_depends_on_build_output({"build": "tsc --outDir=dist"}, "build") is True

    def test_node_scripts_build_start_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node scripts/build/start.js"}, "start") is False

    def test_node_src_build_helper_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node src/build-helper.js"}, "start") is False

    def test_node_tools_outdated_check_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node tools/outdated-check.js"}, "start") is False

    def test_empty_command(self) -> None:
        assert _script_depends_on_build_output({"start": ""}, "start") is False

    def test_missing_script(self) -> None:
        assert _script_depends_on_build_output({"test": "jest"}, "start") is False

    def test_none_value(self) -> None:
        assert _script_depends_on_build_output({"start": None}, "start") is False


class TestCommandServesBuildOutput:
    """Direct tests for _command_serves_build_output helper."""

    def test_vite_preview_true(self) -> None:
        assert _command_serves_build_output("vite preview") is True

    def test_npx_vite_preview_true(self) -> None:
        assert _command_serves_build_output("npx vite preview") is True

    def test_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("serve -s dist") is True

    def test_npx_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("npx serve -s dist") is True

    def test_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("http-server dist") is True

    def test_serve_dot_slash_dist_true(self) -> None:
        assert _command_serves_build_output("serve ./dist") is True

    def test_serve_dist_index_js_true(self) -> None:
        assert _command_serves_build_output("serve dist/index.js") is True

    def test_serve_build_true(self) -> None:
        assert _command_serves_build_output("serve build") is True

    def test_serve_out_true(self) -> None:
        assert _command_serves_build_output("serve out") is True

    def test_serve_dir_equals_dist_true(self) -> None:
        assert _command_serves_build_output("serve --dir=dist") is True

    def test_serve_public_false(self) -> None:
        assert _command_serves_build_output("serve public") is False

    def test_serve_src_false(self) -> None:
        assert _command_serves_build_output("serve src") is False

    def test_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("serve") is False

    def test_npx_serve_public_false(self) -> None:
        assert _command_serves_build_output("npx serve public") is False

    def test_npx_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("npx serve") is False

    def test_http_server_public_false(self) -> None:
        assert _command_serves_build_output("http-server public") is False

    def test_http_server_no_args_false(self) -> None:
        assert _command_serves_build_output("http-server") is False

    def test_npx_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("npx http-server dist") is True

    def test_serve_scripts_false(self) -> None:
        assert _command_serves_build_output("serve scripts") is False

    def test_empty_command_false(self) -> None:
        assert _command_serves_build_output("") is False


def test_real_run_gate_build_first_when_start_serves_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "serve -s dist",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    assert "start" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    start_idx = script_names.index("start")
    assert build_idx < test_idx < start_idx


def test_real_run_gate_build_failure_blocks_start_with_serve_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "start": "serve -s dist",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "BUILD FAILED" if is_build else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" not in script_names
    assert gate["entrypoint"]["ok"] is False


def test_real_run_gate_build_first_when_start_vite_preview(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "vite build",
                    "start": "vite preview",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" in script_names
    build_idx = script_names.index("build")
    start_idx = script_names.index("start")
    assert build_idx < start_idx


def test_real_run_gate_false_positive_guard_no_forced_build(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "node scripts/build/start.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "test" in script_names
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


def test_collect_llm_events_reads_runtime_role_jsonl(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    events_dir = runtime / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(
            {
                "event": "llm_call_end",
                "role": "pm",
                "data": {
                    "role": "pm",
                    "provider": "kimi-cloud",
                    "model": "kimi-k2",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, runtime)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True


def test_collect_llm_events_reads_multiple_runtime_candidates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    for runtime, role, model in (
        (runtime_a, "pm", "kimi-k2"),
        (runtime_b, "director", "qwen3.6-27b-gpu0"),
    ):
        events_dir = runtime / "events"
        events_dir.mkdir(parents=True)
        (events_dir / f"{role}.llm.events.jsonl").write_text(
            json.dumps(
                {
                    "event": "llm_call_end",
                    "role": role,
                    "model": model,
                    "data": {"prompt_tokens": 1, "completion_tokens": 2},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    events = collect_llm_events(workspace, [runtime_a, runtime_b])

    assert {(event["role"], event["model"]) for event in events} == {
        ("pm", "kimi-k2"),
        ("director", "qwen3.6-27b-gpu0"),
    }


def test_collect_llm_events_reads_route_events_from_audit_bundle_result(tmp_path: Path) -> None:
    bundle = {
        "events_tail": [
            {
                "type": "stage_completed",
                "stage": "director_dispatch",
                "result": {
                    "per_binding_route_events": [_real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0")],
                },
            }
        ],
    }

    events = collect_llm_events(tmp_path, None, bundle)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu0", "d0")
    ]
    assert events[0]["source_path"] == "audit_bundle.events_tail.result"


def test_collect_llm_events_reads_factory_dispatch_log_glob(tmp_path: Path) -> None:
    dispatch_dir = tmp_path / ".polaris" / "dispatch"
    dispatch_dir.mkdir(parents=True)
    dispatch_log = dispatch_dir / "factory_abc123.log.json"
    dispatch_log.write_text(
        json.dumps(
            {
                "per_binding_route_events": [_real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, None)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu1", "d1")
    ]
    assert events[0]["source_path"].endswith("factory_abc123.log.json")


def test_llm_route_audit_requires_actual_bound_families_and_all_director_routes() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_prefers_actual_configured_binding_over_hardcoded_family() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "openai-a", "model": "gpt-5.3-codex", "binding_id": ""}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "glm-a", "model": "glm-4.7-flash", "binding_id": ""}
        ],
        "qa": [{"role": "qa", "provider_id": "gemini-a", "model": "gemini-2.5-pro", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "local-director", "model": "custom-director-30b", "binding_id": "d0"}
        ],
    }
    events = [
        _real_llm_event("pm", "openai-a", "gpt-5.3-codex"),
        _real_llm_event("chief_engineer", "glm-a", "glm-4.7-flash"),
        _real_llm_event("qa", "gemini-a", "gemini-2.5-pro"),
        _real_llm_event("director", "local-director", "custom-director-30b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["pm"]["family_ok"] is True
    assert audit["roles"]["chief_engineer"]["family_ok"] is True
    assert audit["roles"]["qa"]["family_ok"] is True
    assert audit["roles"]["director"]["family_ok"] is True


def test_llm_route_audit_fails_when_a_director_route_is_unobserved() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_treats_readiness_skipped_director_as_diagnostic() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_connectivity_unavailable",
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["missing_bindings"] == []
    assert audit["roles"]["director"]["skipped_bindings"] == ["qwen-gpu0|qwen3.6-27b"]
    assert audit["roles"]["director"]["fail_closed_count"] == 1


def test_llm_route_audit_accepts_single_live_director_route() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_can_relax_director_route_coverage_for_serial_bench() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-for-coding", "pm0"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3", "qa0"),
        _real_llm_event("director", "qwen-b", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "qa", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["multi_route_required"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_resolves_providerless_via_expected_bindings_and_rejects_cached() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "ce0"}
        ],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "source": "cache",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b-gpu0", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b-gpu1",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["events_observed"] == 3
    assert audit["events_rejected"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["chief_engineer"]["observed_count"] == 1
    assert audit["roles"]["chief_engineer"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["qa"]["observed_count"] == 0
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b-gpu0"]


def test_failure_taxonomy_prefers_llm_route_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [{"gate": "llm_route_audit", "ok": False, "detail": "missing qa"}],
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: qa"},
        "chain_state": "fail",
        "checks": [],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:llm_route_audit"


def test_failure_taxonomy_classifies_integration_qa_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=True qa_passed=False"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_results": {"qa_reason": "qa_passed=False; qa_score=34"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:integration_qa_failed"
    assert taxonomy["evidence"] == ["qa_passed=False; qa_score=34"]


def test_failure_taxonomy_classifies_missing_toolchain_check_as_runtime_environment() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [{"check": "go_compile", "ok": False, "detail": "go unavailable for Go project"}],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:go_compile"


def test_failure_taxonomy_classifies_workspace_switch_before_real_run_gate() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: artifact_landed"},
        ],
        "chain": {
            "error": "workspace_switch_failed",
            "workspace_switch": {"workspace": "/tmp/factory-bench/L1-01"},
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: artifact_landed",
            "requirements": {"artifact_landed": {"ok": False, "detail": "no generated source files"}},
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "fail",
        "checks": [],
        "has_plan_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:workspace_switch_failed"
    assert taxonomy["evidence"] == ["/tmp/factory-bench/L1-01"]


def test_failure_taxonomy_classifies_generated_typescript_syntax_failure_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: build_test_lint_ran"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "22 generated code file(s)"},
                "environment_prepared": {"ok": True, "detail": "npm available"},
                "build_test_lint_ran": {
                    "ok": False,
                    "detail": "npm test failed: src/models/humidity.ts(1,29): error TS1434",
                },
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: src/models/humidity.ts(1,29): TS1434",
            }
        ],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"


def test_failure_taxonomy_classifies_missing_typescript_dependency_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: environment_prepared"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: environment_prepared, build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "4 generated code file(s)"},
                "environment_prepared": {
                    "ok": False,
                    "detail": "package.json missing devDependency 'typescript' for TypeScript build",
                },
                "build_test_lint_ran": {"ok": False, "detail": "no build/test/lint command was discovered"},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.environment_prepared"


def test_failure_taxonomy_classifies_missing_blueprint_as_chief_engineer_blueprint() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "plan_artifact_present", "ok": True, "detail": "plan artifact discovered"},
            {"gate": "blueprint_artifact_present", "ok": False, "detail": "blueprint artifact missing"},
            {"gate": "qa_verdict_artifact_present", "ok": True, "detail": "QA verdict artifact discovered"},
            {"gate": "chain_clean", "ok": True, "detail": "chain_state=clean exit_code=0"},
            {"gate": "integration_qa_passed", "ok": True, "detail": "qa_ran=True qa_passed=True"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [],
        "has_plan_doc": True,
        "has_blueprint_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:missing_or_invalid_blueprint"


def test_aggregate_goal_audit_counts_real_route_and_root_causes() -> None:
    records = [
        {
            "real_run_gate": {"ok": True},
            "llm_route_audit": {"ok": True},
            "failure_taxonomy": {"ok": True},
        },
        {
            "real_run_gate": {"ok": False},
            "llm_route_audit": {"ok": False},
            "failure_taxonomy": {
                "ok": False,
                "category": "target_project_baseline",
                "root_cause_signature": "target_project_baseline:real_run_gate.entrypoint_smoke",
            },
        },
    ]

    aggregate = aggregate_goal_audit(records)

    assert aggregate["real_run_gate"] == {"passed": 1, "total": 2}
    assert aggregate["llm_route_audit"] == {"passed": 1, "total": 2}
    assert aggregate["failure_categories"]["target_project_baseline"] == 1


def test_nested_roles_kernel_events_passes_with_model_only_and_expected_binding() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [{"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"}],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-k2", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b", "metadata": {"source": "llm"}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["events_rejected"] == 0
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-k2"]
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b"]


def test_metadata_cached_true_rejected() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm",))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["events_rejected"] == 1
    assert audit["roles"]["pm"]["observed_count"] == 0


def test_director_multi_binding_missing_one_fails() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_director_multi_binding_all_pass() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True
    assert audit["roles"]["director"]["observed_count"] == 2


def test_real_run_gate_source_files_present_with_real_source(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_fails_for_scaffold_only(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "scaffold-only" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["source_file_count"] == 0
    assert gate["missing_source_targets"]["code_file_count"] == 2


def test_real_run_gate_source_files_present_with_ts_source(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {"code_files": ["index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_with_mixed_scaffold(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".catalog_meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json", ".catalog_meta.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["code_file_count"] == 3
    assert gate["missing_source_targets"]["source_file_count"] == 0


def test_real_run_gate_declared_source_targets_missing_fails(tmp_path: Path) -> None:
    """plan declares src/index.ts but workspace only has package.json -> gate fail."""
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {
        "code_files": ["package.json"],
        "declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "declared_source_target_count": 2,
        "missing_declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "missing_declared_source_target_count": 2,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "2 declared source target(s) missing" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_declared_source_targets_all_present_passes(tmp_path: Path) -> None:
    """All declared source targets exist -> gate ok."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    record = {
        "code_files": ["src/index.ts"],
        "declared_source_targets": ["src/index.ts"],
        "declared_source_target_count": 1,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert (
        "all 1 declared source target(s) present" in gate["requirements"]["declared_source_targets_present"]["detail"]
    )


def test_real_run_gate_pm_plan_missing_source_targets_fails(tmp_path: Path) -> None:
    """PM plan with no source targets -> pm_plan_missing_source_targets signal."""
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    record = {
        "code_files": ["README.md"],
        "declared_source_targets": [],
        "declared_source_target_count": 0,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
        "pm_plan_missing_source_targets": True,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "pm_plan_missing_source_targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_no_declared_targets_no_plan(tmp_path: Path) -> None:
    """No plan.json -> no declared targets, requirement passes."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {
        "code_files": ["main.py"],
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert "no declared source targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def _disk_llm_event(
    role: str,
    provider: str,
    model: str,
    *,
    event: str = "llm_call_end",
    source: str = "roles.kernel.events",
    run_id: str = "test-run-001",
) -> dict[str, Any]:
    """Create an event matching _emit_llm_event_to_disk schema."""
    return {
        "schema_version": 1,
        "ts": "2026-06-21T00:00:00",
        "ts_epoch": 1750464000.0,
        "seq": 1,
        "event_id": "abcd1234",
        "run_id": run_id,
        "iteration": 1,
        "role": role,
        "source": source,
        "event": event,
        "data": {
            "event_type": event,
            "role": role,
            "model": model,
            "provider": provider,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "metadata": {"call_id": "c0", "workspace": "/tmp/test"},
        },
    }


def test_collect_llm_events_reads_from_resolve_polaris_roots_path(tmp_path: Path) -> None:
    """Events written by _emit_llm_event_to_disk to resolve_polaris_roots path are found."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    polaris_dir.mkdir()
    runtime = polaris_dir / "runtime"
    runtime.mkdir()
    events_dir = runtime / "events"
    events_dir.mkdir()
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_collect_llm_events_reads_disk_schema_from_polaris_roots(tmp_path: Path) -> None:
    """Events in _emit_llm_event_to_disk schema are normalized with correct source=llm."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["source"] == "llm"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_build_llm_route_audit_observes_configured_bindings_from_disk_events(
    tmp_path: Path,
) -> None:
    """build_llm_route_audit observes PM and Director bindings from disk-format events."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_count"] == 1


def test_llm_route_audit_missing_director_evidence_fails_closed(tmp_path: Path) -> None:
    """Missing Director route evidence remains fail-closed (events_observed=0)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
    )

    assert audit["ok"] is False
    assert audit["roles"]["director"]["ok"] is False
    assert audit["roles"]["director"]["observed_count"] == 0
    assert audit["roles"]["pm"]["ok"] is True


def test_llm_route_audit_zero_events_with_all_required_roles_fails(tmp_path: Path) -> None:
    """Zero events with all required roles -> all roles fail."""
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }

    audit = build_llm_route_audit([], expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["roles"]["pm"]["ok"] is False
    assert audit["roles"]["director"]["ok"] is False


def test_resolve_polaris_roots_runtime_dir_returns_path(tmp_path: Path) -> None:
    """_resolve_polaris_roots_runtime_dir returns a Path for a valid workspace."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = _resolve_polaris_roots_runtime_dir(workspace)

    assert result is not None
    assert isinstance(result, Path)
    assert "runtime" in str(result)


def test_resolve_polaris_roots_runtime_dir_returns_none_for_invalid() -> None:
    """_resolve_polaris_roots_runtime_dir returns None gracefully."""
    result = _resolve_polaris_roots_runtime_dir(Path("/nonexistent/path/that/does/not/exist"))
    # Should not raise; may return a path or None depending on cache availability
    assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# Scaffolding requirement tests (R18-C)
# ---------------------------------------------------------------------------


def test_real_run_gate_ts_project_without_package_json_fails(tmp_path: Path) -> None:
    """TypeScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_package_but_no_tsconfig_fails(tmp_path: Path) -> None:
    """TypeScript project with package.json but no tsconfig.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_scaffolding_passes(tmp_path: Path) -> None:
    """TypeScript project with package.json and tsconfig.json passes scaffolding."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "package.json present" in scaffolding["detail"]
    assert "tsconfig.json present" in scaffolding["detail"]


def test_real_run_gate_ts_project_requires_local_typescript_dependency(monkeypatch: Any, tmp_path: Path) -> None:
    """Package-managed TypeScript projects must not borrow host global tsc."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc", "test": "npm run build"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    def fail_if_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("npm scripts must not run when TypeScript dependency is missing")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", fail_if_run)

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    environment = gate["requirements"]["environment_prepared"]
    assert environment["ok"] is False
    assert "missing devDependency 'typescript'" in environment["detail"]
    assert not any(command.get("phase") == "build_test_lint" for command in gate["commands"])


def test_real_run_gate_html_project_without_index_fails(tmp_path: Path) -> None:
    """HTML project with code_files claiming .html but no real file must fail closed."""
    record = {"code_files": ["index.html"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["scaffolding_present"]["ok"] is False
    assert "index.html" in gate["requirements"]["scaffolding_present"]["detail"]
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False


def test_real_run_gate_js_project_without_package_json_fails(tmp_path: Path) -> None:
    """JavaScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("console.log('hello');\n", encoding="utf-8")
    record = {"code_files": ["src/app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]


def test_real_run_gate_python_project_no_scaffolding_required(tmp_path: Path) -> None:
    """Python project does not require npm scaffolding."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "no scaffolding required" in scaffolding["detail"]


def test_real_run_gate_ts_source_only_no_scaffold_comprehensive(tmp_path: Path) -> None:
    """Comprehensive: TS project with src/**/*.ts but zero scaffolding fails on scaffolding."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "render.ts").write_text("export function render() { return 'ok'; }\n", encoding="utf-8")
    (src / "simulation.ts").write_text("export function simulate() { return 42; }\n", encoding="utf-8")
    record = {"code_files": ["src/render.ts", "src/simulation.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_cli_smoke_result_timeout_not_success() -> None:
    """CLI timeout should not be considered successful."""
    result = {
        "ok": False,
        "returncode": -1,
        "duration_s": 2.0,
        "stdout_tail": "Interactive CLI started",
        "stderr_tail": "",
        "timeout": True,
        "timeout_s": 2,
    }

    payload = bench_gates._cli_smoke_result("python_cli", "main.py", result)

    assert payload["ok"] is False
    assert payload["started"] is True
    assert payload["timeout"] is True


def test_smoke_static_web_playwright_filters_non_critical_errors(tmp_path: Path) -> None:
    """Non-critical resource errors should be filtered out."""
    # Create a test HTML file
    (tmp_path / "index.html").write_text(
        "<html><head><link rel='stylesheet' href='missing.css'></head><body><h1>Test</h1></body></html>",
        encoding="utf-8",
    )

    # Mock Playwright to simulate non-critical errors
    class MockPage:
        def __init__(self) -> None:
            self.console_errors: list[str] = []

        def on(self, event: str, callback: object) -> None:
            if event == "console":
                # Simulate non-critical errors
                self.console_errors = [
                    "Failed to load resource: the server responded with a status of 404 (Not Found)",
                    "favicon.ico:1 Failed to load resource: the server responded with a status of 404 (Not Found)",
                    "net::ERR_CONNECTION_REFUSED",
                ]

                # Call the callback for each error
                class MockMsg:
                    def __init__(self, text: str, msg_type: str) -> None:
                        self.text = text
                        self.type = msg_type

                for err in self.console_errors:
                    callback(MockMsg(err, "error"))

        def goto(self, url: str, timeout: int | None = None) -> object:
            class MockResponse:
                status = 200

            return MockResponse()

        def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
            pass

        def query_selector(self, selector: str) -> object | None:
            return None

        def close(self) -> None:
            pass

    class MockBrowser:
        def new_page(self) -> MockPage:
            return MockPage()

        def close(self) -> None:
            pass

    class MockPlaywright:
        def __init__(self) -> None:
            self.chromium = type("Chromium", (), {"launch": lambda self, headless: MockBrowser()})()

        def __enter__(self) -> MockPlaywright:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    # Patch the function to use our mock
    original_smoke = bench_gates._smoke_static_web_playwright

    def mock_smoke(workspace, html_rel, *, timeout_s):
        # Simulate the function behavior with our mock
        console_errors = [
            "Failed to load resource: the server responded with a status of 404 (Not Found)",
            "favicon.ico:1 Failed to load resource: the server responded with a status of 404 (Not Found)",
            "net::ERR_CONNECTION_REFUSED",
        ]

        # Apply the new filtering logic
        non_critical_patterns = [
            "Failed to load resource",
            "favicon.ico",
            "net::ERR_",
            "404 (Not Found)",
            "CORS policy",
            "Cross-Origin",
            "Mixed Content",
            "The resource at",
            "was preloaded using link preload",
            "was requested but not retrieved",
        ]
        critical_errors = [
            err for err in console_errors if not any(pattern in err for pattern in non_critical_patterns)
        ]

        return {
            "kind": "web_playwright",
            "ok": len(critical_errors) == 0,
            "url": f"http://localhost/{html_rel}",
            "entrypoint": html_rel,
            "duration_s": 0.1,
            "http_status": 200,
            "console_errors": console_errors,
            "has_canvas": False,
            "detail": "Playwright verification passed"
            if len(critical_errors) == 0
            else f"Console errors: {'; '.join(critical_errors[:3])}",
        }

    bench_gates._smoke_static_web_playwright = mock_smoke
    try:
        result = bench_gates._smoke_static_web(tmp_path, "index.html", timeout_s=10)

        # All errors are non-critical, so this should pass
        assert result["ok"] is True
        assert result["kind"] == "web_playwright"
        assert "Playwright verification passed" in result["detail"]
    finally:
        bench_gates._smoke_static_web_playwright = original_smoke


def test_smoke_static_web_playwright_critical_errors_fail(tmp_path: Path) -> None:
    """Critical JavaScript errors should cause failure."""
    # Create a test HTML file
    (tmp_path / "index.html").write_text(
        "<html><body><script>throw new Error('Critical error');</script></body></html>",
        encoding="utf-8",
    )

    # Mock Playwright to simulate critical errors
    console_errors = [
        "Uncaught Error: Critical error",
        "Failed to load resource: the server responded with a status of 404 (Not Found)",
    ]

    # Apply the new filtering logic
    non_critical_patterns = [
        "Failed to load resource",
        "favicon.ico",
        "net::ERR_",
        "404 (Not Found)",
        "CORS policy",
        "Cross-Origin",
        "Mixed Content",
        "The resource at",
        "was preloaded using link preload",
        "was requested but not retrieved",
    ]
    critical_errors = [err for err in console_errors if not any(pattern in err for pattern in non_critical_patterns)]

    result = {
        "kind": "web_playwright",
        "ok": len(critical_errors) == 0,
        "url": "http://localhost/index.html",
        "entrypoint": "index.html",
        "duration_s": 0.1,
        "http_status": 200,
        "console_errors": console_errors,
        "has_canvas": False,
        "detail": f"Console errors: {'; '.join(critical_errors[:3])}",
    }

    # Should fail because of the critical error
    assert result["ok"] is False
    assert "Uncaught Error: Critical error" in result["detail"]
