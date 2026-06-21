"""Audit diagnostics — extract stable diagnostic signals from factory-bench audit artifacts.

Reads ``factory_audits.json`` (run-level aggregate) and per-project
``audits/{run_id}/*.audit.json`` files, extracting:
- director configured/observed/missing bindings
- real_run failed command tails
- stage failures
- QA derived missing artifacts
- failure taxonomy (root-cause category + signature)

Outputs a stable JSON diagnostic report suitable for regression comparison.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DIAGNOSTIC_SCHEMA_VERSION = "audit-diagnostics/1"

_EXPECTED_AUDIT_KEYS = frozenset(
    {
        "schema_version",
        "project_id",
        "level",
        "domain",
        "title",
        "code_file_count",
        "code_files",
        "doc_files",
        "artifacts",
        "has_plan_doc",
        "has_blueprint_doc",
        "has_qa_verdict",
        "checks",
        "all_checks_passed",
    }
)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_director_route_diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract director configured/observed/missing from llm_route_audit."""
    route_audit = _as_dict(record.get("llm_route_audit"))
    if not route_audit:
        return {
            "has_audit": False,
            "ok": False,
            "roles": {},
            "summary": "llm_route_audit missing",
        }

    roles_raw = _as_dict(route_audit.get("roles"))
    roles: dict[str, dict[str, Any]] = {}
    for role_name, role_data in roles_raw.items():
        if not isinstance(role_data, dict):
            continue
        roles[role_name] = {
            "ok": bool(role_data.get("ok")),
            "configured_count": len(role_data.get("configured") or []),
            "observed_count": int(role_data.get("observed_count") or 0),
            "missing_bindings": list(role_data.get("missing_bindings") or []),
            "observed_bindings": list(role_data.get("observed_bindings") or []),
            "family_ok": bool(role_data.get("family_ok")),
            "multi_route_ok": bool(role_data.get("multi_route_ok")),
        }

    return {
        "has_audit": True,
        "ok": bool(route_audit.get("ok")),
        "roles": roles,
        "events_observed": int(route_audit.get("events_observed") or 0),
        "events_rejected": int(route_audit.get("events_rejected") or 0),
        "terminal_events_observed": int(route_audit.get("terminal_events_observed") or 0),
        "summary": _norm(route_audit.get("summary")),
    }


