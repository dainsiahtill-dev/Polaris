"""Physical command diagnostics remain available across the public receipt boundary."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as owner
from polaris.cells.runtime.execution_broker.public.project_verification import (
    query_project_verification_receipt,
    run_project_verification,
)
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import (
    _AuthorityPort,
    _bind_runner,
    _Runner,
    _verification_command,
    _verification_query,
)


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", _AuthorityPort())


def test_failed_public_receipt_carries_exact_physical_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(tmp_path, monkeypatch)
    diagnostic = "src/main.py:4: AssertionError: 实际测试失败\n"
    output = diagnostic.encode("utf-8")
    _bind_runner(monkeypatch, _Runner(exit_code=1, output=output))
    command = _verification_command(tmp_path)
    receipt = run_project_verification(command).receipt
    assert receipt is not None
    assert getattr(receipt, "output_excerpt", None) == output.decode("utf-8")
    assert receipt.output_hash == hashlib.sha256(output).hexdigest()
    assert not receipt.succeeded
    queried = query_project_verification_receipt(_verification_query(command))
    assert queried == receipt
    with pytest.raises(ValueError, match="owner-sealed"):
        replace(receipt, output_excerpt="fabricated passing output")


def test_public_feedback_is_bounded_and_keeps_last_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source(tmp_path, monkeypatch)
    output = ("编译输出\n" * 3000 + "src/main.py:99: missing required export\n").encode("utf-8")
    _bind_runner(monkeypatch, _Runner(exit_code=2, output=output))
    receipt = run_project_verification(_verification_command(tmp_path)).receipt
    assert receipt is not None
    excerpt = getattr(receipt, "output_excerpt", None)
    assert isinstance(excerpt, str)
    assert len(excerpt) <= 8192
    assert excerpt.endswith("src/main.py:99: missing required export\n")
    assert receipt.output_hash == hashlib.sha256(output).hexdigest()
    assert not receipt.succeeded


def test_reused_receipt_keeps_feedback_without_a_second_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(tmp_path, monkeypatch)
    runner = _Runner(output=b"1 passed in 0.01s\n")
    _bind_runner(monkeypatch, runner)
    command = _verification_command(tmp_path)
    original = run_project_verification(command)
    repeated = run_project_verification(command)
    assert original.receipt is not None
    assert getattr(original.receipt, "output_excerpt", None) == "1 passed in 0.01s\n"
    assert repeated.receipt == original.receipt
    assert repeated.spawned is False
    assert runner.calls == 1


def test_receipt_rehydration_accepts_legacy_body_without_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(tmp_path, monkeypatch)
    _bind_runner(monkeypatch, _Runner(exit_code=1, output=b"legacy failure\n"))
    receipt = run_project_verification(_verification_command(tmp_path)).receipt
    assert receipt is not None
    payload = asdict(receipt)
    for field in ("receipt_hash", "receipt_ref", "output_excerpt"):
        payload.pop(field)
    payload["schema_version"] = "runtime.execution_broker.project_verification_receipt.v1"
    payload["argv"] = list(receipt.argv)
    payload["input_artifacts"] = [asdict(item) for item in receipt.input_artifacts]
    rehydrated = owner._build_verification_receipt(payload, owner._hash_payload(payload))
    assert rehydrated.output_excerpt == ""
    assert rehydrated.output_hash == receipt.output_hash
    assert rehydrated.exit_code == 1
    assert not rehydrated.succeeded
