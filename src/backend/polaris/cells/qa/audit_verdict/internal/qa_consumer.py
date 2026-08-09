"""QA consumer that polls PENDING_QA and emits audit verdicts."""

from __future__ import annotations

import logging
import os
import re
import shlex
import threading
from typing import Any

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.qa.audit_verdict.internal.evidence_commit import (
    commit_qa_evidence,
    commit_qa_verdict,
)
from polaris.cells.qa.audit_verdict.internal.qa_service import QAService
from polaris.cells.qa.audit_verdict.internal.verdict_engine import (
    classify_qa_audit_failure,
)
from polaris.cells.qa.audit_verdict.public.project_verification import (
    ProjectVerificationReceiptV1,
    QueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
    query_project_verification_receipt,
    run_project_verification,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)
_VALID_ROUTE_STAGES = frozenset({"pending_design", "pending_exec", "pending_qa", "waiting_human"})
_CODE_FILE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
    }
)
_CODE_PATH_PREFIXES = ("src/", "test/", "tests/", "app/", "apps/", "backend/", "frontend/", "lib/", "scripts/")
_NO_CHANGE_FLAGS = frozenset(
    {
        "allow_no_changes",
        "no_changes_expected",
        "allow_empty_changed_files",
        "director_noop_allowed",
    }
)
_NO_CHANGE_MODES = frozenset(
    {
        "noop",
        "no_op",
        "no-op",
        "read_only",
        "read-only",
        "inspection",
        "inspection_only",
        "analysis_only",
    }
)
_VERIFIED_EXISTING_SCOPE_MODES = frozenset({"verified_existing_workspace_scope"})
_QA_CONTEXT_INPUT_KEYS = (
    "input",
    "qa_input",
    "role_input",
    "review_input",
    "factory_qa_input",
)
_QA_CONTEXT_MAX_CHARS = 16000


def _sanitize_qa_context_for_prompt(text: str) -> str:
    """Keep QA evidence readable while removing factory control-plane field names."""

    sanitized = text
    for before, after in (
        ('"factory_run_id"', '"factory_run_ref"'),
        ("'factory_run_id'", "'factory_run_ref'"),
        ("factory_run_id:", "factory_run_ref:"),
        ("factory_run_id=", "factory_run_ref="),
    ):
        sanitized = sanitized.replace(before, after)
    return sanitized.strip()


