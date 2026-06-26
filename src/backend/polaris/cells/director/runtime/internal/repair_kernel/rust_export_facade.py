"""Rust lib target and root facade shadow classifiers plus narrow runtime planning."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import tomllib

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

RUST_MISSING_LIB_TARGET_SOURCE_TOOL = "deterministic_rust_missing_lib_target_repair"
RUST_LIB_ROOT_FACADE_SOURCE_TOOL = "deterministic_rust_lib_root_facade_repair"
RUST_MISSING_LIB_TARGET_STUB = "// Library crate root.\n"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RUST_MISSING_LIB_TARGET_RE = re.compile(
    r"can't find library [`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)[`'\"] "
    r"at path [`'\"](?P<path>[^`'\"\n]+\.rs)[`'\"]",
    re.IGNORECASE,
)
_CARGO_LIB_PATH_MISSING_RE = re.compile(
    r"\[lib\]\.path\s+[`'\"]?(?P<path>[^`'\"\s]+\.rs)[`'\"]?\s+is missing",
    re.IGNORECASE,
)
_ROOT_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<import>[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+)[`'\"]",
    re.IGNORECASE,
)
_NO_SYMBOL_IN_ROOT_RE = re.compile(
    r"no [`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] in the root",
    re.IGNORECASE,
)
_LIB_RS_EXPOSE_RE = re.compile(
    r"lib\.rs\s+must\s+expose\s+(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_LIB_ROOT_PATH_REWRITE_RE = re.compile(
    r"replace [`'\"]"
    r"(?P<expected>(?P<prefix>crate|[A-Za-z_][A-Za-z0-9_]*)::lib::"
    r"(?P<tail>[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*))"
    r"[`'\"]\s+with\s+[`'\"](?P<replacement>(?P=prefix)::(?P=tail))[`'\"]",
    re.IGNORECASE,
)
_RUST_PUBLIC_ITEM_TEMPLATE = (
    r"(?m)^\s*pub(?:\([^)]*\))?\s+"
    r"(?:async\s+|unsafe\s+|extern\s+)*"
    r"(?:fn|struct|enum|trait|type|const|static)\s+{symbol}\b"
)
_ROOT_MODULE_DECL_RE = re.compile(r"(?m)^\s*(?:pub\s+)?mod\s+(?P<module>[A-Za-z_][A-Za-z0-9_]*)\s*;")
_EXPORT_DECLARATION_LINE_RE = re.compile(r"^\s*pub\s+use\b")
_MODULE_DECLARATION_LINE_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+")
_ALIAS_IMPORT_RE = re.compile(r"(?m)^\s*(?:pub\s+)?use\s+[^;\n]*\s+as\s+[A-Za-z_][A-Za-z0-9_]*")
_GLOB_IMPORT_RE = re.compile(r"(?m)^\s*(?:pub\s+)?use\s+[^;\n]*::\*\s*;")
_CFG_ATTR_RE = re.compile(r"(?m)^\s*#\s*\[\s*cfg(?:_attr)?\b")
_MACRO_CONTEXT_RE = re.compile(r"\bmacro_rules!\b|\binclude!\s*\(")


@dataclass(frozen=True)
class RustExportFacadeShadowCandidate:
    """Read-only candidate produced by the lib-target/facade shadow classifier."""

    rule_id: str
    source_tool: str
    candidate_kind: str
    diagnostic_id: str
    target_path: str = ""
    source_path: str = ""
    symbol: str = ""
    module_path: str = ""
    span_start: int | None = None
    span_end: int | None = None
    expected: str = ""
    replacement: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", str(self.rule_id or "").strip())
        object.__setattr__(self, "source_tool", str(self.source_tool or "").strip())
        object.__setattr__(self, "candidate_kind", str(self.candidate_kind or "").strip())
        object.__setattr__(self, "diagnostic_id", str(self.diagnostic_id or "").strip())
        object.__setattr__(self, "target_path", str(self.target_path or "").strip().replace("\\", "/"))
        object.__setattr__(self, "source_path", str(self.source_path or "").strip().replace("\\", "/"))
        object.__setattr__(self, "symbol", str(self.symbol or "").strip())
        object.__setattr__(self, "module_path", str(self.module_path or "").strip())
        object.__setattr__(self, "expected", str(self.expected or ""))
        object.__setattr__(self, "replacement", str(self.replacement or ""))
        metadata = dict(self.metadata or {})
        runtime_plan_available = bool(metadata.get("runtime_plan_available"))
        metadata.update(
            {
                "planner_only_shadow": True,
                "runtime_plan_available": runtime_plan_available,
                "writes_allowed": False,
                "authoritative": False,
            }
        )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "candidate_kind": self.candidate_kind,
            "diagnostic_id": self.diagnostic_id,
            "target_path": self.target_path,
            "source_path": self.source_path,
            "symbol": self.symbol,
            "module_path": self.module_path,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "expected": self.expected,
            "replacement": self.replacement,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RustExportFacadeShadowBlocker:
    """Read-only blocker explaining why no facade shadow candidate is safe."""

    rule_id: str
    source_tool: str
    reason: str
    diagnostic_id: str
    message: str = ""
    path: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", str(self.rule_id or "").strip())
        object.__setattr__(self, "source_tool", str(self.source_tool or "").strip())
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "diagnostic_id", str(self.diagnostic_id or "").strip())
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "path", str(self.path or "").strip().replace("\\", "/"))
        metadata = dict(self.metadata or {})
        metadata.update(
            {
                "planner_only_shadow": True,
                "runtime_plan_available": False,
                "writes_allowed": False,
                "authoritative": False,
            }
        )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "reason": self.reason,
            "diagnostic_id": self.diagnostic_id,
            "message": self.message,
            "path": self.path,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RustExportFacadeShadowClassification:
    """Aggregate read-only shadow classification for Rust facade candidate discovery."""

    candidates: tuple[RustExportFacadeShadowCandidate, ...] = ()
    blockers: tuple[RustExportFacadeShadowBlocker, ...] = ()
    runtime_plan_available: bool = False
    executable: bool = False

    def __post_init__(self) -> None:
        candidates = tuple(self.candidates or ())
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "blockers", tuple(self.blockers or ()))
        object.__setattr__(
            self,
            "runtime_plan_available",
            any(bool(candidate.metadata.get("runtime_plan_available")) for candidate in candidates),
        )
        object.__setattr__(self, "executable", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_plan_available": self.runtime_plan_available,
            "executable": False,
            "candidate_count": len(self.candidates),
            "blocker_count": len(self.blockers),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }


def classify_rust_export_facade_shadow(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> RustExportFacadeShadowClassification:
    """Classify metadata-only Rust lib target/facade diagnostics without planning writes."""

    missing_lib = classify_rust_missing_lib_target_shadow(base_files=base_files, diagnostics=diagnostics)
    facade = classify_rust_lib_root_facade_shadow(base_files=base_files, diagnostics=diagnostics)
    return RustExportFacadeShadowClassification(
        candidates=_dedupe_candidates((*missing_lib.candidates, *facade.candidates)),
        blockers=_dedupe_blockers((*missing_lib.blockers, *facade.blockers)),
    )


def classify_rust_missing_lib_target_shadow(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> RustExportFacadeShadowClassification:
    """Identify candidate missing lib root paths while keeping the rule non-executable."""

    normalized_base = _normalize_base_files(base_files)
    cargo = _read_cargo_manifest(normalized_base)
    declared_lib_path = _declared_lib_path(cargo)
    candidates: list[RustExportFacadeShadowCandidate] = []
    blockers: list[RustExportFacadeShadowBlocker] = []
    for diagnostic in diagnostics:
        target = _missing_lib_target_from_diagnostic(diagnostic)
        if target is None:
            continue
        path, crate_name, signal_kind = target
        blocker = _missing_lib_target_blocker(
            diagnostic=diagnostic,
            path=path,
            base_files=normalized_base,
            cargo=cargo,
            declared_lib_path=declared_lib_path,
        )
        if blocker is not None:
            blockers.append(blocker)
            continue
        candidates.append(
            RustExportFacadeShadowCandidate(
                rule_id="rust.missing_lib_target",
                source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
                candidate_kind="missing_lib_target",
                diagnostic_id=diagnostic.diagnostic_id,
                target_path=path,
                symbol=crate_name,
                metadata={
                    "signal_kind": signal_kind,
                    "declared_manifest_lib_path": declared_lib_path,
                    "candidate_action": "create_lib_root_file",
                    "runtime_plan_available": path == "src/lib.rs",
                    "runtime_plan_scope": "src_lib_rs_missing_file_only" if path == "src/lib.rs" else "",
                    "write_deferred_until_runtime_rule_migration": True,
                },
            )
        )
    return RustExportFacadeShadowClassification(
        candidates=_dedupe_candidates(candidates),
        blockers=_dedupe_blockers(blockers),
    )


def build_rust_missing_lib_target_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build the executable subset: create a missing default ``src/lib.rs`` only."""

    normalized_base = _normalize_base_files(base_files)
    normalized_diagnostics = tuple(diagnostics or ())
    shadow = classify_rust_missing_lib_target_shadow(
        base_files=normalized_base,
        diagnostics=normalized_diagnostics,
    )
    if shadow.blockers:
        return None

    candidates = tuple(candidate for candidate in shadow.candidates if candidate.candidate_kind == "missing_lib_target")
    executable_candidates = tuple(candidate for candidate in candidates if candidate.target_path == "src/lib.rs")
    if len(candidates) != 1 or len(executable_candidates) != 1:
        return None

    candidate = executable_candidates[0]
    if "src/lib.rs" in normalized_base:
        return None

    operation = RepairOperation(
        kind="write_file",
        path="src/lib.rs",
        content=RUST_MISSING_LIB_TARGET_STUB,
        before_hash=sha256_text(""),
        metadata={
            "repair_kind": "rust_missing_lib_target",
            "runtime_plan_scope": "src_lib_rs_missing_file_only",
            "edit_strategy": "write_file",
            "write_file_reason": "new_file_or_empty_file",
            "create_file_rollback_strategy": "delete_created_file_via_repair_executor",
            "candidate_action": "create_lib_root_file",
            "target_path": candidate.target_path,
            "diagnostic_id": candidate.diagnostic_id,
            "signal_kind": str(candidate.metadata.get("signal_kind") or ""),
            "comment_only_stub": True,
            "symbol_generation": False,
        },
    )
    return RepairPlan(
        rule_id="rust.missing_lib_target_src_lib",
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        operations=(operation,),
        diagnostics=normalized_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_missing_lib_target",
            "runtime_plan_scope": "src_lib_rs_missing_file_only",
            "edit_strategy": "write_file",
            "write_file_reason": "new_file_or_empty_file",
            "candidate_count": len(candidates),
            "target_path": candidate.target_path,
            "diagnostic_id": candidate.diagnostic_id,
            "comment_only_stub": True,
            "symbol_generation": False,
        },
    )


