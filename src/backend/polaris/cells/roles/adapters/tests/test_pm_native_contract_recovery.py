"""Replay real PM drafts through public admission and real local provider HTTP."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import (
    _authority,
    _resolved_cut,
    _Resolver,
)
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.pm_planning.public.task_intent import (
    PmTaskIntentError,
    normalize_pm_task_intent,
    pm_task_intent_payload,
)
from polaris.cells.roles.adapters.internal.pm.parsing import _decode_complete_pm_payload, _PMContractError
from polaris.cells.roles.adapters.public import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _local_runtime
from polaris.cells.roles.adapters.tests.test_pm_task_intent import _intent
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
from polaris.cells.roles.kernel.public.generation_outcome import GENERATION_OUTCOME_SCHEMA, RoleGenerationOutcomeV1
from polaris.cells.roles.profile.public.service import RoleProfileRegistry
from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.llm.engine.executor import AIExecutor

_FIXTURES = Path(__file__).parent / "fixtures/native_pm_contract"


def _native(name: str) -> str:
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    expected = {
        "semantic_advisory.txt": (4086, "7965b30fac3140a659be4d03f0a3d0a1170b6a4635cddbe09acb0e8359a1616f"),
        "top_verification_hint.txt": (3594, "3f04ac02b5c514da8b03cd3ea3da8d4d69e91d5a8f27c7034e677cdeaec0472f"),
        "duplicate_schema.txt": (3315, "6b5f65d548d4bc94a4a992a37357b425bdffd196615e13a286b985bd0f61142c"),
    }
    assert (len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()) == expected[name]
    return text


def test_exact_native_semantic_advisories_preserve_content_without_capability() -> None:
    raw = json.loads(_native("semantic_advisory.txt"))
    before = deepcopy(raw)
    tasks = normalize_pm_task_intent(raw)
    assert raw == before
    assert len(tasks) == 1
    task = tasks[0]
    assert task["target_files"] == task["scope_paths"] == task["verification_commands"] == []
    assert task["description"] == raw["tasks"][0]["scope"]
    advisory = task["metadata"]["planning_advisory"]
    assert advisory["project_metadata"] == raw["project_metadata"]
    assert advisory["verification_recipe_hint"] == raw["tasks"][0]["verification_recipe_hint"]
    assert task["metadata"]["planning_advisory_execution_authority"] is False
    assert task["metadata"]["explicit_technical_fields"] == []
    quality = evaluate_pm_task_quality({"tasks": tasks}, workspace_full="/example/workspace")
    assert quality["ok"] is True, quality
    assert quality["score"] == 100
    assert "pm_task_intent_advisory_not_authority" in quality["warnings"]
    assert any(row["code"] == "pm_task_intent_advisory_not_authority" for row in quality["contract_diagnostics"])
    rebuilt = pm_task_intent_payload(tasks)
    assert rebuilt["project_metadata"] == raw["project_metadata"]
    assert rebuilt["tasks"][0]["verification_recipe_hint"] == raw["tasks"][0]["verification_recipe_hint"]
    assert rebuilt["tasks"][0]["description"] == raw["tasks"][0]["scope"]


def test_exact_native_top_hint_is_advisory_and_roundtrips() -> None:
    raw = json.loads(_native("top_verification_hint.txt"))
    before = deepcopy(raw)
    tasks = normalize_pm_task_intent(raw)
    assert raw == before
    assert (
        tasks[0]["metadata"]["planning_advisory"]["project_verification_recipe_hint"] == raw["verification_recipe_hint"]
    )
    assert tasks[0]["metadata"]["planning_advisory_execution_authority"] is False
    assert tasks[0]["target_files"] == tasks[0]["verification_commands"] == []
    assert evaluate_pm_task_quality({"tasks": tasks}, workspace_full="/example/workspace")["ok"] is True
    assert pm_task_intent_payload(tasks)["verification_recipe_hint"] == raw["verification_recipe_hint"]


@pytest.mark.parametrize("bad", [None, [], "advice", {"nested": float("nan")}, {"nested": {"bad"}}])
def test_top_hint_rejects_non_json_objects(bad: Any) -> None:
    payload = _intent()
    payload["verification_recipe_hint"] = bad
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(payload)
    assert error.value.diagnostic["path"] == "$.verification_recipe_hint"


def test_top_and_task_hint_never_override_authority_or_each_other() -> None:
    payload = _intent()
    injected = {
        "product_goal": "different",
        "acceptance": [],
        "id": "OTHER",
        "run_id": "foreign",
        "execution_authority_hash": "forged",
        "depth_policy": {"required": False},
        "scope_paths": ["../escape"],
        "verification_commands": [{"argv": ["bash", "-c", "anything"], "cwd": "/outside"}],
    }
    payload["verification_recipe_hint"] = injected
    payload["tasks"][0]["verification_recipe_hint"] = {"language": "python"}
    task = normalize_pm_task_intent(payload)[0]
    assert task["product_goal"] == payload["product_goal"] and task["product_acceptance"] == payload["acceptance"]
    assert task["id"] == "TASK-1" and task["assigned_to"] == "Director"
    assert task["scope_paths"] == task["target_files"] == task["verification_commands"] == []
    assert "depth_policy" not in task and "execution_authority_hash" not in task and "run_id" not in task
    advisory = task["metadata"]["planning_advisory"]
    assert advisory["project_verification_recipe_hint"] == injected
    assert advisory["verification_recipe_hint"] == {"language": "python"}
    rebuilt = pm_task_intent_payload([task])
    assert rebuilt["verification_recipe_hint"] == injected
    assert rebuilt["tasks"][0]["verification_recipe_hint"] == {"language": "python"}
    payload["tasks"][0]["target_files"] = ["../escape"]
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(payload)
    assert error.value.diagnostic["hard_boundary"] is True


@pytest.mark.parametrize("projection", [False, True])
def test_retry_generation_projection_keeps_diagnostics_without_control_ids(tmp_path: Path, projection: bool) -> None:
    raw = _native("top_verification_hint.txt")
    own = RoleGenerationOutcomeV1(
        schema_version=GENERATION_OUTCOME_SCHEMA,
        run_id="pm-8e663fe945df",
        call_id="53c52fa4520f484e9b506a6a963c866a",
        native_finish_reason="end_turn",
        max_tokens=60000,
        response_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        request_freeze_id="69483c0d16134d9dbb9bd3535da433af",
        final_semantic_request_hash="fff989d4815ea316a430cd1263a4ff77820211ae5e6e466d46419674c7f0f154",
    )
    adapter = PMAdapter(workspace=str(tmp_path))
    signals: list[dict[str, Any]] = []
    diagnostic = adapter._record_pm_generation_outcome(
        {"metadata": {"generation_outcome": own.to_payload()}}, raw, {"run_id": own.run_id}, signals
    )
    directive = "Preserve exact original requirements and constraints."
    prompt = adapter._build_pm_retry_message(
        directive=directive,
        quality={
            "ok": False,
            "score": 0,
            "contract_diagnostics": [
                {"code": "pm_task_intent_invalid", "path": "$", "unknown_fields": ["foreign_field"]}
            ],
        },
        previous_output=raw,
        validated_candidate=False,
        generation_diagnostic=diagnostic,
        output_budget_tokens=60000,
        projection_hint={"projection": {"scenario_id": "sample", "project_slug": "sample"}} if projection else None,
    )
    assert signals[0]["generation_outcome"] == diagnostic["generation_outcome"] == own.to_payload()
    assert (
        directive in prompt
        and own.response_sha256 in prompt
        and "3594" in prompt
        and "end_turn" in prompt
        and "60000" in prompt
    )
    assert all(
        value not in prompt
        for value in [own.run_id, own.call_id, own.request_freeze_id, own.final_semantic_request_hash]
    )
    audit = audit_context_os_prompt_messages(
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": directive}],
        expected=True,
        current_user_instruction=directive,
    )
    assert audit["requirements"]["control_plane_isolated"] is True, audit


@pytest.mark.parametrize("typed", [False, True])
def test_feedback_projects_runtime_metadata_without_clipping_repair_operands(tmp_path: Path, typed: bool) -> None:
    from polaris.cells.roles.adapters.internal.pm.revision import make_revision_base

    adapter = PMAdapter(workspace=str(tmp_path))
    payload = _intent()
    payload["tasks"][0]["acceptance"] = []
    raw = json.dumps(payload)
    quality = {
        "ok": False,
        "score": 0,
        "summary": "Repair acceptance",
        "critical_issues": ["missing_acceptance"],
        "warnings": [],
        "contract_diagnostics": [
            {
                "code": "missing_acceptance",
                "path": "$.tasks[0].acceptance",
                "task_id": "TASK-1",
                "message": "Acceptance required",
                "repair_fields": ["acceptance"],
                "provenance": {"original_task_id": "TASK-1", "run_id": "private-run", "call_id": "private-call"},
                "evidence": {
                    "primary_language": "python",
                    "run_id": "private-run",
                    "execution_authority_hash": "private-authority",
                },
            }
        ],
        "generation_outcome": {"run_id": "private-run"},
        "request_freeze_id": "private-freeze",
    }
    original = deepcopy(quality)
    base = (
        make_revision_base(
            raw,
            workspace=str(tmp_path),
            run_id="base-run",
            task_id="base-task",
            directive="Exact original product requirement.",
            quality=quality,
        )
        if typed
        else None
    )
    if typed:
        assert base is not None
    prompt = adapter._build_pm_retry_message(
        directive="Exact original product requirement.",
        quality=quality,
        previous_output=raw,
        validated_candidate=bool(base),
        revision_base=base,
        output_budget_tokens=60000,
    )
    assert quality == original
    assert all(value not in prompt for value in ["private-run", "private-call", "private-authority", "private-freeze"])
    assert all(
        value in prompt
        for value in [
            "missing_acceptance",
            "$.tasks[0].acceptance",
            "TASK-1",
            "python",
            "Exact original product requirement.",
        ]
    )
    if typed:
        assert base.view_json in prompt and base.base_hash in prompt


def test_exact_native_duplicate_schema_remains_ambiguous_and_unadmitted() -> None:
    with pytest.raises(_PMContractError, match="Duplicate JSON field 'schema_version'"):
        _decode_complete_pm_payload(_native("duplicate_schema.txt"))


@pytest.mark.parametrize("container", ["envelope", "task"])
def test_unknown_semantic_fields_name_exact_rejected_operands(container: str) -> None:
    payload = _intent()
    row = payload if container == "envelope" else payload["tasks"][0]
    row["foreign_authority"] = True
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(payload)
    assert error.value.diagnostic["unknown_fields"] == ["foreign_authority"]
    assert error.value.diagnostic["allowed_fields"]
    assert "foreign_authority" in str(error.value)


def test_advisory_cannot_mask_unsafe_explicit_scope() -> None:
    payload = _intent()
    payload["project_metadata"] = {"allowed_write_paths": ["../outside.py"], "execution_authority_hash": "forged"}
    payload["tasks"][0]["target_files"] = ["../outside.py"]
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(payload)
    assert error.value.diagnostic["hard_boundary"] is True


def test_nested_advice_cannot_replace_explicit_identity_scope_recipe_or_depth() -> None:
    payload = _intent()
    payload["project_metadata"] = {
        "primary_language": "rust",
        "id": "OTHER",
        "assigned_to": "PM",
        "bench_level": {"required_minimums": {"min_prod_lines": 0}},
        "execution_authority_hash": "forged",
        "target_files": ["../outside.py"],
    }
    row = payload["tasks"][0]
    row["target_files"] = ["records.py"]
    row["verification_commands"] = [{"modality": "test", "argv": ["python3", "-m", "unittest", "discover"], "cwd": "."}]
    row["verification_recipe_hint"] = {
        "verification_commands": [{"argv": ["bash", "-c", "anything"], "cwd": "/outside"}],
        "target_files": ["../outside.py"],
        "task_id": "OTHER",
        "depth_policy": {"required": False},
    }
    task = normalize_pm_task_intent(payload)[0]
    assert task["id"] == "TASK-1" and task["assigned_to"] == "Director"
    assert task["target_files"] == row["target_files"]
    assert task["verification_commands"] == row["verification_commands"]
    assert task["explicit_constraints"] == payload["explicit_constraints"]
    assert "execution_authority_hash" not in task and "depth_policy" not in task
    assert "bench_level" not in task["metadata"]
    assert task["metadata"]["topology_authority"] == "chief_engineer"
    assert task["metadata"]["planning_advisory_execution_authority"] is False
    assert evaluate_pm_task_quality({"tasks": [task]}, workspace_full="/example/workspace")["ok"] is True


def test_same_base_semantic_revision_keeps_advisory_readonly(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base

    payload = json.loads(_native("semantic_advisory.txt"))
    payload["tasks"][0]["acceptance"] = []
    adapter = PMAdapter(workspace=str(tmp_path))
    raw = json.dumps(payload)
    tasks = adapter._extract_task_contracts(raw, directive="Create the requested simulation")
    _, quality = adapter._evaluate_contract_quality(tasks, directive="Create the requested simulation")
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="advice-run",
        task_id="advice-task",
        directive="Create the requested simulation",
        quality=quality,
    )
    assert base is not None
    assert "metadata" not in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    patch = {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [{"task_id": "TASK-1", "set": {"acceptance": ["A seed produces reproducible output"]}}],
        "add_tasks": [],
        "remove_task_ids": [],
    }
    merged = apply_revision(base, json.dumps(patch))
    assert merged["project_metadata"] == payload["project_metadata"]
    assert merged["tasks"][0]["verification_recipe_hint"] == payload["tasks"][0]["verification_recipe_hint"]
    assert merged["tasks"][0]["description"] == payload["tasks"][0]["scope"]
    assert merged["tasks"][0].get("target_files", []) == []
    patch["updates"][0]["set"] = {"metadata": {"execution_authority_hash": "forged"}}
    with pytest.raises(_PMContractError) as error:
        apply_revision(base, json.dumps(patch))
    assert error.value.diagnostic["repairable"] is False


def test_scope_alias_never_displaces_distinct_description() -> None:
    payload = _intent()
    payload["tasks"][0].update(scope="Natural scope description", description="Original distinct requirement")
    task = normalize_pm_task_intent(payload)[0]
    assert task["description"] == "Original distinct requirement"
    assert task["metadata"]["planning_advisory"]["scope"] == "Natural scope description"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "native",
        "syntax_then_duplicate",
        "repeat_duplicate",
        "unsafe_after_syntax",
        "bound_native_top",
        "bound_semantic_then_first",
        "bound_exact_first",
    ],
)
async def test_native_semantic_and_format_recovery_keeps_its_admission_contract(
    tmp_path: Path, scenario: str
) -> None:
    """The native PM drafts keep their public admission contract.

    The scenario this test drove replayed the captured drafts through a real
    local provider HTTP endpoint and the retired legacy role session entry.
    That entry is closed now: a new engineering run has exactly one owner,
    the engineering run service. The admission contract of each captured
    draft stays a live server-side fact, so it is asserted directly here.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id=f"retired-pm-native-{scenario}",
        workspace=str(tmp_path),
    )
    native = _native("semantic_advisory.txt")
    duplicate = _native("duplicate_schema.txt")
    malformed = '{"schema_version":"pm.task_intent.v1","tasks":'
    valid = _intent()
    top = _native("top_verification_hint.txt")
    exact_first = (_FIXTURES / "semantic_path_1.txt").read_text(encoding="utf-8")
    ordinary = json.loads(exact_first)
    ordinary["required_requirements"] = {"background": "Useful advice is not a required-requirements array"}
    ordinary = json.dumps(ordinary, ensure_ascii=False)
    unsafe = deepcopy(valid)
    unsafe["tasks"][0]["target_files"] = ["../outside.py"]

    def admitted(candidate: str) -> dict[str, Any]:
        """Project one captured draft through the public admission gate."""
        tasks = normalize_pm_task_intent(json.loads(candidate))
        quality = evaluate_pm_task_quality({"tasks": tasks}, workspace_full=str(tmp_path))
        assert quality["ok"] is True, quality
        assert tasks[0]["target_files"] == tasks[0]["verification_commands"] == []
        return tasks[0]

    if scenario == "native":
        task = admitted(native)
        assert task["metadata"]["planning_advisory_execution_authority"] is False
    elif scenario == "bound_native_top":
        task = admitted(top)
        assert task["metadata"]["planning_advisory_execution_authority"] is False
    elif scenario == "bound_exact_first":
        task = admitted(exact_first)
        assert task["metadata"]["planning_advisory"]["task_metadata"] == json.loads(exact_first)["tasks"][0][
            "task_metadata"
        ]
        assert task["metadata"]["planning_advisory_execution_authority"] is False
    elif scenario == "bound_semantic_then_first":
        # The first draft is refused for its non-array required requirements,
        # so the stage needs one complete re-answer. That re-answer is
        # admitted and stays free of the rejected draft's bytes.
        with pytest.raises(PmTaskIntentError):
            normalize_pm_task_intent(json.loads(ordinary))
        task = admitted(exact_first)
        assert task["metadata"]["planning_advisory_execution_authority"] is False
    elif scenario == "syntax_then_duplicate":
        # The malformed draft has no candidate and the ambiguous duplicate
        # schema stays unadmitted, so the stage needs one complete re-answer.
        with pytest.raises(_PMContractError):
            _decode_complete_pm_payload(malformed)
        with pytest.raises(_PMContractError, match="Duplicate JSON field 'schema_version'"):
            _decode_complete_pm_payload(duplicate)
        admitted(json.dumps(valid))
    elif scenario == "repeat_duplicate":
        # The ambiguous duplicate schema stays unadmitted on every attempt.
        for _attempt in range(2):
            with pytest.raises(_PMContractError, match="Duplicate JSON field 'schema_version'"):
                _decode_complete_pm_payload(duplicate)
    else:
        # The malformed draft has no candidate and the unsafe explicit scope
        # is a terminal hard boundary, so no repair can erase it.
        with pytest.raises(_PMContractError):
            _decode_complete_pm_payload(malformed)
        with pytest.raises(PmTaskIntentError) as error:
            normalize_pm_task_intent(unsafe)
        assert error.value.diagnostic["hard_boundary"] is True
