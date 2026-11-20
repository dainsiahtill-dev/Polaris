"""The internal comparison consumes a predeclared project pair exactly once."""

import hashlib
import json

import pytest
from scripts.factory_bench._bench_lib import cli
from scripts.factory_bench._bench_lib.comparison import artifact_digest, evidence_fingerprint, load_plan


def write_json(path, data):
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(root, n=12, treatment=None):
    ids = [f"P-{i}" for i in range(n)]
    arms = {
        a: {
            "batch_id": digit * 32,
            "source_fingerprint": digit * 16,
            "policy_sha256": "e" * 64,
            "host_config_sha256": "c" * 64,
        }
        for a, digit in (("baseline", "1"), ("candidate", "2"))
    }
    plan = {
        "schema_version": "engineering.paired_plan.v1",
        "development": ["DEV"],
        "calibration": ["CAL"],
        "holdout": ids,
        "reviewer": "external team",
        "protocol_sha256": "a" * 64,
        "arms": arms,
        "frozen_inputs": {"catalog": "b" * 64, "host_config": "c" * 64, "llm_config": "d" * 64},
        "workflow": "direct",
        "permission_profile_id": None,
        "confidence": 0.90,
        "max_release_error": 0.4,
        "noninferiority_margin": 0.4,
        "max_cost_usd": 10.0,
    }
    if treatment:
        plan["host_treatment"] = treatment
        plan["frozen_inputs"].pop("host_config")
        for arm, digit in zip(("baseline", "candidate"), ("1", "2"), strict=True):
            plan["arms"][arm].update(
                source_fingerprint="f" * 16,
                policy_sha256=digit * 64,
                host_config_sha256=digit * 64,
                common_host_policy_sha256="4" * 64,
            )
            if treatment == "routing_record":
                plan["arms"][arm]["routing_record_sha256"] = None if arm == "baseline" else "a" * 64
    plan_sha = write_json(root / "plan.json", plan)
    evidence = root / "evidence"
    evidence.mkdir()
    for arm, setup in arms.items():
        rows = []
        (evidence / setup["batch_id"]).mkdir()
        for pid in ids:
            run_id = arm + pid
            inventory = {
                "status": "read_only_observation",
                "files": [{"path": "main.py", "bytes": 1, "sha256": "3" * 64}],
            }
            rows.append(
                {
                    "resources_closed": True,
                    "project_id": pid,
                    "observation": {
                        "harness_status": "OBSERVED_ACCEPTED",
                        "exit_code": 0,
                        "declared_check_results_present": True,
                        "evidence_consistency": "consistent",
                        "platform": {
                            "run_id": run_id,
                            "status": "completed",
                            "project_outcome": "accepted",
                            "require_qa": True,
                            "qa_verdict": "approved",
                            "acceptance_basis": "independent_qa",
                            "verification": "passed",
                            "required_checks": [],
                            "verification_commands": ["pytest"],
                            "checks": [
                                {"ok": True, "call_id": "v1", "exit_code": 0, "interrupted": False, "timed_out": False}
                            ],
                        },
                        "artifact_inventory": inventory,
                        "elapsed_seconds": 2.0,
                    },
                }
            )
            dest = evidence / setup["batch_id"] / pid
            dest.mkdir()
            identity = {"run_id": run_id, "project_id": pid, "batch_id": setup["batch_id"], "plan_sha256": plan_sha}
            write_json(
                dest / "review.json",
                {
                    **identity,
                    "kind": "external_full_contract_review",
                    "reviewer": "external team",
                    "protocol_sha256": "a" * 64,
                    "artifact_sha256": artifact_digest(inventory),
                    "verdict": "PASS",
                },
            )
            write_json(
                dest / "bill.json",
                {
                    **identity,
                    "kind": "settled_billing",
                    "all_attempts": True,
                    "currency": "USD",
                    "amount": 2.0 if arm == "baseline" else 1.0,
                },
            )
        summary = {
            "batch_id": setup["batch_id"],
            "records": rows,
            "selected_project_ids": ids,
            "comparison": {**setup, "arm": arm, "plan_sha256": plan_sha},
            "workflow": "direct",
            "permission_profile_id": None,
            "frozen_inputs": {k: {"sha256": v} for k, v in plan["frozen_inputs"].items()},
            "project_budget_seconds": 5400,
            "overall_budget_seconds": 6000,
        }
        summary["frozen_inputs"]["host_config"] = {"sha256": setup["host_config_sha256"]}
        write_json(root / (arm + ".json"), summary)
    return plan, ids


