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
_CPP_HEADER_OWNED_DIAGNOSTIC_RE = re.compile(
    r"(?P<path>(?:src|include)/[^\s:'\"]+\.(?:h|hh|hpp|hxx)):\d+(?::\d+)?:\s+error:\s+"
    r"(?:"
    r".*does not name a type"
    r"|.*is not a member of\s+['\"‘’]std['\"‘’]"
    r")",
    re.IGNORECASE,
)
_CPP_UNKNOWN_TYPE_NAME_RE = re.compile(
    r"['\"‘’](?P<name>[A-Za-z_]\w*)['\"‘’]\s+does not name a type",
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
_UNITTEST_TRACEBACK_TEST_RE = re.compile(
    r'File "[^"\n]+/(?P<path>tests/test_[^"/\s]+\.py)"',
    re.IGNORECASE,
)
_PYTHON_TRACEBACK_SRC_RE = re.compile(
    r'File "[^"\n]+/(?P<path>src/[^"\n]+\.py)"',
    re.IGNORECASE,
)
_PYTHON_MODULE_NOT_FOUND_NAME_RE = re.compile(
    r"ModuleNotFoundError:\s+No module named ['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)['\"]",
    re.IGNORECASE,
)
_TYPESCRIPT_SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
_FAILING_TU_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".java"}) | _TYPESCRIPT_SOURCE_SUFFIXES
_TYPESCRIPT_TSC_ERROR_RE = re.compile(
    r"(?m)^(?P<path>(?:src|tests|lib|app)/[^\s:()]+?\.(?:ts|tsx|mts|cts))\(\d+,\d+\):\s+error\s+TS\d+",
    re.IGNORECASE,
)
_NODE_STACK_SOURCE_RE = re.compile(
    r"(?P<path>(?:src|tests)/[A-Za-z0-9_./-]+\.(?:ts|tsx|mts|cts|js|mjs|cjs)):\d+(?::\d+)?",
    re.IGNORECASE,
)
_JS_TAP_LOCATION_RE = re.compile(
    r"(?im)location:\s*['\"][^'\"]*?(?P<path>(?:src|tests)/[A-Za-z0-9_./-]+\.(?:js|mjs|cjs)):(?P<line>\d+)"
)
_JS_CALL_IDENT_RE = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
_JS_TAP_RESERVED_CALLS = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "function",
        "return",
        "typeof",
        "await",
        "void",
        "assert",
        "test",
        "describe",
        "it",
        "equal",
        "deepEqual",
        "strictEqual",
        "throws",
        "doesNotThrow",
        "ok",
        "ifError",
        "match",
        "doesNotMatch",
        "rejects",
        "doesNotReject",
        "notEqual",
        "notDeepEqual",
        "notStrictEqual",
        "Error",
        "TypeError",
        "Object",
        "Array",
        "String",
        "Number",
        "Boolean",
        "JSON",
        "Math",
        "console",
        "require",
        "import",
    }
)
_CPP_LINKER_TU_RE = re.compile(
    r"(?:(?P<obj>[A-Za-z_][\w-]*)\.cpp\.o|(?P<file>[A-Za-z_][\w-]*\.cpp):)",
    re.IGNORECASE,
)


_CPP_RUNTIME_CTOR_THROW_RE = re.compile(
    r"what\(\):\s+(?P<type>[A-Za-z_]\w*)::(?P=type):",
    re.IGNORECASE,
)


_DELIVERY_DEPTH_PROD_SHORTFALL_RE = re.compile(
    r"(?:production_source_(?:files|lines)|behavior_symbol_count|branch_count)\s*=\s*\d+\s*<",
    re.IGNORECASE,
)
_PROD_SOURCE_SUFFIXES = {
    ".java",
    ".kt",
    ".scala",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
}


def _delivery_depth_prod_shortfall_targets(blob: str, workspace: Path) -> list[str]:
    """Lease production sources when delivery-depth is the residual.

    Live L2-16: unittest went green; leftover still leased tests/ because the
    depth blob mentioned ``test_files=2`` / ``File ".../tests/test_*.py"``.
    Depth shortfalls are implementation size, not test rewrites.
    """

    # ``delivery_depth_contract_failed`` is a container error.  It may contain
    # only test_source_files/test_assertion_count failures.  Treating the
    # container marker itself as a production shortfall leased a source task
    # for L3-21 even though the final provider request correctly identified a
    # test-only residual.  Require an actual production-depth metric.
    if _DELIVERY_DEPTH_PROD_SHORTFALL_RE.search(blob) is None or not workspace.is_dir():
        return []
    search_roots = [path for path in (workspace / "src" / "main" / "java", workspace / "src") if path.is_dir()]
    if not search_roots:
        return []
    found: list[str] = []
    for root in search_roots:
        try:
            hits = [path for path in root.rglob("*") if path.is_file()]
        except OSError:
            continue
        for path in sorted(hits, key=lambda item: item.as_posix()):
            if path.suffix.lower() not in _PROD_SOURCE_SUFFIXES:
                continue
            if any(part.lower() in {"test", "tests"} for part in path.parts):
                continue
            if any(part in {"build", "out", "target", "runtime", ".polaris"} for part in path.parts):
                continue
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel not in found:
                found.append(rel)
        if found:
            break
    return found


