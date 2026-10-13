"""Manifest-scoped correction guidance reaches the actual native HTTP body."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base
from polaris.cells.roles.adapters.public import PMAdapter, create_role_adapter
from polaris.cells.roles.adapters.tests.test_pm_revision_document_verifiers import _candidate
from polaris.infrastructure.llm.providers import anthropic_provider
from polaris.kernelone.llm.engine.contracts import Usage
from polaris.kernelone.llm.types import InvokeResult


def _native_body(prompt: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    def intercept(_url: str, _headers: Any, payload: dict[str, Any], *_args: Any) -> InvokeResult:
        captured.append(deepcopy(payload))
        return InvokeResult(ok=False, output="", latency_ms=0, usage=Usage.estimate("", ""), error="intercepted")

    monkeypatch.setattr(anthropic_provider, "invoke_with_retry", intercept)
    anthropic_provider.AnthropicProvider().invoke(
        prompt,
        "fixture-model",
        {
            "api_key": "fixture",
            "base_url": "https://provider.test/anthropic",
            "max_tokens": 60_000,
            "chat_messages": [
                {"role": "system", "content": "You are Polaris PM; preserve the immutable edit manifest."},
                {"role": "user", "content": prompt},
            ],
        },
    )
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize("artifact_mode", [True, False])
def test_native_revision_examples_follow_manifest_without_admitting_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_mode: bool
) -> None:
    candidate = _candidate()
    original_verifiers = deepcopy(candidate["tasks"][1]["verification_commands"])
    if artifact_mode:
        # Same failure structure as R32: a compiled output binds a .js input
        # while the authored target is .ts, and QA declares an unsupported script.
        candidate["tasks"][1]["generated_outputs"][0]["inputs"][0]["path"] = "src/main.js"
        candidate["tasks"][2]["verification_commands"] = [
            {"modality": "environment_prep", "argv": ["bash", "scripts/verify.sh"], "cwd": "."}
        ]
    else:
        candidate["tasks"][0]["verification_commands"] = [
            {"modality": "environment_prep", "argv": ["node", "--version"], "cwd": "."}
        ]
    original = json.dumps(candidate, ensure_ascii=False)
    before = deepcopy(candidate)
    adapter = cast(PMAdapter, create_role_adapter("pm", workspace=str(tmp_path)))
    _, quality = adapter._evaluate_contract_quality(candidate["tasks"])
    assert quality["ok"] is False
    base = make_revision_base(
        original,
        workspace=str(tmp_path),
        run_id="pm-manifest",
        task_id="task-0-pm",
        directive="Implement the declared TypeScript CLI and verify its behavior.",
        quality=quality,
    )
    assert base is not None
    manifest = deepcopy(base.view["edit_manifest"])
    prompt = adapter._build_pm_retry_message(
        directive="Implement the declared TypeScript CLI and verify its behavior.",
        quality=quality,
        previous_output=original,
        validated_candidate=True,
        revision_base=base,
        output_budget_tokens=60_000,
    )
    body = _native_body(prompt, monkeypatch)
    physical = json.dumps(body, ensure_ascii=False)
    assert base.base_hash in physical
    assert body["max_tokens"] == 60_000
    assert candidate == before and base.response == original and base.view["edit_manifest"] == manifest

    if artifact_mode:
        assert all(not permissions["append"] for permissions in manifest["tasks"].values())
        assert "append:{steps:" not in physical, "Empty append permission still teaches an unauthorized step"
        assert "append.acceptance" not in physical, "Empty append permission still teaches acceptance appends"
        assert "no append fields authorized" in physical
        fixed_outputs = deepcopy(candidate["tasks"][1]["generated_outputs"])
        fixed_outputs[0]["inputs"][0]["path"] = "src/main.ts"
        updates = [
            {"task_id": "TASK-IMPLEMENT", "set": {"generated_outputs": fixed_outputs}},
            {"task_id": "TASK-QA", "set": {"verification_commands": _candidate()["tasks"][2]["verification_commands"]}},
        ]
    else:
        assert "steps" in manifest["tasks"]["TASK-DOC"]["append"]
        assert "acceptance" in manifest["tasks"]["TASK-DOC"]["append"]
        assert "append.acceptance" in physical
        assert "steps:[new_step]" in physical or "append.steps" in physical
        updates = [
            {
                "task_id": "TASK-DOC",
                "set": {"verification_commands": []},
                "append": {"acceptance": ["BLUEPRINT.md records the CLI verification contract"]},
            }
        ]
    merged = apply_revision(
        base,
        json.dumps(
            {
                "schema_version": "pm.task_revision.v1",
                "base_hash": base.base_hash,
                "updates": updates,
                "add_tasks": [],
                "remove_task_ids": [],
            }
        ),
    )
    _, corrected_quality = adapter._evaluate_contract_quality(merged["tasks"])
    assert corrected_quality["ok"] is True, corrected_quality
    assert merged["tasks"][1]["verification_commands"] == original_verifiers
    assert base.tasks == before["tasks"] and base.view["edit_manifest"] == manifest
