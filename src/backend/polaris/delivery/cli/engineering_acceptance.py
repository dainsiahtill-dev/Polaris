"""Internal test-only launch/observation adapter; never a delivery fact owner.

This command does not edit targets, install dependencies, execute target gates,
initialize Git, or manufacture receipts/QA verdicts. Root operators run real
instances only after reviewing and freezing the inputs and source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from polaris.kernelone.fs.text_ops import write_text_atomic

PROJECT_BUDGET_SECONDS = 5400
OVERALL_BUDGET_SECONDS = 6000
_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_PROTECTED_PORTS = frozenset({49977, 5173, 59977})
_HOST_KEYS = frozenset(
    {
        "permission_profiles",
        "default_permission_profile_id",
        "require_qa",
        "run_timeout_seconds",
        "verification_output_roots",
        "max_output_bytes",
        "max_tool_output_bytes",
    }
)
_NOTICE = """Use the current engineering_v2 collaboration loop for the frozen requirements below.
Preserve every original business goal, numeric depth threshold, real build/test,
dependency preparation and executable-entry requirement verbatim. Historical
references to PM task-contract JSON, CE blueprint records, target_files, hashes,
covers/obligation graphs and Factory audit records describe obsolete bookkeeping,
not a model output protocol or authority. Use ordinary planning/design and real
native tools; current platform verification and independent QA retain all actual
product obligations. Do not manufacture old contracts, receipts or ledger facts.