_JAVA_PUBLIC_TYPE_FILE_RE = re.compile(
    r"(?P<path>[^\s:]+\.java):\d+:\s+error:\s+(?:class|enum|interface|record)\s+"
    r"(?P<type>[A-Za-z_]\w*)\s+is public, should be declared in a file named "
    r"(?P<want>[A-Za-z_]\w*\.java)",
    re.IGNORECASE,
)


_JAVA_MISSING_PACKAGE_SYMBOL_RE = re.compile(
    r"symbol:\s+class\s+(?P<type>[A-Za-z_]\w*)\s+"
    r"location:\s+package\s+(?P<pkg>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
    re.IGNORECASE,
)


def _java_sibling_declares_nested_type(workspace: Path, package_dir: Path, type_name: str) -> bool:
    """True when an existing same-package .java already declares nested ``type_name``."""

    parent = workspace / package_dir
    if not parent.is_dir() or not type_name:
        return False
    nested = re.compile(
        rf"\b(?:(?:public|protected|private|static|final|sealed|non-sealed)\s+)*"
        rf"(?:class|enum|interface|record)\s+{re.escape(type_name)}\b"
    )
    try:
        siblings = list(parent.glob("*.java"))
    except OSError:
        return False
    for path in siblings:
        if path.stem == type_name:
            continue
        try:
            if path.stat().st_size <= 0:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if nested.search(text):
            return True
    return False


def _java_missing_package_symbol_targets(blob: str, workspace: Path) -> list[str]:
    """Map ``cannot find symbol class X`` + package P onto ``src/main/java/P/X.java``.

    Live L2-16 remint-5: Melody.java existed, then javac failed
    ``class Plant`` / ``class Season`` in ``polaris.factory.domain`` while
    leftover stayed on claimed PlantEngine.java.
    """

    found: list[str] = []
    if "cannot find symbol" not in blob.lower():
        return found
    for match in _JAVA_MISSING_PACKAGE_SYMBOL_RE.finditer(blob):
        type_name = str(match.group("type") or "").strip()
        pkg = str(match.group("pkg") or "").strip()
        if not type_name or not pkg:
            continue
        rel = "src/main/java/" + pkg.replace(".", "/") + "/" + type_name + ".java"
        parent = workspace / Path(rel).parent
        if not parent.is_dir():
            continue
        candidate = workspace / rel
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                continue
        except OSError:
            continue
        if _java_sibling_declares_nested_type(workspace, Path(rel).parent, type_name):
            # Live L2-16 remint-11: ``class Note`` already lives in Melody.java.
            # Inventing domain/Note.java forced write_file and a type clash.
            continue
        if rel not in found:
            found.append(rel)
    return found


def _java_official_public_type_targets(
    blob: str,
    compile_tus: Sequence[str] | None = None,
) -> list[str]:
    """Map javac public-type filename errors onto the official basename.

    Live L2-16 remint-2: eight source-modules rounds edited ``melodymodel.java``
    / dropped ``public`` instead of writing ``Melody.java``.
    Live L2-16 remint-12: FAILING_TUS stayed on PlantEngine.java, but a
    public-type residual on plantmodel.java rotated leftover to a missing
    PlantModel.java create. Only rewrite the official basename of a
    still-red compile TU.
    """

    compile_set = {
        str(path or "").replace("\\", "/").strip() for path in (compile_tus or []) if str(path or "").strip()
    }
    found: list[str] = []
    for match in _JAVA_PUBLIC_TYPE_FILE_RE.finditer(blob):
        src = str(match.group("path") or "").replace("\\", "/").strip()
        want = str(match.group("want") or "").strip()
        if not src or not want:
            continue
        if compile_set and src not in compile_set:
            continue
        official = Path(src).with_name(want).as_posix()
        if official not in found:
            found.append(official)
        if src not in found:
            found.append(src)
    return found


def _drop_java_case_duplicate_paths(paths: Sequence[str]) -> list[str]:
    """Keep official PascalCase .java when a case-variant sibling is listed."""

    chosen: dict[tuple[str, str], str] = {}
    order: list[tuple[str, str]] = []
    for raw in paths:
        path = str(raw or "").strip().replace("\\", "/")
        if not path.endswith(".java"):
            key = ("", path)
            if key not in chosen:
                chosen[key] = path
                order.append(key)
            continue
        key = (str(Path(path).parent.as_posix()), Path(path).name.lower())
        current = chosen.get(key)
        if current is None:
            chosen[key] = path
            order.append(key)
            continue
        if Path(path).stem[:1].isupper() and not Path(current).stem[:1].isupper():
            chosen[key] = path
    return [chosen[key] for key in order]


def _prefer_java_prod_failing_tus(paths: Sequence[str]) -> list[str]:
    """Keep src/main/java ahead of src/test when both javac-fail.

    Live L2-16 remint-1: official javac listed plantenginetest.java. After
    claiming prod files leftover_tus rotated to TASK-1-tests.
    """

    ordered = [str(path or "").strip().replace("\\", "/") for path in paths if str(path or "").strip()]
    prod = [
        path
        for path in ordered
        if path.endswith(".java") and "/test/" not in f"/{path.lower()}/" and "/tests/" not in f"/{path.lower()}/"
    ]
    return prod or ordered


