"""Owner scenario observations retain real failure and physical effect semantics."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.public import DirectedEffectExecutionContextV1, DirectedEffectFencePortsV1
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import (
    build_synthetic_mutation_context,
    observe_context_error_retry,
    observe_final_request_metadata,
    observe_line_range_execution,
    observe_review_source_request,
    observe_sibling_request_evidence,
)

CONSUMERS = (
    "test_director_completion_command_projection.py",
    "test_director_context_preflight_failure.py",
    "test_director_dependency_artifact_evidence.py",
    "test_director_directed_effect_mutation_port.py",
    "test_director_line_range_contract_closure.py",
    "test_director_quality_failure_evidence_metrics.py",
    "test_director_repair_repeat_attempt_widening.py",
    "test_director_write_guidance.py",
    "test_pm_factory_controlled_identity.py",
    "test_qa_blueprint_source_run_binding.py",
    "test_qa_source_evidence_slots.py",
    "test_unplannable_probe_governed_continuation.py",
)


@pytest.mark.parametrize("filename", CONSUMERS)
def test_adapter_consumers_keep_kernel_implementation_owner_boundary(filename: str) -> None:
    path = Path(__file__).resolve().parents[2] / "adapters/tests" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    crossings: list[tuple[object, ...]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            private = node.module.startswith("polaris.cells.roles.kernel.internal")
            tests = node.module.startswith("polaris.cells.roles.kernel.tests")
            for alias in node.names:
                if private or (tests and alias.name.startswith("_")):
                    crossings.append((node.lineno, node.module, alias.name))
        elif isinstance(node, ast.Import):
            crossings.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name.startswith("polaris.cells.roles.kernel.internal")
            )
    assert crossings == []


@pytest.mark.parametrize(
    "error,category,retryable",
    [
        (
            "Final provider request evidence coverage failed; missing_required_refs=framework_best_practices",
            "validation",
            False,
        ),
        ('529 Server Error from provider: {"error":{"type":"overloaded_error"}}', "network", True),
    ],
)
def test_caller_error_retry_observations_preserve_owner_semantics(error: str, category: str, retryable: bool) -> None:
    observation = observe_context_error_retry(error)
    assert observation["category"] == category
    assert observation["retryable"] is retryable


def test_synthetic_contract_input_returns_public_values_and_actual_single_use_fence() -> None:
    context, ports, workspace = build_synthetic_mutation_context()
    assert type(context) is DirectedEffectExecutionContextV1
    assert type(ports) is DirectedEffectFencePortsV1
    assert workspace == "/workspace"
    first = ports.consume.consume(context)
    second = ports.consume.consume(context)
    assert first.ok is True
    assert second.ok is False
    assert second.error_code == "deo_context_replayed"


def test_review_evidence_assembly_preserves_goal_and_rejects_hash_drift() -> None:
    content = "VALUE = 7\n"
    record = {
        "path": "source.py",
        "status": "readable",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    context = {"context_evidence_slots": {"current_source": {"files": [record], "source_drift": []}}}
    original = [{"role": "system", "content": "QA identity"}, {"role": "user", "content": "Review original goal"}]
    observed = observe_review_source_request(original, context)
    assert observed["messages"][-1] == original[-1]
    assert observed["messages"][0] == original[0]
    assert observed["messages"][-2]["name"] == "current_source_evidence"
    assert content.rstrip() in observed["messages"][-2]["content"]
    assert observed["final_request_audit"]["message_count"] == 3
    record["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="current_source_evidence_hash_mismatch"):
        observe_review_source_request(original, context)


def test_final_audit_observes_delivery_numbers_and_rejects_invalid_sibling_input() -> None:
    audit = observe_final_request_metadata(
        {"delivery_depth_contract": {"level": 3, "minimums": {"min_test_files": 2, "note": "excluded"}}}, []
    )
    assert audit["request_metadata_summary"]["delivery_depth_contract_summary"]["minimums"] == {"min_test_files": 2}
    sibling = observe_sibling_request_evidence({}, messages=[{"role": "user", "content": "unbound"}])
    assert sibling["accepted"] is False
    assert sibling["structurally_valid"] is False
    assert sibling["final_request_audit"]["has_actual_sibling_exports"] is False


@pytest.mark.asyncio
async def test_line_range_scenario_returns_detached_observations_of_real_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = await observe_line_range_execution(
        tmp_path,
        monkeypatch,
        [("range", "edit_blocks", {"file": "owned.txt", "startLine": 2, "endLine": 2, "newText": "BETA\n"})],
    )
    assert observed["content"] == "alpha\nBETA\ngamma\ndelta\n"
    row = observed["result"]["batch_receipt"]["results"][0]
    assert row["result"]["before_sha256"] == hashlib.sha256(observed["original_content"].encode("utf-8")).hexdigest()
    assert row["result"]["after_sha256"] == hashlib.sha256(observed["content"].encode("utf-8")).hexdigest()
    assert row["result"]["effect_receipt_commit"]["state"] == "RECEIPT_COMMITTED"
    assert len(observed["commits"]) == 1
    assert not ({"executor", "ledger", "gateway", "chain"} & set(observed))