def compare(root, output):
    return cli.main(
        [
            "compare",
            "--plan",
            str(root / "plan.json"),
            "--baseline",
            str(root / "baseline.json"),
            "--candidate",
            str(root / "candidate.json"),
            "--evidence-dir",
            str(root / "evidence"),
            "--evidence-sha256",
            evidence_fingerprint(root / "evidence"),
            "--output",
            str(output),
        ]
    )


def test_normal_cli_qualifies_only_complete_bound_holdout(tmp_path):
    plan, ids = inputs(tmp_path)
    out = tmp_path / "result.json"
    assert compare(tmp_path, out) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["qualification"] == "PASS"
    assert result["candidate"]["unit_cost_per_independent_success_usd"] == 1.0
    assert result["baseline"]["unit_cost_per_independent_success_usd"] == 2.0
    assert 0 < result["candidate"]["release_error_upper"] < plan["max_release_error"]
    assert result["paired_success_delta_lower"] < 0
    review = tmp_path / "evidence" / plan["arms"]["candidate"]["batch_id"] / ids[0] / "review.json"
    stale = json.loads(review.read_text(encoding="utf-8"))
    stale["artifact_sha256"] = "f" * 64
    write_json(review, stale)
    second = tmp_path / "stale.json"
    assert compare(tmp_path, second) != 0
    assert json.loads(second.read_text(encoding="utf-8"))["qualification"] == "UNKNOWN"


def test_development_and_holdout_must_be_disjoint(tmp_path):
    plan, ids = inputs(tmp_path, 1)
    plan["calibration"] = ids
    write_json(tmp_path / "plan.json", plan)
    with pytest.raises(ValueError, match="disjoint"):
        load_plan(tmp_path / "plan.json")


def test_incomplete_fixed_sample_rejects_qualification(tmp_path):
    inputs(tmp_path)
    summary = tmp_path / "baseline.json"
    report = json.loads(summary.read_text(encoding="utf-8"))
    report["records"].pop()
    write_json(summary, report)
    output = tmp_path / "partial.json"
    assert compare(tmp_path, output) == 2
    assert "fixed sample" in json.loads(output.read_text(encoding="utf-8"))["reason"]


def test_joint_success_declines_with_actual_cost_or_failed_review(tmp_path):
    plan, ids = inputs(tmp_path)
    directory = tmp_path / "evidence" / plan["arms"]["candidate"]["batch_id"]
    for pid in ids:
        bill = directory / pid / "bill.json"
        doc = json.loads(bill.read_text(encoding="utf-8"))
        doc["amount"] = 100.0
        write_json(bill, doc)
    out = tmp_path / "overbudget.json"
    assert compare(tmp_path, out) == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["qualification"] == "FAIL"
    assert result["candidate"]["independent_success_rate"] == 1
    assert result["candidate"]["successful_deliveries"] == 0
    assert result["candidate"]["unit_cost_per_verified_delivery_usd"] is None
    bill.unlink()
    out = tmp_path / "missingbill.json"
    assert compare(tmp_path, out) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["qualification"] == "UNKNOWN"


def test_runner_binds_plan_before_first_case_and_keeps_prior_batch(tmp_path, monkeypatch):
    from scripts.factory_bench._bench_lib import comparison

    plan, _ = inputs(tmp_path, 1)
    plan["development"] = ["L1-02"]
    plan["calibration"] = ["L1-03"]
    plan["holdout"] = ["L1-01"]
    frozen = plan["frozen_inputs"]
    for k in ("host_config", "llm_config"):
        frozen[k] = write_json(tmp_path / k, {"test": k})
    frozen["catalog"] = hashlib.sha256(cli.Path(cli._FIXTURE).read_bytes()).hexdigest()
    for arm in plan["arms"].values():
        arm["host_config_sha256"] = frozen["host_config"]
    plan_sha = write_json(tmp_path / "plan.json", plan)
    arm = plan["arms"]["baseline"]
    monkeypatch.setattr(comparison, "arm_identity", lambda *_: {k: v for k, v in arm.items() if k != "batch_id"})
    root = tmp_path / "actual"
    path = root / "reports" / arm["batch_id"] / "summary.json"

    def prepare(*args, **kwargs):
        before = json.loads(path.read_text(encoding="utf-8"))
        assert before["records"] == []
        assert before["comparison"]["plan_sha256"] == plan_sha
        return None

    monkeypatch.setattr(cli, "prepare_engineering_case", prepare)
    monkeypatch.setattr(cli, "run_engineering_case", lambda _: {"exit_code": 1, "resources_closed": True})
    argv = [
        "--case",
        "L1-01",
        "--comparison-arm",
        "baseline",
        "--comparison-plan",
        str(tmp_path / "plan.json"),
        "--host-config-file",
        str(tmp_path / "host_config"),
        "--llm-config-file",
        str(tmp_path / "llm_config"),
        "--work-dir",
        str(root),
    ]
    assert cli.main(argv) == 1
    original = path.read_bytes()
    assert cli.main(argv) == 2
    assert path.read_bytes() == original


