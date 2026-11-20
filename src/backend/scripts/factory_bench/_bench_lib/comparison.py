"""Fixed-sample comparison of two internal batches on disjoint held-out projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, TypeGuard

from polaris.bootstrap.config import ServerConfig
from polaris.delivery.cli.engineering_acceptance import _HOST_KEYS
from scripts.factory_bench._bench_lib.routing_evidence import routing_witness
from scripts.factory_bench.backend_fingerprint import compute_source_fingerprint

ARMS = ("baseline", "candidate")


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _number(value: Any) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(value)


def load_plan(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    plan = json.loads(content.decode("utf-8"))
    _require(isinstance(plan, dict) and plan.get("schema_version") == "engineering.paired_plan.v1", "unsupported plan")
    all_ids = []
    for split in ("development", "calibration", "holdout"):
        values = plan.get(split)
        _require(
            isinstance(values, list)
            and bool(values)
            and all(isinstance(v, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", v) for v in values),
            "invalid split " + split,
        )
        all_ids.extend(values)
    _require(len(all_ids) == len(set(all_ids)), "project splits must be disjoint and internally unique")
    _require(isinstance(plan.get("reviewer"), str) and bool(plan["reviewer"].strip()), "independent reviewer required")
    _require(bool(re.fullmatch("[a-f0-9]{64}", plan.get("protocol_sha256", ""))), "review protocol digest required")
    for field in ("max_release_error", "noninferiority_margin", "confidence"):
        value = plan.get(field)
        _require(_number(value) and 0 <= value <= 1, "invalid threshold: " + field)
    _require(0 < plan["confidence"] < 1, "confidence must be strictly between zero and one")
    _require(_number(plan.get("max_cost_usd")) and plan["max_cost_usd"] > 0, "positive per-attempt USD budget required")
    treatment = plan.get("host_treatment")
    if treatment is not None:
        _validate_treatment(treatment)
    frozen = plan.get("frozen_inputs")
    _require(
        isinstance(frozen, dict)
        and {"catalog", "llm_config"} <= frozen.keys()
        and ("host_config" in frozen) == (treatment is None)
        and all(isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v) for v in frozen.values()),
        "invalid frozen input hashes",
    )
    _require(
        plan.get("workflow") in {"direct", "collaborative"} and "permission_profile_id" in plan,
        "missing workflow or grants",
    )
    arms = plan.get("arms")
    _require(isinstance(arms, dict) and set(arms) == set(ARMS), "two declared arms required")
    for arm in arms.values():
        _require(
            isinstance(arm, dict)
            and all(
                isinstance(arm.get(key), str) and re.fullmatch("[a-f0-9]{" + str(size) + "}", arm[key])
                for key, size in (
                    ("batch_id", 32),
                    ("source_fingerprint", 16),
                    ("policy_sha256", 64),
                    ("host_config_sha256", 64),
                )
            ),
            "invalid arm identity",
        )
    _require(arms["baseline"]["batch_id"] != arms["candidate"]["batch_id"], "distinct arm batch IDs required")
    baseline, candidate = arms["baseline"], arms["candidate"]
    if treatment == "routing_record":
        _require(plan["workflow"] == "direct", "routing treatment requires the direct Director workflow")
        _require(
            isinstance(candidate.get("routing_record_sha256"), str)
            and bool(re.fullmatch("[a-f0-9]{64}", candidate["routing_record_sha256"])),
            "candidate must pin its actual routing record",
        )
    if treatment is None:
        _require(
            all(a["host_config_sha256"] == frozen["host_config"] for a in arms.values())
            and baseline["policy_sha256"] == candidate["policy_sha256"],
            "source comparison must freeze host policy",
        )
        _require(baseline["source_fingerprint"] != candidate["source_fingerprint"], "no effective source treatment")
    else:
        common = baseline.get("common_host_policy_sha256")
        _require(
            isinstance(common, str)
            and re.fullmatch("[a-f0-9]{64}", common) is not None
            and common == candidate.get("common_host_policy_sha256"),
            "non-treatment host policy differs",
        )
        _require(
            baseline["source_fingerprint"] == candidate["source_fingerprint"]
            and baseline["policy_sha256"] != candidate["policy_sha256"]
            and baseline["host_config_sha256"] != candidate["host_config_sha256"],
            "host comparison must change only the declared effective policy field",
        )
    return plan, hashlib.sha256(content).hexdigest()


def _validate_treatment(field: Any) -> None:
    _require(
        field in {"max_output_bytes", "max_tool_output_bytes", "routing_record", "counterexample_refs"},
        "invalid host treatment field",
    )


def arm_identity(host_config: Path, treatment: str | None = None) -> dict[str, Any]:
    content = host_config.read_bytes()
    raw = json.loads(content.decode("utf-8"))
    _require(isinstance(raw, dict) and raw.keys() <= _HOST_KEYS, "unsupported frozen host policy")
    # The isolated launcher scrubs ambient engineering overrides. Use the
    # validated host file, including effective defaults and canonical set order.
    config = ServerConfig.model_validate({"engineering_" + k: v for k, v in raw.items()})
    values = config.model_dump(mode="json")
    policy = {k: values["engineering_" + k] for k in _HOST_KEYS}
    for profile in policy["permission_profiles"].values():
        profile["enabled_tools"] = sorted(profile["enabled_tools"])
    route_file = policy.pop("routing_record_file", None)
    route_sha = policy.pop("routing_record_sha256", None)
    policy["routing_record"] = None
    if route_file is not None or route_sha is not None:
        _require(
            isinstance(route_file, str) and Path(route_file).is_absolute(), "absolute frozen routing record required"
        )
        route_bytes = Path(route_file).read_bytes()
        _require(hashlib.sha256(route_bytes).hexdigest() == route_sha, "routing record digest mismatch")
        policy["routing_record"] = json.loads(route_bytes.decode("utf-8"))
    identity: dict[str, Any] = {
        "source_fingerprint": compute_source_fingerprint(),
        "policy_sha256": _digest(policy),
        "host_config_sha256": hashlib.sha256(content).hexdigest(),
    }
    if treatment is not None:
        _validate_treatment(treatment)
        if treatment == "routing_record":
            identity["routing_record_sha256"] = route_sha
        identity["common_host_policy_sha256"] = _digest({k: v for k, v in policy.items() if k != treatment})
    return identity


def check_inputs(plan: dict[str, Any], summary: dict[str, Any], arm: str) -> None:
    _require(
        [summary.get("project_budget_seconds"), summary.get("overall_budget_seconds")] == [5400, 6000], "budget drift"
    )
    _require(
        summary.get("workflow") == plan["workflow"]
        and summary.get("permission_profile_id") == plan["permission_profile_id"],
        "workflow or grants changed",
    )
    _require(plan["holdout"] == summary["selected_project_ids"], "holdout selection changed")
    _require(
        {k: v["sha256"] for k, v in summary["frozen_inputs"].items()}
        == {**plan["frozen_inputs"], "host_config": plan["arms"][arm]["host_config_sha256"]},
        "frozen inputs changed",
    )


def artifact_digest(inventory: dict[str, Any]) -> str:
    _require(
        inventory.get("status") == "read_only_observation" and isinstance(inventory.get("files"), list),
        "missing terminal artifact inventory",
    )
    files = sorted(inventory["files"], key=lambda f: f["path"])
    _require(
        len({f["path"] for f in files}) == len(files)
        and all(
            isinstance(f["path"], str)
            and type(f["bytes"]) is int
            and f["bytes"] >= 0
            and re.fullmatch("[a-f0-9]{64}", f["sha256"])
            for f in files
        ),
        "invalid artifact inventory",
    )
    return _digest(files)


def _upper(errors: int, n: int, alpha: float) -> float:
    """One-sided Clopper-Pearson upper bound by inverting the binomial CDF."""
    if errors == n:
        return 1.0
    lo, hi = 0.0, 1.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) for i in range(errors + 1)]
    for _ in range(50):
        mid = (lo + hi) / 2
        cdf = sum(math.exp(c + i * math.log(mid) + (n - i) * math.log1p(-mid)) for i, c in enumerate(logs))
        if cdf > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def _evidence_files(root: Path) -> tuple[dict[str, bytes], str]:
    root = root.resolve(strict=True)
    files = {}
    for path in root.glob("*/*/*.json"):
        _require(path.resolve(strict=True).is_relative_to(root) and not path.is_symlink(), "external evidence path")
        files[path.relative_to(root).as_posix()] = path.read_bytes()
    hashes = {name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()}
    return files, _digest(hashes)


def evidence_fingerprint(root: Path) -> str:
    return _evidence_files(root)[1]


def _coherent_release(observation: dict[str, Any]) -> bool:
    platform = observation["platform"]
    checks = platform.get("checks")
    commands = platform.get("verification_commands")
    required = platform.get("required_checks")
    return (
        observation.get("harness_status") == "OBSERVED_ACCEPTED"
        and observation.get("exit_code") == 0
        and observation.get("declared_check_results_present") is True
        and observation.get("evidence_consistency") == "consistent"
        and bool((observation.get("artifact_inventory") or {}).get("files"))
        and platform.get("status") == "completed"
        and platform.get("verification") == "passed"
        and platform.get("require_qa") is True
        and platform.get("qa_verdict") == "approved"
        and platform.get("acceptance_basis") == "independent_qa"
        and isinstance(commands, list)
        and bool(commands)
        and all(isinstance(c, str) and c.strip() for c in commands)
        and isinstance(required, list)
        and commands[: len(required)] == required
        and isinstance(checks, list)
        and len(checks) == len(commands)
        and all(
            isinstance(c, dict)
            and isinstance(c.get("call_id"), str)
            and bool(c["call_id"])
            and c.get("ok") is True
            and type(c.get("exit_code")) is int
            and c["exit_code"] == 0
            and c.get("interrupted") is False
            and c.get("timed_out") is not True
            for c in checks
        )
        and len({c["call_id"] for c in checks}) == len(checks)
    )


def _routing_effect(plan: dict[str, Any], reports: dict[str, tuple[dict[str, Any], Path]]) -> dict[str, Any]:
    unresolved = []
    for index, pid in enumerate(plan["holdout"]):
        bindings = {}
        changed = set()
        for arm, (report, directory) in reports.items():
            record = report["records"][index]
            observation = record.get("final_observation") or record.get("observation") or {}
            pin = plan["arms"][arm].get("routing_record_sha256")
            witness = routing_witness(directory / pid, observation, pin)
            if witness["status"] != "observed" or witness != record.get("routing_witness"):
                unresolved.append(
                    {"project_id": pid, "arm": arm, "reason": witness.get("reason", "saved evidence changed or absent")}
                )
                break
            bindings[arm] = {(r["provider"], r["model"]) for r in witness["requests"]}
            if arm == "candidate":
                changed = {(r["provider"], r["model"]) for r in witness["requests"] if r["activated"]}
        else:
            if not (changed - bindings["baseline"]):
                unresolved.append({"project_id": pid, "reason": "no activated physical binding different from control"})
    return {"status": "UNKNOWN" if unresolved else "observed", "unresolved": unresolved}


def _arm_results(
    plan: dict[str, Any], sha: str, arm: str, report: dict[str, Any], files: dict[str, bytes], run_ids: set[str]
) -> tuple[dict[str, Any], list[bool]]:
    check_inputs(plan, report, arm)
    setup = plan["arms"][arm]
    _require(
        report.get("comparison") == {**setup, "arm": arm, "plan_sha256": sha}
        and report.get("batch_id") == setup["batch_id"],
        "batch identity or plan binding changed",
    )
    rows = report["records"]
    _require(
        [r["project_id"] for r in rows] == plan["holdout"] and not report.get("batch_error"), "incomplete fixed sample"
    )
    releases = release_errors = independently_passed = 0
    incoherent_releases = 0
    cost = 0.0
    delivery = []
    for r in rows:
        pid = r["project_id"]
        o = r.get("final_observation") or r.get("observation") or {}
        platform = o.get("platform") or {}
        run_id = platform.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("missing run identity for " + pid)
        _require(
            run_id not in run_ids
            and r.get("resources_closed") is True
            and platform.get("status") in {"completed", "cancelled", "failed", "interrupted"},
            "missing settled unique run for " + pid,
        )
        run_ids.add(run_id)
        outcome = platform.get("project_outcome")
        _require(outcome in {"accepted", "rejected", "not_accepted"}, "unknown release decision for " + pid)
        elapsed = o.get("elapsed_seconds")
        if not _number(elapsed) or elapsed < 0:
            raise ValueError("unknown duration for " + pid)
        path = f"{setup['batch_id']}/{pid}/"
        review = json.loads(files[path + "review.json"].decode("utf-8"))
        bill = json.loads(files[path + "bill.json"].decode("utf-8"))
        identity = {"run_id": run_id, "project_id": pid, "batch_id": setup["batch_id"], "plan_sha256": sha}
        _require(
            all(doc.get(k) == v for doc in (review, bill) for k, v in identity.items()),
            "unbound review or bill for " + pid,
        )
        _require(
            review.get("kind") == "external_full_contract_review"
            and review.get("reviewer") == plan["reviewer"]
            and review.get("protocol_sha256") == plan["protocol_sha256"]
            and review.get("artifact_sha256") == artifact_digest(o.get("artifact_inventory") or {})
            and review.get("verdict") in {"PASS", "FAIL"},
            "missing or stale full-contract review for " + pid,
        )
        _require(
            bill.get("kind") == "settled_billing"
            and bill.get("all_attempts") is True
            and bill.get("currency") == "USD"
            and _number(bill.get("amount"))
            and bill["amount"] >= 0,
            "incomplete physical USD bill for " + pid,
        )
        passed = review["verdict"] == "PASS"
        released = outcome == "accepted"
        coherent = released and _coherent_release(o)
        incoherent_releases += released and not coherent
        releases += released
        release_errors += released and not passed
        independently_passed += passed
        cost += bill["amount"]
        delivery.append(
            passed
            and coherent
            and bill["amount"] <= plan["max_cost_usd"]
            and elapsed <= report["project_budget_seconds"]
        )
    n = len(rows)
    _require(math.isfinite(cost), "billing total overflow")
    return {
        "projects": n,
        "released": releases,
        "incoherent_releases": incoherent_releases,
        "release_coverage": releases / n,
        "release_errors": release_errors,
        "release_error_rate": release_errors / releases if releases else None,
        "independently_correct": independently_passed,
        "independent_success_rate": independently_passed / n,
        "successful_deliveries": sum(delivery),
        "unit_cost_per_verified_delivery_usd": cost / sum(delivery) if any(delivery) else None,
        "joint_success_rate": sum(delivery) / n,
        "all_attempt_cost_usd": cost,
        "unit_cost_per_independent_success_usd": cost / independently_passed if independently_passed else None,
    }, delivery


def compare(
    plan_path: Path, baseline: Path, candidate: Path, evidence_dir: Path, evidence_sha256: str
) -> dict[str, Any]:
    plan, sha = load_plan(plan_path)
    files, digest = _evidence_files(evidence_dir)
    _require(digest == evidence_sha256, "frozen independent evidence digest mismatch")
    result: dict[str, Any] = {
        "schema_version": "engineering.paired_result.v1",
        "plan_sha256": sha,
        "evidence_sha256": digest,
        "thresholds": {
            k: plan[k] for k in ("confidence", "max_release_error", "noninferiority_margin", "max_cost_usd")
        },
        "scope": "Fixed held-out projects, one paired observation per project. Population bounds assume independent identically distributed project pairs and trustworthy independent reviews and billing.",
    }
    outcomes = []
    reports = {}
    run_ids: set[str] = set()
    for arm, path in zip(ARMS, (baseline, candidate), strict=True):
        report_bytes = path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
        reports[arm] = (report, path.parent)
        result[arm], values = _arm_results(plan, sha, arm, report, files, run_ids)
        result[arm]["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
        outcomes.append(values)
    # Bonferroni spends half of alpha on release risk and half on the two
    # one-sided bounds on discordant paired outcomes. No sequential stopping.
    alpha = (1 - plan["confidence"]) / 2
    gains = sum(c and not b for b, c in zip(*outcomes, strict=True))
    losses = sum(b and not c for b, c in zip(*outcomes, strict=True))
    n = len(plan["holdout"])
    delta_lower = 1 - _upper(n - gains, n, alpha / 2) - _upper(losses, n, alpha / 2)
    candidate_metrics = result["candidate"]
    releases = candidate_metrics["released"]
    risk_upper = _upper(candidate_metrics["release_errors"], releases, alpha) if releases else None
    candidate_metrics["release_error_upper"] = risk_upper
    result.update(
        paired_gains=gains,
        paired_losses=losses,
        paired_success_delta=(gains - losses) / n,
        paired_success_delta_lower=delta_lower,
    )
    result["qualification"] = (
        "UNKNOWN"
        if risk_upper is None
        else (
            "PASS"
            if risk_upper <= plan["max_release_error"] and delta_lower >= -plan["noninferiority_margin"]
            else "FAIL"
        )
    )
    result["assignment_threshold_verdict"] = result["qualification"]
    if plan.get("host_treatment") == "routing_record":
        result["treatment_exposure"] = _routing_effect(plan, reports)
    elif plan.get("host_treatment") == "counterexample_refs":
        result["treatment_exposure"] = {"status": "UNKNOWN", "reason": "counterexamples are only projected in shadow"}
    if result.get("treatment_exposure", {}).get("status") == "UNKNOWN" or any(
        result[arm]["incoherent_releases"] for arm in ARMS
    ):
        result["qualification"] = "UNKNOWN"
    return result


def comparison_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a prespecified paired internal holdout")
    for key in ("plan", "baseline", "candidate", "evidence-dir", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--evidence-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        try:
            result = compare(args.plan, args.baseline, args.candidate, args.evidence_dir, args.evidence_sha256)
        except (KeyError, ValueError, TypeError, OSError, AttributeError) as exc:
            result = {"qualification": "UNKNOWN", "reason": str(exc)}
        with args.output.open("x", encoding="utf-8") as h:
            h.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(args.output)
        return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[result["qualification"]]
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