def extract_real_run_diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract real_run gate details: which requirement failed, command tails."""
    real_run = _as_dict(record.get("real_run_gate"))
    if not real_run:
        return {
            "has_gate": False,
            "ok": False,
            "summary": "real_run_gate missing",
            "failing_requirements": [],
            "failed_commands": [],
            "declared_source_targets": {
                "declared_count": 0,
                "missing_count": 0,
                "missing_targets": [],
                "pm_plan_missing_source_targets": False,
            },
        }

    requirements = _as_dict(real_run.get("requirements"))
    failing_requirements: list[str] = []
    for name, req_data in requirements.items():
        if isinstance(req_data, dict) and not req_data.get("ok"):
            failing_requirements.append(name)

    commands = real_run.get("commands") or []
    failed_commands: list[dict[str, Any]] = []
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        if not cmd.get("ok"):
            failed_commands.append(
                {
                    "command": list(cmd.get("command") or []),
                    "returncode": cmd.get("returncode"),
                    "timeout": bool(cmd.get("timeout")),
                    "stderr_tail": _norm(cmd.get("stderr_tail"))[-500:],
                    "stdout_tail": _norm(cmd.get("stdout_tail"))[-500:],
                    "phase": _norm(cmd.get("phase")),
                    "script": _norm(cmd.get("script")),
                }
            )

    entrypoint = _as_dict(real_run.get("entrypoint"))

    # Extract declared source targets from the record
    declared_source_targets = {
        "declared_count": int(record.get("declared_source_target_count") or 0),
        "missing_count": int(record.get("missing_declared_source_target_count") or 0),
        "missing_targets": list(record.get("missing_declared_source_targets") or []),
        "pm_plan_missing_source_targets": bool(record.get("pm_plan_missing_source_targets")),
    }

    return {
        "has_gate": True,
        "ok": bool(real_run.get("ok")),
        "summary": _norm(real_run.get("summary")),
        "failing_requirements": failing_requirements,
        "failed_commands": failed_commands,
        "entrypoint": {
            "ok": bool(entrypoint.get("ok")),
            "kind": _norm(entrypoint.get("kind")),
            "detail": _norm(entrypoint.get("detail")),
        },
        "declared_source_targets": declared_source_targets,
    }


def extract_stage_failure(record: dict[str, Any]) -> dict[str, Any]:
    """Extract stage-level failure signals from factory_gates and chain_results."""
    chain_results = _as_dict(record.get("chain_results"))
    chain_state = _norm(record.get("chain_state"))

    gates = record.get("factory_gates") or []
    gate_failures: list[dict[str, Any]] = []
    for gate in gates:
        if isinstance(gate, dict) and not gate.get("ok"):
            gate_failures.append(
                {
                    "gate": _norm(gate.get("gate")),
                    "detail": _norm(gate.get("detail")),
                }
            )

    checks = record.get("checks") or []
    check_failures: list[dict[str, Any]] = []
    for check in checks:
        if isinstance(check, dict) and not check.get("ok"):
            check_failures.append(
                {
                    "check": _norm(check.get("check")),
                    "detail": _norm(check.get("detail")),
                }
            )

    return {
        "chain_state": chain_state,
        "chain_exit_class": _norm(chain_results.get("exit_class")),
        "qa_ran": chain_results.get("qa_ran"),
        "qa_passed": chain_results.get("qa_passed"),
        "qa_reason": _norm(chain_results.get("qa_reason")),
        "qa_blocked": bool(chain_results.get("qa_blocked")),
        "qa_blocked_stage": _norm(chain_results.get("qa_blocked_stage")),
        "qa_failure_reason": _norm(chain_results.get("qa_failure_reason")),
        "director_total": chain_results.get("director", {}).get("total")
        if isinstance(chain_results.get("director"), dict)
        else None,
        "director_failures": chain_results.get("director", {}).get("failures")
        if isinstance(chain_results.get("director"), dict)
        else None,
        "director_blocked": chain_results.get("director", {}).get("blocked")
        if isinstance(chain_results.get("director"), dict)
        else None,
        "gate_failures": gate_failures,
        "check_failures": check_failures,
    }


def extract_qa_diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract QA-derived missing artifacts and verdict signals."""
    chain_results = _as_dict(record.get("chain_results"))
    qa_blocked = bool(chain_results.get("qa_blocked") or record.get("qa_blocked"))
    return {
        "has_plan_doc": bool(record.get("has_plan_doc")),
        "has_blueprint_doc": bool(record.get("has_blueprint_doc")),
        "has_qa_verdict": bool(record.get("has_qa_verdict")),
        "qa_blocked": qa_blocked,
        "qa_blocked_stage": str(chain_results.get("qa_blocked_stage") or record.get("qa_blocked_stage") or ""),
        "qa_failure_reason": str(chain_results.get("qa_failure_reason") or record.get("qa_failure_reason") or ""),
        "qa_artifact_path": str(record.get("qa_artifact_path") or ""),
        "wrong_product_suspect": bool(record.get("wrong_product_suspect")),
        "wrong_product_match": _norm(record.get("wrong_product_match")),
        "brief_goal_overlap": record.get("brief_goal_overlap"),
    }


def extract_failure_taxonomy(record: dict[str, Any]) -> dict[str, Any]:
    """Extract failure taxonomy (root-cause category + signature)."""
    taxonomy = _as_dict(record.get("failure_taxonomy"))
    if not taxonomy:
        return {
            "has_taxonomy": False,
            "ok": bool(record.get("all_checks_passed")),
            "category": "",
            "root_cause_signature": "pass" if record.get("all_checks_passed") else "unclassified",
            "reasons": [],
            "evidence": [],
        }
    return {
        "has_taxonomy": True,
        "ok": bool(taxonomy.get("ok")),
        "category": _norm(taxonomy.get("category")),
        "root_cause_signature": _norm(taxonomy.get("root_cause_signature")),
        "reasons": list(taxonomy.get("reasons") or []),
        "evidence": list(taxonomy.get("evidence") or []),
    }