def test_arm_identity_only_uses_effective_host_policy(tmp_path, monkeypatch):
    from scripts.factory_bench._bench_lib import comparison

    monkeypatch.setattr(comparison, "compute_source_fingerprint", lambda: "f" * 16)
    host = tmp_path / "host.json"
    write_json(host, {"max_tool_output_bytes": 10, "require_qa": True})
    first = comparison.arm_identity(host, "max_tool_output_bytes")
    monkeypatch.setenv("KERNELONE_ENGINEERING_WRAP_UP_CYCLES", "999999")
    assert comparison.arm_identity(host, "max_tool_output_bytes") == first
    write_json(host, {"max_tool_output_bytes": 20, "require_qa": True})
    second = comparison.arm_identity(host, "max_tool_output_bytes")
    assert second["policy_sha256"] != first["policy_sha256"]
    assert second["common_host_policy_sha256"] == first["common_host_policy_sha256"]
    write_json(host, {"require_qa": True})
    implicit = comparison.arm_identity(host, "max_tool_output_bytes")
    from polaris.bootstrap.config import ServerConfig

    write_json(host, {"require_qa": True, "max_tool_output_bytes": ServerConfig().engineering_max_tool_output_bytes})
    assert comparison.arm_identity(host, "max_tool_output_bytes")["policy_sha256"] == implicit["policy_sha256"]


