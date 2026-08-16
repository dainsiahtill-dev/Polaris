"""Pure workspace-quality repair evidence, discrepancy, and projection helpers.

Extracted from ``OrchestrationStageExecutor``. Every function is pure (no
``self``) and operates on repair result dicts, quality-check summaries, and
artifact quality error lists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

_WORKSPACE_QUALITY_MUTATION_TOKENS = WRITE_TOOLS | frozenset({"create_file", "text_replace"})

_GO_STACK_OVERFLOW_MARKERS = ("fatal error: stack overflow", "goroutine stack exceeds")
_GO_STACK_FUNC_RE = re.compile(r"([A-Za-z0-9_./]+\.\(\*?[A-Za-z0-9_]+\)\.[A-Za-z0-9_]+)")


def compact_go_stack_overflow_diagnostic(output: str) -> str:
    """Collapse a Go 1GiB stack dump to the repeating owner frames.

    Live L2-13: ``go test``/``go run`` overflowed in
    ``exhibitionIDs``/``allCapsules``. The untruncated dump became 173
    uncovered diagnostics and a 6.7MiB validation artifact.
    """

    text = str(output or "")
    if not any(marker in text for marker in _GO_STACK_OVERFLOW_MARKERS):
        return text
    counts = Counter(_GO_STACK_FUNC_RE.findall(text))
    repeating = [name for name, count in counts.most_common() if count >= 3]
    unique_frames = list(dict.fromkeys(_GO_STACK_FUNC_RE.findall(text)))
    lines = ["fatal error: stack overflow"]
    if repeating:
        lines.append("repeating_frames=" + ",".join(repeating[:4]))
    if unique_frames:
        lines.append("frames=" + ",".join(unique_frames[:8]))
    return "\n".join(lines)


_COMPILER_ERROR_START_RE = re.compile(r"(?m)^(?:error(?:\[[^\]]+\])?|warning(?:\[[^\]]+\])?):")
_COMPILER_SUMMARY_RE = re.compile(r"(?m)^error: could not compile\b")
_COMPILER_ARROW_RE = re.compile(r"(?m)^\s*-->\s*(?P<path>\S+):(?P<line>\d+)")
_CARGO_TEST_STDOUT_FAILURE_RE = re.compile(
    r"(?ms)^---- (?P<name>\S+) stdout ----\n(?P<body>.*?)(?=\n---- |\nfailures:\n|\Z)",
)


def compact_compiler_error_blocks(output: str, *, limit: int = 12_000) -> str:
    r"""Keep unique compiler error blocks (with rustc help) instead of a tail.

    Live L2-14: ``cargo test`` emitted 87 errors. The 8KiB tail kept only
    the last Eq/label residuals and dropped ``help: a method \`name\` also
    exists``. Line-suggestion then looked unplannable and quality LLM
    invented more enum variants.
    """

    text = str(output or "")
    if "error[" not in text and not re.search(r"(?m)^error:", text):
        return text
    starts = [match.start() for match in _COMPILER_ERROR_START_RE.finditer(text)]
    if not starts:
        return text
    unique_blocks: list[tuple[bool, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        summary = _COMPILER_SUMMARY_RE.search(text, start, end)
        if summary is not None and summary.start() == start:
            continue
        if summary is not None:
            end = summary.start()
        block = text[start:end].strip()
        if not block:
            continue
        first_line = block.splitlines()[0].strip()
        if first_line.lower().startswith("warning"):
            continue
        arrow = _COMPILER_ARROW_RE.search(block)
        key = (
            first_line,
            str(arrow.group("path") if arrow is not None else ""),
            str(arrow.group("line") if arrow is not None else ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_blocks.append(("help:" in block.lower(), block))
    ordered = [block for has_help, block in unique_blocks if has_help]
    ordered.extend(block for has_help, block in unique_blocks if not has_help)
    packed: list[str] = []
    used = 0
    for block in ordered:
        extra = len(block) + (1 if packed else 0)
        if packed and used + extra > max(256, int(limit)):
            break
        if not packed and extra > max(256, int(limit)):
            packed.append(block[: max(256, int(limit))])
            break
        packed.append(block)
        used += extra
    # Live L2-14: after rustc went green, cargo test left
    # ``error: test failed, to rerun pass --lib`` plus panic bodies.
    # Keeping only that one-liner made quality no-op twice.
    if used < max(256, int(limit)):
        seen_panics: set[str] = set()
        for match in _CARGO_TEST_STDOUT_FAILURE_RE.finditer(text):
            name = str(match.group("name") or "").strip()
            body = str(match.group("body") or "").strip()
            if not name or "panicked at" not in body:
                continue
            if name in seen_panics:
                continue
            seen_panics.add(name)
            block = f"---- {name} stdout ----\n{body}"
            extra = len(block) + (1 if packed else 0)
            if packed and used + extra > max(256, int(limit)):
                break
            packed.append(block)
            used += extra
    return "\n".join(packed)


_LANGUAGE_NEUTRAL_FILENAMES: frozenset[str] = frozenset(
    {
        "go.mod",
        "go.sum",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "cmakelists.txt",
    }
)

_WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES = frozenset(
    {
        ".css",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
    }
)

_LEDGER_REPAIR_LIST_LIMIT = 24
_LEDGER_REPAIR_TEXT_LIMIT = 512


def _is_workspace_quality_repair_path(path: str) -> bool:
    normalized = os.path.normpath(str(path or "").strip().replace("\\", "/")).replace("\\", "/")
    if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
        return False
    candidate = Path(normalized)
    return (
        candidate.suffix.lower() in _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES
        or candidate.name.lower() in _LANGUAGE_NEUTRAL_FILENAMES
    )


def _dedupe_workspace_repair_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = os.path.normpath(str(raw_path or "").strip().replace("\\", "/")).replace("\\", "/")
        if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
            continue
        if not _is_workspace_quality_repair_path(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def workspace_quality_repair_result_has_mutation(item: dict[str, Any]) -> bool:
    """Return true only for a path-bound, non-no-op physical write receipt.

    A successful write-shaped tool row proves dispatch, not mutation.  Quality
    repair settlement must additionally carry the affected path and the
    before/after content hashes; otherwise a rejected/no-op ``edit_file`` can
    incorrectly complete the Director task without changing the workspace.
    """

    if not isinstance(item, dict) or not bool(item.get("success")):
        return False
    raw_result = item.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    tool_name = str(
        item.get("tool")
        or item.get("tool_name")
        or result.get("tool")
        or result.get("tool_name")
        or result.get("operation")
        or ""
    ).strip()
    operation = str(result.get("operation") or "").strip()
    if tool_name not in _WORKSPACE_QUALITY_MUTATION_TOKENS and operation not in _WORKSPACE_QUALITY_MUTATION_TOKENS:
        return False
    file_name = str(result.get("file") or result.get("path") or "").strip()
    if not _is_workspace_quality_repair_path(file_name):
        return False
    before_hash = str(result.get("before_sha256") or result.get("before_hash") or "").strip().lower()
    after_hash = str(result.get("after_sha256") or result.get("after_hash") or "").strip().lower()
    valid_hash_tokens = {"file_absent"}

    def valid_hash(value: str) -> bool:
        return value in valid_hash_tokens or (len(value) == 64 and all(char in "0123456789abcdef" for char in value))

    return bool(valid_hash(before_hash) and valid_hash(after_hash) and before_hash != after_hash)


def workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    for item in repair_results:
        if not isinstance(item, dict) or not bool(item.get("success")):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        source_tool = str(result.get("source_tool") or item.get("source_tool") or "").strip()
        file_name = str(result.get("file") or result.get("path") or "").strip()
        operation = str(result.get("operation") or "").strip()
        if source_tool or file_name:
            evidence.append(
                "repair_write:"
                f"tool={source_tool or str(item.get('tool') or item.get('tool_name') or 'unknown')};"
                f"file={file_name or 'unknown'};"
                f"operation={operation or 'unknown'}"
            )
        before_hash = str(result.get("before_sha256") or "").strip()
        after_hash = str(result.get("after_sha256") or "").strip()
        if before_hash or after_hash:
            evidence.append(
                f"repair_hash:file={file_name or 'unknown'};before={before_hash[:16]};after={after_hash[:16]}"
            )
        diff_excerpt = str(result.get("diff_excerpt") or "").strip()
        if diff_excerpt:
            compact_diff = " ".join(diff_excerpt.split())
            evidence.append(f"repair_diff:file={file_name or 'unknown'};excerpt={compact_diff[:360]}")
        if len(evidence) >= 12:
            break
    return evidence


def workspace_quality_summary_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
    if bool(summary.get("task_boundary_interface_discrepancy_retry_authorized")):
        return False
    owned_repair_targets = [
        str(item or "").strip().replace("\\", "/")
        for item in (summary.get("repair_target_files") or [])
        if str(item or "").strip()
    ]
    if owned_repair_targets:
        # Live L2-14 TASK-3: E0061 Reef::new arity in tests/product.rs matched
        # line_suggestion but was unplannable. The current task already owns
        # that file; this is Director LLM work, not CE interface triage.
        return False
    stage = str(summary.get("stage") or "").strip()
    if stage == "runtime_plan_probe_unplannable":
        return True
    evidence = summary.get("interface_discrepancy_evidence")
    if (
        isinstance(evidence, dict)
        and str(evidence.get("reason") or "") == "coverage_matched_but_unplannable"
        and not bool(evidence.get("director_retry_allowed"))
    ):
        return True
    plan_probe = summary.get("plan_probe_preaudit")
    if not isinstance(plan_probe, dict):
        return False
    return str(plan_probe.get("status") or "").strip() == "coverage_matched_but_unplannable" and not bool(
        plan_probe.get("plannable_source_tools")
    )


def workspace_quality_latest_task_boundary_scope_filter(repair: Mapping[str, Any]) -> dict[str, Any]:
    """Lift last-round owner-handoff scope filter onto the repair payload.

    Live L2-12 wrote ``ownership_handoff_requests`` only under
    ``rounds[*].repair_summary.task_boundary_scope_filter``. Factory rework
    and scope-authority extractors read the repair object itself, so the
    latest typed filter must be a first-class repair field.
    """

    existing = repair.get("task_boundary_scope_filter")
    if isinstance(existing, Mapping) and existing:
        return dict(existing)
    rounds = repair.get("rounds")
    if not isinstance(rounds, list | tuple):
        return {}
    for item in reversed(rounds):
        if not isinstance(item, Mapping):
            continue
        summary = item.get("repair_summary")
        if not isinstance(summary, Mapping):
            continue
        scope_filter = summary.get("task_boundary_scope_filter")
        if isinstance(scope_filter, Mapping) and scope_filter:
            return dict(scope_filter)
    return {}


_CPP_OR_CMAKE_RESIDUAL_PATH_RE = re.compile(
    r"(?P<path>(?:src|include|tests)/[^\s:'\"]+\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx)|cmakelists\.txt)"
    r":\d+(?::\d+)?:\s+error:",
    re.IGNORECASE,
)
_CPP_FAILING_TU_RE = re.compile(r"(?m)^###\s+(?P<path>\S+)")
_CPP_FAILING_TU_INDEX_RE = re.compile(r"(?m)^###\s+FAILING_TUS\s+(?P<paths>.+)$")
_CPP_STD_NAMESPACE_POLLUTION_RE = re.compile(
    r"(?:in namespace\s+['\"‘’][^'\"‘’]*::std['\"‘’]|[A-Za-z_]\w*::std::)",
    re.IGNORECASE,
)
_CPP_NAMESPACE_OPEN_RE = re.compile(r"(?m)^\s*namespace\b")
_CPP_HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx"}


def _cpp_header_unclosed_namespace_count(text: str) -> int:
    """Return extra ``namespace`` openers left unclosed before ``#endif``."""

    opens = len(_CPP_NAMESPACE_OPEN_RE.findall(text))
    if opens <= 0:
        return 0
    head = re.split(r"(?m)^\s*#endif\b", text, maxsplit=1)[0]
    type_end = head.rfind("};")
    region = head[type_end + 2 :] if type_end >= 0 else head
    closes = region.count("}")
    return max(0, opens - closes)


def workspace_quality_unclosed_namespace_headers(workspace: Path) -> list[str]:
    """Return project headers with more namespace openers than closers.

    Live L2-15: ``namespace patrol_chess { namespace models {`` plus one
    ``} // namespace patrol_chess::models`` sucked later includes into
    ``patrol_chess::std``. Use-site TU edits cannot close that header.
    """

    if not workspace.is_dir():
        return []
    found: list[str] = []
    search_roots = [path for path in (workspace / "src", workspace / "include") if path.is_dir()]
    if not search_roots:
        search_roots = [workspace]
    for root in search_roots:
        try:
            headers = [path for suffix in ("*.h", "*.hh", "*.hpp", "*.hxx") for path in root.rglob(suffix)]
        except OSError:
            continue
        for header in sorted(headers, key=lambda item: item.as_posix()):
            if any(part in {"build", "cmake-build"} for part in header.parts):
                continue
            try:
                rel = header.relative_to(workspace).as_posix()
                text = header.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if _cpp_header_unclosed_namespace_count(text) > 0:
                found.append(rel)
    return found


def _cpp_residuals_have_std_namespace_pollution(residual_errors: Sequence[str]) -> bool:
    blob = "\n".join(str(item or "") for item in residual_errors)
    return _CPP_STD_NAMESPACE_POLLUTION_RE.search(blob) is not None


def workspace_quality_unclaimed_residual_targets(
    residual_errors: Sequence[str],
    *,
    claimed_targets: Sequence[str],
    workspace: Path,
) -> list[str]:
    """Return residual C++/CMake paths the last owner did not lease.

    Live L2-15: four stagnant TASK-2 rounds stayed on generator.cpp/.hpp
    while ``src/main.cpp`` and ``cmakelists.txt`` still failed. Rotate-by-stem
    never saw those files after task-scope partition.
    """

    claimed = {str(path or "").strip().replace("\\", "/") for path in claimed_targets if str(path or "").strip()}
    leftover: list[str] = []
    blob = "\n".join(str(item or "") for item in residual_errors)
    # Official C++ syntax wrapper prints ``### path`` per failing TU.
    # Live remint-5 leased energy.cpp from member-name rebound while
    # ``### src/main.cpp`` was the other failing unit.
    for match in _CPP_FAILING_TU_INDEX_RE.finditer(blob):
        for rel in str(match.group("paths") or "").split():
            rel = rel.replace("\\", "/").strip()
            if not rel or rel in claimed or rel in leftover:
                continue
            if (workspace / rel).is_file():
                leftover.append(rel)
    for match in _CPP_FAILING_TU_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/")
        if not rel or rel == "FAILING_TUS" or rel in claimed or rel in leftover:
            continue
        if (workspace / rel).is_file():
            leftover.append(rel)
    for match in _CPP_OR_CMAKE_RESIDUAL_PATH_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/")
        if not rel or rel in claimed or rel in leftover:
            continue
        candidate = workspace / rel
        if candidate.is_file():
            leftover.append(rel)
            if rel.lower() == "cmakelists.txt" and "CMakeLists.txt" not in claimed and "CMakeLists.txt" not in leftover:
                leftover.append("CMakeLists.txt")
            continue
        if rel.lower() == "cmakelists.txt":
            # Live L2-15 remint-16: leftover leased the existing
            # ``cmakelists.txt`` and docs no_op'd. Official Linux cmake
            # needs the exact ``CMakeLists.txt`` basename as a write target.
            if "CMakeLists.txt" not in claimed and "CMakeLists.txt" not in leftover:
                leftover.append("CMakeLists.txt")
            try:
                for path in workspace.iterdir():
                    if path.is_file() and path.name.lower() == "cmakelists.txt":
                        name = path.name
                        if name not in claimed and name not in leftover:
                            leftover.append(name)
                        break
            except OSError:
                continue
    # Live L2-15 remint-4: leftover leased energy.hpp (note/include site)
    # while src/main.cpp still failed. Prefer translation units + cmake lists.
    preferred = [
        path
        for path in leftover
        if Path(path).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"} or path.lower() == "cmakelists.txt"
    ]
    if _cpp_residuals_have_std_namespace_pollution(residual_errors):
        headers = [path for path in workspace_quality_unclosed_namespace_headers(workspace) if path not in claimed]
        if headers:
            return list(dict.fromkeys([*headers, *preferred, *leftover]))
    return preferred or leftover


def workspace_quality_unclaimed_failing_tu_targets(
    residual_errors: Sequence[str],
    *,
    claimed_targets: Sequence[str],
    workspace: Path,
) -> list[str]:
    """Return unclaimed ``### path`` translation units only.

    Post-progress owner rotation must ignore header/note sites and artifact-
    scan noise. Live remint-9 mutated queue.hpp as ``progress`` while
    ``### src/main.cpp`` stayed red; only the official C++ wrapper's
    ``###`` list is a safe rotate signal.
    """

    claimed = {str(path or "").strip().replace("\\", "/") for path in claimed_targets if str(path or "").strip()}
    leftover: list[str] = []
    blob = "\n".join(str(item or "") for item in residual_errors)
    indexed: list[str] = []
    for match in _CPP_FAILING_TU_INDEX_RE.finditer(blob):
        indexed.extend(str(match.group("paths") or "").split())
    for rel in (*indexed, *(str(match.group("path") or "") for match in _CPP_FAILING_TU_RE.finditer(blob))):
        rel = rel.replace("\\", "/").strip()
        if not rel or rel == "FAILING_TUS" or rel in claimed or rel in leftover:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in {".c", ".cc", ".cpp", ".cxx"}:
            continue
        if (workspace / rel).is_file():
            leftover.append(rel)
    if _cpp_residuals_have_std_namespace_pollution(residual_errors):
        headers = [
            path
            for path in workspace_quality_unclosed_namespace_headers(workspace)
            if path not in claimed and path not in leftover
        ]
        if headers:
            leftover = [*headers, *leftover]
    return leftover


def workspace_quality_deferred_owner_targets(summary: dict[str, Any]) -> list[str]:
    """Return precise targets deferred because the first repair task did not own them."""

    if str(summary.get("stage") or "").strip() != "task_boundary_repair_targets_deferred":
        return []
    scope_filter = summary.get("task_boundary_scope_filter")
    if not isinstance(scope_filter, Mapping):
        return []
    raw_targets = scope_filter.get("out_of_scope_repair_target_files")
    if not isinstance(raw_targets, list | tuple | set):
        return []
    skipped_parts = {".git", ".polaris", "runtime", "node_modules", "__pycache__"}
    cleaned: list[str] = []
    for item in raw_targets:
        rel = str(item or "").strip().replace("\\", "/")
        if not rel:
            continue
        parts = Path(rel).parts
        if any(part.startswith(".") or part in skipped_parts for part in parts):
            continue
        cleaned.append(rel)
    return _dedupe_workspace_repair_paths(cleaned)


def workspace_quality_interface_discrepancy_evidence(
    summary: dict[str, Any],
    artifact_quality_errors: list[str] | None = None,
) -> dict[str, Any]:
    raw_evidence = summary.get("interface_discrepancy_evidence")
    evidence: dict[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    plan_probe = summary.get("plan_probe_preaudit")
    plan_probe_payload = plan_probe if isinstance(plan_probe, dict) else {}
    covered_unplannable_source_tools = [
        str(item)
        for item in plan_probe_payload.get(
            "covered_unplannable_source_tools",
            evidence.get("covered_unplannable_source_tools", []),
        )
        if str(item or "").strip()
    ]
    if not evidence:
        evidence = {
            "schema_version": "director.interface_discrepancy_receipt.v1",
            "route": "task_boundary_quality_loop",
            "plan_probe_status": str(plan_probe_payload.get("status") or ""),
            "covered_unplannable_source_tools": covered_unplannable_source_tools,
            "covered_unplannable_diagnostic_count": int(
                plan_probe_payload.get("covered_unplannable_diagnostic_count") or 0
            ),
            "coverage_gap_count": int(plan_probe_payload.get("coverage_gap_count") or 0),
            "reason": "coverage_matched_but_unplannable",
        }
    diagnostic_blob = "\n".join(
        [
            json.dumps(plan_probe_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            *[str(item or "") for item in artifact_quality_errors or []],
        ]
    ).lower()
    cross_artifact_markers = (
        "unresolved import",
        "unresolved relative import",
        "cannot find module",
        "has no exported member",
        "module has no exported member",
        "does not provide an export",
        "sibling module does not define",
        "is not exported",
        "undefined:",
        "undefined symbol",
        "unresolved external symbol",
        "undefined reference",
        "cannot find symbol",
        "cannot find type",
        "could not find",
        "no such file or directory",
        "file not found for module",
        "unresolved import `",
        "no `",
        "not found in",
        "was not declared in this scope",
        "no member named",
        "has no member named",
        "ts2305",
        "ts2306",
        "ts2307",
        "ts2459",
        "e0432",
        "e0583",
        "e0761",
    )
    local_implementation_markers = (
        "ts2322",
        "ts2339",
        "ts2345",
        "ts2552",
        "property ",
        "does not exist on type",
        "cannot find name",
        "type ",
        "is not assignable to type",
    )
    raw_owner_evidence = summary.get("task_boundary_owner_evidence")
    owner_evidence = dict(raw_owner_evidence) if isinstance(raw_owner_evidence, Mapping) else {}
    owner_target_files = _dedupe_workspace_repair_paths(owner_evidence.get("owner_target_files") or [])
    diagnostic_target_files = _dedupe_workspace_repair_paths(owner_evidence.get("diagnostic_target_files") or [])
    in_scope_diagnostic_target_files = _dedupe_workspace_repair_paths(
        owner_evidence.get("in_scope_diagnostic_target_files") or []
    )
    claimed_task_subset_allows_director_retry = (
        str(owner_evidence.get("source") or "") == "task_runtime_execution_attempt"
        and bool(str(owner_evidence.get("task_id") or "").strip())
        and bool(owner_target_files)
        and bool(diagnostic_target_files)
        and bool(in_scope_diagnostic_target_files)
        and set(in_scope_diagnostic_target_files).issubset(set(diagnostic_target_files))
        and set(in_scope_diagnostic_target_files).issubset(set(owner_target_files))
        and bool(owner_evidence.get("director_local_repair_allowed"))
    )
    cross_artifact = any(marker in diagnostic_blob for marker in cross_artifact_markers)
    local_implementation = any(marker in diagnostic_blob for marker in local_implementation_markers)
    if claimed_task_subset_allows_director_retry:
        # An import/symbol diagnostic can cross module files without crossing
        # the current Director authority.  A verifier payload may contain paths
        # from several tasks; repair only this claim's in-scope subset, then
        # re-run the verifier so the residual selects the next owner.  This
        # never authorizes writes outside ``owner_target_files``.
        recommended_owner = "director"
        recommended_route = "director_retry_with_interface_discrepancy_context"
        cross_artifact_route = "director_repair_within_claimed_task"
    elif cross_artifact:
        recommended_owner = "chief_engineer"
        recommended_route = "pending_design_interface_contract"
        cross_artifact_route = "contract_amendment_request"
    elif local_implementation:
        recommended_owner = "director"
        recommended_route = "director_retry_with_interface_discrepancy_context"
        cross_artifact_route = "director_repair_within_contract"
    else:
        recommended_owner = str(evidence.get("recommended_owner") or "chief_engineer")
        recommended_route = str(evidence.get("recommended_route") or "pending_design_interface_contract")
        cross_artifact_route = (
            "director_repair_within_contract" if recommended_owner == "director" else "contract_amendment_request"
        )
    director_retry_allowed = (
        recommended_owner == "director" and recommended_route == "director_retry_with_interface_discrepancy_context"
    )
    plan_probe_status = str(evidence.get("plan_probe_status") or plan_probe_payload.get("status") or "")
    covered_unplannable_diagnostic_count = int(
        plan_probe_payload.get(
            "covered_unplannable_diagnostic_count",
            evidence.get("covered_unplannable_diagnostic_count") or 0,
        )
        or 0
    )
    coverage_gap_count = int(plan_probe_payload.get("coverage_gap_count", evidence.get("coverage_gap_count") or 0) or 0)
    metadata_raw = evidence.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    metadata.update(
        {
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": cross_artifact_route,
            "coverage_gap_count": coverage_gap_count,
            "task_boundary_owner_evidence": owner_evidence,
        }
    )
    canonical = DirectorInterfaceDiscrepancyReceiptV1.from_mapping(
        {
            **evidence,
            "task_id": str(
                summary.get("task_id") or summary.get("target_task_id") or summary.get("run_id") or "workspace-quality"
            ),
            "source": evidence.get("source") or "factory.pipeline.workspace_quality",
            "plan_probe_status": plan_probe_status,
            "covered_unplannable_source_tools": covered_unplannable_source_tools,
            "recommended_owner": recommended_owner,
            "recommended_route": recommended_route,
            "director_retry_allowed": director_retry_allowed,
            "llm_fallback_blocked": not director_retry_allowed,
            "reason": "coverage_matched_but_unplannable",
            "metadata": metadata,
        },
    ).to_dict()
    canonical.update(
        {
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": cross_artifact_route,
            "coverage_gap_count": coverage_gap_count,
            "covered_unplannable_diagnostic_count": covered_unplannable_diagnostic_count,
        }
    )
    return canonical