--- Frozen original requirements (verbatim) ---
"""


@dataclass(frozen=True)
class LaunchInputs:
    polaris_root: Path
    workspace: Path
    evidence_dir: Path
    catalog_file: Path
    catalog_sha256: str
    project_id: str
    requirements_file: Path
    requirements_sha256: str
    checks_file: Path
    checks_sha256: str
    host_config_file: Path
    host_config_sha256: str
    llm_config_file: Path
    llm_config_sha256: str
    instance_id: str
    registry_home: Path | None = None
    permission_profile_id: str | None = None
    timeout: int = PROJECT_BUDGET_SECONDS


class SupervisorPort(Protocol):
    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def health(self, instance_id: str) -> dict[str, Any]: ...
    def stop_instance(self, instance_id: str) -> dict[str, Any]: ...


class HTTPPort(Protocol):
    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


HTTPFactory = Callable[[dict[str, Any]], HTTPPort]


class _BoundHTTP:
    def __init__(self, binding: dict[str, Any]) -> None:
        import httpx

        instance = binding["instance"]
        self._client = httpx.Client(
            base_url=instance["backend_url"],
            headers={"Authorization": f"Bearer {instance['token']}"},
            timeout=30,
            trust_env=False,
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.request(method, path, json=payload)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("backend response is not an object")
        return value

    def close(self) -> None:
        self._client.close()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _write(path: Path, value: Any) -> None:
    write_text_atomic(str(path), _json(value))
    path.chmod(0o600)


def _frozen(path: Path, expected: str, label: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"{label} expected hash must be 64 lowercase hex")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"{label} hash changed")
    return content


def build_launch_plan(inputs: LaunchInputs) -> dict[str, Any]:
    if inputs.timeout != PROJECT_BUDGET_SECONDS:
        raise ValueError("acceptance requires the original 5400-second project budget")
    workspace, source, evidence = (
        inputs.workspace.resolve(),
        inputs.polaris_root.resolve(),
        inputs.evidence_dir.resolve(),
    )
    if workspace == source or workspace.is_relative_to(source):
        raise ValueError("acceptance target must be outside the Polaris source tree")
    if evidence == workspace or evidence.is_relative_to(workspace):
        raise ValueError("evidence must remain outside the target")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("fresh target workspace must be absent or empty; no target cleanup is performed")
    # This ASCII subset is unchanged by Supervisor's canonical normalization.
    if (
        inputs.instance_id == "main"
        or len(inputs.instance_id) > 80
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", inputs.instance_id)
    ):
        raise ValueError("a fresh canonical non-main instance ID of at most 80 characters is required")
    for path in (
        inputs.catalog_file,
        inputs.requirements_file,
        inputs.checks_file,
        inputs.host_config_file,
        inputs.llm_config_file,
    ):
        if path.resolve().is_relative_to(workspace):
            raise ValueError("frozen inputs must not be seeded inside the target")
    catalog = json.loads(_frozen(inputs.catalog_file, inputs.catalog_sha256, "catalog").decode("utf-8"))
    if (
        not isinstance(catalog, dict)
        or catalog.get("schema_version") != "factory-bench/2"
        or not isinstance(catalog.get("projects"), list)
    ):
        raise ValueError("the original v2 catalog is required")
    projects = [row for row in catalog["projects"] if isinstance(row, dict) and row.get("id") == inputs.project_id]
    if len(projects) != 1:
        raise ValueError("project ID must identify exactly one original catalog case")
    requirements = _frozen(inputs.requirements_file, inputs.requirements_sha256, "requirements").decode("utf-8")
    checks = json.loads(_frozen(inputs.checks_file, inputs.checks_sha256, "checks").decode("utf-8"))
    if (
        not requirements.strip()
        or not isinstance(checks, list)
        or not checks
        or any(not isinstance(check, str) or not check.strip() for check in checks)
    ):
        raise ValueError("original requirements and nonempty unchanged required commands are mandatory")
    host = json.loads(_frozen(inputs.host_config_file, inputs.host_config_sha256, "host config").decode("utf-8"))
    if not isinstance(host, dict) or set(host) - _HOST_KEYS:
        raise ValueError("host config has unsupported fields")
    if host.get("require_qa") is not True or host.get("run_timeout_seconds") != PROJECT_BUDGET_SECONDS:
        raise ValueError("host must require independent QA and the original 5400-second project budget")
    profiles = host.get("permission_profiles")
    selected = inputs.permission_profile_id or host.get("default_permission_profile_id")
    if (
        not isinstance(selected, str)
        or not selected
        or not isinstance(profiles, dict)
        or not isinstance(profiles.get(selected), dict)
    ):
        raise ValueError("an exact host-configured permission profile is required")
    _frozen(inputs.llm_config_file, inputs.llm_config_sha256, "shared LLM config")
    frozen = {
        label: {"path": str(path.resolve()), "sha256": digest}
        for label, path, digest in (
            ("catalog", inputs.catalog_file, inputs.catalog_sha256),
            ("requirements", inputs.requirements_file, inputs.requirements_sha256),
            ("checks", inputs.checks_file, inputs.checks_sha256),
            ("host_config", inputs.host_config_file, inputs.host_config_sha256),
            ("llm_config", inputs.llm_config_file, inputs.llm_config_sha256),
        )
    }
    return {
        "internal_test_only": True,
        "project": projects[0],
        "catalog_total": len(catalog["projects"]),
        "frozen_inputs": frozen,
        "workspace": str(workspace),
        "polaris_root": str(source),
        "evidence_dir": str(evidence),
        "instance_id": inputs.instance_id,
        "registry_home": str(inputs.registry_home.resolve()) if inputs.registry_home else None,
        "permission_profile_id": selected,
        "host_require_qa": True,
        "project_budget_seconds": PROJECT_BUDGET_SECONDS,
        "overall_budget_seconds": OVERALL_BUDGET_SECONDS,
        "instruction": _NOTICE + requirements,
        "required_checks": checks,
    }


@contextmanager
def _startup_environment(plan: dict[str, Any]) -> Iterator[None]:
    values = {
        "KERNELONE_ENGINEERING_HOST_CONFIG": plan["frozen_inputs"]["host_config"]["path"],
        "KERNELONE_LLM_CONFIG": plan["frozen_inputs"]["llm_config"]["path"],
    }
    # Snapshot only engineering overrides; unrelated environment values and
    # credentials stay outside this temporary configuration scope.
    inherited_overrides = {key: value for key, value in os.environ.items() if key.startswith("KERNELONE_ENGINEERING_")}
    previous = {
        **inherited_overrides,
        "KERNELONE_ENGINEERING_HOST_CONFIG": os.environ.get("KERNELONE_ENGINEERING_HOST_CONFIG"),
        "KERNELONE_LLM_CONFIG": os.environ.get("KERNELONE_LLM_CONFIG"),
    }
    if plan["registry_home"]:
        values["KERNELONE_INSTANCE_HOME"] = plan["registry_home"]
        previous["KERNELONE_INSTANCE_HOME"] = os.environ.get("KERNELONE_INSTANCE_HOME")
    try:
        # The frozen single host file must not be shadowed by unrelated small
        # overrides inherited from a different CLI/backend environment.
        for key in inherited_overrides:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ.update({key: value})


def _supervisor(home: str | None) -> SupervisorPort:
    from polaris.cells.instances.public.service import get_instance_supervisor

    return get_instance_supervisor(Path(home) if home else None)


def _validate_instance(instance: Mapping[str, Any], binding: dict[str, Any]) -> None:
    plan = binding["plan"]
    metadata = instance.get("metadata")
    if (
        instance.get("instance_id") != plan["instance_id"]
        or instance.get("workspace") != plan["workspace"]
        or instance.get("runtime_root") != str(Path(plan["workspace"]) / ".polaris" / "runtime")
        or instance.get("kind") != "bench_project"
        or not isinstance(metadata, dict)
        or metadata.get("internal_test_only") is not True
        or metadata.get("acceptance_owner") != binding["owner_id"]
    ):
        raise ValueError("instance scope/ownership differs from the recorded acceptance launch")
    for key, port_key in (("backend_url", "backend_port"), ("frontend_url", "frontend_port")):
        value = urlparse(str(instance.get(key) or ""))
        if (
            value.scheme != "http"
            or value.hostname not in {"127.0.0.1", "localhost", "::1"}
            or value.port != instance.get(port_key)
            or value.port in _PROTECTED_PORTS
        ):
            raise ValueError("instance endpoint is missing, foreign or reserved")
    if not isinstance(instance.get("token"), str) or not instance["token"]:
        raise ValueError("explicit instance token is required")


def _read_binding(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "engineering.acceptance_binding.v1"
        or value.get("internal_test_only") is not True
    ):
        raise ValueError("not an internal acceptance binding")
    _validate_instance(value["instance"], value)
    return value


def _public_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: instance.get(key)
        for key in (
            "instance_id",
            "workspace",
            "runtime_root",
            "kind",
            "backend_url",
            "frontend_url",
            "backend_port",
            "frontend_port",
            "backend_pid",
            "frontend_pid",
            "backend_alive",
            "frontend_alive",
            "status",
        )
    }


def _record(binding: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    directory = Path(binding["plan"]["evidence_dir"])
    report.update(
        internal_test_only=True,
        binding_file=str(directory / "binding.json"),
        project_id=binding["plan"]["project"]["id"],
        instance=_public_instance(binding["instance"]),
    )
    _write(directory / "operations" / f"{time.time_ns()}-{uuid4().hex[:8]}.json", report)
    _write(directory / "report.json", report)
    return report


def launch(
    inputs: LaunchInputs,
    *,
    supervisor: SupervisorPort | None = None,
    http_factory: HTTPFactory = _BoundHTTP,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    plan = build_launch_plan(inputs)
    directory = Path(plan["evidence_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # This exclusive audit claim prevents accidental launch replay after an
    # indeterminate POST. It never grants model/tool execution authority.
    descriptor = os.open(directory / "launch.claim", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    started = now()
    binding: dict[str, Any] = {
        "schema_version": "engineering.acceptance_binding.v1",
        "internal_test_only": True,
        "owner_id": uuid4().hex,
        "plan": plan,
        "started_at_epoch": started,
        "overall_deadline_epoch": started + OVERALL_BUDGET_SECONDS,
        "request_key": uuid4().hex,
        "run_id": None,
        "phase": "before_instance",
        "instance": {},
    }
    path = directory / "binding.json"
    _write(path, binding)
    instance_request = {
        "instance_id": plan["instance_id"],
        "name": f"Engineering acceptance {plan['project']['id']}",
        "kind": "bench_project",
        "polaris_root": plan["polaris_root"],
        "workspace": plan["workspace"],
        "runtime_root": str(Path(plan["workspace"]) / ".polaris" / "runtime"),
        "backend_port": None,
        "frontend_port": None,
        "backend_reload": False,
        "frontend_vite": True,
        "start_frontend": True,
        "require_fresh_instance": True,
        "metadata": {
            "internal_test_only": True,
            "backend_binding": "isolated_backend_instance",
            "acceptance_owner": binding["owner_id"],
            "project_id": plan["project"]["id"],
        },
    }
    try:
        owner = supervisor or _supervisor(plan["registry_home"])
        with _startup_environment(plan):
            binding["instance"] = owner.start_instance(instance_request)
        _validate_instance(binding["instance"], binding)
        binding["phase"] = "instance_started"
        _write(path, binding)
        payload = {
            "instruction": plan["instruction"],
            "workspace": plan["workspace"],
            "workflow": "collaborative",
            "role": "director",
            "permission_profile_id": plan["permission_profile_id"],
            "required_checks": plan["required_checks"],
            "request_key": binding["request_key"],
        }
        _write(directory / "formal-request.json", payload)
        binding["phase"] = "request_in_flight"
        _write(path, binding)
        client = http_factory(binding)
        try:
            reply = client.request("POST", "/v2/engineering/runs", payload)
        finally:
            client.close()
        _validate_snapshot(reply, binding, require_run=False)
        binding["run_id"] = reply["run_id"]
        binding["phase"] = "run_created"
        instance = binding["instance"]
        binding["launch_url"] = (
            instance["frontend_url"].rstrip("/")
            + "/?"
            + urlencode(
                {
                    "instance": instance["instance_id"],
                    "backend": instance["backend_url"],
                    "token": instance["token"],
                    "workspace": plan["workspace"],
                    "engine": "engineering_v2",
                    "engineering_run": binding["run_id"],
                }
            )
        )
        _write(path, binding)
        _write(directory / "launch-response.json", reply)
        return _record(
            binding,
            {
                "harness_status": "LAUNCHED",
                "exit_code": 0,
                "run_id": binding["run_id"],
                "acceptance": "NOT_EVALUATED",
                "project_budget_seconds": PROJECT_BUDGET_SECONDS,
                "overall_budget_seconds": OVERALL_BUDGET_SECONDS,
            },
        )
    except Exception as error:  # noqa: BLE001 - preserve unknown boundary outcomes without replay.
        uncertain = binding["phase"] == "request_in_flight"
        binding["phase"] = "request_unknown" if uncertain else "launch_failed"
        _write(path, binding)
        return _record(
            binding,
            {
                "harness_status": "UNKNOWN" if uncertain else "FAILED",
                "exit_code": 2 if uncertain else 1,
                "error_type": type(error).__name__,
                "request_replayed": False,
                "instance_retained": True,
            },
        )


def _validate_snapshot(snapshot: Mapping[str, Any], binding: dict[str, Any], *, require_run: bool = True) -> None:
    identity = snapshot.get("run_id")
    if (
        snapshot.get("engine_version") != "engineering_v2"
        or snapshot.get("workspace") != binding["plan"]["workspace"]
        or not isinstance(identity, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", identity)
    ):
        raise ValueError("backend snapshot engine/workspace/run identity mismatch")
    if snapshot.get("session_id") != identity or (require_run and identity != binding.get("run_id")):
        raise ValueError("backend snapshot is not the bound engineering run")


def _measurements(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    attempts: dict[str, dict[str, Any]] = {}
    refs: set[str] = set()
    events = snapshot.get("events")
    events = events if isinstance(events, list) else []
    identity_gaps = 0
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("kind") != "llm_request_observed"
            or not isinstance(event.get("payload"), dict)
        ):
            continue
        payload = event["payload"]
        identity = payload.get("provider_request_id")
        if isinstance(identity, str) and identity:
            attempts[identity] = payload
        else:
            # capture_id is port-scoped and cannot replace the per-dispatch ID:
            # doing so would collapse explicit transport retries.
            identity_gaps += 1
        ref = payload.get("context_snapshot_ref")
        if isinstance(ref, str) and re.fullmatch(r"[0-9a-f]{24}", ref):
            refs.add(ref)
    subtotals: dict[str, dict[str, int]] = {"reported": {}, "estimated": {}}
    unknown = 0
    for item in attempts.values():
        status, usage = item.get("usage_status"), item.get("usage")
        if item.get("phase") != "terminal" or status not in subtotals or not isinstance(usage, dict):
            unknown += 1
            continue
        for key, value in usage.items():
            if key.endswith("tokens") and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                subtotals[status][key] = subtotals[status].get(key, 0) + value
    sequences = [event.get("seq") for event in events if isinstance(event, dict)]
    complete = sequences == list(range(1, len(events) + 1)) and snapshot.get("last_seq") == len(events)
    return {
        "observed_dispatch_attempts": None if identity_gaps else len(attempts),
        "known_observed_dispatch_attempts": len(attempts),
        "attempt_identity_status": "unknown" if identity_gaps else "known_observed",
        "unidentified_observation_events": identity_gaps,
        "event_coverage_status": "complete" if complete else "unknown",
        "observed_attempts": list(attempts.values()),
        "materialized_snapshot_refs": sorted(refs),
        "physical_http_sends": None,
        "physical_http_sends_status": "unknown",
        "usage_known_subtotal": subtotals,
        "unknown_usage_attempts": None if identity_gaps else unknown,
        "bill": None,
        "bill_status": "unknown",
        "human_interventions": None,
        "human_interventions_status": "unknown",
        "harness_target_edits": 0,
        "harness_installs": 0,
        "harness_gate_commands": 0,
        "harness_git_initializations": 0,
    }


def _inventory(workspace: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if not workspace.is_dir():
        return {"status": "unavailable", "files": []}
    for directory, names, leaves in os.walk(workspace, followlinks=False):
        names[:] = [
            name
            for name in names
            if name not in {".polaris", ".git", "node_modules"} and not (Path(directory) / name).is_symlink()
        ]
        for name in sorted(leaves):
            path = Path(directory) / name
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                continue
            content = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(workspace)),
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    return {"status": "read_only_observation", "files": files, "is_platform_verdict": False}


def _platform_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: snapshot.get(key)
        for key in (
            "run_id",
            "engine_version",
            "workspace",
            "status",
            "active_roles",
            "verification",
            "require_qa",
            "qa_verdict",
            "project_outcome",
            "acceptance_basis",
            "required_checks",
            "checks",
            "last_seq",
        )
    }


def _observed_result(binding: dict[str, Any], snapshot: dict[str, Any], client: HTTPPort, now: float) -> dict[str, Any]:
    _validate_snapshot(snapshot, binding)
    directory = Path(binding["plan"]["evidence_dir"])
    _write(directory / "snapshots" / f"{snapshot.get('last_seq', 'unknown')}-{time.time_ns()}.json", snapshot)
    metrics = _measurements(snapshot)
    context_reads: list[dict[str, Any]] = []
    for ref in metrics["materialized_snapshot_refs"]:
        destination = directory / "contexts" / f"{ref}.json"
        if destination.is_file():
            continue
        try:
            payload = client.request(
                "GET", f"/v2/context/{ref}/final-request?" + urlencode({"workspace": binding["plan"]["workspace"]})
            )
            _write(destination, payload)
            context_reads.append(
                {
                    "context_snapshot_ref": ref,
                    "status": "available",
                    "physical_capture_status": payload.get("physical_capture_status"),
                }
            )
        except Exception as error:  # noqa: BLE001 - preserve unknown boundary outcomes without replay.
            context_reads.append(
                {"context_snapshot_ref": ref, "status": "unavailable", "error_type": type(error).__name__}
            )
    check_results = snapshot.get("checks")
    checks_present = (
        isinstance(check_results, list)
        and len(check_results) == len(binding["plan"]["required_checks"])
        and all(
            isinstance(item, dict)
            and item.get("ok") is True
            and item.get("exit_code") == 0
            and item.get("interrupted") is False
            for item in check_results
        )
    )
    inventory = _inventory(Path(binding["plan"]["workspace"])) if snapshot.get("status") in _TERMINAL else None
    artifact_present = bool(inventory and inventory.get("status") == "read_only_observation" and inventory.get("files"))
    coherent = (
        snapshot.get("project_outcome") == "accepted"
        and snapshot.get("require_qa") is True
        and snapshot.get("qa_verdict") == "approved"
        and snapshot.get("acceptance_basis") == "independent_qa"
        and snapshot.get("verification") == "passed"
        and snapshot.get("required_checks") == binding["plan"]["required_checks"]
        and snapshot.get("status") == "completed"
        and checks_present
        and artifact_present
    )
    status = snapshot.get("status")
    harness_status, exit_code = (
        ("OBSERVED_ACCEPTED", 0)
        if coherent
        else (
            ("RUNNING", 0)
            if status not in _TERMINAL
            else (
                ("CANCELLED", 130)
                if status == "cancelled"
                else (
                    ("REJECTED", 1)
                    if snapshot.get("project_outcome") == "rejected" or snapshot.get("verification") == "failed"
                    else ("UNKNOWN", 2)
                )
            )
        )
    )
    elapsed = now - binding["started_at_epoch"]
    report: dict[str, Any] = {
        "harness_status": harness_status,
        "exit_code": exit_code,
        "platform": _platform_fields(snapshot),
        "measurements": metrics,
        "context_reads": context_reads,
        "elapsed_seconds": elapsed if elapsed >= 0 else None,
        "project_budget_seconds": PROJECT_BUDGET_SECONDS,
        "overall_budget_seconds": OVERALL_BUDGET_SECONDS,
        "acceptance_authority": "platform project_outcome and current QA/checks; no harness verdict",
    }
    if status in _TERMINAL:
        report["artifact_inventory"] = inventory
        report["declared_check_results_present"] = checks_present
        report["evidence_consistency"] = "consistent" if coherent else "unknown_or_not_accepted"
        # No acceptance or QA value is written back or substituted. An accepted
        # owner projection without its declared result/artifact evidence remains
        # an observation gap for the independent acceptance report.
    return report


def observe(
    binding_file: Path,
    *,
    wait: bool = False,
    http_factory: HTTPFactory = _BoundHTTP,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    binding = _read_binding(binding_file)
    if not binding.get("run_id"):
        return _record(
            binding,
            {
                "harness_status": "UNKNOWN",
                "exit_code": 2,
                "reason": "launch response unknown; no automatic POST replay",
            },
        )
    client = http_factory(binding)
    try:
        while True:
            try:
                snapshot = client.request(
                    "GET",
                    f"/v2/engineering/runs/{binding['run_id']}?"
                    + urlencode({"workspace": binding["plan"]["workspace"]}),
                )
                report = _observed_result(binding, snapshot, client, now())
            except Exception as error:  # noqa: BLE001 - preserve unknown boundary outcomes without replay.
                report = {"harness_status": "UNKNOWN", "exit_code": 2, "error_type": type(error).__name__}
            if wait and now() >= binding["overall_deadline_epoch"]:
                report.update(harness_status="TIMEOUT", exit_code=124, implicit_cancel=False)
                return _record(binding, report)
            if (
                not wait
                or report["harness_status"] in {"OBSERVED_ACCEPTED", "REJECTED", "CANCELLED"}
                or report.get("platform", {}).get("status") in _TERMINAL
            ):
                return _record(binding, report)
            sleep(1)
    finally:
        client.close()


def cancel(binding_file: Path, *, http_factory: HTTPFactory = _BoundHTTP) -> dict[str, Any]:
    binding = _read_binding(binding_file)
    if not binding.get("run_id"):
        return _record(
            binding, {"harness_status": "UNKNOWN", "exit_code": 2, "reason": "no known run; mutation is not replayed"}
        )
    client = http_factory(binding)
    try:
        snapshot = client.request(
            "POST", f"/v2/engineering/runs/{binding['run_id']}/cancel", {"workspace": binding["plan"]["workspace"]}
        )
        report = _observed_result(binding, snapshot, client, time.time())
        report["operation"] = "cancel"
        return _record(binding, report)
    except Exception as error:  # noqa: BLE001 - preserve unknown boundary outcomes without replay.
        return _record(
            binding,
            {"harness_status": "UNKNOWN", "exit_code": 2, "error_type": type(error).__name__, "cancel_replayed": False},
        )
    finally:
        client.close()


def _read_activity(workspace: Path) -> dict[str, Any]:
    database = workspace / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3"
    try:
        # Read-only canonical SQL is an independent internal operational check,
        # never a second execution store or a source of acceptance verdicts.
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            active = [
                dict(row)
                for row in connection.execute(
                    "SELECT session_id,status FROM runs WHERE status IN ('pending','running')"
                )
            ]
            started = [
                dict(row)
                for row in connection.execute(
                    "SELECT session_id,turn_seq,call_id,tool,status FROM tool_calls WHERE status='started'"
                )
            ]
            reconciled = {
                (row["session_id"], payload["turn_seq"], payload["call_id"])
                for row in connection.execute("SELECT session_id,payload FROM events WHERE kind='tool_reconciled'")
                if isinstance((payload := json.loads(row["payload"])), dict)
                and isinstance(payload.get("turn_seq"), int)
                and isinstance(payload.get("call_id"), str)
            }
        pending = [row for row in started if (row["session_id"], row["turn_seq"], row["call_id"]) not in reconciled]
        retained = [
            {**row, "original_effect": "unknown", "continuation_basis": "canonical_tool_reconciled_event"}
            for row in started
            if (row["session_id"], row["turn_seq"], row["call_id"]) in reconciled
        ]
        return {
            "status": "readable",
            "active_runs": active,
            "pending_tool_calls": pending,
            "reconciled_tool_calls": retained,
        }
    except (OSError, ValueError, sqlite3.Error) as error:
        return {"status": "unknown", "error_type": type(error).__name__}


def _process_alive(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def stop(
    binding_file: Path,
    *,
    supervisor: SupervisorPort | None = None,
    http_factory: HTTPFactory = _BoundHTTP,
    activity_reader: Callable[[Path], dict[str, Any]] = _read_activity,
    process_probe: Callable[[int], bool | None] = _process_alive,
) -> dict[str, Any]:
    binding = _read_binding(binding_file)
    client = http_factory(binding)
    owner = supervisor or _supervisor(binding["plan"]["registry_home"])
    try:
        snapshot = client.request(
            "GET", f"/v2/engineering/runs/{binding['run_id']}?" + urlencode({"workspace": binding["plan"]["workspace"]})
        )
        _validate_snapshot(snapshot, binding)
        activity = activity_reader(Path(binding["plan"]["workspace"]))
        if (
            snapshot.get("status") not in _TERMINAL
            or activity.get("status") != "readable"
            or activity.get("active_runs")
            or activity.get("pending_tool_calls")
        ):
            return _record(
                binding,
                {
                    "harness_status": "UNKNOWN",
                    "exit_code": 2,
                    "platform": _platform_fields(snapshot),
                    "resources": {"stop_performed": False, "canonical_activity": activity},
                },
            )
        current = owner.health(binding["plan"]["instance_id"])
        _validate_instance(current, binding)
        owner.stop_instance(binding["plan"]["instance_id"])
        after = owner.health(binding["plan"]["instance_id"])
        _validate_instance(after, binding)
        pids = [
            pid for key in ("backend_pid", "frontend_pid") if isinstance((pid := current.get(key)), int) and pid > 0
        ]
        process_states = {str(pid): process_probe(pid) for pid in pids}
        released = (
            after.get("status") == "stopped"
            and after.get("backend_alive") is False
            and after.get("frontend_alive") is False
            and all(value is False for value in process_states.values())
        )
        return _record(
            binding,
            {
                "harness_status": "STOPPED" if released else "UNKNOWN",
                "exit_code": 0 if released else 2,
                "platform": _platform_fields(snapshot),
                "resources": {
                    "stop_performed": True,
                    "instance_after": _public_instance(after),
                    "original_process_alive": process_states,
                    "canonical_activity": activity,
                },
                "acceptance_unchanged_by_cleanup": True,
            },
        )
    except Exception as error:  # noqa: BLE001 - preserve unknown boundary outcomes without replay.
        return _record(
            binding,
            {"harness_status": "UNKNOWN", "exit_code": 2, "error_type": type(error).__name__, "stop_replayed": False},
        )
    finally:
        client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Internal test-only engineering acceptance adapter; no target repair or local gate execution"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "launch"):
        command = commands.add_parser(action)
        command.add_argument("--internal-test", action="store_true")
        for name in (
            "polaris-root",
            "workspace",
            "catalog-file",
            "requirements-file",
            "checks-file",
            "host-config-file",
            "llm-config-file",
        ):
            command.add_argument("--" + name, type=Path, required=True)
        for name in (
            "catalog-sha256",
            "requirements-sha256",
            "checks-sha256",
            "host-config-sha256",
            "llm-config-sha256",
        ):
            command.add_argument("--" + name, required=True)
        command.add_argument("--project-id", "--case", dest="project_id", required=True)
        command.add_argument("--evidence-dir", "--report-dir", dest="evidence_dir", type=Path, required=True)
        command.add_argument("--timeout", type=int, choices=(PROJECT_BUDGET_SECONDS,), required=True)
        command.add_argument("--instance-id", default=None)
        command.add_argument("--registry-home", type=Path, default=None)
        command.add_argument("--permission-profile-id", default=None)
    for action in ("observe", "cancel", "stop"):
        command = commands.add_parser(action)
        command.add_argument("--internal-test", action="store_true")
        command.add_argument("--binding-file", type=Path, required=True)
        if action == "observe":
            command.add_argument("--wait", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if not args.internal_test:
            parser.error("--internal-test is required; this is not a production project API")
    except SystemExit as error:
        return int(error.code or 0)
    result: dict[str, Any]
    try:
        with redirect_stdout(sys.stderr):
            if args.action in {"plan", "launch"}:
                fields = vars(args).copy()
                fields.pop("action")
                fields.pop("internal_test")
                fields["instance_id"] = fields["instance_id"] or "engineering-acceptance-" + uuid4().hex[:12]
                inputs = LaunchInputs(**fields)
                if args.action == "plan":
                    plan = build_launch_plan(inputs)
                    plan.pop("instruction")
                    result = {"harness_status": "PLAN_ONLY", "exit_code": 0, "plan": plan, "execution": "NOT_STARTED"}
                else:
                    result = launch(inputs)
            elif args.action == "observe":
                result = observe(args.binding_file, wait=args.wait)
            elif args.action == "cancel":
                result = cancel(args.binding_file)
            else:
                result = stop(args.binding_file)
    except (OSError, ValueError, KeyError) as error:
        result = {
            "harness_status": "UNKNOWN",
            "exit_code": 2,
            "error_type": type(error).__name__,
            "operation": args.action,
        }
    print(_json(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