def _extract_qa_prompt_context(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    candidates: list[str] = []

    def add_candidate(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    for key in _QA_CONTEXT_INPUT_KEYS:
        add_candidate(payload.get(key))

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in _QA_CONTEXT_INPUT_KEYS:
            add_candidate(metadata.get(key))

    if not candidates:
        return ""
    merged = "\n\n".join(candidates)
    sanitized = _sanitize_qa_context_for_prompt(merged)
    if len(sanitized) <= _QA_CONTEXT_MAX_CHARS:
        return sanitized
    return sanitized[:_QA_CONTEXT_MAX_CHARS].rstrip() + "\n[qa_context_truncated]"


def _mapping_copy(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _extract_control_plane_job_token(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable capability token carried by the claimed work item."""

    metadata = _mapping_copy(payload.get("metadata"))
    for container in (payload, metadata):
        for key in ("job_token", "control_plane_job_token", "capability_token"):
            token = container.get(key)
            if isinstance(token, dict) and str(token.get("token_id") or "").strip():
                return dict(token)
    return {}


def _qa_control_plane_metadata(
    payload: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve the control-plane lineage when QA ack/requeue mutates a task."""

    metadata: dict[str, Any] = dict(extra or {})
    job_token = _extract_control_plane_job_token(payload)
    if job_token:
        metadata["job_token"] = dict(job_token)
        metadata["control_plane_job_token"] = dict(job_token)
        metadata["capability_token"] = dict(job_token)
        metadata["job_token_id"] = str(job_token.get("token_id") or "")
    for key in ("contract_hash", "pm_contract_hash", "blueprint_hash"):
        value = str(payload.get(key) or job_token.get(key) or "").strip()
        if value:
            metadata[key] = value
    lineage = _mapping_copy(payload.get("control_plane_lineage"))
    if not lineage:
        lineage = _mapping_copy(_mapping_copy(payload.get("metadata")).get("control_plane_lineage"))
    if lineage:
        metadata["control_plane_lineage"] = lineage
    return metadata


def _qa_local_repair_context(
    *,
    task_id: str,
    payload: dict[str, Any],
    audit_result: dict[str, Any],
    engine_payload: dict[str, Any],
    verdict_receipt: dict[str, str],
) -> dict[str, Any]:
    """Project compact QA evidence for one same-contract Director repair turn."""

    classification = _mapping_copy(engine_payload.get("classification"))
    projection = _mapping_copy(payload.get("task_completion_projection"))
    findings = [str(item).strip() for item in list(audit_result.get("findings") or []) if str(item).strip()][
        :_QA_FEEDBACK_MAX_FINDINGS
    ]
    evidence_refs = [str(item).strip() for item in list(engine_payload.get("evidence_refs") or []) if str(item).strip()][
        :_QA_FEEDBACK_MAX_FINDINGS
    ]
    failed_verifier = _mapping_copy(payload.get("qa_failed_verifier"))
    exact_receipt = bool(
        str(failed_verifier.get("receipt_hash") or "").strip()
        and str(failed_verifier.get("receipt_ref") or "").strip()
    )
    authority_kind = "exact_verifier_receipt" if exact_receipt else "diagnostic_effect"
    return {
        "schema_version": "qa.local_repair_context.v1",
        "task_id": task_id,
        "failure_class": str(classification.get("failure_class") or "").strip(),
        "responsible_layer": str(classification.get("responsible_layer") or "").strip(),
        "reason": str(classification.get("reason") or "").strip()[:_QA_FEEDBACK_MAX_CHARS],
        "findings": findings,
        "evidence_refs": evidence_refs,
        "qa_verdict_content_hash": str(engine_payload.get("content_hash") or "").strip(),
        "qa_verdict_receipt": dict(verdict_receipt),
        "repair_authority_kind": authority_kind,
        "failed_verifier": failed_verifier,
        "diagnostic_effect_authority": (
            {}
            if exact_receipt
            else {
                "schema_version": "qa.local-repair-diagnostic-effect.v1",
                "diagnostic_kind": "non_executable_qa_diagnostic",
                "authority_source": "qa_canonical_verdict",
                "executable_verifier_observed": False,
                "task_id": task_id,
                "failure_class": str(classification.get("failure_class") or "").strip(),
                "responsible_layer": str(classification.get("responsible_layer") or "").strip(),
                "qa_verdict_content_hash": str(engine_payload.get("content_hash") or "").strip(),
                "task_completion_projection_hash": str(projection.get("projection_hash") or "").strip(),
                "requires_material_effect": True,
                "revalidate_in_qa": True,
            }
        ),
        "task_completion_projection_hash": str(projection.get("projection_hash") or "").strip(),
        "project_completion_contract_hash": str(
            projection.get("project_contract_hash") or payload.get("completion_contract_hash") or ""
        ).strip(),
        "repair_policy": {
            "same_task_only": True,
            "reuse_existing_context_snapshot": True,
            "automatic_upstream_replan": False,
            "automatic_escalation": False,
            "rerun_exact_failed_verifier": exact_receipt,
            "require_material_effect": True,
            "return_to_qa": True,
        },
    }


def _project_verification_receipt_projection(receipt: ProjectVerificationReceiptV1) -> dict[str, Any]:
    """Project the immutable exact-verifier identity needed by one repair turn."""

    return {
        "schema_version": "qa.failed-verifier-receipt.v1",
        "project_id": receipt.project_id,
        "run_id": receipt.run_id,
        "completion_contract_hash": receipt.completion_contract_hash,
        "obligation_id": receipt.obligation_id,
        "owner_task_id": receipt.owner_task_id,
        "modality": receipt.modality,
        "argv": list(receipt.argv),
        "cwd": receipt.cwd,
        "command_authority_hash": receipt.command_authority_hash,
        "executable_path": receipt.executable_path,
        "executable_realpath": receipt.executable_realpath,
        "executable_hash": receipt.executable_hash,
        "input_artifact_hash": receipt.input_artifact_hash,
        "exit_code": receipt.exit_code,
        "timed_out": receipt.timed_out,
        "output_hash": receipt.output_hash,
        "proof_satisfied": receipt.proof_satisfied,
        "proof_evidence_hash": receipt.proof_evidence_hash,
        "process_pid": receipt.process_pid,
        "process_start_token": receipt.process_start_token,
        "readiness_probe_kind": receipt.readiness_probe_kind,
        "readiness_satisfied": receipt.readiness_satisfied,
        "controlled_termination": receipt.controlled_termination,
        "receipt_hash": receipt.receipt_hash,
        "receipt_ref": receipt.receipt_ref,
    }


def _step_verification_obligation(payload: dict[str, Any], verify: str) -> dict[str, Any]:
    """Resolve one exact task-local verifier mapping from the CE projection."""

    projection = _mapping_copy(payload.get("task_completion_projection"))
    raw_rows = projection.get("verification_execution_authority")
    rows = [dict(row) for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    matches = [row for row in rows if str(row.get("command") or "").strip() == verify]
    if len(matches) != 1:
        raise ValueError("step verify must match exactly one CE-projected project verifier obligation")
    return matches[0]


def _qa_contract_authority_blocker(
    *,
    task_id: str,
    payload: dict[str, Any],
    engine_payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a non-escalating terminal blocker for a true contract contradiction."""

    job_token = _extract_control_plane_job_token(payload)
    projection = _mapping_copy(payload.get("task_completion_projection"))
    identity = {
        "task_id": str(task_id or "").strip(),
        "run_id": str(
            engine_payload.get("run_id") or payload.get("run_id") or job_token.get("run_id") or "missing"
        ).strip(),
        "trace_id": str(payload.get("trace_id") or job_token.get("trace_id") or "missing").strip(),
        "blueprint_id": str(payload.get("blueprint_id") or "missing").strip(),
        "completion_contract_hash": str(
            projection.get("project_contract_hash")
            or payload.get("completion_contract_hash")
            or job_token.get("contract_hash")
            or "missing"
        ).strip(),
    }
    missing = [key for key, value in identity.items() if value == "missing" or not value]
    return {
        "schema_version": "qa.contract_authority_blocker.v1",
        "blocker_kind": "contract_or_authority_contradiction",
        **identity,
        "identity_complete": not missing,
        "missing_identity_fields": missing,
        "classification": _mapping_copy(engine_payload.get("classification")),
        "qa_verdict_content_hash": str(engine_payload.get("content_hash") or "").strip(),
        "automatic_upstream_replan": False,
        "automatic_escalation": False,
        "retry_same_contract": False,
    }


def _qa_findings_count(findings: Any) -> int:
    if isinstance(findings, list):
        return len([item for item in findings if str(item or "").strip()])
    return 0


_QA_LLM_AUDIT_ENABLED_ENV = "KERNELONE_QA_LLM_AUDIT_ENABLED"
_QA_LLM_AUDIT_TIMEOUT_ENV = "KERNELONE_QA_LLM_AUDIT_TIMEOUT_SECONDS"
_BOOL_TRUE = {"1", "true", "yes", "on", "enabled"}
_BOOL_FALSE = {"0", "false", "no", "off", "disabled"}
_VERIFY_SCRIPT_NAMES = frozenset({"verify.js", "scripts/verify.js"})
_VERIFY_SCRIPT_CHECK_LABELS = {
    "py_compile": "py_compile Python syntax check",
    "html": "html entrypoint check",
    "js_syntax": "js_syntax JavaScript syntax check",
    "ts_syntax": "ts_syntax / tsc TypeScript syntax check",
    "go_compile": "go_compile Go compile/test check",
    "rust_compile": "rust_compile Rust compile check",
    "cpp_compile": "cpp_compile C++ compile check",
    "java_compile": "java_compile Java compile check",
    "package_scripts": "package_scripts npm script validation",
    "runnable_any": "runnable_any shape-neutral runnability check",
    "real_run": "real_run runtime smoke check",
    "min_files": "min_files code inventory validation",
    "content_any": "content_any feature keyword probe",
}
_VERIFY_SCRIPT_SIMPLE_REQUIREMENT_MARKERS = {
    "py_compile": ("py_compile", "verifypycompile", "python compile", "python syntax"),
    "html": ("html", "verifyhtml", "html entry", "index.html"),
    "js_syntax": ("js_syntax", "verifyjssyntax", "javascript syntax", "js syntax"),
    "ts_syntax": ("ts_syntax", "verifytssyntax", "ts syntax", "typescript syntax"),
    "go_compile": ("go_compile", "verifygocompile", "go compile", "go test"),
    "rust_compile": ("rust_compile", "verifyrustcompile", "rust compile", "cargo check", "cargo test"),
    "cpp_compile": ("cpp_compile", "verifycppcompile", "c++ compile", "cpp compile", "g++", "clang++"),
    "java_compile": ("java_compile", "verifyjavacompile", "java compile", "javac"),
    "runnable_any": ("runnable_any", "verifyrunnable", "runnable shape", "runnability"),
    "real_run": ("real_run", "verifyrealrun", "real run", "runtime smoke", "smoke run"),
}


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    return default


def _qa_verdict_projection_payload(envelope: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    projection = dict(envelope)
    projection["local_evidence_diff"] = dict(diff)
    return projection


def _canonical_route_from_projection(
    *,
    engine_payload: dict[str, Any],
) -> tuple[str, str, str]:
    """Validate and return the sole route authorized by the canonical envelope."""

    if engine_payload.get("schema_version") != "qa.verdict_envelope.v1" or engine_payload.get("error"):
        return "BLOCKED", "pending_qa", ""
    ledger = engine_payload.get("ledger")
    ledger_map = ledger if isinstance(ledger, dict) else {}
    evidence = engine_payload.get("evidence")
    evidence_map = evidence if isinstance(evidence, dict) else {}
    conflict_matrix = evidence_map.get("conflict_matrix")
    conflict_map = conflict_matrix if isinstance(conflict_matrix, dict) else {}
    conflicts = conflict_map.get("conflicts")
    if (
        ledger_map.get("source") != "run_ledger_projection"
        or ledger_map.get("available") is not True
        or not isinstance(conflicts, list)
        or conflicts
        or not str(engine_payload.get("content_hash") or "").strip()
    ):
        return "BLOCKED", "pending_qa", ""
    verdict = str(engine_payload.get("verdict") or "BLOCKED").strip().upper() or "BLOCKED"
    next_stage = str(engine_payload.get("next_stage") or "").strip().lower()
    terminal_status = str(engine_payload.get("terminal_status") or "").strip().lower()
    if next_stage and next_stage not in _VALID_ROUTE_STAGES:
        return "BLOCKED", "pending_qa", ""
    if verdict == "PASS" and (engine_payload.get("ok") is not True or next_stage or terminal_status != "resolved"):
        return "BLOCKED", "pending_qa", ""
    if not next_stage and not terminal_status:
        terminal_status = "resolved" if verdict == "PASS" else ""
        if not terminal_status:
            next_stage = "pending_qa"
    return verdict, next_stage, terminal_status


def _qa_llm_audit_timeout_seconds() -> float:
    raw = os.environ.get(_QA_LLM_AUDIT_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return 180.0
        if value > 0:
            return min(value, 900.0)
    return 180.0


def _iter_payload_strings(value: Any, *, _depth: int = 0) -> list[str]:
    if _depth > 5:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, item in value.items():
            strings.extend(_iter_payload_strings(key, _depth=_depth + 1))
            strings.extend(_iter_payload_strings(item, _depth=_depth + 1))
        return strings
    if isinstance(value, (list, tuple, set)):
        strings = []
        for item in value:
            strings.extend(_iter_payload_strings(item, _depth=_depth + 1))
        return strings
    return []


def _payload_text(payload: dict[str, Any]) -> str:
    return "\n".join(_iter_payload_strings(payload)).lower()


def _payload_paths_for_contract_gate(payload: dict[str, Any]) -> list[str]:
    paths = _collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))
    paths.extend(_extract_director_changed_files(payload))
    paths.extend(_extract_fallback_audit_files(payload))
    step = payload.get("construction_step")
    if isinstance(step, dict):
        paths.extend(_normalize_path_values(step.get("target_file")))
    return _normalize_path_values(paths)


def _path_set_contains(paths: list[str], expected: str) -> bool:
    wanted = expected.replace("\\", "/").lower().lstrip("./")
    for raw in paths:
        path = raw.replace("\\", "/").lower().lstrip("./")
        if path == wanted or path.endswith(f"/{wanted}"):
            return True
    return False


def _contract_mentions_package_scripts(payload_text: str) -> bool:
    return (
        "package_scripts" in payload_text
        or ("package.json" in payload_text and "script" in payload_text)
        or ("package.json" in payload_text and "脚本" in payload_text)
    )


def _extract_verify_script_requirements(payload: dict[str, Any]) -> dict[str, str]:
    """Extract explicit deterministic-check requirements a verify script must encode."""
    text = _payload_text(payload)
    required: dict[str, str] = {}
    for kind, markers in _VERIFY_SCRIPT_SIMPLE_REQUIREMENT_MARKERS.items():
        if any(marker in text for marker in markers):
            required[kind] = kind
    if _contract_mentions_package_scripts(text) or "verifypackagescripts" in text or "package scripts" in text:
        required["package_scripts"] = "package_scripts"
    min_files_match = re.search(r"\bmin_files\s*:\s*(\d+)", text)
    if min_files_match:
        required["min_files"] = f"min_files:{min_files_match.group(1)}"
    elif "verifyfilecount" in text or "file count" in text or "至少 3 个文件" in text:
        required["min_files"] = "min_files:3"
    content_match = re.search(r"\bcontent_any\s*:\s*([a-z0-9_|.-]+)", text)
    if content_match:
        required["content_any"] = f"content_any:{content_match.group(1)}"
    elif (
        "verifycontentexists" in text
        or "content exists" in text
        or all(term in text for term in ("firefly", "flower", "moon", "humidity"))
    ):
        required["content_any"] = "content_any:firefly|flower|moon|humidity"
    return required


def _verify_script_covers_requirement(script_text: str, kind: str, requirement: str) -> bool:
    content = script_text.lower()
    if kind == "py_compile":
        return (
            "py_compile" in content
            or "compileall" in content
            or ("python" in content and ("-m py_compile" in content or "py_compile" in content))
        )
    if kind == "html":
        return "verifyhtml" in content or ("index.html" in content and "<html" in content)
    if kind == "js_syntax":
        return "js_syntax" in content or re.search(r"\bnode\s+--check\b", content) is not None
    if kind == "ts_syntax":
        return "ts_syntax" in content or re.search(r"\btsc\b", content) is not None
    if kind == "go_compile":
        return "go_compile" in content or re.search(r"\bgo\s+(test|build)\b", content) is not None
    if kind == "rust_compile":
        return "rust_compile" in content or re.search(r"\b(cargo\s+(check|test|build)|rustc)\b", content) is not None
    if kind == "cpp_compile":
        return "cpp_compile" in content or any(tool in content for tool in ("g++", "clang++", "c++"))
    if kind == "java_compile":
        return "java_compile" in content or any(tool in content for tool in ("javac", "mvn test", "gradle test"))
    if kind == "package_scripts":
        return "package_scripts" in content or (
            "package.json" in content
            and "scripts" in content
            and any(token in content for token in ("placeholder", "占位", "echo", "node scripts/verify.js"))
        )
    if kind == "min_files":
        return "min_files" in content or (
            re.search(r"(>=\s*3|>\s*2|至少\s*3|min(?:imum)?[^\n]{0,24}3|3\s*个文件)", content) is not None
            and any(token in content for token in ("readdir", "recursive", "glob", "walk"))
        )
    if kind == "content_any":
        if "content_any" in content:
            return True
        pattern = requirement.split(":", 1)[1] if ":" in requirement else ""
        terms = [term.strip().lower() for term in pattern.split("|") if term.strip()]
        return bool(terms) and all(term in content for term in terms)
    if kind == "runnable_any":
        return "runnable_any" in content or (
            any(token in content for token in ("index.html", "main.py", "main.go", "main.rs", "main.cpp", "main.java"))
            and any(token in content for token in ("exists", "existssync", "spawnsync", "execsync", "readfilesync"))
        )
    if kind == "real_run":
        return "real_run" in content or (
            any(token in content for token in ("spawnsync", "execsync", "subprocess", "npm run", "go run", "cargo run"))
            and any(token in content for token in ("start", "run", "smoke", "timeout"))
        )
    return False


def _verify_script_gate_failure(workspace: str, payload: dict[str, Any]) -> str:
    paths = _payload_paths_for_contract_gate(payload)
    text = _payload_text(payload)
    script_targeted = (
        _path_set_contains(paths, "scripts/verify.js")
        or _path_set_contains(paths, "verify.js")
        or "scripts/verify.js" in text
        or "验收脚本" in text
    )
    if not script_targeted:
        return ""

    requirements = _extract_verify_script_requirements(payload)
    if not requirements:
        return ""

    script_path = os.path.join(workspace, "scripts", "verify.js")
    if not os.path.isfile(script_path):
        return "verification script gate failed: scripts/verify.js is required but missing"
    try:
        with open(script_path, encoding="utf-8") as handle:
            script_text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return f"verification script gate failed: could not read scripts/verify.js: {exc}"

    if re.search(r"\bnode\s+scripts/verify\.js\b", script_text.lower()):
        return "verification script gate failed: scripts/verify.js recursively invokes itself"

    missing = [
        f"{requirement} ({_VERIFY_SCRIPT_CHECK_LABELS.get(kind, kind)})"
        for kind, requirement in requirements.items()
        if not _verify_script_covers_requirement(script_text, kind, requirement)
    ]
    if not missing:
        return ""
    return (
        "verification script gate failed: scripts/verify.js is manifest-only or incomplete; "
        "encode these deterministic checks directly: " + "; ".join(missing)
    )


def _package_scripts_gate_failure(workspace: str, payload: dict[str, Any]) -> str:
    paths = _payload_paths_for_contract_gate(payload)
    text = _payload_text(payload)
    if not (_path_set_contains(paths, "package.json") or _contract_mentions_package_scripts(text)):
        return ""
    from polaris.kernelone.quality import check_package_scripts

    result = check_package_scripts(workspace)
    if result.ok:
        return ""
    return f"package script gate failed: {result.detail}"


# RANK 1 (Reflexion / Actor-Critic): the critic's precise findings must reach the
# Keep findings compact when a canonical envelope routes repair to the Director.
_QA_FEEDBACK_MAX_FINDINGS = 5
_QA_FEEDBACK_MAX_CHARS = 600
_QA_FEEDBACK_COUNTERS_KEY = "feedback_counters"


def _normalize_feedback_counters(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    counters: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        counters[name] = max(0, count)
    return counters


def _qa_feedback_counters_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    return _normalize_feedback_counters(payload.get(_QA_FEEDBACK_COUNTERS_KEY))


def _qa_findings_are_actionable(findings: Any) -> bool:
    """True iff findings is a non-empty list with at least one non-blank entry."""
    return isinstance(findings, list) and any(str(item).strip() for item in findings)


def _format_qa_findings_feedback(findings: list[Any], verdict: str) -> str:
    """Render QA findings as an actionable, content-preserving repair directive."""
    lines = [str(item).strip() for item in findings if str(item).strip()][:_QA_FEEDBACK_MAX_FINDINGS]
    body = "\n".join(f"- {line}" for line in lines)
    message = (
        f"QA rejected this change ({verdict}). Fix these findings IN PLACE, preserving all "
        f"existing working code (do not rewrite the file from scratch):\n{body}"
    )
    return message[:_QA_FEEDBACK_MAX_CHARS]


def _format_qa_requeue_feedback(audit_result: dict[str, Any], verdict: str) -> str:
    findings = audit_result.get("findings", [])
    if _qa_findings_are_actionable(findings):
        return _format_qa_findings_feedback(findings, verdict)
    metrics = audit_result.get("metrics", {})
    if isinstance(metrics, dict) and metrics.get("missing_director_changed_files_evidence"):
        return (
            "QA requested Director retry: missing_director_changed_files evidence. "
            "Preserve the implementation and publish changed_files/director_changed_files metadata for QA."
        )
    reason = str(audit_result.get("reason") or audit_result.get("summary") or "").strip()
    if reason:
        return f"QA requested Director retry ({verdict}): {reason}"[:_QA_FEEDBACK_MAX_CHARS]
    return f"QA requested Director retry ({verdict})."[:_QA_FEEDBACK_MAX_CHARS]


def _failure_class_for_observed_verdict(verdict: str) -> str:
    """Map a local observation token to typed evidence, never to a transition."""

    failure_class, _ = classify_qa_audit_failure({"verdict": verdict})
    return failure_class


def _normalize_path_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, os.PathLike)):
        raw_values: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, (str, os.PathLike)):
            continue
        token = str(item).strip()
        if not token:
            continue
        key = token.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _truthy_payload_flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _allows_no_director_changes(payload: dict[str, Any]) -> bool:
    for key in _NO_CHANGE_FLAGS:
        if _truthy_payload_flag(payload, key):
            return True

    for key in ("execution_mode", "task_mode", "mode", "change_mode"):
        mode = str(payload.get(key) or "").strip().lower()
        if mode in _NO_CHANGE_MODES:
            return True
    return False


def _iter_director_evidence_mappings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mappings = [payload]
    for key in ("director_adapter", "execution_result", "director_result", "execution"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue
        mappings.append(nested)
        adapter_nested = nested.get("adapter_result")
        if isinstance(adapter_nested, dict):
            mappings.append(adapter_nested)
    return mappings


def _has_verified_existing_scope_evidence(payload: dict[str, Any]) -> bool:
    for mapping in _iter_director_evidence_mappings(payload):
        mode = str(mapping.get("materialization_mode") or "").strip().lower()
        if mode not in _VERIFIED_EXISTING_SCOPE_MODES:
            continue
        if mapping.get("success") is False:
            continue
        evidence = mapping.get("existing_contract_evidence")
        if isinstance(evidence, dict) and evidence.get("ok") is True:
            return True
    return False


def _collect_payload_paths(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for key in keys:
        paths.extend(_normalize_path_values(payload.get(key)))
    scope = payload.get("scope")
    if isinstance(scope, list) and "scope" in keys:
        for item in scope:
            if isinstance(item, dict):
                paths.extend(_normalize_path_values(item.get("path") or item.get("file")))
            else:
                paths.extend(_normalize_path_values(item))
    return _normalize_path_values(paths)


def _has_code_path(paths: list[str]) -> bool:
    for raw_path in paths:
        path = raw_path.replace("\\", "/").strip().lower().lstrip("./")
        if not path:
            continue
        if path.endswith(tuple(_CODE_FILE_EXTENSIONS)):
            return True
        if path.startswith(_CODE_PATH_PREFIXES):
            return True
    return False


def _is_code_task_payload(payload: dict[str, Any]) -> bool:
    if _has_code_path(_collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))):
        return True

    task_payload = payload.get("task")
    if isinstance(task_payload, dict) and _has_code_path(
        _collect_payload_paths(task_payload, ("target_files", "scope_paths", "scope"))
    ):
        return True

    text_fields = (
        payload.get("type"),
        payload.get("task_type"),
        payload.get("category"),
        payload.get("title"),
        payload.get("subject"),
        payload.get("goal"),
    )
    haystack = " ".join(str(value).lower() for value in text_fields if value)
    if "document" in haystack or "docs" in haystack:
        return False
    return any(token in haystack for token in ("code", "implement", "fix", "refactor", "test"))


def _requires_director_changed_files(payload: dict[str, Any]) -> bool:
    if _allows_no_director_changes(payload):
        return False
    if _has_verified_existing_scope_evidence(payload):
        return False
    if str(payload.get("blueprint_id") or "").strip():
        return True
    return _is_code_task_payload(payload)


def _extract_director_changed_files(payload: dict[str, Any]) -> list[str]:
    changed_files = _normalize_path_values(payload.get("changed_files"))
    if changed_files:
        return changed_files

    for key in ("director_changed_files", "files_changed"):
        changed_files = _normalize_path_values(payload.get(key))
        if changed_files:
            return changed_files

    for key in ("execution_result", "director_result", "execution"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            changed_files = _normalize_path_values(nested.get("changed_files"))
            if changed_files:
                return changed_files
    return []


def _extract_fallback_audit_files(payload: dict[str, Any]) -> list[str]:
    return _collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))


def _extract_verified_existing_scope_files(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for mapping in _iter_director_evidence_mappings(payload):
        evidence = mapping.get("existing_contract_evidence")
        if isinstance(evidence, dict):
            paths.extend(_normalize_path_values(evidence.get("existing_paths")))
    if paths:
        return _normalize_path_values(paths)
    return _extract_fallback_audit_files(payload)


def _first_failing_verify_clause(verify: str, *, cwd: str) -> str:
    """Clause-level teaching diagnosis — delegates to the KernelOne toolkit
    (single source of truth for the three verify touchpoints)."""
    from polaris.kernelone.quality.step_verify import first_failing_verify_clause

    return first_failing_verify_clause(verify, cwd=cwd)


class QAConsumer:
    """QA consumer for PENDING_QA tasks.

    This consumer polls the task market for tasks in the ``pending_qa`` stage,
    runs the QA audit, and acknowledges the task with ``resolved`` or
    ``rejected`` as the terminal status.

    Args:
        workspace: Workspace path for task market operations.
        worker_id: Unique identifier for this worker instance.
        visibility_timeout_seconds: How long a claimed task is locked before it
            becomes visible to other workers again on failure.
        poll_interval: Seconds to sleep between poll cycles when no task is found.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "qa_worker",
        visibility_timeout_seconds: int = 900,
        poll_interval: float = 5.0,
        enable_llm_audit: bool | None = None,
        wake_event: threading.Event | None = None,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("worker_id must be a non-empty string")
        self._visibility_timeout = int(visibility_timeout_seconds)
        self._stop_event = threading.Event()
        self._work_event = wake_event or threading.Event()
        self._svc = get_task_market_service()
        self._enable_llm_audit = (
            bool(enable_llm_audit)
            if enable_llm_audit is not None
            else _read_bool_env(_QA_LLM_AUDIT_ENABLED_ENV, default=False)
        )
        # Initialize QA service
        from polaris.cells.qa.audit_verdict.internal.qa_service import QAConfig

        qa_config = QAConfig(workspace=self._workspace, enable_auto_audit=False)
        self._qa_svc = QAService(qa_config)

    def _build_canonical_verdict_projection(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        gate_name: str = "",
        gate_summary: str = "",
        audit_result: dict[str, Any] | None = None,
        fallback_verdict: str,
        fallback_next_stage: str = "",
        fallback_terminal_status: str = "",
        barrier_receipt: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the sole authoritative verdict from barrier-read evidence."""

        try:
            from polaris.cells.qa.audit_verdict.internal.verdict_engine import (
                QAVerdictEngine,
                diff_verdicts,
            )

            canonical_payload = dict(payload)
            receipt = dict(barrier_receipt or {})
            if receipt.get("append_id"):
                canonical_payload["min_append_id"] = receipt["append_id"]
            if receipt.get("event_hash"):
                canonical_payload["min_event_hash"] = receipt["event_hash"]
            if not receipt:
                canonical_payload["min_append_id"] = "qa-evidence-append-receipt-missing"
            envelope = QAVerdictEngine(self._workspace).build_envelope(
                task_id=task_id,
                payload=canonical_payload,
                gate_name=gate_name,
                gate_summary=gate_summary,
                audit_result=audit_result,
            )
            diff = diff_verdicts(
                fallback_verdict=fallback_verdict,
                fallback_next_stage=fallback_next_stage,
                fallback_terminal_status=fallback_terminal_status,
                engine_envelope=envelope,
            )
            if diff.get("mismatch"):
                logger.info("QA local evidence differs from canonical verdict for task %s: %s", task_id, diff)
            return _qa_verdict_projection_payload(envelope.to_dict(), diff)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "qa.verdict_projection_error.v1",
                "authoritative": False,
                "error": str(exc),
                "verdict": "BLOCKED",
                "next_stage": "pending_qa",
                "terminal_status": "",
            }

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_QA tasks.

        Claims and processes all available tasks until no claimable work remains.
        Returns a list of processed task results, each containing ``task_id``,
        ``ok`` status, and (on failure) ``reason``.
        """
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            processed = self._claim_and_process_one()
            if processed is None:
                break
            results.append(processed)
        return results

    def run(self) -> None:
        """Run the consumer continuously until stop() is called."""
        logger.info("QA consumer running with event_wakeup — press Ctrl+C to stop")
        while not self._stop_event.is_set():
            self._work_event.clear()
            results = self.poll_once()
            if not results:
                retry_delay = self._svc.next_local_retry_delay(self._workspace, "pending_qa")
                self._work_event.wait(timeout=retry_delay)

    def stop(self) -> None:
        """Signal the consumer to stop after the current cycle."""
        self._stop_event.set()
        self._work_event.set()

    def _run_step_verify(self, payload: dict[str, Any]) -> str:
        """Run a CE-authorized verifier through the sandboxed execution broker.

        Returns '' when there is no step verify or it passes; otherwise a
        teaching failure message for the requeue. The verify command comes
        from the CE blueprint contract (ce-blueprint-tasks/1) and is the
        step's acceptance ground truth by design.
        """
        step = payload.get("construction_step")
        if not isinstance(step, dict):
            return ""
        from polaris.kernelone.quality.step_verify import normalize_step_verify

        verify = normalize_step_verify(step.get("verify"))
        if not verify:
            return ""
        try:
            authority = _step_verification_obligation(payload, verify)
            projection = _mapping_copy(payload.get("task_completion_projection"))
            command = authorize_project_verification_command(
                ResolveProjectVerificationAuthorityQueryV1(
                    workspace=self._workspace,
                    project_id=str(projection.get("project_id") or "").strip(),
                    run_id=str(projection.get("run_id") or "").strip(),
                    completion_contract_hash=str(projection.get("project_contract_hash") or "").strip(),
                    obligation_id=str(authority.get("obligation_id") or "").strip(),
                )
            )
            expected_authority = (
                str(authority.get("owner_task_id") or "").strip(),
                str(authority.get("modality") or "").strip(),
                tuple(str(item) for item in list(authority.get("argv") or [])),
                str(authority.get("cwd") or "").strip(),
                str(authority.get("command_authority_hash") or "").strip(),
            )
            actual_authority = (
                command.owner_task_id,
                command.modality,
                command.argv,
                command.cwd,
                command.command_authority_hash,
            )
            if actual_authority != expected_authority:
                raise ValueError("execution broker authority differs from CE task-local projection")
            result = run_project_verification(command)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            payload["qa_failed_verifier"] = {
                "schema_version": "qa.failed_verifier.v2",
                "command": verify[:_QA_FEEDBACK_MAX_CHARS],
                "failure_kind": "command_authority_rejected",
                "reason": str(exc)[:_QA_FEEDBACK_MAX_CHARS],
            }
            return f"step verify command lacks exact execution authority: {exc}"
        receipt = result.receipt
        if not isinstance(receipt, ProjectVerificationReceiptV1):
            payload["qa_failed_verifier"] = {
                "schema_version": "qa.failed_verifier.v2",
                "command": verify[:_QA_FEEDBACK_MAX_CHARS],
                "failure_kind": "execution_error",
                "reason": str(result.code)[:_QA_FEEDBACK_MAX_CHARS],
            }
            return f"step verify produced no authoritative receipt: {result.code}"
        current_receipt = query_project_verification_receipt(
            QueryProjectVerificationReceiptV1(
                workspace=command.workspace,
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
                owner_task_id=command.owner_task_id,
                modality=command.modality,
                argv=command.argv,
                cwd=command.cwd,
                command_authority_hash=command.command_authority_hash,
                input_artifacts=command.input_artifacts,
                timeout_seconds=command.timeout_seconds,
                job_token_id=command.job_token_id,
                job_token_set_hash=command.job_token_set_hash,
                execution_policy_hash=command.execution_policy_hash,
                authority_revision=command.authority_revision,
                policy_profile_id=command.policy_profile_id,
                policy_decision_hash=command.policy_decision_hash,
                executable_path=command.executable_path,
                executable_realpath=command.executable_realpath,
                executable_hash=command.executable_hash,
            )
        )
        if current_receipt != receipt:
            payload["qa_failed_verifier"] = {
                "schema_version": "qa.failed_verifier.v2",
                "command": verify[:_QA_FEEDBACK_MAX_CHARS],
                "failure_kind": "authority_changed_after_run",
                "reason": "execution authority or input closure changed before QA acceptance",
            }
            return "step verify receipt is no longer current under execution authority"
        receipt_projection = _project_verification_receipt_projection(receipt)
        if receipt.succeeded:
            payload.pop("qa_failed_verifier", None)
            payload["qa_verifier_receipt"] = receipt_projection
            return ""
        payload["qa_failed_verifier"] = {
            **receipt_projection,
            "schema_version": "qa.failed_verifier.v2",
            "command": shlex.join(receipt.argv),
            "failure_kind": "timeout" if receipt.timed_out else "proof_or_exit_failure",
        }
        return (
            f"step verify failed: obligation={receipt.obligation_id!r} "
            f"exit={receipt.exit_code!r} timed_out={receipt.timed_out} "
            f"receipt={receipt.receipt_ref}"
        )

    def _run_syntax_gate(self, payload: dict[str, Any]) -> str:
        """Return a precise, weak-model-fixable message when a declared/changed
        source file does NOT parse, else "" (I3-r18 fail-closed backstop).

        Candidates = the step's target_file ∪ Director changed_files ∪ fallback
        audit files, restricted to files that exist on disk. Fail-OPEN when no
        checker could run (the gate blocks only on PROVEN-broken files), so a
        no-node runner never blocks delivery on a file it cannot evaluate.
        """
        from polaris.kernelone.quality import first_syntax_failure
        from polaris.kernelone.quality.artifact_quality import _compress_node_syntax_error

        candidates: list[str] = []
        step = payload.get("construction_step")
        if isinstance(step, dict) and step.get("target_file"):
            candidates.append(str(step["target_file"]))
        candidates.extend(_extract_director_changed_files(payload))
        candidates.extend(_extract_fallback_audit_files(payload))

        seen: set[str] = set()
        existing: list[str] = []
        for raw in candidates:
            rel = str(raw or "").strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            if os.path.isfile(os.path.join(self._workspace, rel)):
                existing.append(rel)
        if not existing:
            return ""

        failure = first_syntax_failure(self._workspace, existing)
        if failure is None:
            return ""
        compact = _compress_node_syntax_error(failure.error, failure.path)
        return f"语法检查失败(node --check / py_compile),逐字修正后重试:\n{compact}"

    def _run_contract_gate(self, payload: dict[str, Any]) -> str:
        """Fail shallow verification artifacts before the generic QA audit.

        The generic file audit only knows whether the changed file is readable.
        Factory contracts, however, often require generated verification scripts
        and npm scripts to encode deterministic checks. Keep this gate narrow and
        explicit: it only fires when the payload names package/verify artifacts
        or explicitly mentions the deterministic check surface.
        """
        for checker in (_package_scripts_gate_failure, _verify_script_gate_failure):
            failure = checker(self._workspace, payload)
            if failure:
                return failure
        return ""

    def _append_qa_gate_to_run_ledger(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        gate_name: str,
        ok: bool,
        summary: str,
        verdict: str = "FAIL",
        audit_result: dict[str, Any] | None = None,
        failure_reason: str = "",
    ) -> dict[str, str] | None:
        """Append QA evidence and return its projection-barrier coordinates."""

        job_token = _extract_control_plane_job_token(payload)
        if not job_token:
            return None
        run_id = str(job_token.get("run_id") or payload.get("run_id") or payload.get("source_run_id") or "").strip()
        return commit_qa_evidence(
            workspace=self._workspace,
            run_id=run_id,
            task_id=task_id,
            gate_name=gate_name,
            ok=ok,
            summary=summary,
            verdict=verdict,
            audit_result=audit_result,
            failure_reason=failure_reason,
            job_token=job_token,
        ).to_dict()

    def _apply_canonical_verdict_transition(
        self,
        *,
        task_id: str,
        lease_token: str,
        payload: dict[str, Any],
        audit_result: dict[str, Any],
        engine_payload: dict[str, Any],
        evidence_commit_receipt: dict[str, str] | None,
        feedback_counters: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Commit a canonical verdict, then apply its authorized route."""

        verdict, next_stage, terminal_status = _canonical_route_from_projection(
            engine_payload=engine_payload,
        )
        job_token = _extract_control_plane_job_token(payload)
        run_id = str(job_token.get("run_id") or payload.get("run_id") or payload.get("source_run_id") or "").strip()
        final_receipt: dict[str, str] = {}
        try:
            if not run_id:
                raise ValueError("canonical QA verdict commit requires run_id")
            if not evidence_commit_receipt:
                raise ValueError("canonical QA verdict commit requires evidence barrier coordinates")
            final_receipt = commit_qa_verdict(
                workspace=self._workspace,
                run_id=run_id,
                task_id=task_id,
                envelope=engine_payload,
                evidence_commit_receipt=evidence_commit_receipt,
                job_token=job_token,
            ).to_dict()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failure_metadata = _qa_control_plane_metadata(
                payload,
                {
                    "qa_verdict_projection": engine_payload,
                    "qa_verdict_commit_failed": True,
                    "qa_verdict_commit_error": str(exc),
                },
            )
            transition = self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_verdict_commit_failed",
                    error_message=str(exc),
                    requeue_stage="pending_qa",
                    failure_disposition="same_task_local_retry",
                    metadata=failure_metadata,
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "verdict": "BLOCKED",
                "status": str(transition.status or "pending_qa"),
                "reason": "qa_verdict_commit_failed",
            }

        metadata: dict[str, Any] = {
            "qa_next_stage": next_stage,
            "qa_terminal_status": terminal_status,
            "qa_verdict_projection": engine_payload,
            "qa_verdict_commit_receipt": final_receipt,
        }
        if feedback_counters:
            metadata[_QA_FEEDBACK_COUNTERS_KEY] = dict(feedback_counters)
        metadata = _qa_control_plane_metadata(payload, metadata)

        classification = _mapping_copy(engine_payload.get("classification"))
        failed_verifier = _mapping_copy(payload.get("qa_failed_verifier"))
        if next_stage == "pending_exec" and failed_verifier and not (
            str(failed_verifier.get("receipt_hash") or "").strip()
            and str(failed_verifier.get("receipt_ref") or "").strip()
        ):
            # A missing physical receipt is an execution/control-plane outage,
            # not proof that the immutable project contract is contradictory.
            # Keep the task at QA with durable bounded backoff; never send the
            # Director an unauthorised shell command and never poison the task
            # as an isolated contract blocker.
            metadata["qa_verifier_control_plane_diagnostic"] = {
                "schema_version": "qa.verifier-control-plane-diagnostic.v1",
                "task_id": task_id,
                "failure_kind": str(failed_verifier.get("failure_kind") or "receipt_unavailable"),
                "reason": str(failed_verifier.get("reason") or "")[:_QA_FEEDBACK_MAX_CHARS],
                "task_completion_projection_hash": str(
                    _mapping_copy(payload.get("task_completion_projection")).get("projection_hash") or ""
                ).strip(),
                "retry_owner": "qa",
            }
            transition = self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_VERIFIER_CONTROL_PLANE_UNAVAILABLE",
                    error_message="QA verifier authority produced no execution-broker receipt",
                    requeue_stage="pending_qa",
                    failure_disposition="same_task_local_retry",
                    metadata=metadata,
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "verdict": verdict,
                "status": str(transition.status or "rejected"),
                "reason": "qa_verifier_control_plane_unavailable",
            }
        if next_stage == "pending_exec":
            counters = _normalize_feedback_counters(metadata.get(_QA_FEEDBACK_COUNTERS_KEY))
            counters["qa_local_repair_rounds"] = counters.get("qa_local_repair_rounds", 0) + 1
            metadata[_QA_FEEDBACK_COUNTERS_KEY] = counters
            metadata["qa_local_repair_context"] = _qa_local_repair_context(
                task_id=task_id,
                payload=payload,
                audit_result=audit_result,
                engine_payload=engine_payload,
                verdict_receipt=final_receipt,
            )

        # QA never sends a failed implementation back to PM/CE. A true
        # contract/authority contradiction terminates only this task with
        # structured evidence; changing the contract requires an explicit
        # operator-authored command outside this automatic loop.
        if next_stage in {"pending_design", "waiting_human"}:
            metadata["structured_blocker"] = _qa_contract_authority_blocker(
                task_id=task_id,
                payload=payload,
                engine_payload=engine_payload,
            )
            transition = self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_CONTRACT_AUTHORITY_BLOCKED",
                    error_message=str(classification.get("reason") or "QA contract authority blocked"),
                    requeue_stage=None,
                    failure_disposition="isolated_contract_blocker",
                    metadata=metadata,
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "verdict": verdict,
                "status": str(transition.status or "rejected"),
                "reason": "qa_contract_authority_blocked",
            }

        if next_stage in {"pending_exec", "pending_qa"}:
            transition = self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code=f"QA_{verdict}_canonical_route",
                    error_message=_format_qa_requeue_feedback(audit_result, verdict),
                    requeue_stage=next_stage,
                    failure_disposition="same_task_local_retry",
                    metadata=metadata,
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "verdict": verdict,
                "status": str(transition.status or next_stage),
                "reason": "canonical_qa_route",
            }

        command_kwargs: dict[str, Any] = {
            "workspace": self._workspace,
            "task_id": task_id,
            "lease_token": lease_token,
            "summary": f"Canonical QA verdict: {verdict}",
            "metadata": {
                **metadata,
                "verdict": verdict,
                "audit_id": str(audit_result.get("audit_id") or ""),
                "findings": list(audit_result.get("findings") or []),
                "metrics": dict(audit_result.get("metrics") or {}),
            },
        }
        if terminal_status in {"resolved", "rejected"}:
            command_kwargs["terminal_status"] = terminal_status
        else:
            transition = self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_canonical_route_missing",
                    error_message="Canonical QA envelope did not authorize a valid transition",
                    requeue_stage="pending_qa",
                    failure_disposition="same_task_local_retry",
                    metadata=metadata,
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "verdict": "BLOCKED",
                "status": str(transition.status or "pending_qa"),
                "reason": "canonical_route_missing",
            }

        transition = self._svc.acknowledge_task_stage(AcknowledgeTaskStageCommandV1(**command_kwargs))
        return {
            "task_id": task_id,
            "ok": bool(transition.ok) and verdict == "PASS" and terminal_status == "resolved",
            "verdict": verdict,
            "status": str(transition.status or terminal_status or next_stage),
            "reason": "canonical_qa_route",
        }

    def _claim_and_process_one(self) -> dict[str, Any] | None:
        """Attempt to claim one PENDING_QA task and process it.

        Returns:
            Processed result dict, or None if no claimable task was found.
        """
        claim = self._svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self._workspace,
                stage="pending_qa",
                worker_id=self._worker_id,
                worker_role="qa",
                visibility_timeout_seconds=self._visibility_timeout,
            )
        )
        if not claim.ok:
            return None

        task_id = str(claim.task_id or "").strip()
        lease_token = str(claim.lease_token or "").strip()
        payload: dict[str, Any] = {}

        try:
            payload = dict(claim.payload) if claim.payload else {}

            # Fission steps carry a machine-executable verify — run it FIRST.
            # The generic audit is blind to the step contract (live I3-r9: a
            # step whose verify starts with `test -f ./readme.md` passed QA
            # with score 10 while readme.md did not exist). A verify failure
            # requeues to pending_exec so the Director can correct course.
            verify_failure = self._run_step_verify(payload)
            if verify_failure:
                audit_result = {
                    "verdict": "FAIL",
                    "findings": [verify_failure],
                    "failure_class": FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
                    "responsible_layer": "director",
                }
                receipt = self._append_qa_gate_to_run_ledger(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_step_verify",
                    ok=False,
                    summary=verify_failure,
                    verdict="FAIL",
                    audit_result=audit_result,
                    failure_reason="step_verify_failed",
                )
                engine = self._build_canonical_verdict_projection(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_step_verify",
                    gate_summary=verify_failure,
                    audit_result=audit_result,
                    fallback_verdict="FAIL",
                    barrier_receipt=receipt,
                )
                return self._apply_canonical_verdict_transition(
                    task_id=task_id,
                    lease_token=lease_token,
                    payload=payload,
                    audit_result=audit_result,
                    engine_payload=engine,
                    evidence_commit_receipt=receipt,
                )

            # I3-r18 fail-closed syntax gate: a grep-based step verify can PASS on
            # a file that does not parse (r18: main.js with a stray ';' inside an
            # object literal satisfied every grep clause but `node --check` failed),
            # shipping a non-running product. Reject a DEFINITELY non-parsing target
            # so the Director repairs it via the corrective re-ask ladder. Fail-OPEN
            # only when no checker could run (node absent / unknown ext / timeout).
            syntax_failure = self._run_syntax_gate(payload)
            if syntax_failure:
                audit_result = {
                    "verdict": "FAIL",
                    "findings": [syntax_failure],
                    "failure_class": FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
                    "responsible_layer": "director",
                }
                receipt = self._append_qa_gate_to_run_ledger(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_syntax",
                    ok=False,
                    summary=syntax_failure,
                    verdict="FAIL",
                    audit_result=audit_result,
                    failure_reason="syntax_failed",
                )
                engine = self._build_canonical_verdict_projection(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_syntax",
                    gate_summary=syntax_failure,
                    audit_result=audit_result,
                    fallback_verdict="FAIL",
                    barrier_receipt=receipt,
                )
                return self._apply_canonical_verdict_transition(
                    task_id=task_id,
                    lease_token=lease_token,
                    payload=payload,
                    audit_result=audit_result,
                    engine_payload=engine,
                    evidence_commit_receipt=receipt,
                )

            # Factory artifacts can be syntactically valid yet hollow (r16:
            # scripts/verify.js checked package name/version while the contract
            # required ts_syntax/package_scripts/min_files/content_any; package
            # test was `echo "No tests yet"`). Reject these narrow, explicit
            # contract misses before the LLM reviewer can rubber-stamp them.
            contract_failure = self._run_contract_gate(payload)
            if contract_failure:
                audit_result = {
                    "verdict": "FAIL",
                    "findings": [contract_failure],
                    "failure_class": FailureClassV1.IMPLEMENTATION_DEFECT.value,
                    "responsible_layer": "director",
                }
                receipt = self._append_qa_gate_to_run_ledger(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_contract",
                    ok=False,
                    summary=contract_failure,
                    verdict="FAIL",
                    audit_result=audit_result,
                    failure_reason="contract_gate_failed",
                )
                engine = self._build_canonical_verdict_projection(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_contract",
                    gate_summary=contract_failure,
                    audit_result=audit_result,
                    fallback_verdict="FAIL",
                    barrier_receipt=receipt,
                )
                return self._apply_canonical_verdict_transition(
                    task_id=task_id,
                    lease_token=lease_token,
                    payload=payload,
                    audit_result=audit_result,
                    engine_payload=engine,
                    evidence_commit_receipt=receipt,
                )

            # Run QA audit
            audit_result = self._run_qa_audit(task_id, payload)

            feedback_counters = _qa_feedback_counters_from_payload(payload)
            observed_verdict = str(audit_result.get("verdict") or "FAIL").strip().upper() or "FAIL"
            if not str(audit_result.get("failure_class") or "").strip():
                failure_class, responsible_layer = classify_qa_audit_failure(audit_result)
                audit_result["failure_class"] = failure_class
                audit_result["responsible_layer"] = responsible_layer
            receipt = self._append_qa_gate_to_run_ledger(
                task_id=task_id,
                payload=payload,
                gate_name="qa_evidence",
                ok=observed_verdict == "PASS",
                summary=f"QA evidence observed: {observed_verdict}",
                verdict=observed_verdict,
                audit_result=audit_result,
            )
            engine = self._build_canonical_verdict_projection(
                task_id=task_id,
                payload=payload,
                audit_result=audit_result,
                fallback_verdict=observed_verdict,
                barrier_receipt=receipt,
            )
            return self._apply_canonical_verdict_transition(
                task_id=task_id,
                lease_token=lease_token,
                payload=payload,
                audit_result=audit_result,
                engine_payload=engine,
                evidence_commit_receipt=receipt,
                feedback_counters=feedback_counters,
            )

        except Exception as exc:
            logger.exception("QA consumer failed for task %s: %s", task_id, exc)
            try:
                self._append_qa_gate_to_run_ledger(
                    task_id=task_id,
                    payload=payload,
                    gate_name="qa_evidence_exception",
                    ok=False,
                    summary=str(exc),
                    verdict="FAIL",
                    failure_reason="qa_consumer_exception",
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.exception("QA consumer could not append failure evidence for task %s", task_id)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_audit_failed",
                    error_message=str(exc),
                    requeue_stage="pending_qa",
                    failure_disposition="same_task_local_retry",
                    metadata=_qa_control_plane_metadata(payload),
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": str(exc),
            }

    def _build_qa_llm_review_message(
        self,
        *,
        task_id: str,
        task_subject: str,
        changed_files: list[str],
        audit_result: dict[str, Any],
        qa_context: str = "",
    ) -> str:
        file_sections: list[str] = []
        workspace_root = os.path.abspath(self._workspace)
        for rel_path in changed_files[:4]:
            rel = str(rel_path or "").strip()
            if not rel:
                continue
            abs_path = os.path.abspath(os.path.join(workspace_root, rel))
            try:
                if os.path.commonpath([workspace_root, abs_path]) != workspace_root:
                    continue
            except ValueError:
                continue
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, encoding="utf-8") as handle:
                    content = handle.read(2400)
            except (OSError, UnicodeDecodeError):
                continue
            file_sections.append(
                f"FILE {rel}\n----- BEGIN UTF-8 EXCERPT -----\n{content}\n----- END UTF-8 EXCERPT -----"
            )

        qa_context_section = ""
        if qa_context.strip():
            qa_context_section = (
                "\n\nPM / Chief Engineer / workspace quality context:\n"
                "----- BEGIN QA CONTEXT -----\n"
                f"{qa_context.strip()}\n"
                "----- END QA CONTEXT -----\n"
            )

        return (
            "你是 Polaris QA。请对当前任务产物做一次独立质量审阅。\n"
            "本次审计禁止调用工具；没有工具可用。只输出 JSON 对象，不要 Markdown，不要解释。格式:\n"
            '{"verdict":"PASS|FAIL|NEEDS_REVIEW","findings":["..."],"summary":"..."}\n\n'
            f"task_id: {task_id}\n"
            f"task_subject: {task_subject}\n"
            f"deterministic_audit: {audit_result}\n"
            f"changed_files: {changed_files}"
            f"{qa_context_section}\n"
            "文件摘录:\n" + ("\n\n".join(file_sections) if file_sections else "(no file excerpts)")
        )

    async def _run_qa_llm_review_async(
        self,
        *,
        task_id: str,
        task_subject: str,
        changed_files: list[str],
        audit_result: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._enable_llm_audit:
            return {"enabled": False}

        run_id = str(payload.get("run_id") or payload.get("source_run_id") or "").strip()
        qa_context = _extract_qa_prompt_context(payload)
        message = self._build_qa_llm_review_message(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            audit_result=audit_result,
            qa_context=qa_context,
        )
        try:
            import asyncio

            from polaris.cells.llm.dialogue.public.contracts import InvokeRoleDialogueCommandV1
            from polaris.cells.llm.dialogue.public.service import LlmDialogueService
            from polaris.kernelone.llm.engine.normalizer import ResponseNormalizer

            response = await asyncio.wait_for(
                LlmDialogueService(settings=None).invoke_role_dialogue(
                    InvokeRoleDialogueCommandV1(
                        workspace=self._workspace,
                        role="qa",
                        message=message,
                        context={
                            "run_id": run_id,
                            "task_id": task_id,
                            "domain": "task_market_qa_audit",
                            "disable_internal_tool_rounds": True,
                            "_transaction_kernel_forced_tool_definitions": [],
                            "_transaction_kernel_forced_tool_choice": "none",
                            "llm_max_tokens": 8192,
                        },
                        metadata={
                            "run_id": run_id,
                            "task_id": task_id,
                            "source": "qa.audit_verdict.task_market",
                            "qa_llm_audit": True,
                            "validate_output": False,
                            "max_retries": 0,
                            "prompt_appendix": "No tool calls are available or allowed. Return only the requested JSON object.",
                        },
                    )
                ),
                timeout=_qa_llm_audit_timeout_seconds(),
            )
            content = str(getattr(response, "content", "") or "")
            parsed = ResponseNormalizer.extract_json_object(content) or {}
            verdict = str(parsed.get("verdict") or "").strip().upper()
            raw_findings = parsed.get("findings")
            if isinstance(raw_findings, list):
                findings = [str(item).strip() for item in raw_findings if str(item).strip()]
            elif isinstance(raw_findings, str) and raw_findings.strip():
                findings = [raw_findings.strip()]
            else:
                findings = []
            valid_verdict = verdict in {"PASS", "FAIL", "NEEDS_REVIEW"}
            review_ok = bool(getattr(response, "ok", False)) and bool(parsed) and valid_verdict
            if bool(getattr(response, "ok", False)) and not review_ok:
                findings = [*findings, "QA LLM response did not contain a valid verdict JSON object"]
            return {
                "enabled": True,
                "ok": review_ok,
                "status": str(getattr(response, "status", "") or ""),
                "verdict": verdict,
                "findings": findings[:8],
                "summary": str(parsed.get("summary") or "").strip(),
                "parse_ok": bool(parsed),
                "content_preview": content[:800],
                "metadata": dict(getattr(response, "metadata", {}) or {}),
                "error_code": getattr(response, "error_code", None),
                "error_message": getattr(response, "error_message", None),
            }
        except (ImportError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            return {
                "enabled": True,
                "ok": False,
                "verdict": "FAIL",
                "findings": [f"QA LLM audit failed: {exc}"],
                "summary": "QA LLM audit failed",
                "parse_ok": False,
                "error_message": str(exc),
            }

    async def _run_qa_audit_async(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run QA audit asynchronously and return result dict.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """

        # Extract Director execution evidence from payload.  Ack metadata from
        # Director is merged into task_market payload before QA claims it.
        changed_files: list[str] = []
        require_director_evidence = False
        if isinstance(payload, dict):
            changed_files = _extract_director_changed_files(payload)
            if not changed_files and _has_verified_existing_scope_evidence(payload):
                changed_files = _extract_verified_existing_scope_files(payload)
            require_director_evidence = _requires_director_changed_files(payload)
            if not changed_files and not require_director_evidence:
                changed_files = _extract_fallback_audit_files(payload)

        task_subject = str(payload.get("title", payload.get("subject", task_id)))

        # Run audit
        audit = await self._qa_svc.audit_task(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            require_changed_files=require_director_evidence,
        )

        # Convert to result dict
        findings = []
        for issue in audit.issues:
            findings.append(
                f"[{issue.get('severity', 'info')}] {issue.get('file', 'unknown')}: {issue.get('message', '')}"
            )

        result: dict[str, Any] = {
            "audit_id": audit.audit_id,
            "verdict": audit.verdict,
            "findings": findings,
            "metrics": dict(audit.metrics),
            "score": audit.metrics.get("files_audited", 0) * 10 if audit.verdict == "PASS" else 0.0,
        }
        llm_review = await self._run_qa_llm_review_async(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            audit_result=result,
            payload=payload,
        )
        if llm_review.get("enabled"):
            result["llm_review"] = llm_review
            llm_findings = [f"[llm] {item}" for item in llm_review.get("findings", []) if str(item or "").strip()]
            if not bool(llm_review.get("ok", False)):
                if str(result.get("verdict") or "").strip().upper() == "PASS":
                    result["llm_review_warning"] = llm_findings or ["[llm] QA LLM audit was inconclusive"]
                else:
                    result["verdict"] = "FAIL"
                    result["score"] = 0.0
                    result["findings"] = [*findings, *llm_findings] or ["[llm] QA LLM audit failed"]
            elif str(llm_review.get("verdict") or "").strip().upper() in {"FAIL", "NEEDS_REVIEW"}:
                if str(result.get("verdict") or "").strip().upper() == "PASS":
                    result["llm_review_warning"] = llm_findings or ["[llm] QA LLM requested review"]
                else:
                    result["verdict"] = str(llm_review.get("verdict") or "FAIL").strip().upper()
                    result["score"] = 0.0
                    result["findings"] = [*findings, *llm_findings] or ["[llm] QA LLM requested review"]
        return result

    def _run_qa_audit(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for QA audit.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self._run_qa_audit_async(task_id, payload))
