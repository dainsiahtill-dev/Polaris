from __future__ import annotations

import sys
from pathlib import Path

from polaris.cells.control_plane.verifier_execution.public import (
    RunVerifierPolicyCommandV1,
    run_verifier_policy,
)


def _policy(script_path: str) -> dict[str, object]:
    return {
        "capabilities": {
            "custom_script": {
                "enabled": True,
                "required": True,
                "available": True,
                "reason": "",
            }
        },
        "custom_scripts": [
            {
                "id": "custom-ok",
                "path": script_path,
                "modality": "custom_script",
                "enabled": True,
                "required": True,
            }
        ],
    }


def test_custom_script_verifier_is_disabled_without_environment_flag(tmp_path: Path) -> None:
    script = tmp_path / "verify.py"
    script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(workspace=str(tmp_path), policy=_policy("verify.py"))
    )

    verifier = result.gate_patch["user_verifiers"][0]
    assert verifier["ok"] is False
    assert "KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED" in verifier["detail"]


def test_custom_script_verifier_executes_when_explicitly_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    script = tmp_path / "verify.py"
    script.write_text(
        "from pathlib import Path\n"
        "assert Path('artifact.txt').read_text(encoding='utf-8') == 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "artifact.txt").write_text("ok", encoding="utf-8")

    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(workspace=str(tmp_path), policy=_policy("verify.py"))
    )

    verifier = result.gate_patch["user_verifiers"][0]
    assert verifier["ok"] is True
    assert verifier["exit_code"] == 0
    assert verifier["script"] == "verify.py"
    assert verifier["hash"].startswith("sha256:")


def test_custom_script_verifier_accepts_gate_policy_fragment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    script = tmp_path / "verify.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            policy={
                "enabled_evidence_modalities": ["custom_script"],
                "required_evidence_modalities": ["custom_script"],
                "custom_scripts": [
                    {
                        "id": "custom-ok",
                        "path": "verify.py",
                        "modality": "custom_script",
                        "enabled": True,
                    }
                ],
            },
        )
    )

    verifier = result.gate_patch["user_verifiers"][0]
    assert verifier["ok"] is True
    assert verifier["required"] is True
    assert verifier["script"] == "verify.py"


def test_custom_script_verifier_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")

    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(workspace=str(tmp_path), policy=_policy("../verify.py"))
    )

    verifier = result.gate_patch["user_verifiers"][0]
    assert verifier["ok"] is False
    assert "workspace-relative" in verifier["detail"]


def test_custom_script_verifier_reports_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    script = tmp_path / "verify.py"
    script.write_text("import sys\nprint('bad')\nsys.exit(3)\n", encoding="utf-8")

    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(workspace=str(tmp_path), policy=_policy("verify.py"))
    )

    verifier = result.gate_patch["user_verifiers"][0]
    assert verifier["ok"] is False
    assert verifier["exit_code"] == 3
    assert "bad" in verifier["stdout_tail"]
    assert sys.executable