def _prefer_java_unittest_helper_when_official_compile_green(
    leftover: Sequence[str],
    blob: str,
    workspace: Path,
) -> list[str]:
    """Keep the unittest helper when official javac already passed.

    Live L2-16 remint-24: official wrapper printed ``Java javac passed for
    9 source file(s)``. Unittest staging javac of PlantEngine.java alone
    still failed. leftover leased PlantEngine and R8 deferred to
    TASK-1-source-modules.
    """

    ordered = [str(path or "").strip().replace("\\", "/") for path in leftover if str(path or "").strip()]
    if "### FAILING_TUS" in blob:
        return ordered
    helpers: list[str] = [
        path
        for path in ordered
        if path.endswith(".py") and (path.startswith("tests/") or "/tests/" in f"/{path.lower()}/")
    ]
    for match in _UNITTEST_TRACEBACK_TEST_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if (
            rel.endswith(".py")
            and rel not in helpers
            and (workspace / rel).is_file()
            and (rel.startswith("tests/") or "/tests/" in f"/{rel.lower()}/")
        ):
            helpers.append(rel)
    staging_only = "build/staging" in blob.replace("\\", "/")
    unittest_javac = "javac failed" in blob.lower() and "test_product.py" in blob
    if helpers and staging_only and unittest_javac:
        return list(dict.fromkeys(helpers))
    return ordered


def _workspace_relative_source_exists(workspace: Path, rel: str) -> bool:
    cleaned = str(rel or "").replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ".." in Path(cleaned).parts:
        return False
    try:
        return (workspace / cleaned).is_file()
    except OSError:
        return False