def workspace_quality_interface_discrepancy_allows_director_retry(evidence: dict[str, Any]) -> bool:
    return bool(evidence.get("director_retry_allowed")) and (
        str(evidence.get("recommended_owner") or "") == "director"
        and str(evidence.get("recommended_route") or "") == "director_retry_with_interface_discrepancy_context"
    )


def workspace_quality_claimed_owner_repair_targets(evidence: dict[str, Any]) -> list[str]:
    """Return only diagnostic targets covered by the authorized task claim."""

    if not workspace_quality_interface_discrepancy_allows_director_retry(evidence):
        return []
    metadata_raw = evidence.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    owner_raw = metadata.get("task_boundary_owner_evidence")
    owner = dict(owner_raw) if isinstance(owner_raw, Mapping) else {}
    if (
        str(owner.get("source") or "") != "task_runtime_execution_attempt"
        or not bool(str(owner.get("task_id") or "").strip())
        or not bool(owner.get("director_local_repair_allowed"))
    ):
        return []
    owner_targets = set(_dedupe_workspace_repair_paths(owner.get("owner_target_files") or []))
    in_scope_targets = _dedupe_workspace_repair_paths(owner.get("in_scope_diagnostic_target_files") or [])
    return [path for path in in_scope_targets if path in owner_targets]


