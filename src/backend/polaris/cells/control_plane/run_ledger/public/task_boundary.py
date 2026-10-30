"""Task-boundary verdict contracts for execution control-plane projections."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

import tomllib
from polaris.cells.control_plane.run_ledger.public.failure_evidence import FailureClassV1, normalize_failure_class

_LOCAL_ENTRYPOINT_SUFFIXES = (
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".go",
    ".rs",
    ".html",
)
_ENTRYPOINT_PATTERN_CHARS = frozenset("*?[]{}")
_CONCRETE_LOCAL_ENTRYPOINT_RE = re.compile(r"^[A-Za-z0-9_@.+-]+(?:/[A-Za-z0-9_@.+-]+)*$")
_GENERATED_ENTRYPOINT_PREFIXES = (
    ".next/",
    ".nuxt/",
    "build/",
    "coverage/",
    "dist/",
    "out/",
)
_LOCAL_SOURCE_IMPORT_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
# NodeNext/ESM TypeScript requires importing the emitted ".js" specifier even
# when the on-disk source is ".ts"; accept the TypeScript sibling sources that
# tsc would compile into the referenced JavaScript file before declaring the
# local import unresolved.
_TYPESCRIPT_SOURCE_SIBLINGS_BY_EMITTED_SUFFIX: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx"),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}


class TaskBoundaryFailureClassV1(str, Enum):
    """Task-boundary failure classes owned by the run-ledger public contract."""

    PASSED = "PASSED"
    INCOMPLETE_MATERIALIZATION = "INCOMPLETE_MATERIALIZATION"
    MISSING_ENTRYPOINT_TARGET = "MISSING_ENTRYPOINT_TARGET"
    UNRESOLVED_LOCAL_IMPORT = "UNRESOLVED_LOCAL_IMPORT"
    EXECUTION_EVIDENCE_MISSING = "EXECUTION_EVIDENCE_MISSING"
    REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED = "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    NO_MATERIALIZED_EFFECT = "NO_MATERIALIZED_EFFECT"
    COMPILER_OR_TEST_FAILURE = "COMPILER_OR_TEST_FAILURE"
    IMPLEMENTATION_DEFECT = "IMPLEMENTATION_DEFECT"
    DEPENDENCY_NOT_UNLOCKED = "DEPENDENCY_NOT_UNLOCKED"
    DEFERRED_FOLLOWUP_REQUIRED = "DEFERRED_FOLLOWUP_REQUIRED"
    TASKBOARD_DEADLOCK = "TASKBOARD_DEADLOCK"
    TASK_BOUNDARY_FAILED = "TASK_BOUNDARY_FAILED"
    TASK_BOUNDARY_UNKNOWN = "TASK_BOUNDARY_UNKNOWN"


_ESM_LOCAL_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import\s+(?:[^'\"\n]+?\s+from\s*)?|export\s+[^'\"\n]+?\s+from\s*)"
    r"['\"](?P<specifier>\.{1,2}/[^'\"\n]+)['\"]"
)
_CJS_LOCAL_REQUIRE_RE = re.compile(
    r"(?m)^\s*(?:(?:const|let|var)\s+[^=\n]+=\s*)?require\(\s*['\"]"
    r"(?P<specifier>\.{1,2}/[^'\"\n]+)['\"]\s*\)"
)
_TEST_FRAMEWORK_IMPORT_RE = re.compile(
    r"""(?mx)
    (?:from\s+['"](?:vitest|jest|@jest/globals|node:test)['"])
    |
    (?:require\(\s*['"](?:vitest|jest|@jest/globals|node:test)['"]\s*\))
    """
)
_TEST_STRUCTURE_RE = re.compile(r"(?m)\b(?:describe|it|test|expect)\s*\(")
_TEST_LIKE_PATH_RE = re.compile(r"(^|/)(?:tests?|specs?|__tests__)/|(?:\.test|\.spec)(?:\.|$)|(?:^|/)test_")
_TASK_BOUNDARY_FAILURE_CLASS_ALIASES = {
    "passed": TaskBoundaryFailureClassV1.PASSED.value,
    "incomplete_materialization": TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value,
    "missing_entrypoint_target": TaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
    "unresolved_local_import": TaskBoundaryFailureClassV1.UNRESOLVED_LOCAL_IMPORT.value,
    "execution_evidence_missing": TaskBoundaryFailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
    "required_tool_text_fallback_not_dispatched": (
        TaskBoundaryFailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value
    ),
    "no_materialized_effect": TaskBoundaryFailureClassV1.NO_MATERIALIZED_EFFECT.value,
    "compiler_or_test_failure": TaskBoundaryFailureClassV1.COMPILER_OR_TEST_FAILURE.value,
    "implementation_defect": TaskBoundaryFailureClassV1.IMPLEMENTATION_DEFECT.value,
    "dependency_not_unlocked": TaskBoundaryFailureClassV1.DEPENDENCY_NOT_UNLOCKED.value,
    "deferred_followup_required": TaskBoundaryFailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value,
    "taskboard_deadlock": TaskBoundaryFailureClassV1.TASKBOARD_DEADLOCK.value,
    "task_boundary_failed": TaskBoundaryFailureClassV1.TASK_BOUNDARY_FAILED.value,
    "task_boundary_unknown": TaskBoundaryFailureClassV1.TASK_BOUNDARY_UNKNOWN.value,
    FailureClassV1.TOOL_DISPATCH_DROPPED.value.casefold(): FailureClassV1.TOOL_DISPATCH_DROPPED.value,
}


def _task_boundary_failure_class_key(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _normalize_task_boundary_failure_class(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return TaskBoundaryFailureClassV1.TASK_BOUNDARY_UNKNOWN.value
    run_ledger_token = normalize_failure_class(raw)
    return _TASK_BOUNDARY_FAILURE_CLASS_ALIASES.get(
        _task_boundary_failure_class_key(run_ledger_token),
        _TASK_BOUNDARY_FAILURE_CLASS_ALIASES.get(_task_boundary_failure_class_key(raw), run_ledger_token.upper()),
    )


def _clean_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    return token.strip("/")


def _is_concrete_local_entrypoint_target(value: Any) -> bool:
    token = _clean_path(value)
    if not token or token.startswith("node_modules/"):
        return False
    if _is_generated_entrypoint_artifact(token):
        return False
    if any(char in token for char in _ENTRYPOINT_PATTERN_CHARS):
        return False
    # Package scripts can contain arbitrary JavaScript/shell expressions.  A
    # token merely ending in ``.js`` is not necessarily a path (live defect:
    # ``import('node:fs').then(...'dist/index.html'))``).  Admit only a safe,
    # relative path grammar here; executable code, operators, quotes,
    # parentheses, variables, and redirections are not entrypoint targets.
    return bool(_CONCRETE_LOCAL_ENTRYPOINT_RE.fullmatch(token))


def _is_generated_entrypoint_artifact(value: Any) -> bool:
    """Return true for build/verifier outputs that are not source obligations."""

    token = _clean_path(value).lower()
    return bool(token and token.startswith(_GENERATED_ENTRYPOINT_PREFIXES))


def _typescript_source_sibling_for_entrypoint(entrypoint: str) -> tuple[str, ...]:
    token = _clean_path(entrypoint)
    if not token.endswith(".js"):
        return ()
    stem = token[:-3]
    return (f"{stem}.ts", f"{stem}.tsx")


def _entrypoint_is_satisfied_by_typescript_source(workspace: Path, entrypoint: str, known_artifacts: set[str]) -> bool:
    """Return true when a missing JS entrypoint is represented by TS source.

    Browser HTML often points at an emitted JavaScript module while Director is
    working on the TypeScript source module in the same task. TaskBoundary must
    not classify that as a PM/CE scope gap; artifact quality and verifier gates
    still remain responsible for ensuring the HTML points at a runnable output.
    """

    for candidate in _typescript_source_sibling_for_entrypoint(entrypoint):
        if _path_exists(workspace, candidate) or candidate in known_artifacts:
            return True
    return False


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    rows: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = _clean_path(item)
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _path_exists(workspace: Path, relative_path: str) -> bool:
    token = _clean_path(relative_path)
    if not token:
        return False
    try:
        resolved = (workspace / token).resolve()
        root = workspace.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return resolved.exists()


def _package_json_entrypoints(workspace: Path) -> list[str]:
    manifest = workspace / "package.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    candidates: list[str] = []
    for key in ("main", "module", "browser"):
        value = data.get(key)
        if isinstance(value, str):
            candidates.append(value)
    bin_value = data.get("bin")
    if isinstance(bin_value, str):
        candidates.append(bin_value)
    elif isinstance(bin_value, dict):
        candidates.extend(str(value) for value in bin_value.values() if isinstance(value, str))

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for script in scripts.values():
            if not isinstance(script, str):
                continue
            try:
                parts = shlex.split(script)
            except ValueError:
                parts = script.split()
            for part in parts:
                token = part.strip().strip("'\"")
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", token):
                    continue
                if token.startswith("-"):
                    continue
                if token.endswith(_LOCAL_ENTRYPOINT_SUFFIXES) or ("/" in token and "." in Path(token).name):
                    candidates.append(token)

    rows: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = _clean_path(candidate)
        if not token or token.startswith("node_modules/"):
            continue
        if token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _html_script_entrypoints(workspace: Path) -> list[str]:
    candidates: list[str] = []
    for html_path in workspace.rglob("*.html"):
        relative = _clean_path(html_path.relative_to(workspace))
        if relative.startswith((".polaris/", "runtime/", "node_modules/")):
            continue
        try:
            content = html_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", content, re.IGNORECASE):
            src = _clean_path(match.group(1).split("#", 1)[0].split("?", 1)[0])
            if not src or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", src):
                continue
            candidates.append(posixpath.normpath(_clean_path(html_path.parent.relative_to(workspace) / src)))
    return _dedupe_paths(candidates)


def _go_entrypoints(workspace: Path) -> list[str]:
    if not (workspace / "go.mod").is_file():
        return []
    if (workspace / "main.go").is_file() or any(workspace.glob("cmd/*/main.go")):
        return []
    return ["main.go"]


def _pyproject_entrypoints(workspace: Path) -> list[str]:
    manifest = workspace / "pyproject.toml"
    if not manifest.is_file():
        return []
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return []
    candidates: list[str] = []
    for value in scripts.values():
        if not isinstance(value, str):
            continue
        module = value.split(":", 1)[0].strip().replace(".", "/")
        if module:
            candidates.append(f"{module}.py")
    return _dedupe_paths(candidates)


def _dedupe_paths(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _clean_path(value)
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _workspace_entrypoint_targets(workspace: Path) -> list[str]:
    return _dedupe_paths(
        [
            entrypoint
            for entrypoint in (
                *_package_json_entrypoints(workspace),
                *_html_script_entrypoints(workspace),
                *_go_entrypoints(workspace),
                *_pyproject_entrypoints(workspace),
            )
            if _is_concrete_local_entrypoint_target(entrypoint)
        ]
    )


def _is_local_source_file(path: str) -> bool:
    return _clean_path(path).endswith(_LOCAL_SOURCE_IMPORT_SUFFIXES)


def _is_test_like_source_path(path: str) -> bool:
    return bool(_TEST_LIKE_PATH_RE.search(_clean_path(path).lower()))


def _artifact_semantic_mismatches(*, workspace: Path, source_paths: tuple[str, ...]) -> tuple[str, ...]:
    findings: list[str] = []
    for source_path in source_paths:
        normalized_source = _clean_path(source_path)
        if (
            not _is_local_source_file(normalized_source)
            or _is_test_like_source_path(normalized_source)
            or not _path_exists(workspace, normalized_source)
        ):
            continue
        try:
            text = (workspace / normalized_source).read_text(encoding="utf-8")
        except OSError:
            continue
        if not _TEST_FRAMEWORK_IMPORT_RE.search(text):
            continue
        test_call_count = len(_TEST_STRUCTURE_RE.findall(text))
        if test_call_count < 2:
            continue
        finding = f"{normalized_source}: non-test source contains test framework structure"
        if finding not in findings:
            findings.append(finding)
    return tuple(findings)


def _local_import_specifiers(text: str) -> list[str]:
    specifiers: list[str] = []
    for pattern in (_ESM_LOCAL_IMPORT_RE, _CJS_LOCAL_REQUIRE_RE):
        for match in pattern.finditer(text):
            specifier = str(match.group("specifier") or "").strip()
            if specifier and specifier not in specifiers:
                specifiers.append(specifier)
    return specifiers


def _local_import_target_candidates(importer_path: str, specifier: str) -> list[str]:
    importer_parent = PurePosixPath(_clean_path(importer_path)).parent
    target = posixpath.normpath((importer_parent / specifier).as_posix()).lstrip("./")
    if not target or target.startswith("../") or "/../" in target:
        return []
    suffix = PurePosixPath(target).suffix
    if suffix:
        stem = target[: len(target) - len(suffix)]
        return _dedupe_paths(
            [
                target,
                *(
                    f"{stem}{sibling_suffix}"
                    for sibling_suffix in _TYPESCRIPT_SOURCE_SIBLINGS_BY_EMITTED_SUFFIX.get(suffix, ())
                ),
            ]
        )
    candidates = [f"{target}{ext}" for ext in _LOCAL_SOURCE_IMPORT_SUFFIXES]
    candidates.extend(f"{target}/index{ext}" for ext in _LOCAL_SOURCE_IMPORT_SUFFIXES)
    return _dedupe_paths(candidates)


def _unresolved_local_imports(
    *,
    workspace: Path,
    source_paths: tuple[str, ...],
    known_artifacts: set[str],
) -> tuple[str, ...]:
    unresolved: list[str] = []
    for source_path in source_paths:
        normalized_source = _clean_path(source_path)
        if not _is_local_source_file(normalized_source) or not _path_exists(workspace, normalized_source):
            continue
        try:
            text = (workspace / normalized_source).read_text(encoding="utf-8")
        except OSError:
            continue
        for specifier in _local_import_specifiers(text):
            candidates = _local_import_target_candidates(normalized_source, specifier)
            if not candidates:
                continue
            if any(_path_exists(workspace, candidate) or candidate in known_artifacts for candidate in candidates):
                continue
            preferred = candidates[0]
            finding = f"{normalized_source} -> {specifier} ({preferred})"
            if finding not in unresolved:
                unresolved.append(finding)
    return tuple(unresolved)


def _required_items_not_satisfied(
    *,
    required: tuple[str, ...],
    present: tuple[str, ...],
    failed: tuple[str, ...],
    explicit_missing: tuple[str, ...],
) -> tuple[str, ...]:
    """Return required obligations with no present or failed evidence."""

    observed = set(present) | set(failed)
    missing: list[str] = [item for item in required if item not in observed]
    for item in explicit_missing:
        if item not in observed and item not in missing:
            missing.append(item)
    return tuple(missing)


@dataclass(frozen=True)
class TaskBoundaryVerdictV1:
    """Canonical task-boundary completion verdict.

    The verdict is intentionally platform-level: it explains whether one task
    can be considered materially complete before QA tries to classify product
    behavior. It is not a repair plan and does not mutate TaskBoard state.
    """

    task_id: str
    status: str
    ok: bool
    failure_class: str
    responsible_layer: str
    reason: str
    run_id: str = ""
    target_files: tuple[str, ...] = field(default_factory=tuple)
    missing_target_files: tuple[str, ...] = field(default_factory=tuple)
    missing_entrypoint_targets: tuple[str, ...] = field(default_factory=tuple)
    unresolved_local_imports: tuple[str, ...] = field(default_factory=tuple)
    artifact_semantic_mismatches: tuple[str, ...] = field(default_factory=tuple)
    downstream_pending_artifacts: tuple[str, ...] = field(default_factory=tuple)
    completed_artifacts: tuple[str, ...] = field(default_factory=tuple)
    blocked_dependencies: tuple[str, ...] = field(default_factory=tuple)
    required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    present_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    missing_required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    failed_required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    completed_verifiers: tuple[str, ...] = field(default_factory=tuple)
    missing_required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    failed_required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    tool_dispatch: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "polaris.task_boundary_verdict.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "ok": bool(self.ok),
            "failure_class": self.failure_class,
            "responsible_layer": self.responsible_layer,
            "reason": self.reason,
            "target_files": list(self.target_files),
            "missing_target_files": list(self.missing_target_files),
            "missing_entrypoint_targets": list(self.missing_entrypoint_targets),
            "unresolved_local_imports": list(self.unresolved_local_imports),
            "artifact_semantic_mismatches": list(self.artifact_semantic_mismatches),
            "downstream_pending_artifacts": list(self.downstream_pending_artifacts),
            "completed_artifacts": list(self.completed_artifacts),
            "blocked_dependencies": list(self.blocked_dependencies),
            "required_evidence_modalities": list(self.required_evidence_modalities),
            "present_evidence_modalities": list(self.present_evidence_modalities),
            "missing_required_evidence_modalities": list(self.missing_required_evidence_modalities),
            "failed_required_evidence_modalities": list(self.failed_required_evidence_modalities),
            "required_verifiers": list(self.required_verifiers),
            "completed_verifiers": list(self.completed_verifiers),
            "missing_required_verifiers": list(self.missing_required_verifiers),
            "failed_required_verifiers": list(self.failed_required_verifiers),
            "tool_dispatch": dict(self.tool_dispatch),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ReprojectTaskBoundaryVerdictCommandV1:
    """Re-evaluate one immutable task boundary without replaying role work.

    A deferred placeholder requires a normal verdict from Factory's existing
    CE/current-Broker producer plus its immutable TaskRuntime claim id. Receipt
    bindings contain (obligation_id, receipt_ref, receipt_hash, input_hash).
    They are locators, not independent authentication or execution authority.
    """

    workspace: str
    run_id: str
    task_id: str
    expected_content_id: str
    reason: str
    revalidated_verdict: TaskBoundaryVerdictV1 | None = None
    source_claim_fact_event_id: str | None = None
    current_receipt_bindings: tuple[tuple[str, str, str, str], ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("workspace", "run_id", "task_id", "reason"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value)
        content_id = str(self.expected_content_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_id):
            raise ValueError("expected_content_id must be a 64-character hex content id")
        object.__setattr__(self, "expected_content_id", content_id)
        if self.revalidated_verdict is None:
            if self.source_claim_fact_event_id is not None or self.current_receipt_bindings:
                raise ValueError("task_boundary_revalidation_incomplete")
            return
        if type(self.revalidated_verdict) is not TaskBoundaryVerdictV1:
            raise TypeError("task_boundary_revalidation_exact_verdict_required")
        claim_id = self.source_claim_fact_event_id
        if not isinstance(claim_id, str) or not claim_id or claim_id != claim_id.strip():
            raise ValueError("task_boundary_revalidation_claim_id_required")
        if type(self.current_receipt_bindings) is not tuple:
            raise TypeError("task_boundary_revalidation_receipt_bindings_invalid")
        obligation_ids: set[str] = set()
        receipt_refs: set[str] = set()
        for row in self.current_receipt_bindings:
            if (
                type(row) is not tuple
                or len(row) != 4
                or any(not isinstance(value, str) or not value or value != value.strip() for value in row)
                or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in row[2:])
                or row[0] in obligation_ids
                or row[1] in receipt_refs
            ):
                raise ValueError("task_boundary_revalidation_receipt_bindings_invalid")
            obligation_ids.add(row[0])
            receipt_refs.add(row[1])
        # This is detached input data, not an additional receipt authority.
        # Factory's existing producer authenticates CE/current Broker evidence;
        # this owner independently checks its captured claim and complete scope.
        object.__setattr__(self, "revalidated_verdict", deepcopy(self.revalidated_verdict))


@dataclass(frozen=True, slots=True)
class TaskBoundaryReprojectionResultV1:
    """Typed receipt for one local task-boundary reprojection attempt."""

    status: str
    changed: bool
    idempotent: bool
    previous_content_id: str
    content_id: str
    append_id: str
    verdict: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed": self.changed,
            "idempotent": self.idempotent,
            "previous_content_id": self.previous_content_id,
            "content_id": self.content_id,
            "append_id": self.append_id,
            "verdict": dict(self.verdict),
        }


_TASK_BOUNDARY_REPROJECTION_COMPARE_FIELDS = (
    "status",
    "ok",
    "failure_class",
    "responsible_layer",
    "reason",
    "target_files",
    "missing_target_files",
    "missing_entrypoint_targets",
    "unresolved_local_imports",
    "artifact_semantic_mismatches",
    "downstream_pending_artifacts",
    "completed_artifacts",
    "blocked_dependencies",
    "required_evidence_modalities",
    "present_evidence_modalities",
    "missing_required_evidence_modalities",
    "failed_required_evidence_modalities",
    "required_verifiers",
    "completed_verifiers",
    "missing_required_verifiers",
    "failed_required_verifiers",
    "tool_dispatch",
    "evidence_refs",
)


def _task_boundary_reprojection_changed(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    return any(previous.get(key) != current.get(key) for key in _TASK_BOUNDARY_REPROJECTION_COMPARE_FIELDS)


def _validated_task_boundary_revalidation(
    command: ReprojectTaskBoundaryVerdictCommandV1,
    source_event: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate normal-boundary data against this owner's immutable claim cut.

    Receipt authentication and current input verification remain with Factory's
    existing CE/Broker consumer. Locators are not standalone capabilities. This
    cell checks the original execution identity, complete declared obligations,
    unchanged claim cut, and deterministic local verdict before persisting it.
    """
    from polaris.cells.control_plane.run_ledger.public.contracts import (
        ControlPlaneRunLedgerV1Error,
        ReadRunLedgerProjectionQueryV1,
    )
    from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
    from polaris.cells.control_plane.run_ledger.public.service import (
        _canonical_qualification_inputs,
        _capture_strict_fact_stream,
        _captured_native_task_identity,
        _validate_native_lineage_session,
    )

    def reject(code: str) -> NoReturn:
        raise ControlPlaneRunLedgerV1Error(f"task_boundary_revalidation_{code}")

    normal = command.revalidated_verdict
    if type(normal) is not TaskBoundaryVerdictV1:
        reject("required")
    assert normal is not None
    workspace = Path(command.workspace).expanduser().resolve()
    try:
        task_facts, events, _runs, canonical = _canonical_qualification_inputs(
            workspace,
            ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=command.run_id, max_runs=1),
        )
        if not any(event.get("content_id") == source_event.get("content_id") for event in events):
            reject("predecessor_not_canonical")
        if not any(fact.get("fact_event_id") == command.source_claim_fact_event_id for fact in task_facts):
            reject("claim_missing")
        captured = _capture_strict_fact_stream(workspace, "task_runtime.execution")
        if len(captured) != canonical["captured_heads"]["task_runtime.execution"]:
            reject("claim_head_drift")
        claim = next((row for row in captured if row.get("event_id") == command.source_claim_fact_event_id), None)
        if (
            not isinstance(claim, dict)
            or claim.get("event_type") != "claimed"
            or claim.get("source") != "runtime.task_runtime"
        ):
            reject("claim_missing")
        payload = claim.get("payload")
        if not isinstance(payload, dict):
            reject("claim_invalid")
        assert isinstance(payload, dict)
        _validate_native_lineage_session(claim, payload)
        identity = _captured_native_task_identity(claim, payload, workspace)
        if (
            identity is None
            or identity.role_id != "director"
            or identity.run_id != command.run_id
            or identity.external_task_id != command.task_id
        ):
            reject("claim_identity_mismatch")
        snapshot = payload.get("task_row_snapshot")
        metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
        projection = metadata.get("task_completion_projection") if isinstance(metadata, dict) else None
        if not isinstance(projection, dict):
            reject("projection_missing")
        assert isinstance(metadata, dict) and isinstance(projection, dict)
        factory = str(payload.get("factory_run_id") or "")
        project = str(payload.get("project_id") or payload.get("factory_bench_project_id") or "")
        projection_hash = projection.get("projection_hash")
        contract_hash = projection.get("project_contract_hash")
        if (
            not factory
            or not project
            or projection.get("schema_version") != "polaris.task_completion_projection.v1"
            or projection.get("run_id") != factory
            or projection.get("project_id") != project
            or projection.get("task_id") != command.task_id
            or not isinstance(contract_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", contract_hash)
            or projection_hash
            != stable_hash({key: value for key, value in projection.items() if key != "projection_hash"})
        ):
            reject("projection_identity_mismatch")
        token = metadata.get("control_plane_job_token", metadata.get("job_token"))
        policy = token.get("gate_policy") if isinstance(token, dict) else None
        if (
            not isinstance(token, dict)
            or not isinstance(policy, dict)
            or token.get("run_id") != factory
            or token.get("project_id") != project
            or policy.get("project_completion_contract_hash") != contract_hash
        ):
            reject("claim_contract_mismatch")
        predecessor_token = source_event.get("job_token")
        if (
            source_event.get("task_id") != command.task_id
            or source_event.get("run_id") != command.run_id
            or source_event.get("factory_run_id", factory) != factory
            or source_event.get("project_id", project) not in (project, command.task_id)
            or not isinstance(predecessor_token, dict)
            or predecessor_token.get("run_id") != command.run_id
            or predecessor_token.get("task_id") != command.task_id
            or predecessor_token.get("project_id") not in (project, command.task_id)
        ):
            reject("predecessor_identity_mismatch")
        artifact_rows = projection.get("owned_artifacts")
        verification_rows = projection.get("owned_verification")
        execution_rows = projection.get("verification_execution_authority")
        if any(not isinstance(rows, list) for rows in (artifact_rows, verification_rows, execution_rows)):
            reject("scope_missing")
        assert (
            isinstance(artifact_rows, list) and isinstance(verification_rows, list) and isinstance(execution_rows, list)
        )
        for rows in (artifact_rows, verification_rows, execution_rows):
            ids: set[str] = set()
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or row.get("owner_task_id") != command.task_id
                    or not isinstance(row.get("obligation_id"), str)
                    or not row["obligation_id"]
                    or row["obligation_id"] in ids
                    or row.get("applicability", "required") not in {"required", "optional", "not_applicable"}
                ):
                    reject("scope_invalid")
                ids.add(row["obligation_id"])
        required_artifacts = [row for row in artifact_rows if row.get("applicability", "required") == "required"]
        targets = [row.get("path") for row in required_artifacts]
        all_targets = [
            row.get("path") for row in artifact_rows if row.get("applicability", "required") != "not_applicable"
        ]
        if any(
            not isinstance(path, str)
            or not path
            or path != _clean_path(path)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            for path in all_targets
        ):
            reject("artifact_scope_invalid")
        required_rows = [row for row in verification_rows if row.get("applicability", "required") == "required"]
        by_id = {row["obligation_id"]: row for row in execution_rows}
        executable_rows = [row for row in verification_rows if row.get("applicability", "required") != "not_applicable"]
        if set(by_id) != {row["obligation_id"] for row in executable_rows} or any(
            by_id[row["obligation_id"]].get("command") != row.get("command") for row in executable_rows
        ):
            reject("verifier_scope_invalid")
        commands: list[str] = []
        for row in required_rows:
            value = row.get("command")
            if not isinstance(value, str) or not value or by_id[row["obligation_id"]].get("command") != value:
                reject("verifier_scope_invalid")
            if value not in commands:
                commands.append(value)
        binding_ids = {row[0] for row in command.current_receipt_bindings}
        declared_modalities = policy.get("required_evidence_modalities", policy.get("required_modalities", []))
        if not isinstance(declared_modalities, (list, tuple)):
            reject("claim_modalities_invalid")
        director_modalities = {name for name in declared_modalities if name in {"code", "command"}}
        if binding_ids != {row["obligation_id"] for row in required_rows} or not {
            row[1] for row in command.current_receipt_bindings
        }.issubset(set(normal.evidence_refs)):
            reject("receipt_scope_incomplete")
        if (
            normal.task_id != command.task_id
            or normal.run_id != command.run_id
            or not (targets or commands)
            or set(normal.target_files) != set(targets)
            or len(normal.target_files) != len(set(targets))
            or set(normal.required_verifiers) != set(commands)
            or len(normal.required_verifiers) != len(commands)
            or not set(targets).issubset(set(normal.completed_artifacts))
            or not set(normal.completed_artifacts).issubset(set(all_targets))
            or set(normal.completed_verifiers) != set(commands)
            or not normal.ok
            or normal.status != "completed_verified"
            or normal.missing_required_verifiers
            or normal.failed_required_verifiers
            or normal.missing_required_evidence_modalities
            or normal.failed_required_evidence_modalities
            or set(normal.required_evidence_modalities) != director_modalities
            or not director_modalities.issubset(set(normal.present_evidence_modalities))
        ):
            reject("normal_scope_incomplete")
        verdict = evaluate_task_boundary_verdict(
            workspace=workspace,
            task_id=command.task_id,
            run_id=command.run_id,
            **{
                name: value
                for name, value in normal.to_dict().items()
                if name
                in {
                    "target_files",
                    "completed_artifacts",
                    "downstream_pending_artifacts",
                    "blocked_dependencies",
                    "required_evidence_modalities",
                    "present_evidence_modalities",
                    "missing_required_evidence_modalities",
                    "failed_required_evidence_modalities",
                    "required_verifiers",
                    "completed_verifiers",
                    "missing_required_verifiers",
                    "failed_required_verifiers",
                    "tool_dispatch",
                    "evidence_refs",
                }
            },
        ).to_dict()
        if not verdict.get("ok"):
            reject("local_verdict_failed")
        if captured != _capture_strict_fact_stream(workspace, "task_runtime.execution"):
            reject("claim_head_drift")
        basis = {
            "source_claim_fact_event_id": claim["event_id"],
            "source_claim_fact_event_seq": claim["seq"],
            "source_claim_integrity_digest": claim["integrity_digest"],
            "factory_run_id": factory,
            "project_id": project,
            "completion_contract_hash": contract_hash,
            "task_projection_hash": projection_hash,
            "current_receipt_bindings": [list(row) for row in sorted(command.current_receipt_bindings)],
        }
        basis["revalidation_basis_hash"] = stable_hash({"verdict": verdict, "basis": basis})
        return verdict, basis
    except ControlPlaneRunLedgerV1Error:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ControlPlaneRunLedgerV1Error(f"task_boundary_revalidation_claim_invalid:{type(exc).__name__}") from exc


def reproject_task_boundary_verdict(
    command: ReprojectTaskBoundaryVerdictCommandV1,
) -> TaskBoundaryReprojectionResultV1:
    """Append one corrected TaskBoundary fact from existing delivery evidence.

    This path is intentionally local: it consumes the latest immutable verdict,
    re-evaluates only deterministic workspace facts, and appends a successor.
    It never invokes PM, Chief Engineer, Director, QA, or an LLM.
    """

    from polaris.cells.control_plane.run_ledger.public.contracts import (
        AppendRunLedgerEventCommandV1,
        ControlPlaneRunLedgerV1Error,
        ReadRunLedgerProjectionQueryV1,
    )
    from polaris.cells.control_plane.run_ledger.public.service import (
        _read_execution_control_plane_facts,
        append_run_ledger_event,
        read_run_ledger_projection,
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=command.workspace,
            run_id=command.run_id,
            max_runs=1,
        )
    ).projection
    boundary = projection.get("task_boundary")
    boundary_map = boundary if isinstance(boundary, dict) else {}
    latest_by_task = boundary_map.get("latest_by_task")
    latest_map = latest_by_task if isinstance(latest_by_task, dict) else {}
    previous_raw = latest_map.get(command.task_id)
    if not isinstance(previous_raw, dict):
        raise ControlPlaneRunLedgerV1Error("task_boundary_reprojection_source_missing")
    previous = dict(previous_raw)
    previous_content_id = str(previous.get("content_id") or "").strip().lower()

    reprojection = previous.get("reprojection")
    reprojection_map = reprojection if isinstance(reprojection, dict) else {}
    source_event: dict[str, Any] | None = None
    basis: dict[str, Any] = {}
    if previous_content_id != command.expected_content_id and not (
        str(reprojection_map.get("source_content_id") or "").strip().lower() == command.expected_content_id
        and str(reprojection_map.get("reason") or "").strip() == command.reason
    ):
        raise ControlPlaneRunLedgerV1Error("task_boundary_reprojection_stale_predecessor")
    if command.revalidated_verdict is not None:
        source_event = next(
            (
                event
                for event in _read_execution_control_plane_facts(
                    workspace=Path(command.workspace).expanduser().resolve(), run_id=command.run_id
                )
                if event.get("content_id") == command.expected_content_id
            ),
            None,
        )
        if source_event is None:
            raise ControlPlaneRunLedgerV1Error("task_boundary_revalidation_predecessor_missing")
        verdict, basis = _validated_task_boundary_revalidation(command, source_event)
    if previous_content_id != command.expected_content_id:
        if (
            str(reprojection_map.get("source_content_id") or "").strip().lower() == command.expected_content_id
            and str(reprojection_map.get("reason") or "").strip() == command.reason
            and reprojection_map.get("revalidation_basis_hash") == basis.get("revalidation_basis_hash")
        ):
            return TaskBoundaryReprojectionResultV1(
                status="idempotent",
                changed=True,
                idempotent=True,
                previous_content_id=command.expected_content_id,
                content_id=previous_content_id,
                append_id=str(previous.get("append_id") or ""),
                verdict=previous,
            )
        raise ControlPlaneRunLedgerV1Error("task_boundary_reprojection_stale_predecessor")
    if bool(previous.get("ok")):
        raise ControlPlaneRunLedgerV1Error("task_boundary_reprojection_source_already_passed")
    if command.revalidated_verdict is None:
        if (
            previous.get("status") == "deferred_followup_required"
            or previous.get("failure_class") == TaskBoundaryFailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value
        ):
            raise ControlPlaneRunLedgerV1Error("task_boundary_revalidation_required")
        verdict = evaluate_task_boundary_verdict(
            workspace=command.workspace,
            task_id=command.task_id,
            run_id=command.run_id,
            target_files=previous.get("target_files"),
            completed_artifacts=previous.get("completed_artifacts"),
            downstream_pending_artifacts=previous.get("downstream_pending_artifacts"),
            blocked_dependencies=previous.get("blocked_dependencies"),
            required_evidence_modalities=previous.get("required_evidence_modalities"),
            present_evidence_modalities=previous.get("present_evidence_modalities"),
            missing_required_evidence_modalities=previous.get("missing_required_evidence_modalities"),
            failed_required_evidence_modalities=previous.get("failed_required_evidence_modalities"),
            required_verifiers=previous.get("required_verifiers"),
            completed_verifiers=previous.get("completed_verifiers"),
            missing_required_verifiers=previous.get("missing_required_verifiers"),
            failed_required_verifiers=previous.get("failed_required_verifiers"),
            tool_dispatch=previous.get("tool_dispatch") if isinstance(previous.get("tool_dispatch"), dict) else {},
            evidence_refs=previous.get("evidence_refs"),
        ).to_dict()
    if not _task_boundary_reprojection_changed(previous, verdict):
        return TaskBoundaryReprojectionResultV1(
            status="no_change",
            changed=False,
            idempotent=False,
            previous_content_id=previous_content_id,
            content_id=previous_content_id,
            append_id=str(previous.get("append_id") or ""),
            verdict=previous,
        )

    # Reprojection changes the verdict, not its project or capability facts.
    # Reuse the immutable predecessor rather than inventing a successful token.
    source_event = source_event or next(
        (
            event
            for event in _read_execution_control_plane_facts(
                workspace=Path(command.workspace).expanduser().resolve(), run_id=command.run_id
            )
            if event.get("content_id") == previous_content_id
        ),
        None,
    )
    if source_event is None:
        raise ControlPlaneRunLedgerV1Error("task_boundary_reprojection_source_event_missing")

    verdict["reprojection"] = {
        "schema_version": "polaris.task_boundary_reprojection.v1",
        "source_content_id": previous_content_id,
        "reason": command.reason,
        "reused_delivery_evidence": True,
        "pm_restart_allowed": False,
        "chief_engineer_restart_allowed": False,
        "director_restart_allowed": False,
        **basis,
    }
    successor_event = {
        "event_type": "task_boundary_verdict",
        "stage": "task_boundary_reprojection",
        "task_id": command.task_id,
        "run_id": command.run_id,
        "supersedes_task_boundary_content_id": previous_content_id,
        "task_boundary_reprojection": dict(verdict["reprojection"]),
        "task_boundary_verdict": verdict,
    }
    for scope_field in ("factory_run_id", "project_id", "job_token"):
        if scope_field in source_event:
            successor_event[scope_field] = source_event[scope_field]
    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=command.workspace,
            run_id=command.run_id,
            event=successor_event,
        )
    )
    receipt = append_result.receipt
    event = receipt.get("event")
    persisted = event if isinstance(event, dict) else {}
    persisted_verdict = persisted.get("task_boundary_verdict")
    return TaskBoundaryReprojectionResultV1(
        status="reprojected",
        changed=True,
        idempotent=False,
        previous_content_id=previous_content_id,
        content_id=str(persisted.get("content_id") or ""),
        append_id=str(persisted.get("append_id") or ""),
        verdict=dict(persisted_verdict) if isinstance(persisted_verdict, dict) else verdict,
    )


def build_completed_task_boundary_verdict(
    *,
    task_id: str,
    run_id: str = "",
    target_files: list[str] | tuple[str, ...] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Return a completed task-boundary verdict."""

    targets = tuple(_string_list(target_files))
    return TaskBoundaryVerdictV1(
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        status="completed_verified",
        ok=True,
        failure_class=TaskBoundaryFailureClassV1.PASSED.value,
        responsible_layer="execution_control_plane",
        reason="Task boundary materialization and entrypoint obligations are satisfied",
        target_files=targets,
        evidence_refs=tuple(_string_list(evidence_refs)),
    )


def build_deferred_followup_task_boundary_verdict(
    *,
    task_id: str,
    run_id: str = "",
    reason: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Return a verdict for work that requires another governed execution step."""

    return TaskBoundaryVerdictV1(
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        status="deferred_followup_required",
        ok=False,
        failure_class=TaskBoundaryFailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value,
        responsible_layer="execution_control_plane",
        reason=str(reason or "Execution requires a governed follow-up workflow before completion").strip(),
        evidence_refs=tuple(_string_list(evidence_refs)),
    )


def reconcile_task_boundary_artifacts_with_workspace(
    *,
    workspace: str | Path,
    target_files: list[str] | tuple[str, ...] | None = None,
    completed_artifacts: list[str] | tuple[str, ...] | None = None,
    downstream_pending_artifacts: list[str] | tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Promote on-disk paths into completed; drop them from downstream_pending.

    R181/M06: multi-task delivery often lands files via sibling tasks or
    end-of-stage settle while the current task's tool_results only list a
    subset. Stale ``downstream_pending`` lists then contradict the workspace
    and falsely block director_dispatch even when real_run would pass.

    Complexity: O(n) path checks for n declared artifact paths.
    """

    workspace_path = Path(workspace).expanduser().resolve()
    targets = _string_list(target_files)
    completed = list(_string_list(completed_artifacts))
    completed_set = set(completed)
    pending_raw = _string_list(downstream_pending_artifacts)
    for path in [*targets, *pending_raw]:
        if not path or path in completed_set:
            continue
        if _path_exists(workspace_path, path):
            completed.append(path)
            completed_set.add(path)
    downstream = tuple(
        path for path in pending_raw if path not in completed_set and not _path_exists(workspace_path, path)
    )
    return tuple(completed), downstream


def evaluate_task_boundary_verdict(
    *,
    workspace: str | Path,
    task_id: str,
    run_id: str = "",
    target_files: list[str] | tuple[str, ...] | None = None,
    completed_artifacts: list[str] | tuple[str, ...] | None = None,
    downstream_pending_artifacts: list[str] | tuple[str, ...] | None = None,
    blocked_dependencies: list[str] | tuple[str, ...] | None = None,
    required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    present_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    missing_required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    failed_required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    required_verifiers: list[str] | tuple[str, ...] | None = None,
    completed_verifiers: list[str] | tuple[str, ...] | None = None,
    missing_required_verifiers: list[str] | tuple[str, ...] | None = None,
    failed_required_verifiers: list[str] | tuple[str, ...] | None = None,
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Evaluate task-boundary completeness from deterministic workspace facts."""

    workspace_path = Path(workspace).expanduser().resolve()
    targets = tuple(_string_list(target_files))
    completed, downstream = reconcile_task_boundary_artifacts_with_workspace(
        workspace=workspace_path,
        target_files=targets,
        completed_artifacts=completed_artifacts,
        downstream_pending_artifacts=downstream_pending_artifacts,
    )
    blocked = tuple(_string_list(blocked_dependencies))
    required_evidence = tuple(_string_list(required_evidence_modalities))
    present_evidence = tuple(_string_list(present_evidence_modalities))
    failed_evidence = tuple(_string_list(failed_required_evidence_modalities))
    missing_evidence = _required_items_not_satisfied(
        required=required_evidence,
        present=present_evidence,
        failed=failed_evidence,
        explicit_missing=tuple(_string_list(missing_required_evidence_modalities)),
    )
    required_verifier_names = tuple(_string_list(required_verifiers))
    completed_verifier_names = tuple(_string_list(completed_verifiers))
    failed_verifier_names = tuple(_string_list(failed_required_verifiers))
    missing_verifier_names = _required_items_not_satisfied(
        required=required_verifier_names,
        present=completed_verifier_names,
        failed=failed_verifier_names,
        explicit_missing=tuple(_string_list(missing_required_verifiers)),
    )
    evidence = tuple(_string_list(evidence_refs))
    dispatch = dict(tool_dispatch or {})
    base_kwargs: dict[str, Any] = {
        "task_id": str(task_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "target_files": targets,
        "completed_artifacts": completed,
        "downstream_pending_artifacts": downstream,
        "blocked_dependencies": blocked,
        "required_evidence_modalities": required_evidence,
        "present_evidence_modalities": present_evidence,
        "missing_required_evidence_modalities": missing_evidence,
        "failed_required_evidence_modalities": failed_evidence,
        "required_verifiers": required_verifier_names,
        "completed_verifiers": completed_verifier_names,
        "missing_required_verifiers": missing_verifier_names,
        "failed_required_verifiers": failed_verifier_names,
        "tool_dispatch": dispatch,
        "evidence_refs": evidence,
    }

    dispatch_failure_class = _normalize_task_boundary_failure_class(dispatch.get("failure_class"))
    if dispatch_failure_class == TaskBoundaryFailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value:
        return TaskBoundaryVerdictV1(
            status="required_tool_text_fallback_not_dispatched",
            ok=False,
            failure_class=dispatch_failure_class,
            responsible_layer="execution_control_plane",
            reason=(
                str(dispatch.get("reason") or "").strip()
                or "Required-tool text compatibility mode produced no authoritative tool dispatch"
            ),
            **base_kwargs,
        )

    if dispatch_failure_class == TaskBoundaryFailureClassV1.NO_MATERIALIZED_EFFECT.value:
        return TaskBoundaryVerdictV1(
            status="no_materialized_effect",
            ok=False,
            failure_class=dispatch_failure_class,
            responsible_layer="execution_control_plane",
            reason=(
                str(dispatch.get("reason") or "").strip()
                or "Required materialization completed without an authoritative effect receipt"
            ),
            **base_kwargs,
        )

    if bool(dispatch.get("dropped")) or str(dispatch.get("status") or "").strip() == "dropped":
        return TaskBoundaryVerdictV1(
            status="tool_dispatch_dropped",
            ok=False,
            failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
            responsible_layer="execution_control_plane",
            reason="Provider emitted tool calls, but no authoritative tool dispatch receipt was committed",
            **base_kwargs,
        )

    if blocked:
        return TaskBoundaryVerdictV1(
            status="dependency_not_unlocked",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.DEPENDENCY_NOT_UNLOCKED.value,
            responsible_layer="execution_control_plane",
            reason="Task dependencies are not unlocked for completion",
            **base_kwargs,
        )

    missing_targets = tuple(path for path in targets if not _path_exists(workspace_path, path))
    if missing_targets:
        return TaskBoundaryVerdictV1(
            status="incomplete_materialization",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value,
            responsible_layer="director",
            reason="Required target files were not materialized",
            missing_target_files=missing_targets,
            **base_kwargs,
        )

    known_artifacts = set(targets) | set(completed) | set(downstream)
    missing_entrypoints = tuple(
        entrypoint
        for entrypoint in _workspace_entrypoint_targets(workspace_path)
        if not _path_exists(workspace_path, entrypoint)
        and entrypoint not in known_artifacts
        and not _entrypoint_is_satisfied_by_typescript_source(workspace_path, entrypoint, known_artifacts)
    )
    if missing_entrypoints:
        return TaskBoundaryVerdictV1(
            status="missing_entrypoint_target",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
            responsible_layer="task_boundary",
            reason="Manifest references local entrypoint files that are neither complete nor declared downstream",
            missing_entrypoint_targets=missing_entrypoints,
            **base_kwargs,
        )

    # Live L2-17: scanning every completed artifact let TASK-2 inherit
    # TASK-1-entrypoints `src/models/index.ts -> ./models/types.js` and
    # fail-close after it had already materialized its own targets.
    owner_source_paths = tuple(_dedupe_paths(list(targets))) or tuple(_dedupe_paths(list(completed)))
    semantic_mismatches = _artifact_semantic_mismatches(
        workspace=workspace_path,
        source_paths=owner_source_paths,
    )
    if semantic_mismatches:
        return TaskBoundaryVerdictV1(
            status="artifact_semantic_mismatch",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.IMPLEMENTATION_DEFECT.value,
            responsible_layer="director",
            reason="Source files contain content that does not match their task-boundary file role",
            artifact_semantic_mismatches=semantic_mismatches,
            **base_kwargs,
        )

    unresolved_imports = _unresolved_local_imports(
        workspace=workspace_path,
        source_paths=owner_source_paths,
        known_artifacts=known_artifacts,
    )
    if unresolved_imports:
        return TaskBoundaryVerdictV1(
            status="unresolved_local_import",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.UNRESOLVED_LOCAL_IMPORT.value,
            responsible_layer="director",
            reason="Source files import local files that are neither materialized nor declared downstream",
            unresolved_local_imports=unresolved_imports,
            **base_kwargs,
        )

    if missing_evidence:
        return TaskBoundaryVerdictV1(
            status="execution_evidence_missing",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
            responsible_layer="director",
            reason="Required execution evidence was not committed",
            **base_kwargs,
        )

    if failed_evidence:
        return TaskBoundaryVerdictV1(
            status="required_evidence_failed",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.COMPILER_OR_TEST_FAILURE.value,
            responsible_layer="director",
            reason="Required execution evidence was committed but failed",
            **base_kwargs,
        )

    if missing_verifier_names:
        return TaskBoundaryVerdictV1(
            status="required_verifier_missing",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
            responsible_layer="director",
            reason="Required verifier execution was not committed",
            **base_kwargs,
        )

    if failed_verifier_names:
        return TaskBoundaryVerdictV1(
            status="required_verifier_failed",
            ok=False,
            failure_class=TaskBoundaryFailureClassV1.COMPILER_OR_TEST_FAILURE.value,
            responsible_layer="director",
            reason="Required verifier execution failed",
            **base_kwargs,
        )

    return TaskBoundaryVerdictV1(
        status="completed_verified",
        ok=True,
        failure_class=TaskBoundaryFailureClassV1.PASSED.value,
        responsible_layer="execution_control_plane",
        reason="Task boundary materialization, entrypoint, evidence, and verifier obligations are satisfied",
        **base_kwargs,
    )


def normalize_task_boundary_verdict(value: Any) -> dict[str, Any]:
    """Return a safe task-boundary verdict mapping."""

    if isinstance(value, TaskBoundaryVerdictV1):
        return value.to_dict()
    if isinstance(value, dict):
        payload = dict(value)
        payload.setdefault("schema_version", "polaris.task_boundary_verdict.v1")
        payload.setdefault("status", "unknown")
        payload.setdefault("ok", False)
        payload.setdefault("failure_class", "TASK_BOUNDARY_UNKNOWN")
        payload["failure_class"] = _normalize_task_boundary_failure_class(payload.get("failure_class"))
        payload.setdefault("responsible_layer", "execution_control_plane")
        payload.setdefault("reason", "Task boundary verdict was incomplete")
        return payload
    return {
        "schema_version": "polaris.task_boundary_verdict.v1",
        "status": "unknown",
        "ok": False,
        "failure_class": TaskBoundaryFailureClassV1.TASK_BOUNDARY_UNKNOWN.value,
        "responsible_layer": "execution_control_plane",
        "reason": "Task boundary verdict was missing",
    }


__all__ = [
    "ReprojectTaskBoundaryVerdictCommandV1",
    "TaskBoundaryFailureClassV1",
    "TaskBoundaryReprojectionResultV1",
    "TaskBoundaryVerdictV1",
    "build_completed_task_boundary_verdict",
    "build_deferred_followup_task_boundary_verdict",
    "evaluate_task_boundary_verdict",
    "normalize_task_boundary_verdict",
    "reconcile_task_boundary_artifacts_with_workspace",
    "reproject_task_boundary_verdict",
]