def build_rust_lib_root_facade_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build the executable facade subset, preferring path rewrites before exports."""

    path_rewrite = build_rust_lib_root_facade_path_rewrite_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    if path_rewrite is not None:
        return path_rewrite
    return build_rust_lib_root_facade_export_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )


def build_rust_lib_root_facade_path_rewrite_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build the executable facade subset: one existing-file ``text_replace`` only."""

    normalized_base = _normalize_base_files(base_files)
    normalized_diagnostics = tuple(diagnostics or ())
    candidates: list[RustExportFacadeShadowCandidate] = []
    blockers: list[RustExportFacadeShadowBlocker] = []
    for diagnostic in normalized_diagnostics:
        rewrite = _lib_root_path_rewrite_candidate(base_files=normalized_base, diagnostic=diagnostic)
        if isinstance(rewrite, RustExportFacadeShadowCandidate):
            candidates.append(rewrite)
        elif isinstance(rewrite, RustExportFacadeShadowBlocker):
            blockers.append(rewrite)
    if blockers:
        return None

    executable_candidates = tuple(candidate for candidate in candidates if candidate.candidate_kind == "path_rewrite")
    if len(executable_candidates) != 1 or len(candidates) != 1:
        return None

    candidate = executable_candidates[0]
    if not candidate.source_path or candidate.source_path not in normalized_base:
        return None
    if candidate.span_start is None or candidate.span_end is None:
        return None
    content = normalized_base[candidate.source_path]
    if content[int(candidate.span_start) : int(candidate.span_end)] != candidate.expected:
        return None

    operation = RepairOperation(
        kind="text_replace",
        path=candidate.source_path,
        span_start=candidate.span_start,
        span_end=candidate.span_end,
        expected=candidate.expected,
        replacement=candidate.replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_lib_root_facade_path_rewrite",
            "runtime_plan_scope": "crate_lib_prefix_path_rewrite_only",
            "edit_strategy": "span_text_replace",
            "write_file_fallback_allowed": False,
            "candidate_action": "span_text_replace",
            "candidate_kind": candidate.candidate_kind,
            "diagnostic_id": candidate.diagnostic_id,
            "unique_span": True,
            "unique_context": candidate.expected,
            "rewrite_family": "crate_lib_prefix_collapse",
        },
    )
    return RepairPlan(
        rule_id="rust.lib_root_facade_path_rewrite",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        operations=(operation,),
        diagnostics=normalized_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_lib_root_facade_path_rewrite",
            "runtime_plan_scope": "crate_lib_prefix_path_rewrite_only",
            "edit_strategy": "span_text_replace",
            "write_file_fallback_allowed": False,
            "candidate_count": len(candidates),
            "target_path": candidate.source_path,
            "diagnostic_id": candidate.diagnostic_id,
        },
    )