def workspace_quality_repair_summary_projection(
    summary: dict[str, Any],
    artifact_quality_errors: list[str] | None = None,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in (
        "task_id",
        "stage",
        "attempt",
        "success",
        "success_reason",
        "reason",
        "error_code",
        "error",
        "repair_mode",
        "missing_target_files",
        "runtime_smoke_target_files",
        "semantic_quality_target_files",
        "explicit_quality_target_files",
        "repair_target_files",
        "rotated_repair_targets",
        "task_boundary_scope_filter",
        "task_boundary_owner_evidence",
        "task_boundary_interface_discrepancy_retry_authorized",
        "deferred_owner_rebind",
        "plan_probe_preaudit",
        "interface_discrepancy_evidence",
        "deterministic_no_materialized_evidence",
        "repair_kernel",
        "deadline_decision",
    ):
        if key in summary:
            projected[key] = summary[key]
    if projected:
        task_boundary_triage_required = workspace_quality_summary_requires_task_boundary_triage(summary)
        projected["task_boundary_triage_required"] = task_boundary_triage_required
        if task_boundary_triage_required:
            projected["triage_stage"] = "runtime_plan_probe_unplannable"
            projected["interface_discrepancy_evidence"] = workspace_quality_interface_discrepancy_evidence(
                summary,
                artifact_quality_errors,
            )
    return projected


def _bounded_ledger_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item or "").strip()[:_LEDGER_REPAIR_TEXT_LIMIT] for item in value if str(item or "").strip()][
        :_LEDGER_REPAIR_LIST_LIMIT
    ]