def test_declared_host_treatment_compares_only_one_effective_policy_field(tmp_path, monkeypatch):
    from scripts.factory_bench._bench_lib import comparison

    plan, _ = inputs(tmp_path, 1)
    plan["host_treatment"] = "max_tool_output_bytes"
    plan["frozen_inputs"].pop("host_config")
    monkeypatch.setattr(comparison, "compute_source_fingerprint", lambda: "f" * 16)
    for arm, value in (("baseline", 10), ("candidate", 20)):
        host = tmp_path / (arm + "-host.json")
        write_json(host, {"max_tool_output_bytes": value, "require_qa": True})
        plan["arms"][arm].update(comparison.arm_identity(host, "max_tool_output_bytes"))
    path = tmp_path / "plan.json"
    write_json(path, plan)
    assert load_plan(path)[0] == plan
    for arm, data in plan["arms"].items():
        report = json.loads((tmp_path / (arm + ".json")).read_text(encoding="utf-8"))
        report["frozen_inputs"]["host_config"]["sha256"] = data["host_config_sha256"]
        comparison.check_inputs(plan, report, arm)
        report["frozen_inputs"]["host_config"]["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="frozen inputs changed"):
            comparison.check_inputs(plan, report, arm)
    plan["arms"]["baseline"]["common_host_policy_sha256"] = "0" * 64
    write_json(path, plan)
    with pytest.raises(ValueError, match="non-treatment"):
        load_plan(path)


@pytest.mark.parametrize("gap", ["unknown_harness", "missing_receipts", "missing_current_qa"])
def test_incoherent_release_counts_risk_but_cannot_count_joint_success(tmp_path, gap):
    inputs(tmp_path)
    path = tmp_path / "candidate.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    for r in report["records"]:
        if gap == "unknown_harness":
            r["observation"]["harness_status"] = "UNKNOWN"
        elif gap == "missing_receipts":
            r["observation"]["platform"]["checks"] = []
        else:
            r["observation"]["platform"]["qa_verdict"] = "unknown"
    write_json(path, report)
    out = tmp_path / "result.json"
    assert compare(tmp_path, out) != 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["candidate"]["released"] == 12
    assert result["candidate"]["successful_deliveries"] == 0


def test_compact_prepared_projection_retains_bounded_routing_facts():
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    data = {
        "activated": True,
        "qualification_record_sha256": "a" * 64,
        "conditions_sha256": "c" * 64,
        "qualified_order": [["p", "m"]],
        "private": "secret",
    }
    fact = compact_engineering_payload("request_prepared", {"binding_selection": data, "request": {"input": "secret"}})
    assert fact["binding_selection"] == {k: v for k, v in data.items() if k != "private"}
    assert "secret" not in str(fact)
    data["qualified_order"] = [["p", "m"]] * 33
    assert "binding_selection" not in compact_engineering_payload("request_prepared", {"binding_selection": data})


@pytest.mark.parametrize("treatment", ["routing_record", "counterexample_refs"])
def test_assignment_pass_without_live_treatment_exposure_remains_unknown(tmp_path, treatment):
    plan, _ = inputs(tmp_path, treatment=treatment)
    out = tmp_path / "result.json"
    assert compare(tmp_path, out) == 2
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["assignment_threshold_verdict"] == "PASS"
    assert result["qualification"] == "UNKNOWN"
    if treatment == "routing_record":
        plan["workflow"] = "collaborative"
        write_json(tmp_path / "plan.json", plan)
        with pytest.raises(ValueError, match="direct Director"):
            load_plan(tmp_path / "plan.json")


def test_routing_requires_activated_distinct_physical_binding_in_each_project(tmp_path):
    from scripts.factory_bench._bench_lib.comparison import _routing_effect
    from scripts.factory_bench._bench_lib.routing_evidence import routing_witness

    plan = {"holdout": ["P"], "arms": {"baseline": {}, "candidate": {"routing_record_sha256": "a" * 64}}}

    def arm_evidence(arm, model, activated):
        root = tmp_path / arm / "P"
        for kind in ("snapshots", "contexts"):
            (root / kind).mkdir(parents=True, exist_ok=True)
        call_id = arm + ":director:1"
        ctx = {
            "context_hash": "1" * 24,
            "role": "director",
            "call_id": call_id,
            "evidence_level": "physical_capture",
            "physical_capture_status": "available",
            "provider_id": "p",
            "final_physical_request": {"body": {"model": model}},
        }
        write_json(root / "contexts" / ("1" * 24 + ".json"), ctx)
        prep = {"turn_seq": 1}
        pin = plan["arms"][arm].get("routing_record_sha256")
        if pin:
            prep["binding_selection"] = {
                "activated": activated,
                "qualification_record_sha256": pin,
                "qualified_order": [["p", model]],
            }
        events = [
            {"seq": 1, "role": "director", "kind": "request_prepared", "payload": prep},
            {
                "seq": 2,
                "role": "director",
                "kind": "llm_request_observed",
                "payload": {
                    "phase": "terminal",
                    "status": "completed",
                    "call_id": call_id,
                    "provider_id": "p",
                    "model": model,
                    "context_snapshot_ref": "1" * 24,
                },
            },
            {
                "seq": 3,
                "role": "director",
                "kind": "provider_response",
                "payload": {"ok": True, "provider_id": "p", "model": model, "turn_seq": 1},
            },
        ]
        write_json(
            root / "snapshots" / "3-100.json",
            {
                "run_id": arm,
                "session_id": arm,
                "workflow": "direct",
                "role": "director",
                "last_seq": 3,
                "status": "completed",
                "events": events,
            },
        )
        obs = {"platform": {"run_id": arm, "status": "completed", "last_seq": 3}}
        witness = routing_witness(root, obs, pin)
        assert witness["status"] == "observed"
        return {"records": [{"observation": obs, "routing_witness": witness}]}, root.parent

    reports = {
        "baseline": arm_evidence("baseline", "same", False),
        "candidate": arm_evidence("candidate", "same", True),
    }
    assert _routing_effect(plan, reports)["status"] == "UNKNOWN"
    reports["candidate"] = arm_evidence("candidate", "different", False)
    assert _routing_effect(plan, reports)["status"] == "UNKNOWN"
    reports["candidate"] = arm_evidence("candidate", "different", True)
    assert _routing_effect(plan, reports)["status"] == "observed"
    ctx = tmp_path / "candidate" / "P" / "contexts" / ("1" * 24 + ".json")
    data = json.loads(ctx.read_text(encoding="utf-8"))
    data["final_physical_request"]["body"]["model"] = "wrong"
    write_json(ctx, data)
    assert _routing_effect(plan, reports)["status"] == "UNKNOWN"