def extract_director_convergence_diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract director convergence diagnostics for director_partial cases.

    When QA did not run because Director did not converge, this extracts:
    - blocking_phase: which stage blocked
    - taskboard initial/final snapshots
    - missing declared delivery targets
    - per-binding task claim/terminal status
    - director summary stats

    Returns diagnostics dict; empty/missing fields indicate insufficient evidence.
    """
    convergence = _as_dict(record.get("director_convergence"))
    chain_results = _as_dict(record.get("chain_results"))
    qa_ran = bool(chain_results.get("qa_ran"))

    if convergence:
        return {
            "has_convergence_data": True,
            "qa_ran": bool(convergence.get("qa_ran", qa_ran)),
            "blocking_phase": _norm(convergence.get("blocking_phase")),
            "taskboard_initial": _as_dict(convergence.get("taskboard_initial")),
            "taskboard_final": _as_dict(convergence.get("taskboard_final")),
            "missing_delivery_targets": list(convergence.get("missing_delivery_targets") or []),
            "per_binding_task_status": list(convergence.get("per_binding_task_status") or []),
            "director_summary": _as_dict(convergence.get("director_summary")),
        }

    # Fallback: derive from chain_results when no explicit convergence block
    director = _as_dict(chain_results.get("director"))
    return {
        "has_convergence_data": bool(director),
        "qa_ran": qa_ran,
        "blocking_phase": _norm(record.get("chain_state")),
        "taskboard_initial": {},
        "taskboard_final": {
            "total": director.get("total"),
            "successes": director.get("successes"),
            "failures": director.get("failures"),
            "blocked": director.get("blocked"),
        }
        if director
        else {},
        "missing_delivery_targets": [],
        "per_binding_task_status": [],
        "director_summary": director if director else None,
    }


def extract_failure_mode(record: dict[str, Any]) -> dict[str, Any]:
    """Extract failure mode classification.

    Classifies failures into:
    - execution_missing: No execution evidence found
    - evidence_loss: Execution occurred but evidence is missing/lost
    - materialization_failure: Execution occurred but no materialized changes

    Returns failure mode classification with evidence.
    """
    # Check if this is a failed record
    all_checks_passed = bool(record.get("all_checks_passed"))
    if all_checks_passed:
        return {
            "failure_mode": "none",
            "is_failure": False,
            "evidence": [],
        }

    # Gather evidence indicators
    has_director_execution = False
    has_materialized_changes = False
    has_evidence_artifacts = False
    evidence_details: list[str] = []

    # Check for director execution evidence
    director_route = _as_dict(record.get("llm_route_audit"))
    if director_route:
        has_director_execution = bool(director_route.get("ok"))
        if has_director_execution:
            evidence_details.append("director_route_audit_ok")

    # Check for real run evidence
    real_run = _as_dict(record.get("real_run_gate"))
    if real_run:
        has_real_run = bool(real_run.get("ok"))
        if has_real_run:
            evidence_details.append("real_run_gate_ok")

    # Check for materialized changes
    code_file_count = int(record.get("code_file_count") or 0)
    if code_file_count > 0:
        has_materialized_changes = True
        evidence_details.append(f"code_files_present:{code_file_count}")

    # Check for evidence artifacts
    artifacts = _as_dict(record.get("artifacts"))
    if artifacts:
        has_evidence_artifacts = bool(artifacts.get("plan") or artifacts.get("blueprint") or artifacts.get("verdict"))
        if has_evidence_artifacts:
            evidence_details.append("artifacts_present")

    # Check for stage failures
    chain_state = _norm(record.get("chain_state"))
    stage_failures: list[str] = []

    if chain_state and chain_state != "clean":
        stage_failures.append(f"chain_state:{chain_state}")

    # Check for QA failures
    has_qa_verdict = bool(record.get("has_qa_verdict"))
    chain_results_for_qa = _as_dict(record.get("chain_results"))
    qa_blocked = bool(chain_results_for_qa.get("qa_blocked") or record.get("qa_blocked"))
    if qa_blocked:
        stage_failures.append("qa_blocked")
    elif not has_qa_verdict:
        stage_failures.append("missing_qa_verdict")

    # Check for failure taxonomy
    taxonomy = _as_dict(record.get("failure_taxonomy"))
    taxonomy_category = _norm(taxonomy.get("category"))
    taxonomy_signature = _norm(taxonomy.get("root_cause_signature"))

    # Determine failure mode
    failure_mode = "unclassified"
    if not has_director_execution and not has_evidence_artifacts:
        failure_mode = "execution_missing"
        evidence_details.append("no_director_execution_no_artifacts")
    elif has_director_execution and not has_materialized_changes:
        failure_mode = "materialization_failure"
        evidence_details.append("director_executed_but_no_changes")
    elif has_director_execution and has_materialized_changes and not has_evidence_artifacts:
        failure_mode = "evidence_loss"
        evidence_details.append("execution_and_changes_but_no_artifacts")
    elif stage_failures:
        failure_mode = "stage_failure"
        evidence_details.extend(stage_failures)

    return {
        "failure_mode": failure_mode,
        "is_failure": True,
        "has_director_execution": has_director_execution,
        "has_materialized_changes": has_materialized_changes,
        "has_evidence_artifacts": has_evidence_artifacts,
        "stage_failures": stage_failures,
        "taxonomy_category": taxonomy_category,
        "taxonomy_signature": taxonomy_signature,
        "evidence": evidence_details,
    }


def diagnose_project(record: dict[str, Any]) -> dict[str, Any]:
    """Produce a full diagnostic summary for one project audit record."""
    git_audit = extract_git_command_audit(record)
    has_git_violations = git_audit.get("has_findings", False)
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "project_id": _norm(record.get("project_id")),
        "level": int(record.get("level") or 0),
        "all_checks_passed": bool(record.get("all_checks_passed")),
        "code_file_count": int(record.get("code_file_count") or 0),
        "director_route": extract_director_route_diagnostics(record),
        "real_run": extract_real_run_diagnostics(record),
        "stage_failure": extract_stage_failure(record),
        "qa": extract_qa_diagnostics(record),
        "director_convergence": extract_director_convergence_diagnostics(record),
        "failure_taxonomy": extract_failure_taxonomy(record),
        "git_command_audit": git_audit,
        "git_command_violation": has_git_violations,
    }


def load_per_project_audits(run_audit_dir: Path) -> list[dict[str, Any]]:
    """Load all *.audit.json from audits/{run_id}/ directory."""
    records: list[dict[str, Any]] = []
    if not run_audit_dir.is_dir():
        return records
    for path in sorted(run_audit_dir.glob("*.audit.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            raw_record = data.get("record")
            record: dict[str, Any] = raw_record if isinstance(raw_record, dict) else data
            records.append(record)
    return records


def load_factory_audits_json(path: Path) -> dict[str, Any]:
    """Load the run-level factory_audits.json aggregate."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def diagnose_run(
    factory_audits: dict[str, Any],
    per_project_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a full diagnostic report for one bench run."""
    project_diagnostics = [diagnose_project(record) for record in per_project_records]

    aggregate_raw = factory_audits.get("aggregate")
    aggregate: dict[str, Any] = aggregate_raw if isinstance(aggregate_raw, dict) else factory_audits
    total = int(aggregate.get("total") or len(per_project_records))
    passed = int(aggregate.get("all_checks_passed") or 0)

    categories: dict[str, int] = {}
    signatures: dict[str, int] = {}
    for diag in project_diagnostics:
        tax = diag["failure_taxonomy"]
        if not tax["ok"]:
            cat = tax["category"] or "unknown"
            sig = tax["root_cause_signature"] or f"{cat}:unknown"
            categories[cat] = categories.get(cat, 0) + 1
            signatures[sig] = signatures.get(sig, 0) + 1

    director_route_fails = sum(
        1 for d in project_diagnostics if d["director_route"]["has_audit"] and not d["director_route"]["ok"]
    )
    real_run_fails = sum(1 for d in project_diagnostics if d["real_run"]["has_gate"] and not d["real_run"]["ok"])
    git_violations = sum(1 for d in project_diagnostics if d.get("git_command_violation"))

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "run_summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
        },
        "director_route_failures": director_route_fails,
        "real_run_failures": real_run_fails,
        "git_command_violations": git_violations,
        "failure_categories": dict(sorted(categories.items())),
        "root_cause_signatures": dict(sorted(signatures.items())),
        "projects": project_diagnostics,
    }


def diagnose_from_paths(
    factory_audits_path: Path | None,
    run_audit_dir: Path | None,
) -> dict[str, Any]:
    """Convenience: load from paths and produce diagnostic report."""
    factory_audits = (
        load_factory_audits_json(factory_audits_path) if factory_audits_path and factory_audits_path.is_file() else {}
    )
    per_project = load_per_project_audits(run_audit_dir) if run_audit_dir and run_audit_dir.is_dir() else []
    return diagnose_run(factory_audits, per_project)


# Git command audit patterns - P0 findings
# Covers all git subcommands that can destroy, hide, or rewrite working-tree state.
_GIT_DANGEROUS_COMMANDS = re.compile(
    r"\bgit\s+(?:"
    r"stash(?:\s+(?:pop|apply|drop|clear|branch|create))?"  # stash + sub-commands
    r"|reset\b"  # git reset (all modes)
    r"|checkout\b"  # git checkout (switch branch/file)
    r"|restore\b"  # git restore (discard changes)
    r"|clean\b"  # git clean (remove untracked)
    r"|switch\b"  # git switch (branch switch)
    r"|branch\s+-[dD]\b"  # git branch -d/-D (delete branch)
    r"|worktree\s+(?:remove|prune|move)"  # git worktree remove/prune/move
    r"|rebase\s+--abort"  # git rebase --abort
    r"|push\s+.*--force"  # git push --force
    r"|commit\s+--amend"  # git commit --amend (rewrites history)
    r")\b",
    re.IGNORECASE,
)

# Patterns that destroy the .git directory or the repo root entirely.
_GIT_REPO_DESTROY = re.compile(
    r"(?:rm\s+-rf?\s+.*\.git\b|rm\s+-rf?\s+.*\.git/)",
    re.IGNORECASE,
)

# Git read-only commands — safe, never mutate state.
_GIT_SAFE_COMMANDS = re.compile(
    r"\bgit\s+(?:status|diff|log|show|branch(?!\s+-[dD])|tag|remote|describe|rev-parse|"
    r"ls-files|ls-tree|cat-file|count-objects|shortlog|blame|bisect\s+log|"
    r"stash\s+(?:list|show)|config\s+--(?:get|list)|symbolic-ref|rev-list|"
    r"for-each-ref|name-rev|archive|fmt-merge-msg|merge-base)\b",
    re.IGNORECASE,
)


def is_git_safe_command(command: str) -> bool:
    """Return True if the command is a known read-only git operation."""
    return bool(_GIT_SAFE_COMMANDS.search(command))


def _classify_git_command(command: str) -> dict[str, Any] | None:
    """Classify a command string for git danger signals.

    Returns a dict with finding details if dangerous, or ``None`` if safe or
    not a git command.
    """
    stripped = command.strip()

    # Check repo destruction first (rm -rf .git) — doesn't contain "git " prefix
    if _GIT_REPO_DESTROY.search(stripped):
        return {
            "command": stripped,
            "severity": "P0",
            "reason": f"Repository destruction command detected: {stripped}",
            "pattern_matched": _GIT_REPO_DESTROY.pattern,
            "category": "repo_destruction",
        }

    if not stripped.lower().startswith("git ") and "git " not in stripped.lower():
        return None

    if is_git_safe_command(stripped):
        return None

    match = _GIT_DANGEROUS_COMMANDS.search(stripped)
    if match:
        subcommand = match.group(0).strip()
        severity = "P0"
        category = "state_mutation"
        if "stash" in subcommand.lower():
            category = "stash_manipulation"
        elif "reset" in subcommand.lower():
            category = "history_rewrite"
        elif "checkout" in subcommand.lower() or "switch" in subcommand.lower():
            category = "branch_switch"
        elif "restore" in subcommand.lower() or "clean" in subcommand.lower():
            category = "file_discard"
        elif "branch" in subcommand.lower() and "-d" in subcommand.lower():
            category = "branch_delete"
        elif "worktree" in subcommand.lower():
            category = "worktree_mutation"
        elif "rebase" in subcommand.lower():
            category = "rebase_abort"
        elif "force" in subcommand.lower():
            category = "force_push"
        elif "amend" in subcommand.lower():
            category = "history_rewrite"

        return {
            "command": stripped,
            "severity": severity,
            "reason": f"Dangerous git command detected: {stripped}",
            "pattern_matched": match.re.pattern,
            "category": category,
        }

    return None


def _extract_git_commands_from_text(text: str) -> list[dict[str, Any]]:
    """Scan free-text or JSONL lines for dangerous git commands."""
    findings: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Try JSONL parse first
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                finding = _classify_git_command_from_dict(data, prefix="jsonl")
                if finding:
                    findings.append(finding)
                continue
        except json.JSONDecodeError:
            pass
        # Plain text fallback — check repo destruction first
        if _GIT_REPO_DESTROY.search(line):
            findings.append(
                {
                    "field": "plain_text",
                    "command": line,
                    "severity": "P0",
                    "reason": f"Repository destruction command detected: {line}",
                    "pattern_matched": _GIT_REPO_DESTROY.pattern,
                    "category": "repo_destruction",
                }
            )
            continue
        finding = _classify_git_command(line)
        if finding:
            finding["field"] = "plain_text"
            findings.append(finding)
    return findings


def _classify_git_command_from_dict(
    item: dict[str, Any],
    prefix: str = "",
) -> dict[str, Any] | None:
    """Check a dict's command-bearing fields for dangerous git commands."""
    fields_to_check = [
        "command",
        "cmd",
        "tool_command",
        "execution_command",
        "last_command",
        "git_command",
    ]
    for field in fields_to_check:
        command = _norm(item.get(field))
        if not command:
            continue
        finding = _classify_git_command(command)
        if finding:
            finding["field"] = f"{prefix}.{field}" if prefix else field
            return finding
    return None


