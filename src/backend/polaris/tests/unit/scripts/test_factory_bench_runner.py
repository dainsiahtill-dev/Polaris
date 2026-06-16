"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

from typing import Any

from scripts.factory_bench.run_factory_bench import apply_factory_bench_gates


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "chain_state": "clean",
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "wrong_product_suspect": False,
    }
    record.update(overrides)
    return record


def test_chain_failure_overrides_static_artifact_checks() -> None:
    record = _record(
        chain_state="fail",
        chain_results={"qa_ran": False, "qa_passed": False},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 1})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["chain_clean"]["ok"] is False
    assert gates["integration_qa_passed"]["ok"] is False


def test_missing_qa_verdict_and_wrong_product_are_fail_closed() -> None:
    record = _record(
        has_qa_verdict=False,
        wrong_product_suspect=True,
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["qa_verdict_artifact_present"]["ok"] is False
    assert gates["wrong_product_guard"]["ok"] is False


def test_clean_chain_preserves_static_pass() -> None:
    record = _record()

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is True
    assert all(gate["ok"] for gate in record["factory_gates"])
