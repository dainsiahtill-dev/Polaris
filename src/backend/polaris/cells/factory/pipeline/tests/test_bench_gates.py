from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.internal import bench_gates
from polaris.cells.factory.pipeline.internal.bench_gates import (
    aggregate_goal_audit,
    build_llm_route_audit,
    build_real_run_gate,
    classify_factory_bench_failure,
    collect_llm_events,
)


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

    assert gate["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["started"] is True


def test_real_run_gate_starts_static_web_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    record = {"code_files": ["index.html", "app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "node --check passed"
    assert gate["entrypoint"]["kind"] == "web_static"


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
    assert gate["entrypoint"]["kind"] == "web_static"


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
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b",
            "binding_id": "d1",
            "terminal": True,
            "invocation": True,
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


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
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "terminal": True,
            "invocation": True,
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


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
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "model": "qwen3.6-27b-gpu1",
            "terminal": True,
            "invocation": True,
        },
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
        {"event": "llm_call_end", "role": "pm", "model": "kimi-for-coding", "terminal": True, "invocation": True},
        {"event": "llm_call_end", "role": "qa", "model": "MiniMax-M3", "terminal": True, "invocation": True},
        {
            "event": "llm_call_end",
            "role": "director",
            "model": "qwen3.6-27b-gpu1",
            "terminal": True,
            "invocation": True,
        },
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


def test_llm_route_audit_matches_providerless_events_by_model() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "ce0"}
        ],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        {"event": "llm_call_end", "role": "pm", "model": "kimi-for-coding", "terminal": True, "invocation": True},
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "model": "kimi-for-coding",
            "terminal": True,
            "invocation": True,
        },
        {"event": "llm_call_end", "role": "qa", "model": "MiniMax-M3", "terminal": True, "invocation": True},
        {
            "event": "llm_call_end",
            "role": "director",
            "model": "qwen3.6-27b-gpu0",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "model": "qwen3.6-27b-gpu1",
            "terminal": True,
            "invocation": True,
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["missing_bindings"] == []


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
