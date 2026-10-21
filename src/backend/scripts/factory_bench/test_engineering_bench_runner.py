"""Synthetic adapter tests; never provider, process, or product delivery evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.delivery.cli import engineering_acceptance as acceptance
from scripts.factory_bench._bench_lib import chain, cli


def _configs(tmp_path: Path) -> tuple[Path, Path]:
    host = tmp_path / "host.json"
    host.write_text(
        json.dumps(
            {
                "permission_profiles": {"frozen": {"enabled_tools": []}},
                "default_permission_profile_id": "frozen",
                "require_qa": True,
                "run_timeout_seconds": 5400,
            }
        ),
        encoding="utf-8",
    )
    llm = tmp_path / "llm.json"
    llm.write_text('{"synthetic":true}', encoding="utf-8")
    return host, llm


def _inputs(tmp_path: Path, **options: Any) -> acceptance.LaunchInputs:
    host, llm = _configs(tmp_path)
    project = chain.load_projects()[0]
    return chain.prepare_engineering_case(
        project,
        workspace=tmp_path / "target",
        evidence_dir=tmp_path / "evidence",
        catalog_file=Path(chain._FIXTURE),
        host_config_file=host,
        llm_config_file=llm,
        instance_id="synthetic-case",
        **options,
    )


class SyntheticSupervisor:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.stops: list[str] = []
        self.record: dict[str, Any] = {}

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        self.starts.append(request)
        self.record = {
            **request,
            "backend_port": 50123,
            "frontend_port": 53123,
            "backend_url": "http://127.0.0.1:50123",
            "frontend_url": "http://127.0.0.1:53123",
            "token": "synthetic-secret",
            "backend_alive": True,
            "frontend_alive": True,
            "status": "running",
            "backend_pid": 900001,
            "frontend_pid": 900002,
        }
        return dict(self.record)

    def health(self, instance_id: str) -> dict[str, Any]:
        assert instance_id == self.record["instance_id"]
        return dict(self.record)

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        self.stops.append(instance_id)
        self.record.update(status="stopped", backend_alive=False, frontend_alive=False)
        return dict(self.record)


class SyntheticHTTP:
    def __init__(self, workspace: Path, *, unknown: bool = False, running: bool = False) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.unknown, self.running = unknown, running
        self.snapshot: dict[str, Any] = {
            "run_id": "owned-run",
            "session_id": "owned-run",
            "workspace": str(workspace),
            "engine_version": "engineering_v2",
            "workflow": "direct",
            "role": "director",
            "status": "running" if running else "completed",
            "required_checks": [],
            "verification": "not_run",
            "require_qa": True,
            "qa_verdict": "not_run",
            "project_outcome": "not_accepted",
            "events": [],
            "last_seq": 0,
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, payload))
        if path == "/v2/engineering/runs" and self.unknown:
            raise TimeoutError("synthetic unknown POST")
        if path == "/v2/engineering/runs" and payload is not None:
            self.snapshot["workflow"] = payload["workflow"]
        if path.endswith("/cancel"):
            self.snapshot.update(status="cancelled")
        return dict(self.snapshot)

    def close(self) -> None:
        pass


def _run(
    inputs: acceptance.LaunchInputs, http: SyntheticHTTP, supervisor: SyntheticSupervisor, **options: Any
) -> dict[str, Any]:
    return chain.run_engineering_case(
        inputs,
        supervisor=supervisor,
        http_factory=lambda _: http,
        activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _: False,
        **options,
    )


def test_all120_original_goals_and_codes_preserved_without_target_writes(tmp_path: Path) -> None:
    host, llm = _configs(tmp_path)
    catalog = Path(chain._FIXTURE)
    original = catalog.read_bytes()
    projects = chain.load_projects()
    assert len(projects) == 120 and {p["level"] for p in projects} == set(range(1, 13))
    for project in projects:
        inputs = chain.prepare_engineering_case(
            project,
            workspace=tmp_path / "targets" / project["id"],
            evidence_dir=tmp_path / "evidence" / project["id"],
            catalog_file=catalog,
            host_config_file=host,
            llm_config_file=llm,
            instance_id="synthetic-" + project["id"].lower(),
        )
        plan = acceptance.build_launch_plan(inputs)
        assert inputs.requirements_file.read_text(encoding="utf-8") == chain.build_requirements_doc(project)
        assert project["brief"] in plan["instruction"]
        assert all(check in plan["instruction"] for check in project["checks"])
        assert plan["project"] == project and plan["required_checks"] == []
        assert plan["catalog_total"] == 120
        assert not inputs.workspace.exists()
    assert catalog.read_bytes() == original


@pytest.mark.parametrize("workflow", ["direct", "collaborative"])
def test_shared_adapter_exact_formal_api_and_workflow(tmp_path: Path, workflow: str) -> None:
    inputs = _inputs(tmp_path, workflow=workflow)
    supervisor, http = SyntheticSupervisor(), SyntheticHTTP(inputs.workspace)
    report = _run(inputs, http, supervisor)
    posts = [call for call in http.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "/v2/engineering/runs"
    assert posts[0][2]["workflow"] == workflow
    assert posts[0][2]["required_checks"] == []
    assert report["observation"]["harness_status"] == "UNKNOWN"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert report["exit_code"] != 0
    assert supervisor.stops == [inputs.instance_id]
    assert not inputs.workspace.exists()
    assert "synthetic-secret" not in json.dumps(report)


def test_default_direct_and_unknown_post_never_replayed(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    assert inputs.workflow == "direct"
    supervisor, http = SyntheticSupervisor(), SyntheticHTTP(inputs.workspace, unknown=True)
    clock = iter([1000.0, 7001.0, 7002.0, 7003.0])
    report = _run(inputs, http, supervisor, now=lambda: next(clock), sleep=lambda _: None)
    assert report["launch"]["harness_status"] == "UNKNOWN"
    assert len([c for c in http.calls if c[:2] == ("POST", "/v2/engineering/runs")]) == 1
    assert not any(path.endswith("/cancel") for _, path, _ in http.calls)
    assert report["exit_code"] != 0


def test_timeout_cancel_exact_owned_run_observe_then_stop(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    supervisor, http = SyntheticSupervisor(), SyntheticHTTP(inputs.workspace, running=True)
    clock = iter([1000.0, 7001.0, 7001.0, 7002.0])
    report = _run(inputs, http, supervisor, now=lambda: next(clock), sleep=lambda _: None)
    mutations = [(m, p) for m, p, _ in http.calls if m == "POST"]
    assert mutations == [("POST", "/v2/engineering/runs"), ("POST", "/v2/engineering/runs/owned-run/cancel")]
    assert report["observation"]["harness_status"] == "TIMEOUT"
    assert report["final_observation"]["platform"]["status"] == "cancelled"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert report["exit_code"] == 124


@pytest.mark.parametrize("name", ["run_factory_chain", "run_chain"])
def test_retired_old_writers_fail_before_effects(tmp_path: Path, name: str) -> None:
    with pytest.raises(RuntimeError, match="retired"):
        getattr(chain, name)({}, tmp_path / "target", timeout_s=5400, log_path=tmp_path / "old.log")
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    "flag",
    [
        "--use-legacy-chain",
        "--director-dispatch-driver",
        "--start-from",
        "--director-workflow-execution-mode",
        "--real-run-timeout",
        "--launcher-supervisor-url",
    ],
)
def test_cli_retired_flags_fail_explicitly(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([flag, "unused"]) == 2
    assert "retired" in capsys.readouterr().err


def test_cli_dry_run_all120_keeps_targets_absent(tmp_path: Path) -> None:
    assert cli.main(["--dry-run", "--work-dir", str(tmp_path / "batch")]) == 0
    report = json.loads(next((tmp_path / "batch" / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert report["selected_count"] == 120 and len(report["records"]) == 120
    assert report["internal_test_only"] is True and report["delivery_proven"] is False
    assert not (tmp_path / "batch" / "workspaces").exists()


def test_explicit_checks_and_alias_keep_original_goal(tmp_path: Path) -> None:
    host, llm = _configs(tmp_path)
    selected, missing, aliases = chain._resolve_explicit_project_selection(chain.load_projects(), ["L2-01"])
    assert not missing and aliases == {"L2-01": "L2-11"}
    checks = tmp_path / "checks.json"
    checks.write_text('["rtk proxy npm run build", "rtk proxy npm test"]', encoding="utf-8")
    inputs = chain.prepare_engineering_case(
        selected[0],
        workspace=tmp_path / "target",
        evidence_dir=tmp_path / "evidence",
        catalog_file=Path(chain._FIXTURE),
        host_config_file=host,
        llm_config_file=llm,
        instance_id="synthetic-alias",
        checks_file=checks,
    )
    plan = acceptance.build_launch_plan(inputs)
    assert plan["project"]["id"] == "L2-11"
    assert plan["required_checks"] == json.loads(checks.read_text(encoding="utf-8"))
    assert plan["instruction"].endswith(chain.build_requirements_doc(chain.load_projects()[10]))


def test_unknown_observation_exception_still_records_and_closes_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _inputs(tmp_path)
    supervisor, http = SyntheticSupervisor(), SyntheticHTTP(inputs.workspace)

    def fail_observation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic observer boundary failure")

    monkeypatch.setattr(acceptance, "observe", fail_observation)
    report = _run(inputs, http, supervisor)
    assert report["observation"]["harness_status"] == "UNKNOWN"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert report["exit_code"] != 0 and supervisor.stops == [inputs.instance_id]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--project-ids", "L1-01,NO-SUCH"],
        ["--levels", "13"],
        ["--timeout", "10"],
        ["--overall-timeout", "100"],
        ["--project-ids", "L2-01,L2-11"],
    ],
)
def test_cli_bad_selection_or_budget_has_no_effects(tmp_path: Path, arguments: list[str]) -> None:
    base = tmp_path / "batch"
    assert cli.main(["--dry-run", "--work-dir", str(base), *arguments]) == 2
    assert not base.exists()


def test_cli_requires_host_authority_before_target_allocation(tmp_path: Path) -> None:
    assert cli.main(["--case", "L1-01", "--work-dir", str(tmp_path / "batch")]) == 2
    assert not (tmp_path / "batch").exists()


def test_batch_config_drift_stops_without_second_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host, llm = _configs(tmp_path)
    calls: list[str] = []

    def run(inputs: acceptance.LaunchInputs) -> dict[str, Any]:
        calls.append(inputs.project_id)
        report = _run(inputs, SyntheticHTTP(inputs.workspace), SyntheticSupervisor())
        llm.write_text('{"changed":true}', encoding="utf-8")
        return report

    monkeypatch.setattr(cli, "run_engineering_case", run)
    assert (
        cli.main(
            [
                "--levels",
                "1",
                "--limit",
                "2",
                "--work-dir",
                str(tmp_path / "batch"),
                "--host-config-file",
                str(host),
                "--llm-config-file",
                str(llm),
            ]
        )
        == 2
    )
    assert calls == ["L1-01"]
    summary = json.loads(next((tmp_path / "batch" / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert len(summary["records"]) == 1 and summary["batch_error"]["harness_status"] == "UNKNOWN"


def test_open_resource_fence_stops_batch_even_with_max_failed_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, llm = _configs(tmp_path)
    calls: list[str] = []

    def run(inputs: acceptance.LaunchInputs) -> dict[str, Any]:
        calls.append(inputs.project_id)
        return chain.run_engineering_case(
            inputs,
            supervisor=SyntheticSupervisor(),
            http_factory=lambda _: SyntheticHTTP(inputs.workspace),
            activity_reader=lambda _: {"status": "unreadable"},
        )

    monkeypatch.setattr(cli, "run_engineering_case", run)
    assert (
        cli.main(
            [
                "--levels",
                "1",
                "--limit",
                "2",
                "--work-dir",
                str(tmp_path / "batch"),
                "--host-config-file",
                str(host),
                "--llm-config-file",
                str(llm),
            ]
        )
        == 1
    )
    assert calls == ["L1-01"]
    summary = json.loads(next((tmp_path / "batch" / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["records"][0]["cleanup"]["harness_status"] == "UNKNOWN"
    assert summary["records"][0]["resources_closed"] is False


def test_active_runner_never_calls_retired_host_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host, llm = _configs(tmp_path)

    def run(inputs: acceptance.LaunchInputs) -> dict[str, Any]:
        return _run(inputs, SyntheticHTTP(inputs.workspace), SyntheticSupervisor())

    monkeypatch.setattr(cli, "run_engineering_case", run)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("retired host helper invoked")

    for name in (
        "run_factory_chain",
        "run_chain",
        "configure_bench_backend",
        "build_real_run_gate",
        "persist_real_run_gate_ledger",
        "_push_bench_session_to_backend",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    assert (
        cli.main(
            [
                "--limit",
                "1",
                "--work-dir",
                str(tmp_path / "batch"),
                "--host-config-file",
                str(host),
                "--llm-config-file",
                str(llm),
            ]
        )
        == 1
    )
    assert not (tmp_path / "batch" / "workspaces").exists()


def test_compatibility_wrapper_exposes_current_adapter() -> None:
    from scripts.factory_bench import run_factory_bench

    assert run_factory_bench.run_engineering_case is chain.run_engineering_case
    assert run_factory_bench.prepare_engineering_case is chain.prepare_engineering_case


def test_observed_platform_acceptance_keeps_separate_cleanup_and_quality(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    class SyntheticArtifactSupervisor(SyntheticSupervisor):
        """Explicit private fixture creation; not authorized-tool delivery proof."""

        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            result = super().start_instance(request)
            source = Path(request["workspace"]) / "src" / "synthetic.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('synthetic')\n", encoding="utf-8")
            return result

    http = SyntheticHTTP(inputs.workspace)
    http.snapshot.update(
        verification="passed",
        qa_verdict="approved",
        project_outcome="accepted",
        acceptance_basis="independent_qa",
        verification_commands=["rtk proxy python -m pytest"],
        checks=[{"command": "rtk proxy python -m pytest", "ok": True, "exit_code": 0, "interrupted": False}],
    )
    report = _run(inputs, http, SyntheticArtifactSupervisor())
    assert report["observation"]["harness_status"] == "OBSERVED_ACCEPTED"
    assert report["observation"]["platform"]["project_outcome"] == "accepted"
    assert report["cleanup"]["harness_status"] == "STOPPED" and report["exit_code"] == 0
    assert report["delivery_proven"] is False
    assert report["catalog_quality"] == "UNKNOWN"


def test_rejected_host_configuration_retains_external_attempt_report(tmp_path: Path) -> None:
    host, llm = _configs(tmp_path)
    host.write_text('{"require_qa":false}', encoding="utf-8")
    assert (
        cli.main(
            [
                "--case",
                "L1-01",
                "--work-dir",
                str(tmp_path / "batch"),
                "--host-config-file",
                str(host),
                "--llm-config-file",
                str(llm),
            ]
        )
        == 2
    )
    report = json.loads(next((tmp_path / "batch" / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert report["batch_error"]["harness_status"] == "UNKNOWN"
    assert report["records"][0]["project_id"] == "L1-01"
    assert report["records"][0]["error_type"] == "ValueError"
    assert not (tmp_path / "batch" / "workspaces").exists()


def test_unknown_create_ack_recovers_same_running_run_to_acceptance(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    class ArtifactSupervisor(SyntheticSupervisor):
        """Explicit private fixture; never authorized-tool product delivery proof."""

        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            instance = super().start_instance(request)
            source = Path(request["workspace"]) / "src" / "synthetic.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('synthetic')\n", encoding="utf-8")
            return instance

    class LostCreateHTTP(SyntheticHTTP):
        def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            if method == "POST" and path == "/v2/engineering/runs":
                assert payload is not None
                self.calls.append((method, path, payload))
                self.snapshot["request_identity"] = {
                    "request_key": payload["request_key"],
                    "instruction_sha256": hashlib.sha256(payload["instruction"].encode("utf-8")).hexdigest(),
                    "workflow": payload["workflow"],
                    "required_checks": payload["required_checks"],
                    "permission_profile_id": payload["permission_profile_id"],
                }
                raise TimeoutError("synthetic response lost after owner committed create")
            return super().request(method, path, payload)

    supervisor, http = ArtifactSupervisor(), LostCreateHTTP(inputs.workspace, running=True)
    sleeps: list[float] = []

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        http.snapshot.update(
            status="completed",
            verification="passed",
            qa_verdict="approved",
            project_outcome="accepted",
            acceptance_basis="independent_qa",
            verification_commands=["rtk proxy python -m pytest"],
            checks=[{"command": "rtk proxy python -m pytest", "ok": True, "exit_code": 0, "interrupted": False}],
        )

    clock = iter([1000.0, 1001.0, 1001.0, 1002.0, 1002.0])
    report = _run(inputs, http, supervisor, now=lambda: next(clock), sleep=advance)
    assert report["launch"]["harness_status"] == "UNKNOWN"
    assert report["observation"]["harness_status"] == "OBSERVED_ACCEPTED"
    assert report["cleanup"]["harness_status"] == "STOPPED" and report["exit_code"] == 0
    assert report["catalog_quality"] == "UNKNOWN" and report["delivery_proven"] is False
    assert len(supervisor.starts) == 1 and supervisor.stops == [inputs.instance_id]
    assert sleeps == [1]
    assert len([call for call in http.calls if call[0] == "POST"]) == 1
    assert any(path.startswith("/v2/engineering/requests/") for _, path, _ in http.calls)
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["run_id"] == "owned-run" and binding["request_recovered"] is True
    assert binding["started_at_epoch"] == 1000.0 and binding["overall_deadline_epoch"] == 7000.0


def test_unknown_prepost_instance_return_uses_same_synthetic_owner_without_http(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    class LostInstanceSupervisor(SyntheticSupervisor):
        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            super().start_instance(request)
            raise TimeoutError("synthetic supervisor return lost before formal POST")

    supervisor, http = LostInstanceSupervisor(), SyntheticHTTP(inputs.workspace)
    factories: list[dict[str, Any]] = []

    def factory(binding: dict[str, Any]) -> SyntheticHTTP:
        factories.append(binding)
        return http

    def unexpected_sleep(_seconds: float) -> None:
        raise AssertionError("no formal request exists to wait for")

    report = chain.run_engineering_case(
        inputs,
        supervisor=supervisor,
        http_factory=factory,
        now=lambda: 1000.0,
        sleep=unexpected_sleep,
        activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _: False,
    )
    assert report["launch"]["harness_status"] == "UNKNOWN"
    assert report["observation"]["harness_status"] == "UNKNOWN"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert not factories and not http.calls
    assert len(supervisor.starts) == 1 and supervisor.stops == [inputs.instance_id]
    assert report["exit_code"] != 0