def _python_modulenotfound_src_importer_targets(blob: str, workspace: Path) -> list[str]:
    """Prefer the src/ importer from a ModuleNotFoundError traceback.

    Live L2-19: official ``unittest discover`` failed because
    ``src/engine/__init__.py`` imported ``waterdrop_rhythm_pad``. Leftover
    only accepted ``tests/test_product.py`` and never leased the in-scope
    production importer.
    """

    match = _PYTHON_MODULE_NOT_FOUND_NAME_RE.search(str(blob or ""))
    if match is None:
        return []
    module = str(match.group("module") or "").strip()
    found: list[str] = []
    for frame in _PYTHON_TRACEBACK_SRC_RE.finditer(str(blob or "")):
        rel = str(frame.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in found and _workspace_relative_source_exists(workspace, rel):
            found.append(rel)
    src_root = workspace / "src"
    if module and src_root.is_dir():
        import_re = re.compile(rf"(?m)^from[ \t]+{re.escape(module)}(?:\.|[ \t]+import)")
        try:
            python_files = sorted(src_root.rglob("*.py"))
        except OSError:
            python_files = []
        for path in python_files:
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel in found or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if import_re.search(text) and _workspace_relative_source_exists(workspace, rel):
                found.append(rel)
    return found


def _is_typescript_test_path(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").strip().lower()
    name = Path(rel).name.lower()
    return (
        rel.startswith("tests/")
        or "/tests/" in f"/{rel}/"
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
    )


def _typescript_tsc_error_targets(blob: str, workspace: Path) -> list[str]:
    """Parse official ``tsc`` ``path.ts(n,m): error TSxxxx`` sites.

    Live L2-17 remint-3: official ``npm run build`` printed simulation /
    reputation / web residuals, but leftover only accepted ``### FAILING_TUS``
    C++/Java suffixes. Eight TASK-2 rounds never rotated onto the still-red
    source-models owner.
    """

    found: list[str] = []
    for match in _TYPESCRIPT_TSC_ERROR_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in found and _workspace_relative_source_exists(workspace, rel):
            found.append(rel)
    return found


def _typescript_runtime_stack_targets(blob: str, workspace: Path) -> list[str]:
    """Parse Node TAP / stack frames such as ``src/verify.ts:11:19``.

    Live L2-17 remint-3: ``npm test`` failed ``ENOENT scandir '/tmp/src'``
    from ``filesUnder`` in ``src/verify.ts``. That is not a tsc site.
    """

    found: list[str] = []
    for match in _NODE_STACK_SOURCE_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in found and _workspace_relative_source_exists(workspace, rel):
            found.append(rel)
    impl = [path for path in found if path.startswith("src/") and not _is_typescript_test_path(path)]
    if impl:
        return [*impl, *[path for path in found if path not in impl]]
    return found


_JS_TAP_NOT_DEFINED_RE = re.compile(
    r"(?:error:\s*['\"]|ReferenceError:\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*) is not defined",
    re.IGNORECASE,
)
_JS_UNDECLARED_ASSIGN_TEMPLATE = r"(?m)^(?P<indent>\s*){name}\s*="


_JS_SYNTAX_ERROR_TEST_RE = re.compile(
    r"(?:syntax error in |SyntaxError[^\n]*?)(?P<path>tests/[A-Za-z0-9_./-]+\.(?:js|mjs|cjs|ts|tsx))",
    re.IGNORECASE,
)


def _javascript_syntax_broken_official_tests(blob: str, workspace: Path) -> list[str]:
    """Keep leftover on the official TAP file while it cannot parse.

    Live L2-18 remint-9: TASK-2 deleted the meteor ``describe(`` opener.
    ``node --test`` failed ``Unexpected token '}'`` at the leftover closer.
    leftover then leased ``src/queue.js`` and the official suite stayed
    unparseable.
    """

    found: list[str] = []
    for match in _JS_SYNTAX_ERROR_TEST_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in found and _workspace_relative_source_exists(workspace, rel):
            found.append(rel)
    return found


def _javascript_tap_reference_error_impl_targets(blob: str, workspace: Path) -> list[str]:
    """Map TAP ``X is not defined`` onto undeclared ``X =`` assignments.

    Live L2-18 remint-7: Director dropped ``const`` on
    ``text = clampString(...)`` in ``src/wish.js``. TAP located the
    ``test()`` header, leftover stayed on tests, and ESM threw
    ReferenceError on every makeWish path.
    """

    names = [
        str(match.group("name") or "")
        for match in _JS_TAP_NOT_DEFINED_RE.finditer(blob)
        if str(match.group("name") or "")
    ]
    if not names:
        return []
    src_root = workspace / "src"
    if not src_root.is_dir():
        return []
    patterns = {
        name: re.compile(_JS_UNDECLARED_ASSIGN_TEMPLATE.format(name=re.escape(name))) for name in dict.fromkeys(names)
    }
    found: list[str] = []
    try:
        files = [path for suffix in ("*.js", "*.mjs", "*.cjs") for path in src_root.rglob(suffix) if path.is_file()]
    except OSError:
        return []
    for path in sorted(files, key=lambda item: item.as_posix()):
        try:
            rel = path.relative_to(workspace).as_posix()
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        name_token = Path(rel).name.lower()
        if (
            any(part in {"node_modules", "dist", "build"} for part in Path(rel).parts)
            or ".test." in name_token
            or ".spec." in name_token
        ):
            continue
        for pattern in patterns.values():
            match = pattern.search(text)
            if match is None:
                continue
            indent_line = match.group(0)
            if re.match(r"^\s*(?:const|let|var)\s+", indent_line):
                continue
            # Same-line declarator already handled; reject `const` on the match
            # start by checking the line prefix.
            line_start = text.rfind("\n", 0, match.start()) + 1
            line = text[line_start : match.end()]
            if re.match(r"^\s*(?:const|let|var)\b", line):
                continue
            if rel not in found:
                found.append(rel)
            break
    return found


def _javascript_tap_callee_impl_targets(blob: str, workspace: Path) -> list[str]:
    """Map TAP location-only residuals onto existing src/*.js callees.

    Live L2-18 remint-5: official residual listed only
    ``tests/product.test.js:206`` and ``meteorId must be a non-empty string``.
    No stack frame named ``src/wish.js``. After TASK-2 claimed the TAP
    file, leftover stayed on tests until equal_count_swap.
    """

    names: list[str] = []
    for match in _JS_TAP_LOCATION_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if not rel or not _workspace_relative_source_exists(workspace, rel):
            continue
        try:
            line_number = max(1, int(match.group("line")))
            lines = (workspace / rel).read_text(encoding="utf-8").splitlines()
        except (OSError, TypeError, UnicodeDecodeError, ValueError):
            continue
        start = max(0, line_number - 4)
        end = min(len(lines), line_number + 20)
        for raw in lines[start:end]:
            for call in _JS_CALL_IDENT_RE.finditer(str(raw or "")):
                name = str(call.group("name") or "")
                if name and name not in _JS_TAP_RESERVED_CALLS and name not in names:
                    names.append(name)
    if not names:
        return []
    src_root = workspace / "src"
    if not src_root.is_dir():
        return []
    patterns = {name: re.compile(rf"(?m)^(?:export\s+)?(?:async\s+)?function\s+{re.escape(name)}\b") for name in names}
    found: list[str] = []
    found_names: set[str] = set()
    try:
        files = [path for suffix in ("*.js", "*.mjs", "*.cjs") for path in src_root.rglob(suffix) if path.is_file()]
    except OSError:
        return []
    for path in sorted(files, key=lambda item: item.as_posix()):
        try:
            rel = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        name_token = Path(rel).name.lower()
        if (
            any(part in {"node_modules", "dist", "build"} for part in Path(rel).parts)
            or ".test." in name_token
            or ".spec." in name_token
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in patterns.items():
            if name in found_names:
                continue
            if pattern.search(text) is None:
                continue
            if rel not in found:
                found.append(rel)
            found_names.add(name)
        if len(found_names) >= len(names):
            break
    return found


def _demote_python_unittest_helpers_when_js_impl(paths: Sequence[str]) -> list[str]:
    """After official TAP is claimed, do not let test_*.py block src/*.js.

    Live L2-18 remint-11 leftover after claiming ``tests/product.test.js``
    was ``tests/test_product.py`` first. TASK-2 re-leased the Python helper
    for six rounds while ``src/index.js`` (validateIndex/createIndex TAP)
    never became leftover[0].
    """

    ordered = [str(path or "").strip().replace("\\", "/") for path in paths if str(path or "").strip()]
    js_impl = [
        path for path in ordered if path.startswith("src/") and Path(path).suffix.lower() in {".js", ".mjs", ".cjs"}
    ]
    py_helpers = [path for path in ordered if path.endswith(".py") and Path(path).name.startswith("test_")]
    if not js_impl or not py_helpers:
        return ordered
    rest = [path for path in ordered if path not in js_impl and path not in py_helpers]
    official = [
        path
        for path in rest
        if path.endswith((".test.js", ".spec.js", ".test.mjs", ".spec.mjs")) or Path(path).name.endswith(".test.cjs")
    ]
    other = [path for path in rest if path not in official]
    return [*official, *js_impl, *other, *py_helpers]


def _prefer_javascript_official_tap_tests(paths: Sequence[str]) -> list[str]:
    """Keep official Node TAP tests ahead of Python unittest helpers.

    Live L2-18 remint-2: leftover listed ``tests/test_product.py`` first because
    the TAP residual also mentioned that helper. Ten QA rounds stayed on
    ``src/meteor.js`` after TASK-2 could not lease the Python helper, while
    official ``npm test`` was ``tests/product.test.js``.
    """

    ordered = [str(path or "").strip().replace("\\", "/") for path in paths if str(path or "").strip()]
    js_tests = [
        path
        for path in ordered
        if path.endswith((".test.js", ".spec.js", ".test.mjs", ".spec.mjs")) or Path(path).name.endswith(".test.cjs")
    ]
    if not js_tests:
        return ordered
    other = [path for path in ordered if path not in js_tests]
    return [*js_tests, *other]


def _prefer_typescript_compile_sites(paths: Sequence[str]) -> list[str]:
    """Keep still-red tsc compile sites ahead of tests/ while compile fails."""

    ordered = [str(path or "").strip().replace("\\", "/") for path in paths if str(path or "").strip()]
    ts_compile = [
        path
        for path in ordered
        if Path(path).suffix.lower() in _TYPESCRIPT_SOURCE_SUFFIXES and not _is_typescript_test_path(path)
    ]
    other = [path for path in ordered if Path(path).suffix.lower() not in _TYPESCRIPT_SOURCE_SUFFIXES]
    if ts_compile:
        return [*ts_compile, *other]
    return ordered


def leftover_rotate_allows_quality_extra_round(
    *,
    round_index: int,
    max_rounds: int,
    leftover_extra_pending: bool,
    extra_cap: int = 2,
) -> bool:
    """Allow a leftover owner rotate after the last scheduled QA round.

    Live L2-17 remint-5: R8 greened ``npm run build`` so leftover_tus became
    ``src/verify.ts`` from the TAP stack, but ``max_rounds=8`` ended the
    loop before TASK-3 could lease the npm-test residual.
    """

    scheduled = max(1, int(max_rounds))
    cap = max(0, int(extra_cap))
    index = max(0, int(round_index))
    if leftover_extra_pending and index < scheduled + cap:
        return True
    return index < scheduled


def leftover_targets_should_force_owner_rotate(
    leftover: Sequence[str],
    claimed_targets: Sequence[str],
) -> bool:
    """Force leftover rotate only onto a path the last owner did not lease.

    Live L2-15 remint-22: leftover_tus kept returning claimed ``src/main.cpp``
    and reset ``consecutive_stagnant_rounds``, so four stagnant CLI-abort
    rounds never tripped the breaker.
    """

    if not leftover:
        return False
    first = str(leftover[0] or "").strip().replace("\\", "/")
    if not first:
        return False
    claimed = {str(path or "").strip().replace("\\", "/") for path in claimed_targets if str(path or "").strip()}
    if first in claimed:
        return False
    # patrol.cpp / patrol.hpp are one owner. Stem-equal leftover is stay,
    # not rotate — remint-22 reset the breaker on the same CLI file.
    claimed_stems = {Path(path).stem.lower() for path in claimed}
    return Path(first).stem.lower() not in claimed_stems


def _cpp_runtime_ctor_throw_targets(blob: str, workspace: Path) -> list[str]:
    """Map ``Type::Type:`` abort text onto existing type sources.

    Live L2-15 remint-22 unittest aborted
    ``Patrol::Patrol: requires at least 2 distinct nodes`` after eight
    ``src/main.cpp`` mutations. The throwing constructor lives on the type.
    """

    if not workspace.is_dir():
        return []
    types: list[str] = []
    for match in _CPP_RUNTIME_CTOR_THROW_RE.finditer(blob):
        name = str(match.group("type") or "").strip()
        if name and name not in types:
            types.append(name)
    if not types:
        return []
    search_roots = [path for path in (workspace / "src", workspace / "include") if path.is_dir()]
    if not search_roots:
        search_roots = [workspace]
    found: list[str] = []
    for name in types:
        stem = name.lower()
        for root in search_roots:
            try:
                hits = [
                    path
                    for ext in (".cpp", ".cc", ".cxx", ".c", ".hpp", ".hh", ".h", ".hxx")
                    for path in root.rglob(stem + ext)
                    if path.is_file() and "build" not in path.parts and "cmake-build" not in path.parts
                ]
            except OSError:
                continue
            for path in sorted(
                hits,
                key=lambda item: (0 if item.suffix.lower() in {".c", ".cc", ".cpp", ".cxx"} else 1, item.as_posix()),
            ):
                try:
                    rel = path.relative_to(workspace).as_posix()
                except ValueError:
                    continue
                if rel not in found:
                    found.append(rel)
    return found


def _rotate_claimed_leftover(paths: Sequence[str], claimed: set[str]) -> list[str]:
    """Drop claimed paths only when another still-failing path exists.

    Live L2-15 remint-21 R3: ``### FAILING_TUS src/main.cpp`` stayed red,
    claimed was that same TU, leftover then leased ``tests/test_product.py``.
    """

    ordered = [str(path or "").strip().replace("\\", "/") for path in paths if str(path or "").strip()]
    unclaimed = [path for path in ordered if path not in claimed]
    return unclaimed or ordered


def _prefer_cpp_cli_entrypoint_for_unittest_residuals(
    leftover: list[str],
    *,
    claimed: set[str],
    workspace: Path,
    persist_claimed: bool = False,
) -> list[str]:
    """CLI unittest residuals are implementation bugs first.

    Live L2-15 remint-20: leftover leased only ``tests/test_product.py``.
    Two equal_count_swap rounds stagnated while ``src/main.cpp`` still
    aborted (``Patrol requires at least 2 distinct nodes``).

    Immediate leftover_tus rotate (``persist_claimed=False``) must keep
    ``src/main.cpp`` even after one claimed attempt. Residual leftover
    after two same-owner stagnations (``persist_claimed=True``) may fall
    through to the unittest file.
    """

    if not any(path.startswith("tests/test_") and path.endswith(".py") for path in leftover):
        return leftover
    entry = "src/main.cpp"
    if entry in leftover or not (workspace / entry).is_file():
        return leftover
    if persist_claimed and entry in claimed:
        return leftover
    return [entry, *leftover]


def _cpp_linker_undefined_reference_targets(blob: str, workspace: Path) -> list[str]:
    """Map ld ``undefined reference`` object names to existing project TUs.

    Live L2-15 remint-18: official cmake --build printed
    ``queue.cpp.o`` / ``queue.cpp:(.text)`` without ``path:line: error:``.
    """

    if "undefined reference" not in blob.lower() or not workspace.is_dir():
        return []
    stems: list[str] = []
    for match in _CPP_LINKER_TU_RE.finditer(blob):
        raw = str(match.group("obj") or match.group("file") or "").strip()
        stem = Path(raw).stem if raw else ""
        if stem and stem not in stems:
            stems.append(stem)
    if not stems:
        return []
    search_roots = [path for path in (workspace / "src", workspace / "include") if path.is_dir()]
    if not search_roots:
        search_roots = [workspace]
    found: list[str] = []
    for stem in stems:
        for root in search_roots:
            try:
                hits = [
                    path
                    for ext in (".cpp", ".cc", ".cxx", ".c")
                    for path in root.rglob(stem + ext)
                    if path.is_file() and "build" not in path.parts and "cmake-build" not in path.parts
                ]
            except OSError:
                continue
            for path in sorted(hits, key=lambda item: item.as_posix()):
                try:
                    rel = path.relative_to(workspace).as_posix()
                except ValueError:
                    continue
                if rel not in found:
                    found.append(rel)
    return found


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


def _cpp_unknown_type_declaration_targets(blob: str, workspace: Path) -> list[str]:
    """Lease the existing header that should own an unknown C++ type.

    Live L2-20 reminted ``entity.hpp`` (use site) while leftover said
    ``'WindSample' does not name a type``. The type belongs in
    ``src/models/wind.hpp``. Prefer that existing stem header so leftover
    does not keep inventing the field on the use site.
    """

    if not workspace.is_dir() or "does not name a type" not in blob.lower():
        return []
    names: list[str] = []
    for match in _CPP_UNKNOWN_TYPE_NAME_RE.finditer(blob):
        name = str(match.group("name") or "").strip()
        if name and name not in names:
            names.append(name)
    stems: set[str] = set()
    for name in names:
        stems.add(name.lower())
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)
        if parts:
            stems.add(parts[0].lower())
    found: list[str] = []
    search_roots = [path for path in (workspace / "src", workspace / "include") if path.is_dir()]
    ignored = {"build", "cmake-build", "runtime", ".polaris", "out", "target"}
    for stem in stems:
        for root in search_roots:
            try:
                hits = [
                    path
                    for ext in (".hpp", ".hh", ".h", ".hxx")
                    for path in root.rglob(stem + ext)
                    if path.is_file() and not any(part in ignored for part in path.parts)
                ]
            except OSError:
                continue
            for path in sorted(hits, key=lambda item: item.as_posix()):
                try:
                    rel = path.relative_to(workspace).as_posix()
                except ValueError:
                    continue
                if rel not in found:
                    found.append(rel)
    return found


def _cpp_header_owned_diagnostic_targets(blob: str, workspace: Path) -> list[str]:
    """Lease the header that owns a type/std-member diagnostic.

    Live L2-20 leftover reminted ``entity.cpp`` while
    ``entity.hpp:39:5: error: 'WindSample' does not name a type`` and
    ``rule.hpp:161:42: error: 'unique_ptr' is not a member of 'std'`` stayed
    red. Prefer those header error sites over the including translation units
    without reviving L2-15 ``has not been declared`` header note sites.
    """

    if not workspace.is_dir():
        return []
    found: list[str] = []
    for match in _CPP_HEADER_OWNED_DIAGNOSTIC_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if not rel or rel in found:
            continue
        if (workspace / rel).is_file():
            found.append(rel)
    return found


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
            if not rel or rel in leftover:
                continue
            if (workspace / rel).is_file():
                leftover.append(rel)
    for match in _CPP_FAILING_TU_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/")
        if not rel or rel == "FAILING_TUS" or rel in leftover:
            continue
        if (workspace / rel).is_file():
            leftover.append(rel)
    for rel in _typescript_tsc_error_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _rotate_claimed_leftover(leftover, claimed)
    for match in _CPP_OR_CMAKE_RESIDUAL_PATH_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/")
        if not rel or rel in leftover:
            continue
        candidate = workspace / rel
        if candidate.is_file():
            leftover.append(rel)
            if rel.lower() == "cmakelists.txt" and "CMakeLists.txt" not in leftover:
                leftover.append("CMakeLists.txt")
            continue
        if rel.lower() == "cmakelists.txt":
            # Live L2-15 remint-16: leftover leased the existing
            # ``cmakelists.txt`` and docs no_op'd. Official Linux cmake
            # needs the exact ``CMakeLists.txt`` basename as a write target.
            if "CMakeLists.txt" not in leftover:
                leftover.append("CMakeLists.txt")
            try:
                for path in workspace.iterdir():
                    if path.is_file() and path.name.lower() == "cmakelists.txt":
                        name = path.name
                        if name not in leftover:
                            leftover.append(name)
                        break
            except OSError:
                continue
    leftover = _rotate_claimed_leftover(leftover, claimed)
    for rel in _cpp_linker_undefined_reference_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _rotate_claimed_leftover(leftover, claimed)
    if claimed:
        for rel in _cpp_runtime_ctor_throw_targets(blob, workspace):
            if rel not in leftover:
                leftover.append(rel)
        leftover = _rotate_claimed_leftover(leftover, claimed)
    depth_prod = _delivery_depth_prod_shortfall_targets(blob, workspace)
    if depth_prod:
        leftover = _rotate_claimed_leftover([*depth_prod, *leftover], claimed) or depth_prod
    src_importers = _python_modulenotfound_src_importer_targets(blob, workspace)
    if src_importers:
        leftover = list(dict.fromkeys([*src_importers, *leftover]))
    for match in _UNITTEST_TRACEBACK_TEST_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in leftover and (workspace / rel).is_file():
            leftover.append(rel)
    compile_still_red = any(
        Path(path).suffix.lower() in _FAILING_TU_SOURCE_SUFFIXES and not _is_typescript_test_path(path)
        for path in leftover
    )
    if not compile_still_red:
        for rel in _typescript_runtime_stack_targets(blob, workspace):
            if rel not in leftover:
                leftover.append(rel)
        for rel in _javascript_tap_callee_impl_targets(blob, workspace):
            if rel not in leftover:
                leftover.append(rel)
        for rel in _javascript_tap_reference_error_impl_targets(blob, workspace):
            if rel not in leftover:
                leftover.append(rel)
    leftover = _prefer_cpp_cli_entrypoint_for_unittest_residuals(
        leftover,
        claimed=claimed,
        workspace=workspace,
        persist_claimed=True,
    )
    leftover = _prefer_typescript_compile_sites(leftover)
    syntax_tests = _javascript_syntax_broken_official_tests(blob, workspace)
    if syntax_tests:
        leftover = list(dict.fromkeys([*syntax_tests, *leftover]))
    else:
        ref_err_impl = _javascript_tap_reference_error_impl_targets(blob, workspace)
        if ref_err_impl:
            leftover = list(dict.fromkeys([*ref_err_impl, *leftover]))
        else:
            leftover = _prefer_javascript_official_tap_tests(leftover)
    leftover = _demote_python_unittest_helpers_when_js_impl(leftover)
    leftover = _rotate_claimed_leftover(leftover, claimed)
    leftover = _demote_python_unittest_helpers_when_js_impl(leftover)
    type_homes = _cpp_unknown_type_declaration_targets(blob, workspace)
    header_owned = _cpp_header_owned_diagnostic_targets(blob, workspace)
    for rel in (*type_homes, *header_owned):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _rotate_claimed_leftover(leftover, claimed)
    # Live L2-15 remint-4: leftover leased energy.hpp (note/include site)
    # while src/main.cpp still failed. Prefer translation units + cmake lists.
    # Live L2-20: prefer unknown-type declaration homes, then header-owned
    # type/std-member error sites, then TUs.
    preferred = [
        path
        for path in leftover
        if Path(path).suffix.lower() in _FAILING_TU_SOURCE_SUFFIXES or path.lower() == "cmakelists.txt"
    ]
    owned_headers = list(dict.fromkeys([*type_homes, *header_owned]))
    if owned_headers:
        preferred = list(dict.fromkeys([*owned_headers, *preferred]))
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
        if not rel or rel == "FAILING_TUS" or rel in leftover:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in _FAILING_TU_SOURCE_SUFFIXES:
            continue
        if (workspace / rel).is_file():
            leftover.append(rel)
    for rel in _typescript_tsc_error_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _prefer_java_prod_failing_tus(leftover)
    leftover = _prefer_typescript_compile_sites(leftover)
    leftover = _prefer_javascript_official_tap_tests(leftover)
    compile_tus = list(leftover)
    leftover = [
        *_java_missing_package_symbol_targets(blob, workspace),
        *_java_official_public_type_targets(blob, compile_tus),
        *compile_tus,
    ]
    leftover = _drop_java_case_duplicate_paths(list(dict.fromkeys(leftover)))
    empty_official: list[str] = []
    for rel in leftover:
        if not rel.endswith(".java") or not Path(rel).stem[:1].isupper():
            continue
        candidate = workspace / rel
        try:
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                empty_official.append(rel)
        except OSError:
            empty_official.append(rel)
    if empty_official:
        leftover = [*empty_official, *leftover]
    leftover = _rotate_claimed_leftover(leftover, claimed)
    for rel in _cpp_linker_undefined_reference_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _rotate_claimed_leftover(leftover, claimed)
    unittest_helpers = _prefer_java_unittest_helper_when_official_compile_green(leftover, blob, workspace)
    # Live L2-16 remint-25: helper kept tests/test_product.py, then
    # _rotate_claimed dropped the only claimed path. Even rounds became
    # owner_missing; only 4 of 8 rounds wrote.
    if unittest_helpers and unittest_helpers != leftover:
        return list(dict.fromkeys(unittest_helpers))
    leftover = unittest_helpers
    leftover = _rotate_claimed_leftover(leftover, claimed)
    type_homes = _cpp_unknown_type_declaration_targets(blob, workspace)
    header_owned = _cpp_header_owned_diagnostic_targets(blob, workspace)
    owned_headers = list(dict.fromkeys([*type_homes, *header_owned]))
    if owned_headers:
        leftover = list(dict.fromkeys([*owned_headers, *leftover]))
        leftover = _rotate_claimed_leftover(leftover, claimed)
    if leftover:
        # Compile/link TUs still fail. Immediate rotate must stay on those
        # TUs — remint-21 R4 leased tests/ while main.cpp was still red.
        if _cpp_residuals_have_std_namespace_pollution(residual_errors):
            headers = [path for path in workspace_quality_unclosed_namespace_headers(workspace) if path not in leftover]
            if headers:
                leftover = [*headers, *leftover]
        return leftover
    depth_prod = _delivery_depth_prod_shortfall_targets(blob, workspace)
    if depth_prod:
        # Depth shortfall is an implementation-size residual. Seed and
        # post-test leftover must lease prod sources, not tests/.
        return _rotate_claimed_leftover(depth_prod, claimed) or depth_prod
    if claimed:
        # First-round seed (claimed empty) still prefers src/main.cpp.
        # After a claimed CLI attempt, rotate to the throwing type home.
        ctor_homes = _rotate_claimed_leftover(_cpp_runtime_ctor_throw_targets(blob, workspace), claimed)
        if ctor_homes:
            return ctor_homes
    src_importers = _python_modulenotfound_src_importer_targets(blob, workspace)
    if src_importers:
        return _rotate_claimed_leftover(src_importers, claimed) or src_importers
    for match in _UNITTEST_TRACEBACK_TEST_RE.finditer(blob):
        rel = str(match.group("path") or "").replace("\\", "/").strip()
        if rel and rel not in leftover and (workspace / rel).is_file():
            leftover.append(rel)
    for rel in _typescript_runtime_stack_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    for rel in _javascript_tap_callee_impl_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    for rel in _javascript_tap_reference_error_impl_targets(blob, workspace):
        if rel not in leftover:
            leftover.append(rel)
    leftover = _prefer_cpp_cli_entrypoint_for_unittest_residuals(
        leftover,
        claimed=claimed,
        workspace=workspace,
        persist_claimed=False,
    )
    syntax_tests = _javascript_syntax_broken_official_tests(blob, workspace)
    if syntax_tests:
        leftover = list(dict.fromkeys([*syntax_tests, *leftover]))
    else:
        ref_err_impl = _javascript_tap_reference_error_impl_targets(blob, workspace)
        if ref_err_impl:
            leftover = list(dict.fromkeys([*ref_err_impl, *leftover]))
        else:
            leftover = _prefer_javascript_official_tap_tests(leftover)
    leftover = _demote_python_unittest_helpers_when_js_impl(leftover)
    js_impl = [
        path for path in leftover if path.startswith("src/") and Path(path).suffix.lower() in {".js", ".mjs", ".cjs"}
    ]
    if js_impl and not syntax_tests:
        leftover = _rotate_claimed_leftover(leftover, claimed)
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
