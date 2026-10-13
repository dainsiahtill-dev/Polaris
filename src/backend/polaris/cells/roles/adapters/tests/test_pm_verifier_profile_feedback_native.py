"""Owner-derived verifier feedback reaches the actual native provider body."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.tests.test_verification_operand_readiness import _command, _task
from polaris.cells.roles.adapters.internal.pm.revision import make_revision_base
from polaris.cells.roles.adapters.public import PMAdapter
from polaris.infrastructure.llm.providers import anthropic_provider
from polaris.kernelone.llm.types import InvokeResult, Usage


@pytest.mark.parametrize(
    "rejected",
    [
        ["node", "--test", "--import", "ts-node/register", "tests/rules.test.ts"],
        ["node", "--import", "tsx", "--test", "tests/rules.test.ts"],
    ],
)
def test_actual_diagnosis_pm_revision_and_native_body_explain_rejected_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rejected: list[str]
) -> None:
    planning = _task("PLAN", ["docs/design.md"], [], [])
    planning.update(phase="planning", assigned_to="ChiefEngineer")
    implementation = _task(
        "IMPLEMENT",
        ["package.json", "tsconfig.json", "src/library.ts", "src/cli.js"],
        ["PLAN"],
        [
            _command(["npm", "ci"], modality="environment_prep"),
            _command(["npx", "tsc", "-p", "tsconfig.json"], modality="build"),
            _command(["node", "src/cli.js"], modality="entrypoint"),
        ],
    )
    qa = _task("QA", ["tests/rules.test.ts"], ["IMPLEMENT"], [_command(rejected)])
    qa.update(phase="qa", assigned_to="QA")
    tasks = [planning, implementation, qa]
    before = deepcopy(tasks)
    adapter = PMAdapter(workspace=str(tmp_path))
    _, quality = adapter._evaluate_contract_quality(tasks)
    diagnosis = next(
        d for d in quality["contract_diagnostics"] if d["code"] == "verification_command_profile_unsupported"
    )
    assert quality["ok"] is False
    assert diagnosis["task_id"] == "QA"
    original = json.dumps({"tasks": tasks}, ensure_ascii=False)
    base = make_revision_base(
        original,
        workspace=str(tmp_path),
        run_id="pm-current",
        task_id="task-0-pm",
        directive="Implement and verify the declared TypeScript module.",
        quality=quality,
    )
    assert base is not None
    base_hash = base.base_hash
    manifest = deepcopy(base.view["edit_manifest"])
    prompt = adapter._build_pm_retry_message(
        directive="Implement and verify the declared TypeScript module.",
        quality=quality,
        previous_output=original,
        validated_candidate=True,
        revision_base=base,
        output_budget_tokens=60_000,
    )
    captured: list[dict[str, Any]] = []

    def intercept(_url: str, _headers: Any, payload: dict[str, Any], *_args: Any) -> InvokeResult:
        captured.append(deepcopy(payload))
        return InvokeResult(
            ok=False, output="", latency_ms=0, usage=Usage.estimate("", ""), error="transport_intercepted"
        )

    # Capture AFTER the production Anthropic provider constructs its HTTP body.
    monkeypatch.setattr(anthropic_provider, "invoke_with_retry", intercept)
    anthropic_provider.AnthropicProvider().invoke(
        prompt,
        "fixture-model",
        {
            "api_key": "fixture",
            "base_url": "https://provider.test/anthropic",
            "max_tokens": 60_000,
            "chat_messages": [
                {"role": "system", "content": "You are Polaris PM; preserve original contract authority."},
                {"role": "user", "content": prompt},
            ],
        },
    )
    assert len(captured) == 1
    physical = json.dumps(captured[0], ensure_ascii=False)
    assert "untrusted_verifier_command" in physical
    assert "rejected_argument" in physical, "Actual native feedback lacks the canonical rejection operand"
    assert "accepted_shape" in physical, "Actual native feedback lacks the existing owner's accepted grammar"
    assert "node.test_runner" in physical
    assert "--import" in physical
    assert "--test-reporter=tap" in physical
    assert base_hash in physical
    assert tasks == before
    assert base.base_hash == base_hash and base.view["edit_manifest"] == manifest
    assert base.tasks[-1]["verification_commands"][0]["argv"] == rejected
    assert captured[0]["max_tokens"] == 60_000
