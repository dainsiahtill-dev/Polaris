"""Tests for QA consumer step verification safety gates."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from polaris.cells.qa.audit_verdict.internal import qa_consumer


def _consumer(tmp_path: Path) -> qa_consumer.QAConsumer:
    consumer = qa_consumer.QAConsumer.__new__(qa_consumer.QAConsumer)
    consumer._workspace = str(tmp_path)
    return consumer


def test_run_step_verify_rejects_unsafe_without_execution(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path)
    payload = {"construction_step": {"verify": "rm -rf ."}}

    with (
        patch.object(qa_consumer.subprocess, "run") as run_mock,
        patch.object(qa_consumer, "_first_failing_verify_clause") as clause_mock,
    ):
        message = consumer._run_step_verify(payload)

    assert "step verify command rejected by safety policy" in message
    assert "blocked_command:rm" in message
    assert "'rm -rf .'" in message
    run_mock.assert_not_called()
    clause_mock.assert_not_called()


def test_run_step_verify_safe_command_still_runs(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path)
    payload = {"construction_step": {"verify": "test -f app.py"}}
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(qa_consumer.subprocess, "run", return_value=completed) as run_mock:
        message = consumer._run_step_verify(payload)

    assert message == ""
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["shell"] is True
    assert run_mock.call_args.kwargs["cwd"] == str(tmp_path)
    assert run_mock.call_args.args == ("test -f app.py",)
