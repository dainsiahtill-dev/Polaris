"""Internal harness component regressions: real negative facts, no model/gates."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.factory_bench import factory_http_client
from scripts.factory_bench._bench_lib import artifacts, chain as bench_chain, cli


@pytest.mark.parametrize("response_kind", ["timeout", "response_loss", "invalid_json", "empty", "missing_id", "list"])
def test_native_factory_post_ambiguous_response_is_unknown_not_predispatch_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, response_kind: str
) -> None:
    """Exercise native POST/parser/chain without a server, provider, or process."""
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    posts: list[str] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            if response_kind == "response_loss":
                raise OSError("response lost after POST accepted")
            return {"invalid_json": b"{broken", "empty": b"", "missing_id": b'{"status":"running"}', "list": b"[]"}[
                response_kind
            ]

    def post(request: Any, **_kwargs: Any) -> Response:
        assert request.get_method() == "POST"
        assert request.full_url == "http://127.0.0.1:60001/v2/factory/runs"
        posts.append(request.full_url)
        if response_kind == "timeout":
            raise TimeoutError("response lost after POST accepted")
        return Response()

    def no_observed_run_operation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no observed Factory identity: no wait, audit fetch or blind cancellation")

    monkeypatch.setattr(factory_http_client.urllib.request, "urlopen", post)
    monkeypatch.setattr(bench_chain, "wait_run_until_terminal", no_observed_run_operation)
    monkeypatch.setattr(bench_chain, "get_audit_bundle", no_observed_run_operation)
    monkeypatch.setattr(bench_chain, "cancel_factory_run", no_observed_run_operation)
    result = bench_chain.run_factory_chain(
        {"id": "L1-01", "level": 1, "title": "Minimal", "brief": "Build a CLI", "checks": []},
        workspace,
        backend_url="http://127.0.0.1:60001",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "chain.log",
    )
    assert posts == ["http://127.0.0.1:60001/v2/factory/runs"]
    assert result["error"] == "start_failed"
    assert result.get("run_id", "") == ""
    if response_kind in {"timeout", "response_loss"}:
        assert result["start_error"]["status"] == 0
        assert result["start_error"]["exception"] == ("TimeoutError" if response_kind == "timeout" else "OSError")
        assert result["start_error"]["reason"] == "response lost after POST accepted"
    if response_kind == "missing_id":
        assert result["start_response"] == {"status": "running"}
    assert artifacts._classify_chain_attempt(result) == {
        "attempt_state": "unknown",
        "observed_run_id": "",
        "terminal": False,
    }


@pytest.mark.parametrize(
    "error",
    [
        "director_resume_run_missing",
        "isolated_instance_start_failed",
        "measurement_contaminated",
        "runtime_project_contamination",
        "workspace_switch_failed",
        "runtime_storage_bootstrap_failed",
    ],
)
def test_definite_predispatch_failures_remain_not_started(error: str) -> None:
    assert artifacts._classify_chain_attempt({"error": error, "exit_code": -1}) == {
        "attempt_state": "not_started",
        "observed_run_id": "",
        "terminal": False,
    }


@pytest.mark.parametrize(
    "chain,started,terminal",
    [
        ({"_runner_exception": "transport interrupted", "exit_code": -1}, False, False),
        ({"error": "transport interrupted", "exit_code": -1}, False, False),
        ({"exit_code": 0}, False, False),
        ({"run_id": 123, "exit_code": 0}, False, False),
        ({"run_id": "factory-real", "_runner_exception": True}, True, False),
        ({"run_id": "factory-real", "error": "event_wait_timeout"}, True, False),
        ({"run_id": "factory-real"}, True, False),
        ({"run_id": "factory-real", "factory_terminal_status": {"status": "running"}}, True, False),
        ({"run_id": "factory-real", "factory_terminal_status": {"status": "completed"}}, True, True),
        ({"run_id": "factory-real", "factory_terminal_status": {"status": "failed"}}, True, True),
        (
            {"run_id": "factory-real", "factory_terminal_status": {"status": "completed", "run_id": "other"}},
            True,
            False,
        ),
    ],
)
def test_attempt_and_terminal_require_observed_backend_facts(
    chain: dict[str, Any], started: bool, terminal: bool
) -> None:
    assert artifacts._chain_attempt_started(chain) is started
    assert artifacts._chain_reached_terminal(chain) is terminal


@pytest.mark.parametrize(
    "error",
    [
        "isolated_instance_start_failed",
        "measurement_contaminated",
        "runtime_project_contamination",
        "director_resume_run_missing",
        "start_failed",
        "workspace_switch_failed",
        "event_wait_timeout",
    ],
)
def test_unobserved_or_never_started_chain_is_not_terminal(error: str) -> None:
    assert artifacts._chain_reached_terminal({"error": error, "exit_code": -1}) is False


@pytest.mark.parametrize("observation", ["unknown", "returned", "callback", "native_timeout", "native_malformed"])
def test_transport_interruption_preserves_only_observed_identity_and_real_negative_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, observation: str
) -> None:
    observed_run_id = "factory-observed-real" if observation in {"returned", "callback"} else ""
    work = tmp_path / "bench"
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "instances"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(work),
            "--launcher-instance-mode",
            "observed",
            "--bench-session-reporting",
            "off",
        ],
    )
    monkeypatch.setattr(
        cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Minimal", "brief": "Build a CLI", "checks": []}],
    )
    monkeypatch.setattr(cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_push_bench_progress_to_backend", lambda **_kwargs: False)
    monkeypatch.setattr(cli, "_push_bench_complete_to_backend", lambda **_kwargs: False)
    monkeypatch.setattr(cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {"backend_freshness": {"ok": False}, "backend_metadata": {"backend_base_url": ""}},
    )
    chain = {"_runner_exception": "transport interrupted", "exit_code": -1}
    if observed_run_id:
        chain["run_id"] = observed_run_id

    def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if observation.startswith("native_"):

            def post(request: Any, **_post_kwargs: Any) -> io.BytesIO:
                assert request.get_method() == "POST"
                assert request.full_url == "http://127.0.0.1:60001/v2/factory/runs"
                if observation == "native_timeout":
                    raise TimeoutError("response lost after POST accepted")
                return io.BytesIO(b"{broken")

            monkeypatch.setattr(factory_http_client.urllib.request, "urlopen", post)
            (_args[1] / ".git").mkdir()
            return bench_chain.run_factory_chain(*_args, **{**_kwargs, "backend_url": "http://127.0.0.1:60001"})
        if observation == "returned":
            return chain
        if observation == "callback":
            _kwargs["on_stage_change"]("running", {"run_id": observed_run_id, "status": "running", "phase": "pm"})
        raise RuntimeError("transport interrupted")

    monkeypatch.setattr(cli, "run_factory_chain", interrupted)
    qa_ids: list[str] = []

    def qa_status(_workspace: Path, run_id: str) -> bool:
        qa_ids.append(run_id)
        return False

    monkeypatch.setattr(cli, "read_factory_qa_invocation_status", qa_status)

    def forbid_verifier(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unknown/nonterminal transport must never run verifier")

    monkeypatch.setattr(cli, "build_real_run_gate", forbid_verifier)
    assert cli.main() == 1
    record = json.loads((work / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert record["factory_run_id"] == observed_run_id
    assert record["chain_attempt_started"] is bool(observed_run_id)
    assert record["chain_attempt_state"] == ("observed" if observed_run_id else "unknown")
    assert record["chain_observed_run_id"] == observed_run_id
    assert record["audit_terminal"] is False
    assert record["real_run_gate"]["skipped"] is True
    assert record["real_run_gate"]["commands"] == []
    assert record["all_checks_passed"] is False
    assert (record["chain"].get("error") or record["chain"].get("_runner_exception")) == (
        "start_failed" if observation.startswith("native_") else "transport interrupted"
    )
    if observation == "native_timeout":
        assert record["chain"]["start_error"]["reason"] == "response lost after POST accepted"
        assert record["chain"]["start_error"]["status"] == 0
    assert qa_ids == ([observed_run_id] if observed_run_id else [])
    rows = [
        json.loads(line) for line in Path(record["run_ledger"]["ledger_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert rows and all(row["gate"]["ok"] is False for row in rows)
    if not observed_run_id:
        assert record["run_ledger_projection"]["event_count"] >= 1


@pytest.mark.parametrize("forbid_verifier,bootstrap_fails", [(True, False), (False, False), (True, True)])
def test_failed_isolated_launch_preserves_real_negative_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    forbid_verifier: bool,
    bootstrap_fails: bool,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "bench"
    monkeypatch.setenv("KERNELONE_HOME", str(home))
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(home / "instances"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(work),
            "--launcher-instance-mode",
            "isolated",
            "--bench-session-reporting",
            "off",
        ],
    )
    monkeypatch.setattr(
        cli,
        "load_projects",
        lambda: [
            {
                "id": "L1-01",
                "level": 1,
                "title": "Minimal",
                "brief": "Build a CLI",
                "checks": [],
            }
        ],
    )
    monkeypatch.setattr(cli, "_resolve_backend_url", lambda: "http://127.0.0.1:60001")
    monkeypatch.setattr(cli, "_resolve_backend_token", lambda: "test-only-token")
    monkeypatch.setattr(cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": False, "detail": "backend never started"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    starts: list[bool] = []

    def start(**_kwargs: Any) -> dict[str, Any]:
        starts.append(True)
        return {"ok": False, "error": "controlled_native_startup_failure", "error_type": "RuntimeError"}

    monkeypatch.setattr(cli, "_start_isolated_bench_project_instance", start)
    if bootstrap_fails:
        from polaris.cells.events.fact_stream import public as fact_stream

        def rejected_bootstrap(_command: object) -> None:
            raise RuntimeError("controlled_runtime_bootstrap_failure")

        monkeypatch.setattr(fact_stream, "bootstrap_fact_stream_workspace", rejected_bootstrap)
    monkeypatch.setattr(
        cli,
        "_non_terminal_chain_diagnostics",
        lambda **_kwargs: {
            "factory_attempt_started": False,
        },
    )
    monkeypatch.setattr(cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(cli, "_push_bench_progress_to_backend", lambda **_kwargs: False)
    monkeypatch.setattr(cli, "_push_bench_complete_to_backend", lambda **_kwargs: False)

    def no_factory(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a failed launch must not call Factory")

    monkeypatch.setattr(cli, "run_factory_chain", no_factory)
    verifier_calls: list[str] = []

    def verifier(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        verifier_calls.append("physical")
        if forbid_verifier:
            raise AssertionError("a never-started Factory must not run physical verifiers")
        return {"ok": False, "summary": "must remain unrun", "commands": []}

    monkeypatch.setattr(cli, "build_real_run_gate", verifier)
    # Deliberately REAL persist_real_run_gate_ledger, projection, bootstrap and
    # filesystem. Stubbing these concealed the physical r08 reporting failure.
    assert cli.main() == 1
    record = json.loads((work / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert record["chain"]["error"] == (
        "runtime_storage_bootstrap_failed" if bootstrap_fails else "isolated_instance_start_failed"
    )
    assert record["launcher_instance"]["error"] == (
        "runtime_storage_bootstrap_failed" if bootstrap_fails else "controlled_native_startup_failure"
    )
    assert record["chain_attempt_started"] is False
    assert record["audit_terminal"] is False
    assert record["factory_run_id"] == ""
    assert record["qa_invoked"]["invoked"] is False
    assert record["all_checks_passed"] is False
    assert record["real_run_gate"]["skipped"] is True
    assert record["real_run_gate"]["commands"] == []
    assert verifier_calls == []
    if bootstrap_fails:
        assert starts == []
        assert record["run_ledger"]["available"] is False
        assert record["run_ledger_projection"]["available"] is False
        assert record["runtime_workspace_bootstrap"]["error_type"] == "RuntimeError"
        return
    workspace = Path(record["workspace"])
    facts = list((workspace / ".polaris" / "runtime" / "events").glob("*.jsonl"))
    assert facts, "negative evidence must have a real canonical fact"
    ledger_path = Path(record["run_ledger"]["ledger_path"])
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert rows and all(row["gate"]["ok"] is False for row in rows)