def extract_git_command_audit(record: dict[str, Any]) -> dict[str, Any]:
    """Extract git command audit from opencode JSONL/command fields.

    Detects dangerous git commands that can destroy, hide, or rewrite
    working-tree state.  Supports:
    - ``git stash`` (push/pop/apply/drop/clear/branch/create)
    - ``git reset`` (all modes)
    - ``git checkout`` / ``git switch`` (branch switching)
    - ``git restore`` / ``git clean`` (file discard)
    - ``git branch -D`` (branch deletion)
    - ``git worktree remove/prune/move``
    - ``git rebase --abort``
    - ``git push --force``
    - ``git commit --amend``
    - ``rm -rf .git`` (repo destruction)

    Safe read-only commands (status, diff, log, show, branch list, etc.)
    are explicitly excluded.

    Supports:
    - JSONL tool logs (``jsonl_content`` / ``log_content`` fields)
    - Plain command strings (``command``, ``cmd``, etc.)
    - Nested structures (``tool_calls``, ``commands``, ``execution_log``, ``events``)
    """
    findings: list[dict[str, Any]] = []

    # Check various fields that might contain git commands
    fields_to_check = [
        "command",
        "cmd",
        "tool_command",
        "execution_command",
        "last_command",
        "git_command",
    ]

    for field in fields_to_check:
        command = _norm(record.get(field))
        if not command:
            continue
        finding = _classify_git_command(command)
        if finding:
            finding["field"] = field
            findings.append(finding)

    # Check nested structures
    for key in ["tool_calls", "commands", "execution_log", "events"]:
        nested = record.get(key)
        if isinstance(nested, list):
            for i, item in enumerate(nested):
                if isinstance(item, dict):
                    finding = _classify_git_command_from_dict(item, prefix=f"{key}[{i}]")
                    if finding:
                        findings.append(finding)
                elif isinstance(item, str):
                    command = _norm(item)
                    if command:
                        finding = _classify_git_command(command)
                        if finding:
                            finding["field"] = f"{key}[{i}]"
                            findings.append(finding)

    # Check JSONL content if present
    jsonl_content = _norm(record.get("jsonl_content") or record.get("log_content"))
    if jsonl_content:
        text_findings = _extract_git_commands_from_text(jsonl_content)
        findings.extend(text_findings)

    return {
        "has_findings": len(findings) > 0,
        "finding_count": len(findings),
        "findings": findings,
        "severity": "P0" if findings else "none",
    }


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "diagnose_from_paths",
    "diagnose_project",
    "diagnose_run",
    "extract_director_convergence_diagnostics",
    "extract_director_route_diagnostics",
    "extract_failure_mode",
    "extract_failure_taxonomy",
    "extract_git_command_audit",
    "extract_qa_diagnostics",
    "extract_real_run_diagnostics",
    "extract_stage_failure",
    "is_git_safe_command",
    "load_factory_audits_json",
    "load_per_project_audits",
]
