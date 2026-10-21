"""Internal harness component regressions: real negative facts, no model/gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from polaris.delivery.cli import engineering_acceptance as acceptance
from scripts.factory_bench._bench_lib import artifacts, chain as bench_chain, cli
from scripts.factory_bench.test_bench_to_backend_integration import _consumer_inputs, _ConsumerSupervisor


def _formal_native_http(
    response_kind: str, calls: list[tuple[str, str]], *, observed: bool = False, recover: bool = False
) -> Any:
    """Actual BoundHTTP/httpx encode/decode with finite private transport only."""

    def factory(binding: dict[str, Any]) -> Any:
        snapshot = {
            "run_id": "observed-engineering-run",
            "session_id": "observed-engineering-run",
            "workspace": binding["plan"]["workspace"],
            "engine_version": "engineering_v2",
            "workflow": binding["plan"]["workflow"],
            "role": "director",
            "status": "interrupted",
            "verification": "unknown",
            "require_qa": True,
            "qa_verdict": "not_run",
            "project_outcome": "unknown",
            "required_checks": [],
            "events": [
                {
                    "seq": 1,
                    "kind": "tool_observed",
                    "role": "director",
                    "payload": {"ok": False, "error": "observed tool transport interruption"},
                }
            ],
            "last_seq": 1,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append((request.method, path))
            if request.method == "POST":
                assert path == "/v2/engineering/runs"
                payload = json.loads(request.content.decode("utf-8"))
                assert payload["role"] == "director" and payload["workflow"] == "direct"
                if observed:
                    return httpx.Response(200, json=snapshot)
                if response_kind == "timeout":
                    raise httpx.ReadTimeout("response lost after POST accepted", request=request)
                if response_kind == "response_loss":
                    raise httpx.ReadError("response lost after POST accepted", request=request)
                raw = {"invalid_json": b"{broken", "empty": b"", "missing_id": b'{"status":"running"}', "list": b"[]"}[
                    response_kind
                ]
                return httpx.Response(200, content=raw)
            assert request.method == "GET"
            if path.startswith("/v2/engineering/requests/"):
                if not recover:
                    return httpx.Response(404, json={"detail": "lookup unavailable; create effect remains unknown"})
                identity = {
                    "request_key": binding["request_key"],
                    "instruction_sha256": hashlib.sha256(binding["plan"]["instruction"].encode("utf-8")).hexdigest(),
                    "workflow": binding["plan"]["workflow"],
                    "required_checks": [],
                    "permission_profile_id": binding["plan"]["permission_profile_id"],
                }
                return httpx.Response(200, json={**snapshot, "request_identity": identity})
            assert path == "/v2/engineering/runs/observed-engineering-run"
            return httpx.Response(200, json=snapshot)

        port = acceptance._BoundHTTP(binding)
        port._client.close()
        port._client = httpx.Client(base_url=binding["instance"]["backend_url"], transport=httpx.MockTransport(handler))
        return port

    return factory


def _formal_run(inputs: Any, supervisor: Any, factory: Any, *, expires: bool = False) -> dict[str, Any]:
    clock = iter([1000.0, 7001.0, 7002.0])
    options: dict[str, Any] = {"now": lambda: next(clock)} if expires else {}
    return bench_chain.run_engineering_case(
        inputs,
        supervisor=supervisor,
        http_factory=factory,
        activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _: False,
        sleep=lambda _: None,
        **options,
    )


@pytest.mark.parametrize("response_kind", ["timeout", "response_loss", "invalid_json", "empty", "missing_id", "list"])
def test_native_factory_post_ambiguous_response_is_unknown_not_predispatch_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, response_kind: str
) -> None:
    """Current formal engineering boundary; private synthetic ports, no target execution."""
    inputs = _consumer_inputs(tmp_path)
    supervisor = _ConsumerSupervisor()
    calls: list[tuple[str, str]] = []
    factory = _formal_native_http(response_kind, calls)
    result = _formal_run(inputs, supervisor, factory, expires=True)
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert result["launch"]["harness_status"] == "UNKNOWN"
    assert binding["phase"] == "request_unknown" and binding["request_attempted"] is True
    assert binding["run_id"] is None
    assert [p for m, p in calls if m == "POST"] == ["/v2/engineering/runs"]
    assert any(p.startswith("/v2/engineering/requests/") for m, p in calls if m == "GET")
    assert all("/v2/factory/" not in p and not p.endswith("/cancel") for _, p in calls)
    assert result["cleanup"]["harness_status"] == "UNKNOWN" and supervisor.stops == []
    assert result["delivery_proven"] is False and result["catalog_quality"] == "UNKNOWN"
    assert not inputs.workspace.exists()
    operations = [
        json.loads(p.read_text(encoding="utf-8")) for p in (inputs.evidence_dir / "operations").glob("*.json")
    ]
    assert any(p["harness_status"] == "UNKNOWN" and p.get("request_replayed") is False for p in operations)
    if response_kind == "timeout":
        assert result["launch"]["error_type"] == "ReadTimeout"
    elif response_kind == "response_loss":
        assert result["launch"]["error_type"] == "ReadError"


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
    """Current formal engineering boundary; private synthetic ports, no target execution."""
    inputs = _consumer_inputs(tmp_path)
    supervisor = _ConsumerSupervisor()
    calls: list[tuple[str, str]] = []
    kind = (
        "timeout"
        if observation == "native_timeout"
        else "invalid_json"
        if observation == "native_malformed"
        else "response_loss"
    )
    factory = _formal_native_http(kind, calls, observed=observation == "returned", recover=observation == "callback")
    result = _formal_run(inputs, supervisor, factory, expires=observation not in {"returned", "callback"})
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    known = observation in {"returned", "callback"}
    assert binding["run_id"] == ("observed-engineering-run" if known else None)
    assert [p for m, p in calls if m == "POST"] == ["/v2/engineering/runs"]
    assert all("/v2/factory/" not in p and "audit-bundle" not in p and not p.endswith("/cancel") for _, p in calls)
    assert result["exit_code"] != 0 and result["delivery_proven"] is False
    assert result["catalog_quality"] == "UNKNOWN"
    assert result["resources_closed"] is known
    assert supervisor.stops == ([inputs.instance_id] if known else [])
    if observation == "returned":
        assert result["observation"]["platform"]["status"] == "interrupted"
        assert result["observation"]["platform"]["verification"] == "unknown"
        snapshots = [
            json.loads(p.read_text(encoding="utf-8")) for p in (inputs.evidence_dir / "snapshots").glob("*.json")
        ]
        assert any(s["events"][0]["payload"]["ok"] is False for s in snapshots)
    elif observation == "callback":
        assert result["launch"]["harness_status"] == "UNKNOWN"
        assert binding["request_recovered"] is True
        assert result["cleanup"]["platform"]["status"] == "interrupted"
    else:
        assert result["launch"]["harness_status"] == "UNKNOWN"
        assert result["cleanup"]["harness_status"] == "UNKNOWN"
    assert not inputs.workspace.exists()
    assert not (inputs.workspace / ".polaris" / "runtime").exists()


@pytest.mark.parametrize("forbid_verifier,bootstrap_fails", [(True, False), (False, False), (True, True)])
def test_failed_isolated_launch_preserves_real_negative_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    forbid_verifier: bool,
    bootstrap_fails: bool,
) -> None:
    """Current formal engineering boundary; private synthetic ports, no target execution."""
    seed = _consumer_inputs(tmp_path / "inputs")
    work = tmp_path / "batch"
    supervisors: list[_ConsumerSupervisor] = []
    http_calls: list[tuple[str, str]] = []
    legacy_verifiers: list[str] = []

    def legacy_gate(*args: Any, **kwargs: Any) -> Any:
        legacy_verifiers.append("called")
        if forbid_verifier:
            raise AssertionError("never-started engineering must not execute host project gates")
        return {"ok": False}

    monkeypatch.setattr(cli, "build_real_run_gate", legacy_gate)
    if bootstrap_fails:
        # Old runtime bootstrap is retired. Its current equivalent is a rejected
        # host authority declaration, before any instance or engineering request.
        seed.host_config_file.write_text('{"require_qa":false,"run_timeout_seconds":5400}', encoding="utf-8")

    class LostInstanceStart(_ConsumerSupervisor):
        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            super().start_instance(request)
            raise RuntimeError("controlled_native_startup_response_loss")

    def execute(inputs: Any) -> dict[str, Any]:
        owner = LostInstanceStart()
        supervisors.append(owner)
        return _formal_run(inputs, owner, _formal_native_http("timeout", http_calls))

    monkeypatch.setattr(cli, "run_engineering_case", execute)
    code = cli.main(
        [
            "--case",
            "L1-01",
            "--work-dir",
            str(work),
            "--host-config-file",
            str(seed.host_config_file),
            "--llm-config-file",
            str(seed.llm_config_file),
        ]
    )
    summary = json.loads(next((work / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    record = summary["records"][0]
    assert code != 0 and summary["delivery_proven"] is False
    assert summary["catalog_quality"] == "UNKNOWN" and legacy_verifiers == []
    assert http_calls == []
    assert not Path(record["workspace"]).exists()
    assert not (work / "factory_audits.json").exists()
    if bootstrap_fails:
        assert code == 2 and supervisors[0].starts == []
        assert record["harness_status"] == "UNKNOWN" and record["error_type"] == "ValueError"
        assert record["resources_closed"] is False
    else:
        binding = json.loads((Path(record["launch"]["binding_file"])).read_text(encoding="utf-8"))
        assert record["launch"]["harness_status"] == "UNKNOWN"
        assert record["launch"]["error_type"] == "RuntimeError"
        assert binding["failed_phase"] == "instance_start_in_flight"
        assert binding["request_attempted"] is False and binding["run_id"] is None
        assert binding["instance_recovered"] is True
        assert record["cleanup"]["harness_status"] == "STOPPED"
        assert record["resources_closed"] is True and supervisors[0].stops == [binding["plan"]["instance_id"]]
