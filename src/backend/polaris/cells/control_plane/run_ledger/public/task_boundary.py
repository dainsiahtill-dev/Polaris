"""Task-boundary verdict contracts for execution control-plane projections."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

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
    return not any(char in token for char in _ENTRYPOINT_PATTERN_CHARS)


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
            candidates.append(_clean_path(html_path.parent.relative_to(workspace) / src))
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
    downstream = tuple(path for path in pending_raw if path not in completed_set and not _path_exists(workspace_path, path))
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

    scoped_source_paths = tuple(_dedupe_paths([*targets, *completed]))
    semantic_mismatches = _artifact_semantic_mismatches(
        workspace=workspace_path,
        source_paths=scoped_source_paths,
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
        source_paths=scoped_source_paths,
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
    "TaskBoundaryFailureClassV1",
    "TaskBoundaryVerdictV1",
    "build_completed_task_boundary_verdict",
    "build_deferred_followup_task_boundary_verdict",
    "evaluate_task_boundary_verdict",
    "normalize_task_boundary_verdict",
    "reconcile_task_boundary_artifacts_with_workspace",
]