def _compact_repair_probe(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    projected: dict[str, Any] = {}
    for key in (
        "status",
        "total_diagnostics",
        "covered_diagnostic_count",
        "uncovered_diagnostic_count",
        "executable_runtime_plan_count",
        "metadata_only_diagnostic_count",
        "coverage_gap_count",
        "covered_unplannable_diagnostic_count",
    ):
        if key in source:
            projected[key] = source[key]
    for key in (
        "plannable_source_tools",
        "covered_unplannable_source_tools",
        "matched_source_tools",
        "source_tools",
    ):
        bounded = _bounded_ledger_strings(source.get(key))
        if bounded:
            projected[key] = bounded
    return projected


def _compact_repair_round(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    projected: dict[str, Any] = {}
    for key in (
        "round",
        "attempted",
        "tool_results",
        "write_tool_evidence",
        "verifier_effect",
        "verifier_authoritative_success",
        "diagnostic_count_before",
        "diagnostic_count_after",
    ):
        if key in source:
            projected[key] = source[key]
    summary = source.get("repair_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    summary_projection = {
        key: summary[key]
        for key in (
            "stage",
            "success",
            "success_reason",
            "reason",
            "error_code",
            "repair_mode",
            "success_authority",
            "verifier_effect",
            "task_boundary_triage_required",
        )
        if key in summary
    }
    if summary_projection:
        projected["repair_summary"] = summary_projection
    return projected


def workspace_quality_repair_ledger_projection(
    repair: dict[str, Any],
    *,
    full_evidence_ref: str,
) -> dict[str, Any]:
    """Project full repair evidence into a bounded Run Ledger receipt.

    Full workspace-quality evidence is already durable in
    ``runtime/qa/workspace-validation.json``.  Re-embedding its nested coverage
    reports in every Run Ledger event duplicated megabytes of data and then
    exceeded NATS' 1 MiB transport limit.  Ledger keeps decision-critical
    scalars plus a content hash/reference; the artifact remains the evidence
    authority.
    """

    canonical = json.dumps(repair, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    projected: dict[str, Any] = {
        "schema_version": "factory.workspace_quality_repair_ledger_projection.v1",
        "full_evidence_ref": str(full_evidence_ref or "").strip(),
        "full_evidence_sha256": hashlib.sha256(canonical).hexdigest(),
        "full_evidence_bytes": len(canonical),
    }
    for key in (
        "attempted",
        "success",
        "revalidated",
        "write_tool_evidence",
        "tool_results",
        "residual_error_count",
        "max_rounds",
        "consecutive_stagnant_rounds",
        "convergence_stop_reason",
        "stage",
        "error_code",
        "repair_mode",
    ):
        if key in repair:
            projected[key] = repair[key]
    for key in ("source_tools", "evidence", "artifact_quality_errors", "residual_errors"):
        bounded = _bounded_ledger_strings(repair.get(key))
        if bounded:
            projected[key] = bounded
            projected[f"{key}_total"] = len(repair.get(key) or [])
    for key in ("plan_probe_preaudit", "director_runtime_repair_coverage"):
        compact = _compact_repair_probe(repair.get(key))
        if compact:
            projected[key] = compact
    rounds = [_compact_repair_round(item) for item in repair.get("rounds", []) if isinstance(item, Mapping)]
    if rounds:
        projected["rounds"] = rounds[:_LEDGER_REPAIR_LIST_LIMIT]
        projected["round_count"] = len(repair.get("rounds") or [])
    return projected
