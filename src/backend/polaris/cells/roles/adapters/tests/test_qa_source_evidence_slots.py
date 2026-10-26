"""Full current source belongs to evidence, never the protected user goal."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter, _qa_review_evidence_slots
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import observe_review_source_request


@pytest.mark.asyncio
async def test_large_source_is_preserved_in_both_judgement_and_json_repair_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    originals = {}
    for index in range(8):
        name = f"module_{index}.py"
        content = f"# 中文边界 {index}\n" + f"VALUE_{index} = 7\n" * 400
        (tmp_path / name).write_text(content, encoding="utf-8")
        originals[name] = content
    adapter = QAAdapter(workspace=str(tmp_path))
    calls = []

    async def invoke(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        assert observe_review_source_request([{"role": "user", "content": kwargs["message"]}], {})["gateway_message_token_estimate"] < 8000
        source = kwargs["context"]["context_evidence_slots"]["current_source"]
        records = {row["path"]: row for row in source["files"]}
        assert set(records) == set(originals)
        for name, content in originals.items():
            assert records[name]["content"] == content
            assert records[name]["content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "response": "invalid" if len(calls) == 1 else '{"verdict":"FAIL","critical_issues":["required behavior"]}'
        }

    monkeypatch.setattr(adapter, "_call_role_llm", invoke)
    monkeypatch.setattr(adapter, "_update_task_progress", lambda *_args: None)
    monkeypatch.setattr(adapter, "_apply_taskboard_qa_verdict", lambda **_kwargs: {})
    result = await adapter.execute(
        "qa-private",
        {"review_target": "Check original requirements"},
        {"run_id": "qa-private", "metadata": {"target_files": list(originals)}},
    )
    assert len(calls) == 2
    assert result["passed"] is False
    assert json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))["passed"] is False


def test_pm_ce_and_current_verifier_proof_reach_data_message_without_repair_history() -> None:
    context = {
        "pm_contract": {"original_requirement": "保留原始湿度语义"},
        "chief_engineer_blueprint": {
            "interface": "clampHumidity",
            "contract_hash": "ce-original",
            "llm_evidence": {
                "context_snapshot_ref": "a" * 24,
                "request_hash": "b" * 64,
                "final_request_context_audit": {"schema_version": "polaris.final_request_evidence_slot.v1"},
            },
        },
        "failure_feedback": {"failed_required_verifier": "node dist/cli/run.js"},
        "workspace_quality_evidence": {
            "artifact_ref": "qa/workspace-validation.json",
            "passed": False,
            "commands": [
                {"command": ["node", "dist/cli/run.js"], "exit_code": 1, "passed": False},
                {
                    "command": ["node", "dist/cli/run.js"],
                    "exit_code": 0,
                    "passed": True,
                    "verification_group": {
                        "input_hash": "current-input",
                        "receipt_sealed": True,
                        "commands": [
                            {"output_hash": "current-output", "exit_code": 0, "wrapped_argv": ["PRIVATE-WRAPPER"]}
                        ],
                    },
                },
            ],
            "repair": {"old_catalog": "HISTORY-MUST-NOT-REPLAY" * 1000},
        },
    }
    before = deepcopy(context)
    source = json.dumps(
        {"files": [{"path": "README.md", "status": "missing", "content": ""}], "source_drift": []}, ensure_ascii=False
    )
    slots = _qa_review_evidence_slots(context, source)
    messages = observe_review_source_request(
        [{"role": "system", "content": "QA identity"}, {"role": "user", "content": "Review the original scope"}],
        {"context_evidence_slots": slots},
    )["messages"]
    rendered = messages[-2]["content"]
    for expected in (
        "保留原始湿度语义",
        "clampHumidity",
        "ce-original",
        "node dist/cli/run.js",
        "current-input",
        "current-output",
        "qa/workspace-validation.json",
    ):
        assert expected in rendered
    assert "HISTORY-MUST-NOT-REPLAY" not in rendered
    assert "PRIVATE-WRAPPER" not in rendered
    assert "polaris.final_request_evidence_slot.v1" not in rendered
    assert "a" * 24 in rendered
    assert "b" * 64 in rendered
    assert '"historical_command_rows_omitted": 1' in rendered
    assert "Source observations complete: false" in rendered
    assert context == before


def test_equal_argv_keeps_distinct_ce_obligations_working_directories_and_legacy_phases() -> None:
    rows = [
        {
            "command": ["npm", "test"],
            "cwd": "frontend",
            "phase": "check",
            "ce_verification_binding": {
                "obligation_id": "front-test",
                "owner": "TASK-1",
                "argv": ["npm", "test"],
                "cwd": "frontend",
                "command_authority_hash": "front-authority",
            },
        },
        {
            "command": ["npm", "test"],
            "cwd": "backend",
            "phase": "check",
            "ce_verification_binding": {
                "obligation_id": "back-test",
                "owner": "TASK-2",
                "argv": ["npm", "test"],
                "cwd": "backend",
                "command_authority_hash": "back-authority",
            },
        },
        {"command": ["npm", "test"], "cwd": "frontend", "phase": "legacy-before-repair"},
        {"command": ["npm", "test"], "cwd": "frontend", "phase": "legacy-after-repair"},
    ]
    slots = _qa_review_evidence_slots({"workspace_quality_evidence": {"commands": rows}}, "")
    assert slots["qa_review"]["workspace_quality_evidence"]["commands"] == rows
