from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    CompileEvidencePolicyCommandV1,
    ControlPlaneVerifierPolicyV1Error,
    ReadVerifierPolicyQueryV1,
    UpdateVerifierPolicyCommandV1,
    compile_evidence_policy,
    read_verifier_policy,
    update_verifier_policy,
    verifier_policy_to_gate_policy,
)


def test_verifier_policy_defaults_all_optional_modalities_disabled(tmp_path: Path) -> None:
    result = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(tmp_path)))
    policy = result.policy

    assert policy["source"] == "control_plane.verifier_policy"
    assert policy["enabled_modalities"] == []
    assert policy["required_modalities"] == []
    assert policy["safety"] == {
        "optional_by_default": True,
        "internal_harness_owned": False,
        "executes_verifiers": False,
        "requires_explicit_user_enablement": True,
    }
    assert policy["capabilities"]["browser"]["enabled"] is False
    assert policy["capabilities"]["visual"]["enabled"] is False


def test_verifier_policy_persists_enabled_browser_and_visual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")
    monkeypatch.setenv("KERNELONE_MULTIMODAL_QA_ENABLED", "1")

    result = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            browser_enabled=True,
            visual_enabled=True,
            required_modalities=("browser", "visual"),
        )
    )

    policy = result.policy
    assert policy["enabled_modalities"] == ["browser", "visual"]
    assert policy["required_modalities"] == ["browser", "visual"]

    loaded = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(tmp_path))).policy
    assert loaded["enabled_modalities"] == ["browser", "visual"]
    assert loaded["required_modalities"] == ["browser", "visual"]


def test_verifier_policy_rejects_required_disabled_modality(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="enabled first"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                browser_enabled=False,
                required_modalities=("browser",),
            )
        )


def test_verifier_policy_rejects_required_unavailable_modality(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="not available"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                browser_enabled=True,
                required_modalities=("browser",),
            )
        )


def test_verifier_policy_persists_required_custom_script_even_when_runtime_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", raising=False)

    result = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            required_modalities=("custom_script",),
            custom_scripts=(
                {
                    "id": "custom-smoke",
                    "path": "verify.py",
                    "modality": "custom_script",
                    "enabled": True,
                    "required": True,
                },
            ),
        )
    )

    policy = result.policy
    assert policy["required_modalities"] == ["custom_script"]
    assert policy["capabilities"]["custom_script"]["enabled"] is True
    assert policy["capabilities"]["custom_script"]["required"] is True
    assert policy["capabilities"]["custom_script"]["available"] is False


def test_verifier_policy_rejects_absolute_custom_script_path(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="workspace-relative"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                custom_script_enabled=True,
                required_modalities=("custom_script",),
                custom_scripts=({"id": "bad", "path": "/tmp/run.sh", "required": True},),
            )
        )


def test_verifier_policy_exports_gate_policy_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")
    monkeypatch.setenv("KERNELONE_MULTIMODAL_QA_ENABLED", "1")

    policy = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            browser_enabled=True,
            visual_enabled=True,
            required_modalities=("browser",),
        )
    ).policy

    fragment = verifier_policy_to_gate_policy(policy)

    assert fragment == {
        "source": "control_plane.verifier_policy",
        "enabled_evidence_modalities": ["browser", "visual"],
        "required_evidence_modalities": ["browser"],
        "custom_scripts": [],
    }


def test_evidence_policy_compiler_keeps_unavailable_browser_advisory_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", raising=False)

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-web",
            run_id="run-web",
            project_type="interactive_visual",
            language="typescript",
            target_files=("index.html", "src/main.ts"),
            acceptance_criteria=("HTML5 canvas paints a visible first frame",),
        )
    ).policy

    assert {"qa", "code", "tool_receipt"} <= set(policy["required_evidence_modalities"])
    assert "browser" not in policy["required_evidence_modalities"]
    assert "browser" in policy["advisory_modalities"]
    assert policy["unavailable_required_blockers"] == []
    waived = {item["modality"] for item in policy["waived_modalities"]}
    assert "browser" in waived


def test_evidence_policy_compiler_requires_browser_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-web",
            run_id="run-web",
            project_type="html5_canvas",
            target_files=("index.html", "src/main.ts"),
            acceptance_criteria=("browser smoke test must pass",),
        )
    ).policy

    assert "browser" in policy["required_evidence_modalities"]
    assert "browser" in policy["enabled_evidence_modalities"]
    assert policy["unavailable_required_blockers"] == []
    assert policy["gate_policy"]["required_evidence_modalities"] == policy["required_evidence_modalities"]
    assert policy["policy_hash"]


def test_evidence_policy_compiler_blocks_explicit_unavailable_required_modality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", raising=False)

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-hard-browser",
            explicit_required_modalities=("browser",),
        )
    ).policy

    assert "browser" in policy["required_evidence_modalities"]
    assert policy["unavailable_required_blockers"] == [
        {
            "modality": "browser",
            "reason": "Set KERNELONE_BROWSER_VERIFIER_AVAILABLE=1 to advertise browser verifier support.",
        }
    ]


def test_evidence_policy_compiler_maps_api_service_to_contract_and_integration(tmp_path: Path) -> None:
    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-api",
            project_type="api_service",
            language="python",
            target_files=("src/api.py", "tests/test_api.py"),
            acceptance_criteria=("health check endpoint and integration test pass",),
        )
    ).policy

    assert {"qa", "code", "tool_receipt", "api_contract", "integration", "command"} <= set(
        policy["required_evidence_modalities"]
    )
    assert "security" in policy["advisory_modalities"]
