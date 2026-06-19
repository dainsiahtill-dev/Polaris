from __future__ import annotations

import json
from pathlib import Path

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


def test_real_run_gate_starts_static_web_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    record = {"code_files": ["index.html", "app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "node --check passed"
    assert gate["entrypoint"]["kind"] == "web_static"


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
