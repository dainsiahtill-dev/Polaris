"""Repeated projects describe stability; each project remains one independent unit."""

import copy

import pytest
from scripts.factory_bench._bench_lib.reliability import repeated_delivery


def batch(batch_id, gates):
    return {
        "batch_id": batch_id,
        "comparison": {"source_fingerprint": "a" * 16, "policy_sha256": "b" * 64},
        "selected_project_ids": ["A", "B", "C"],
        "frozen_inputs": {
            "catalog": {"sha256": "c" * 64},
            "host_config": {"sha256": "d" * 64},
            "llm_config": {"sha256": "e" * 64},
        },
        "workflow": "direct",
        "permission_profile_id": "p",
        "project_budget_seconds": 5400,
        "overall_budget_seconds": 6000,
        "records": [
            {"project_id": key, "bench_gate": gate, "observation": {"platform": {"run_id": batch_id + key}}}
            for key, gate in zip(["A", "B", "C"], gates, strict=True)
        ],
    }


def test_repetitions_count_projects_once_and_bound_unknown():
    first = batch("1", ["FAIL", "PASS", "UNKNOWN"])
    second = batch("2", ["PASS", "PASS", "FAIL"])
    result = repeated_delivery([first, second])
    assert result["projects"] == 3 and result["k"] == 2
    assert result["first_attempt"] == {"pass": 1, "fail": 1, "unknown": 1}
    assert result["at_least_one_pass"] == {"pass": 2, "fail": 0, "unknown": 1}
    assert result["all_attempts_pass"] == {"pass": 1, "fail": 2, "unknown": 0}
    with pytest.raises(ValueError):
        repeated_delivery([first, first])
    drift = copy.deepcopy(second)
    drift["comparison"]["policy_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        repeated_delivery([first, drift])