def build_rust_lib_root_facade_export_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build the narrow executable export subset: one anchored ``pub use`` insert."""

    normalized_base = _normalize_base_files(base_files)
    normalized_diagnostics = tuple(diagnostics or ())
    candidates: list[RustExportFacadeShadowCandidate] = []
    blockers: list[RustExportFacadeShadowBlocker] = []
    for diagnostic in normalized_diagnostics:
        export_symbol = _lib_root_export_symbol(diagnostic)
        if not export_symbol:
            continue
        export = _lib_root_export_candidate(
            base_files=normalized_base,
            diagnostic=diagnostic,
            symbol=export_symbol,
        )
        if isinstance(export, RustExportFacadeShadowCandidate):
            candidates.append(export)
        else:
            blockers.append(export)
    if blockers:
        return None

    executable_candidates = tuple(
        candidate for candidate in candidates if candidate.candidate_kind == "lib_root_export"
    )
    if len(executable_candidates) != 1 or len(candidates) != 1:
        return None

    candidate = executable_candidates[0]
    root_path = candidate.target_path
    if not root_path or root_path not in normalized_base:
        return None
    root_content = normalized_base[root_path]
    export_line = str(candidate.metadata.get("candidate_export_line") or "")
    unique_context = str(candidate.metadata.get("unique_context") or "")
    if not export_line or not unique_context:
        return None
    span_start = candidate.span_start
    span_end = candidate.span_end
    if span_start is None or span_end is None or int(span_start) != int(span_end):
        return None
    if root_content.count(unique_context) != 1:
        return None

    replacement = f"{export_line}\n" if unique_context.endswith("\n") else f"\n{export_line}\n"
    operation = RepairOperation(
        kind="text_replace",
        path=root_path,
        span_start=span_start,
        span_end=span_end,
        expected="",
        replacement=replacement,
        before_hash=sha256_text(root_content),
        metadata={
            "repair_kind": "rust_lib_root_facade_export",
            "runtime_plan_scope": "single_pub_use_export_insert_after_declared_module",
            "edit_strategy": "span_text_insert",
            "write_file_fallback_allowed": False,
            "candidate_action": "insert_pub_use_export",
            "candidate_kind": candidate.candidate_kind,
            "diagnostic_id": candidate.diagnostic_id,
            "unique_span": True,
            "unique_context": unique_context,
            "symbol": candidate.symbol,
            "module_path": candidate.module_path,
            "export_line": export_line,
        },
    )
    return RepairPlan(
        rule_id="rust.lib_root_facade_export",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        operations=(operation,),
        diagnostics=normalized_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_lib_root_facade_export",
            "runtime_plan_scope": "single_pub_use_export_insert_after_declared_module",
            "edit_strategy": "span_text_insert",
            "write_file_fallback_allowed": False,
            "candidate_count": len(candidates),
            "target_path": root_path,
            "diagnostic_id": candidate.diagnostic_id,
            "symbol": candidate.symbol,
            "module_path": candidate.module_path,
        },
    )


def classify_rust_lib_root_facade_shadow(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> RustExportFacadeShadowClassification:
    """Classify root-facade export and span-rewrite opportunities without operations."""

    normalized_base = _normalize_base_files(base_files)
    candidates: list[RustExportFacadeShadowCandidate] = []
    blockers: list[RustExportFacadeShadowBlocker] = []
    for diagnostic in diagnostics:
        rewrite = _lib_root_path_rewrite_candidate(base_files=normalized_base, diagnostic=diagnostic)
        if isinstance(rewrite, RustExportFacadeShadowCandidate):
            candidates.append(rewrite)
        elif isinstance(rewrite, RustExportFacadeShadowBlocker):
            blockers.append(rewrite)

        export_symbol = _lib_root_export_symbol(diagnostic)
        if not export_symbol:
            continue
        export = _lib_root_export_candidate(
            base_files=normalized_base,
            diagnostic=diagnostic,
            symbol=export_symbol,
        )
        if isinstance(export, RustExportFacadeShadowCandidate):
            candidates.append(export)
        else:
            blockers.append(export)
    return RustExportFacadeShadowClassification(
        candidates=_dedupe_candidates(candidates),
        blockers=_dedupe_blockers(blockers),
    )


def _missing_lib_target_from_diagnostic(diagnostic: RepairDiagnostic) -> tuple[str, str, str] | None:
    text = _diagnostic_text(diagnostic)
    lowered = text.lower()
    if "file not found for module" in lowered or "to create the module" in lowered:
        return None
    rustc_match = _RUST_MISSING_LIB_TARGET_RE.search(text)
    if rustc_match is not None:
        return (
            _normalize_repair_path(str(rustc_match.group("path") or "")),
            str(rustc_match.group("crate") or "").strip(),
            "rustc_missing_library_target_path",
        )
    cargo_match = _CARGO_LIB_PATH_MISSING_RE.search(text)
    if cargo_match is not None:
        return (
            _normalize_repair_path(str(cargo_match.group("path") or "")),
            "",
            "cargo_manifest_lib_path_missing",
        )
    return None


def _missing_lib_target_blocker(
    *,
    diagnostic: RepairDiagnostic,
    path: str,
    base_files: Mapping[str, str],
    cargo: Mapping[str, object],
    declared_lib_path: str,
) -> RustExportFacadeShadowBlocker | None:
    if not base_files.get("Cargo.toml"):
        return _blocker(
            rule_id="rust.missing_lib_target",
            source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="missing_cargo_manifest_context",
            message="Missing lib target shadow classification requires Cargo.toml in base_files.",
        )
    if not cargo:
        return _blocker(
            rule_id="rust.missing_lib_target",
            source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="unreadable_cargo_manifest",
            message="Cargo.toml could not be parsed for lib target context.",
        )
    if not _rust_source_path_is_safe(path):
        return _blocker(
            rule_id="rust.missing_lib_target",
            source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="unsafe_lib_target_path",
            message="Missing lib target path is not a safe relative Rust source path.",
            path=path,
        )
    if path in base_files:
        return _blocker(
            rule_id="rust.missing_lib_target",
            source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="lib_target_path_already_exists",
            message="The reported lib target path already exists in base_files.",
            path=path,
        )
    if declared_lib_path and declared_lib_path != path:
        return _blocker(
            rule_id="rust.missing_lib_target",
            source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="diagnostic_path_conflicts_manifest_lib_path",
            message="The diagnostic lib path conflicts with Cargo [lib].path.",
            path=path,
            metadata={"declared_manifest_lib_path": declared_lib_path},
        )
    return None


def _lib_root_path_rewrite_candidate(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> RustExportFacadeShadowCandidate | RustExportFacadeShadowBlocker | None:
    text = _diagnostic_text(diagnostic)
    match = _LIB_ROOT_PATH_REWRITE_RE.search(text)
    if match is None:
        return None
    expected = str(match.group("expected") or "")
    replacement = str(match.group("replacement") or "")
    prefix = str(match.group("prefix") or "")
    if prefix != "crate":
        canonical_crate = _canonical_crate_name(base_files)
        if not canonical_crate:
            return _blocker(
                rule_id="rust.lib_root_facade_path_rewrite",
                source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
                diagnostic=diagnostic,
                reason="missing_canonical_crate_context",
                message="Canonical crate path rewrite requires Cargo.toml package name context.",
            )
        if prefix != canonical_crate:
            return _blocker(
                rule_id="rust.lib_root_facade_path_rewrite",
                source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
                diagnostic=diagnostic,
                reason="non_canonical_crate_prefix",
                message="Lib-root path rewrite prefix does not match the canonical crate name.",
                metadata={"prefix": prefix, "canonical_crate": canonical_crate},
            )
    matches: list[tuple[str, int, int, str]] = []
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or "/target/" in f"/{path}/":
            continue
        start = content.find(expected)
        while start >= 0:
            end = start + len(expected)
            matches.append((path, start, end, content))
            start = content.find(expected, end)
    if not matches:
        return _blocker(
            rule_id="rust.lib_root_facade_path_rewrite",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="rewrite_span_not_found",
            message="The requested crate::lib span was not found in base_files.",
        )
    if len(matches) > 1:
        return _blocker(
            rule_id="rust.lib_root_facade_path_rewrite",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="multiple_span_matches",
            message="The requested crate::lib span matched multiple source locations.",
            metadata={"match_count": len(matches), "expected": expected},
        )
    path, start, end, content = matches[0]
    construct_blocker = _ambiguous_construct_blocker(
        rule_id="rust.lib_root_facade_path_rewrite",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        diagnostic=diagnostic,
        root_content=content,
        candidate_content=content,
        path=path,
    )
    if construct_blocker is not None:
        return construct_blocker
    declaration_blocker = _declaration_context_blocker(
        rule_id="rust.lib_root_facade_path_rewrite",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        diagnostic=diagnostic,
        content=content,
        span_start=start,
        span_end=end,
        path=path,
    )
    if declaration_blocker is not None:
        return declaration_blocker
    return RustExportFacadeShadowCandidate(
        rule_id="rust.lib_root_facade_path_rewrite",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        candidate_kind="path_rewrite",
        diagnostic_id=diagnostic.diagnostic_id,
        source_path=path,
        span_start=start,
        span_end=end,
        expected=expected,
        replacement=replacement,
        metadata={
            "candidate_action": "span_text_replace",
            "unique_span": True,
            "runtime_plan_available": True,
            "runtime_plan_scope": "crate_lib_prefix_path_rewrite_only",
            "rewrite_family": "crate_lib_prefix_collapse",
        },
    )


def _lib_root_export_symbol(diagnostic: RepairDiagnostic) -> str:
    text = _diagnostic_text(diagnostic)
    root_match = _ROOT_IMPORT_RE.search(text)
    if root_match is not None and " in the root" in text.lower():
        imported = str(root_match.group("import") or "")
        symbol = imported.rsplit("::", 1)[-1]
        if _is_rust_identifier(symbol):
            return symbol
    no_symbol_match = _NO_SYMBOL_IN_ROOT_RE.search(text)
    if no_symbol_match is not None:
        symbol = str(no_symbol_match.group("symbol") or "")
        if _is_rust_identifier(symbol):
            return symbol
    expose_match = _LIB_RS_EXPOSE_RE.search(text)
    if expose_match is not None:
        symbol = str(expose_match.group("symbol") or "")
        if _is_rust_identifier(symbol):
            return symbol
    return ""


def _lib_root_export_candidate(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
    symbol: str,
) -> RustExportFacadeShadowCandidate | RustExportFacadeShadowBlocker:
    root_path = _lib_root_path(base_files)
    if not root_path:
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="missing_lib_root_context",
            message="No lib root source file is present in base_files.",
        )
    root_content = base_files[root_path]
    matches = _public_symbol_module_matches(base_files, symbol)
    if not matches:
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="symbol_not_found",
            message="No unique public item matching the missing root symbol was found.",
            metadata={"symbol": symbol},
        )
    if len(matches) > 1:
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="multiple_module_matches",
            message="The missing root symbol is defined by multiple modules.",
            metadata={"symbol": symbol, "matches": [dict(item) for item in matches]},
        )
    match = matches[0]
    module_path = str(match["module_path"])
    source_path = str(match["source_path"])
    candidate_content = base_files[source_path]
    construct_blocker = _ambiguous_construct_blocker(
        rule_id="rust.lib_root_facade_export",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        diagnostic=diagnostic,
        root_content=root_content,
        candidate_content=candidate_content,
        path=root_path,
    )
    if construct_blocker is not None:
        return construct_blocker
    if module_path.split("::", 1)[0] not in _declared_root_modules(root_content):
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="module_not_declared_in_root",
            message="The candidate module is not declared by the lib root.",
            metadata={"symbol": symbol, "module_path": module_path, "source_path": source_path},
        )
    export_line = f"pub use crate::{module_path}::{symbol};"
    if export_line in root_content:
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="export_already_present",
            message="The simple facade export already exists in the lib root.",
            path=root_path,
            metadata={"symbol": symbol, "module_path": module_path},
        )
    if _root_has_export_declarations(root_content):
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="export_declaration_requires_symbol_contract",
            message="Existing root export declarations require a stronger symbol-level contract before insertion.",
            path=root_path,
            metadata={"symbol": symbol, "module_path": module_path, "source_path": source_path},
        )
    insertion = _lib_root_export_insert_anchor(root_content=root_content, module_path=module_path)
    if insertion is None:
        return _blocker(
            rule_id="rust.lib_root_facade_export",
            source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
            diagnostic=diagnostic,
            reason="module_declaration_requires_symbol_contract",
            message="The lib root module declaration did not provide one unique export insertion anchor.",
            path=root_path,
            metadata={"symbol": symbol, "module_path": module_path, "source_path": source_path},
        )
    insert_at, unique_context = insertion
    return RustExportFacadeShadowCandidate(
        rule_id="rust.lib_root_facade_export",
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        candidate_kind="lib_root_export",
        diagnostic_id=diagnostic.diagnostic_id,
        target_path=root_path,
        source_path=source_path,
        symbol=symbol,
        module_path=module_path,
        span_start=insert_at,
        span_end=insert_at,
        expected="",
        replacement=export_line,
        metadata={
            "candidate_action": "insert_pub_use_export",
            "candidate_export_line": export_line,
            "unique_context": unique_context,
            "runtime_plan_available": True,
            "runtime_plan_scope": "single_pub_use_export_insert_after_declared_module",
            "symbol_source_kind": str(match["item_kind"]),
        },
    )


def _ambiguous_construct_blocker(
    *,
    rule_id: str,
    source_tool: str,
    diagnostic: RepairDiagnostic,
    root_content: str,
    candidate_content: str,
    path: str,
) -> RustExportFacadeShadowBlocker | None:
    haystacks = (root_content, candidate_content)
    checks = (
        ("ambiguous_alias_import", _ALIAS_IMPORT_RE),
        ("ambiguous_glob_import", _GLOB_IMPORT_RE),
        ("cfg_gated_context", _CFG_ATTR_RE),
        ("macro_context", _MACRO_CONTEXT_RE),
    )
    for reason, pattern in checks:
        if any(pattern.search(content) for content in haystacks):
            return _blocker(
                rule_id=rule_id,
                source_tool=source_tool,
                diagnostic=diagnostic,
                reason=reason,
                message="Facade shadow classification blocked by ambiguous Rust root/module context.",
                path=path,
            )
    return None


def _declaration_context_blocker(
    *,
    rule_id: str,
    source_tool: str,
    diagnostic: RepairDiagnostic,
    content: str,
    span_start: int,
    span_end: int,
    path: str,
) -> RustExportFacadeShadowBlocker | None:
    del span_end
    line_start = content.rfind("\n", 0, span_start) + 1
    line_end = content.find("\n", span_start)
    if line_end < 0:
        line_end = len(content)
    line = content[line_start:line_end]
    if _EXPORT_DECLARATION_LINE_RE.search(line):
        return _blocker(
            rule_id=rule_id,
            source_tool=source_tool,
            diagnostic=diagnostic,
            reason="export_declaration_context",
            message="Lib-root path rewrite refuses to edit public export declarations.",
            path=path,
        )
    if _MODULE_DECLARATION_LINE_RE.search(line):
        return _blocker(
            rule_id=rule_id,
            source_tool=source_tool,
            diagnostic=diagnostic,
            reason="module_declaration_context",
            message="Lib-root path rewrite refuses to edit module declarations.",
            path=path,
        )
    return None


def _public_symbol_module_matches(base_files: Mapping[str, str], symbol: str) -> list[dict[str, str]]:
    pattern = re.compile(_RUST_PUBLIC_ITEM_TEMPLATE.format(symbol=re.escape(symbol)))
    matches: list[dict[str, str]] = []
    for path, content in sorted(base_files.items()):
        if not path.startswith("src/") or not path.endswith(".rs") or path.endswith("/lib.rs"):
            continue
        if "/target/" in f"/{path}/":
            continue
        match = pattern.search(content)
        if match is None:
            continue
        declaration = match.group(0).strip()
        item_kind_match = re.search(r"\b(fn|struct|enum|trait|type|const|static)\b", declaration)
        matches.append(
            {
                "source_path": path,
                "module_path": _module_path_from_source_path(path),
                "item_kind": item_kind_match.group(1) if item_kind_match else "item",
            }
        )
    return matches


def _declared_root_modules(root_content: str) -> set[str]:
    return {str(match.group("module") or "") for match in _ROOT_MODULE_DECL_RE.finditer(root_content)}


def _root_has_export_declarations(root_content: str) -> bool:
    return any(_EXPORT_DECLARATION_LINE_RE.search(line) for line in root_content.splitlines())


def _lib_root_export_insert_anchor(*, root_content: str, module_path: str) -> tuple[int, str] | None:
    module_root = str(module_path or "").split("::", 1)[0]
    if not _is_rust_identifier(module_root):
        return None
    pattern = re.compile(rf"(?m)^[^\S\n]*(?:pub\s+)?mod\s+{re.escape(module_root)}\s*;[^\S\n]*(?:\n|$)")
    matches = tuple(pattern.finditer(root_content))
    if len(matches) != 1:
        return None
    match = matches[0]
    unique_context = match.group(0)
    if not unique_context or root_content.count(unique_context) != 1:
        return None
    return match.end(), unique_context


def _lib_root_path(base_files: Mapping[str, str]) -> str:
    cargo = _read_cargo_manifest(base_files)
    declared = _declared_lib_path(cargo)
    if declared and declared in base_files:
        return declared
    return "src/lib.rs" if "src/lib.rs" in base_files else ""


def _declared_lib_path(cargo: Mapping[str, object]) -> str:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        path = _normalize_repair_path(str(lib.get("path") or "").strip())
        if path:
            return path
    return ""


def _canonical_crate_name(base_files: Mapping[str, str]) -> str:
    cargo = _read_cargo_manifest(base_files)
    package = cargo.get("package")
    if not isinstance(package, dict):
        return ""
    name = str(package.get("name") or "").strip().replace("-", "_")
    return name if _is_rust_identifier(name) else ""


def _read_cargo_manifest(base_files: Mapping[str, str]) -> dict[str, object]:
    text = str(base_files.get("Cargo.toml") or "")
    if not text.strip():
        return {}
    try:
        payload = tomllib.loads(text)
    except (RuntimeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for path, content in dict(base_files or {}).items():
        normalized_path = _normalize_repair_path(str(path or ""))
        if normalized_path:
            normalized[normalized_path] = str(content or "")
    return normalized


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return ""
    if re.match(r"^[A-Za-z]:/", normalized):
        return ""
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        return ""
    return normalized


def _rust_source_path_is_safe(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    if not normalized or normalized != path or not normalized.endswith(".rs"):
        return False
    parts = set(normalized.split("/"))
    return not bool(parts & {"target", "build", "out"})


def _module_path_from_source_path(path: str) -> str:
    normalized = _normalize_repair_path(path)
    if normalized == "src/lib.rs" or not normalized.startswith("src/") or not normalized.endswith(".rs"):
        return ""
    relative = normalized[len("src/") : -len(".rs")]
    if relative.endswith("/mod"):
        relative = relative[: -len("/mod")]
    return "::".join(part for part in relative.split("/") if part)


def _diagnostic_text(diagnostic: RepairDiagnostic) -> str:
    return _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")


def _is_rust_identifier(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")) is not None


def _blocker(
    *,
    rule_id: str,
    source_tool: str,
    diagnostic: RepairDiagnostic,
    reason: str,
    message: str,
    path: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> RustExportFacadeShadowBlocker:
    return RustExportFacadeShadowBlocker(
        rule_id=rule_id,
        source_tool=source_tool,
        reason=reason,
        diagnostic_id=diagnostic.diagnostic_id,
        message=message,
        path=path,
        metadata=dict(metadata or {}),
    )


def _dedupe_candidates(
    candidates: Sequence[RustExportFacadeShadowCandidate],
) -> tuple[RustExportFacadeShadowCandidate, ...]:
    seen: set[tuple[object, ...]] = set()
    deduped: list[RustExportFacadeShadowCandidate] = []
    for candidate in candidates:
        key = (
            candidate.rule_id,
            candidate.candidate_kind,
            candidate.diagnostic_id,
            candidate.target_path,
            candidate.source_path,
            candidate.symbol,
            candidate.module_path,
            candidate.span_start,
            candidate.span_end,
            candidate.expected,
            candidate.replacement,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return tuple(deduped)


def _dedupe_blockers(
    blockers: Sequence[RustExportFacadeShadowBlocker],
) -> tuple[RustExportFacadeShadowBlocker, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    deduped: list[RustExportFacadeShadowBlocker] = []
    for blocker in blockers:
        key = (blocker.rule_id, blocker.reason, blocker.diagnostic_id, blocker.path, blocker.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return tuple(deduped)
