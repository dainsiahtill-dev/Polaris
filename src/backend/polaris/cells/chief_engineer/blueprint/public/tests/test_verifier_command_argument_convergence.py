"""Verifier argv data uses one existing control-plane codec across owners."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import VerificationCommandAuthorityV1
from polaris.cells.control_plane.verifier_policy import public as policy

# Exact original R27 PM operand (129 UTF-8 bytes), preserved as audit fixture.
R27_ARG = "const s=require('./examples/garden-fullmoon.json');if(!s.fireflies||!s.flowers||!s.moon||s.humidity===undefined){process.exit(1)}"


def _query(argv: Any, workspace: str = ".", *, command_hash: str = "b" * 64) -> Any:
    return policy.EvaluateVerifierCommandPolicyQueryV1(
        workspace=workspace,
        project_id="project",
        run_id="run",
        task_id="TASK-3",
        completion_contract_hash="a" * 64,
        verifier_obligation_id="verify",
        command_authority_hash=command_hash,
        modality="entrypoint",
        argv=argv,
        cwd=".",
        input_obligation_ids=("fixture",),
    )


def test_actual_r27_opaque_argument_derives_same_ce_authority_and_public_policy(tmp_path: Path) -> None:
    argv = ("node", "-e", R27_ARG)
    assert len(R27_ARG.encode("utf-8")) == 129
    assert _query(argv).argv == argv
    ce = VerificationCommandAuthorityV1(task_id="TASK-3", modality="entrypoint", argv=argv, cwd=".")
    assert ce.argv == argv
    assert tuple(shlex.split(ce.command)) == argv
    expected = {
        "domain": "polaris.project_completion_verification_command_authority.v1",
        "task_id": "TASK-3",
        "modality": "entrypoint",
        "argv": list(argv),
        "cwd": ".",
    }
    assert (
        ce.authority_hash
        == hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )
    query = _query(ce.argv, str(tmp_path), command_hash=ce.authority_hash)
    decision = policy.evaluate_verifier_command_policy(query)
    assert decision.normalized_argv == argv and decision.normalized_cwd == "."
    assert decision.authorized is False
    assert decision.error_code == "untrusted_verifier_command"


@pytest.mark.parametrize("value", ["x" * 2048, "字" * 682 + "ab", "a  b'\"c"])
def test_shared_codec_preserves_exact_argument_bytes_and_current_query_boundary(value: str) -> None:
    argv = ["node", "-e", value]
    normalized = policy.normalize_verifier_command_argv(argv)
    assert normalized == tuple(argv)
    assert normalized[2].encode("utf-8") == value.encode("utf-8")
    assert _query(argv).argv == normalized


@pytest.mark.parametrize(
    "argv,error",
    [
        ([], ValueError),
        (("node", ""), ValueError),
        (("node", " a"), ValueError),
        (("node", "a "), ValueError),
        (("node", "a\x00b"), ValueError),
        (("node", "a\nb"), ValueError),
        (("node", "a\tb"), ValueError),
        (("node", "字" * 683), ValueError),
        (("node", "x" * 2049), ValueError),
        (("node", 1), TypeError),
        (("node", False), TypeError),
        (("node", None), TypeError),
        (("node", {}), TypeError),
        (("node",) * 129, ValueError),
        ("node", ValueError),
    ],
)
def test_shared_codec_and_policy_query_reject_same_invalid_payload(argv: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        policy.normalize_verifier_command_argv(argv)
    with pytest.raises(error):
        _query(argv)


def test_argument_count_boundary_and_identity_bound_are_not_widened() -> None:
    assert len(policy.normalize_verifier_command_argv(["node"] * 128)) == 128
    with pytest.raises(ValueError, match=r"task_id.*128"):
        VerificationCommandAuthorityV1(task_id="t" * 129, modality="entrypoint", argv=("node", "app.mjs"))
