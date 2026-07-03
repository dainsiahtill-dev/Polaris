"""Cross-artifact interface contracts and deterministic symbol snapshots.

This module is the physical evidence layer behind ``interface_ledger``.  The
ledger records CE-declared interface intent; this scanner records what the
workspace actually exports/imports after Director writes files.  It is
conservative by design: unsupported or ambiguous constructs fail open instead
of inventing failures.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "cross_artifact.interface_snapshot.v1"
CONTRACT_SCHEMA_VERSION = "cross_artifact.interface_contract.v1"

_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".polaris",
    ".pytest_cache",
    ".ruff_cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
_SOURCE_EXTS = {".cjs", ".go", ".js", ".jsx", ".mjs", ".py", ".ts", ".tsx"}
_TS_JS_EXTS = {".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"}
_TS_JS_RESOLUTION_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_PY_BUILTIN_DUNDER_EXPORTS = {"__all__"}

_TS_NAMED_IMPORT_RE = re.compile(
    r"\bimport\s+(?P<typeonly>type\s+)?(?:[A-Za-z_$][\w$]*\s*,\s*)?"
    r"\{(?P<names>[^{}]*)\}\s*from\s*['\"](?P<spec>[^'\"]+)['\"]",
)
_TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:async\s+)?function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)\s*(?P<fn_params>\([^)]*\))?"
    r"|\bexport\s+(?:abstract\s+)?class\s+(?P<cls>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:interface|type|enum|namespace|module)\s+(?P<ty>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+const\s+enum\s+(?P<cenum>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:const|let|var)\s+(?!enum\b)(?P<var>[A-Za-z_$][\w$]*)",
)
_TS_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")
_TS_EXPORT_CLAUSE_RE = re.compile(
    r"\bexport\s+(?:type\s+)?\{(?P<inner>[^{}]*)\}(?:\s*from\s*['\"](?P<spec>[^'\"]+)['\"])?"
)
_TS_EXPORT_STAR_RE = re.compile(r"\bexport\s+\*\s+from\s*['\"](?P<spec>[^'\"]+)['\"]")
_TS_UNKNOWABLE_EXPORT_RE = re.compile(
    r"\bexport\s*="
    r"|\bmodule\s*\.\s*exports\b"
    r"|\bexports\s*\.\s*[A-Za-z_$]"
    r"|\bexports\s*\["
    r"|\bObject\s*\.\s*defineProperty\s*\(\s*exports\b"
    r"|\bdeclare\s+(?:module|global|namespace)\b"
    r"|\bexport\s+(?:declare\s+)?(?:const|let|var)\s+[\[{]",
)
_GO_EXPORT_DECL_RE = re.compile(r"(?m)^\s*(?:type|func|var|const)\s+(?:\([^)]*\)|(?P<name>[A-Z][A-Za-z0-9_]*))")
_TS_SYMBOL_COHERENCE_FLAG = "KERNELONE_TS_SYMBOL_COHERENCE"


@dataclass(frozen=True, slots=True)
class InterfaceSymbol:
    """A public interface symbol exported by one artifact."""

    name: str
    symbol_kind: str
    owner_path: str
    resolution_path: str
    signature: str = ""
    signature_digest: str = ""
    reexported_from: str = ""
    semantic_role: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "symbol_kind": self.symbol_kind,
            "owner_path": self.owner_path,
            "resolution_path": self.resolution_path,
            "signature": self.signature,
            "signature_digest": self.signature_digest,
            "reexported_from": self.reexported_from,
            "semantic_role": self.semantic_role,
        }


@dataclass(frozen=True, slots=True)
class InterfaceImport:
    """A named cross-artifact dependency consumed by one artifact."""

    importer_path: str
    module: str
    symbols: tuple[str, ...]
    import_kind: str
    resolved_owner_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "importer_path": self.importer_path,
            "module": self.module,
            "symbols": list(self.symbols),
            "import_kind": self.import_kind,
            "resolved_owner_path": self.resolved_owner_path,
        }


@dataclass(frozen=True, slots=True)
class CrossArtifactInterfaceRequirement:
    """CE-declared expected interface owned by an artifact."""

    domain: str
    owner_path: str
    name: str
    kind: str = "code_symbol"
    signature: str = ""
    signature_digest: str = ""
    consumers: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> CrossArtifactInterfaceRequirement:
        consumers = payload.get("consumers") or payload.get("required_by") or ()
        consumer_values: tuple[str, ...]
        if isinstance(consumers, str):
            consumer_values = (consumers,)
        elif isinstance(consumers, Iterable):
            consumer_values = tuple(str(item) for item in consumers if str(item or "").strip())
        else:
            consumer_values = ()
        return cls(
            domain=str(payload.get("domain") or "code_symbol").strip() or "code_symbol",
            owner_path=_normalize_rel_path(payload.get("owner_path") or payload.get("owner") or payload.get("path")),
            name=str(payload.get("name") or "").strip(),
            kind=str(payload.get("kind") or payload.get("symbol_kind") or "code_symbol").strip() or "code_symbol",
            signature=str(payload.get("signature") or "").strip(),
            signature_digest=str(payload.get("signature_digest") or "").strip(),
            consumers=consumer_values,
        )


@dataclass(frozen=True, slots=True)
class CrossArtifactInterfaceContract:
    """Shared interface contract consumed by CE, Director, QA, and repair."""

    task_id: str
    language: str = ""
    interfaces: tuple[CrossArtifactInterfaceRequirement, ...] = field(default_factory=tuple)
    schema_version: str = CONTRACT_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> CrossArtifactInterfaceContract:
        raw_interfaces = payload.get("interfaces") or ()
        interfaces: list[CrossArtifactInterfaceRequirement] = []
        if isinstance(raw_interfaces, Iterable) and not isinstance(raw_interfaces, (str, bytes)):
            for item in raw_interfaces:
                if isinstance(item, Mapping):
                    requirement = CrossArtifactInterfaceRequirement.from_mapping(item)
                    if requirement.owner_path and requirement.name:
                        interfaces.append(requirement)
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            language=str(payload.get("language") or "").strip(),
            interfaces=tuple(interfaces),
            schema_version=str(payload.get("schema_version") or CONTRACT_SCHEMA_VERSION).strip()
            or CONTRACT_SCHEMA_VERSION,
        )


@dataclass(frozen=True, slots=True)
class SymbolIndexSnapshot:
    """Deterministic view of workspace imports/exports."""

    workspace: str
    files: tuple[str, ...]
    physical_exports: Mapping[str, tuple[InterfaceSymbol, ...]]
    namespace_exports: Mapping[str, tuple[InterfaceSymbol, ...]]
    imports: tuple[InterfaceImport, ...]
    unknown_export_paths: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "files": list(self.files),
            "physical_exports": {
                path: [symbol.to_dict() for symbol in symbols]
                for path, symbols in sorted(self.physical_exports.items())
            },
            "namespace_exports": {
                path: [symbol.to_dict() for symbol in symbols]
                for path, symbols in sorted(self.namespace_exports.items())
            },
            "imports": [item.to_dict() for item in self.imports],
            "unknown_export_paths": list(self.unknown_export_paths),
        }

    def stable_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CrossArtifactConsistencyIssue:
    """One deterministic cross-artifact interface mismatch."""

    code: str
    message: str
    severity: str = "high"
    importer_path: str = ""
    owner_path: str = ""
    symbol: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_error_message(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "importer_path": self.importer_path,
            "owner_path": self.owner_path,
            "symbol": self.symbol,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ContractAmendmentRequest:
    """Proposal object Director may raise when the contract is physically wrong."""

    task_id: str
    reason: str
    evidence: tuple[str, ...]
    requested_by: str = "director"
    schema_version: str = "cross_artifact.contract_amendment_request.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "requested_by": self.requested_by,
        }


@dataclass(frozen=True, slots=True)
class CrossArtifactRepairPlan:
    """Typed advisory plan for repair-kernel integration."""

    strategy: str
    authority: str
    issue_code: str
    importer_path: str = ""
    owner_path: str = ""
    symbol: str = ""
    replacement_symbol: str = ""
    confidence: str = "medium"
    evidence: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "authority": self.authority,
            "issue_code": self.issue_code,
            "importer_path": self.importer_path,
            "owner_path": self.owner_path,
            "symbol": self.symbol,
            "replacement_symbol": self.replacement_symbol,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "constraints": list(self.constraints),
        }


def build_symbol_index_snapshot(
    workspace: str | Path,
    *,
    relative_paths: Iterable[str] | None = None,
) -> SymbolIndexSnapshot:
    """Build a deterministic import/export snapshot for supported languages."""

    root = Path(workspace).resolve()
    files = tuple(_iter_source_files(root))
    physical: dict[str, tuple[InterfaceSymbol, ...]] = {}
    imports: list[InterfaceImport] = []
    reexports: dict[str, tuple[_ReexportEdge, ...]] = {}
    unknown_export_paths: set[str] = set()

    for relative_path in files:
        if relative_paths is not None and not _path_in_scope(relative_path, relative_paths):
            # Imports are checked only for scoped files, but exports still need
            # full-workspace visibility for sibling resolution.
            collect_imports = False
        else:
            collect_imports = True
        full_path = root / relative_path
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
        except (OSError, RuntimeError, ValueError):
            continue
        symbols, file_imports, file_reexports, unknown_exports = _scan_file_interfaces(root, relative_path, text)
        if symbols:
            physical[relative_path] = tuple(symbols)
        if collect_imports:
            imports.extend(file_imports)
        if file_reexports:
            reexports[relative_path] = tuple(file_reexports)
        if unknown_exports:
            unknown_export_paths.add(relative_path)

    namespace, resolved_unknown_export_paths = _resolve_namespace_exports(
        root, physical, reexports, unknown_export_paths
    )
    return SymbolIndexSnapshot(
        workspace=root.as_posix(),
        files=files,
        physical_exports={key: tuple(value) for key, value in physical.items()},
        namespace_exports={key: tuple(value) for key, value in namespace.items()},
        imports=tuple(imports),
        unknown_export_paths=tuple(sorted(resolved_unknown_export_paths)),
    )


def scan_cross_artifact_consistency(
    workspace: str | Path,
    *,
    relative_paths: Iterable[str] | None = None,
    contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None = None,
) -> list[CrossArtifactConsistencyIssue]:
    """Compare named consumers and optional CE contract against actual exports."""

    snapshot = build_symbol_index_snapshot(workspace, relative_paths=relative_paths)
    issues: list[CrossArtifactConsistencyIssue] = []
    parsed_contract: CrossArtifactInterfaceContract | None = None
    requirement_by_owner_symbol: dict[tuple[str, str], CrossArtifactInterfaceRequirement] = {}
    if contract is not None:
        parsed_contract = (
            contract
            if isinstance(contract, CrossArtifactInterfaceContract)
            else CrossArtifactInterfaceContract.from_mapping(contract)
        )
        requirement_by_owner_symbol = {
            (requirement.owner_path, requirement.name): requirement for requirement in parsed_contract.interfaces
        }
    for import_ref in snapshot.imports:
        owner_path = import_ref.resolved_owner_path
        if not owner_path:
            continue
        if owner_path in snapshot.unknown_export_paths:
            continue
        export_names = {symbol.name for symbol in snapshot.namespace_exports.get(owner_path, ())}
        for name in import_ref.symbols:
            if name in export_names:
                continue
            if import_ref.import_kind == "python.import_from" and _python_submodule_owner_path(
                Path(snapshot.workspace),
                import_ref.resolved_owner_path,
                name,
            ):
                continue
            declared_requirement = requirement_by_owner_symbol.get((owner_path, name))
            details: dict[str, Any] = {"available_exports": sorted(export_names)[:20]}
            if declared_requirement is not None and parsed_contract is not None:
                details.update(
                    {
                        "contract_declared": True,
                        "task_id": parsed_contract.task_id,
                        "domain": declared_requirement.domain,
                        "signature_digest": declared_requirement.signature_digest,
                    }
                )
            issues.append(
                CrossArtifactConsistencyIssue(
                    code="unresolved_import_symbol",
                    message=(
                        f"Artifact quality scan failed: unresolved import symbol {name!r} "
                        f"from {import_ref.module!r} in {import_ref.importer_path} "
                        "(sibling module does not define it)"
                    ),
                    importer_path=import_ref.importer_path,
                    owner_path=owner_path,
                    symbol=name,
                    details=details,
                )
            )

    if parsed_contract is not None:
        issues.extend(_validate_contract_against_snapshot(parsed_contract, snapshot))

    return _dedupe_issues(issues)


def scan_cross_artifact_consistency_errors(
    workspace: str | Path,
    *,
    relative_paths: Iterable[str] | None = None,
) -> list[str]:
    """Return artifact-quality compatible error strings."""

    return [
        issue.to_error_message() for issue in scan_cross_artifact_consistency(workspace, relative_paths=relative_paths)
    ]


def build_contract_amendment_request(
    *,
    task_id: str,
    issues: Iterable[CrossArtifactConsistencyIssue],
    requested_by: str = "director",
) -> ContractAmendmentRequest | None:
    """Build a CE amendment request for contract-level mismatches.

    Unresolved imports without a declared interface and without a close existing
    export are design gaps, not deterministic code repairs. They must return to
    CE instead of encouraging Director to invent new public contracts.
    """

    amendment_issues: list[CrossArtifactConsistencyIssue] = []
    for issue in issues:
        if issue.code.startswith("contract_") and issue.code not in {
            "contract_export_missing",
            "contract_signature_mismatch",
        }:
            amendment_issues.append(issue)
            continue
        if issue.code != "unresolved_import_symbol":
            continue
        if issue.details.get("contract_declared"):
            continue
        available = tuple(str(item) for item in issue.details.get("available_exports", ()) if str(item or "").strip())
        if not _closest_symbol(issue.symbol, available):
            amendment_issues.append(issue)
    if not amendment_issues:
        return None
    return ContractAmendmentRequest(
        task_id=str(task_id or "").strip(),
        reason="cross-artifact interface contract is missing, ambiguous, or does not match current source evidence",
        evidence=tuple(issue.message for issue in amendment_issues),
        requested_by=str(requested_by or "director").strip() or "director",
    )


def plan_cross_artifact_repairs(
    issues: Iterable[CrossArtifactConsistencyIssue],
) -> list[CrossArtifactRepairPlan]:
    """Classify consistency issues into bounded repair or CE-amendment plans."""

    plans: list[CrossArtifactRepairPlan] = []
    for issue in issues:
        if issue.code == "contract_export_missing":
            plans.append(
                CrossArtifactRepairPlan(
                    strategy="add_real_interface_to_owner",
                    authority="director_repair_within_contract",
                    issue_code=issue.code,
                    owner_path=issue.owner_path,
                    symbol=issue.symbol,
                    confidence="high",
                    evidence=(issue.message,),
                    constraints=(
                        "Implement the real exported interface declared by CE in the owner artifact.",
                        "Do not change the interface contract from Director.",
                        "Do not satisfy this plan with pass, TODO, NotImplemented, or placeholder-only stubs.",
                    ),
                )
            )
            continue
        if issue.code == "contract_signature_mismatch":
            plans.append(
                CrossArtifactRepairPlan(
                    strategy="align_owner_signature_to_contract",
                    authority="director_repair_within_contract",
                    issue_code=issue.code,
                    owner_path=issue.owner_path,
                    symbol=issue.symbol,
                    confidence="high",
                    evidence=(issue.message,),
                    constraints=(
                        "Align the owner artifact implementation to the CE-declared signature.",
                        "If the declared signature is no longer correct, return a contract amendment request to CE.",
                    ),
                )
            )
            continue
        if issue.code.startswith("contract_"):
            plans.append(
                CrossArtifactRepairPlan(
                    strategy="contract_amendment_required",
                    authority="ce_amendment_required",
                    issue_code=issue.code,
                    owner_path=issue.owner_path,
                    symbol=issue.symbol,
                    confidence="high",
                    evidence=(issue.message,),
                    constraints=("Director must not mutate the interface contract.",),
                )
            )
            continue
        if issue.code != "unresolved_import_symbol":
            continue
        available = tuple(str(item) for item in issue.details.get("available_exports", ()) if str(item or "").strip())
        replacement = _closest_symbol(issue.symbol, available)
        if replacement:
            plans.append(
                CrossArtifactRepairPlan(
                    strategy="rename_consumer_to_existing_interface",
                    authority="director_repair_within_contract",
                    issue_code=issue.code,
                    importer_path=issue.importer_path,
                    owner_path=issue.owner_path,
                    symbol=issue.symbol,
                    replacement_symbol=replacement,
                    confidence="high",
                    evidence=(issue.message,),
                    constraints=("Update the consumer reference only; do not create empty stubs.",),
                )
            )
            continue
        if not issue.details.get("contract_declared"):
            plans.append(
                CrossArtifactRepairPlan(
                    strategy="contract_amendment_required",
                    authority="ce_amendment_required",
                    issue_code=issue.code,
                    importer_path=issue.importer_path,
                    owner_path=issue.owner_path,
                    symbol=issue.symbol,
                    confidence="high",
                    evidence=(issue.message,),
                    constraints=(
                        "CE must declare the intended owner export or revise the consumer design.",
                        "Director must not invent owner exports outside a declared interface contract.",
                    ),
                )
            )
            continue
        plans.append(
            CrossArtifactRepairPlan(
                strategy="add_real_interface_to_owner",
                authority="director_repair_within_contract",
                issue_code=issue.code,
                importer_path=issue.importer_path,
                owner_path=issue.owner_path,
                symbol=issue.symbol,
                confidence="high",
                evidence=(issue.message,),
                constraints=(
                    "Implement the real exported interface declared by CE in the owner artifact.",
                    "Do not change the interface contract from Director.",
                    "Do not satisfy this plan with pass, TODO, NotImplemented, or placeholder-only stubs.",
                ),
            )
        )
    return _dedupe_repair_plans(plans)


def _validate_contract_against_snapshot(
    contract: CrossArtifactInterfaceContract,
    snapshot: SymbolIndexSnapshot,
) -> list[CrossArtifactConsistencyIssue]:
    issues: list[CrossArtifactConsistencyIssue] = []
    for requirement in contract.interfaces:
        actual = {symbol.name: symbol for symbol in snapshot.namespace_exports.get(requirement.owner_path, ())}
        symbol = actual.get(requirement.name)
        if symbol is None:
            issues.append(
                CrossArtifactConsistencyIssue(
                    code="contract_export_missing",
                    message=(
                        "Artifact quality scan failed: cross-artifact contract requires "
                        f"{requirement.name!r} in {requirement.owner_path}, but the owner does not export it"
                    ),
                    owner_path=requirement.owner_path,
                    symbol=requirement.name,
                    details={"task_id": contract.task_id, "domain": requirement.domain},
                )
            )
            continue
        if requirement.signature_digest and symbol.signature_digest != requirement.signature_digest:
            issues.append(
                CrossArtifactConsistencyIssue(
                    code="contract_signature_mismatch",
                    message=(
                        "Artifact quality scan failed: cross-artifact contract signature mismatch for "
                        f"{requirement.name!r} in {requirement.owner_path}"
                    ),
                    owner_path=requirement.owner_path,
                    symbol=requirement.name,
                    details={
                        "task_id": contract.task_id,
                        "expected_signature_digest": requirement.signature_digest,
                        "actual_signature_digest": symbol.signature_digest,
                    },
                )
            )
    return issues


@dataclass(frozen=True, slots=True)
class _ReexportEdge:
    specifier: str
    symbols: Mapping[str, str]
    export_all: bool = False


def _scan_file_interfaces(
    root: Path,
    relative_path: str,
    text: str,
) -> tuple[list[InterfaceSymbol], list[InterfaceImport], list[_ReexportEdge], bool]:
    suffix = Path(relative_path).suffix.lower()
    if suffix == ".py":
        return _scan_python_file(root, relative_path, text)
    if suffix in _TS_JS_EXTS:
        return _scan_ts_js_file(root, relative_path, text)
    if suffix == ".go":
        return _scan_go_file(relative_path, text), [], [], False
    return [], [], [], False


def _scan_python_file(
    root: Path, relative_path: str, text: str
) -> tuple[list[InterfaceSymbol], list[InterfaceImport], list[_ReexportEdge], bool]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], [], [], False
    module_id = _python_module_id(relative_path)
    exports: list[InterfaceSymbol] = []
    imports: list[InterfaceImport] = []
    reexports: list[_ReexportEdge] = []
    unknown_exports = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "__getattr__":
                unknown_exports = True
            signature = _python_function_signature(node)
            exports.append(_symbol(node.name, "function", relative_path, f"{module_id}.{node.name}", signature))
        elif isinstance(node, ast.ClassDef):
            signature = _python_class_signature(node)
            exports.append(_symbol(node.name, "class", relative_path, f"{module_id}.{node.name}", signature))
        elif isinstance(node, ast.Assign):
            for name in _python_assignment_names(node.targets):
                if name not in _PY_BUILTIN_DUNDER_EXPORTS:
                    exports.append(_symbol(name, "value", relative_path, f"{module_id}.{name}", "value"))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exports.append(_symbol(node.target.id, "value", relative_path, f"{module_id}.{node.target.id}", "value"))
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                unknown_exports = True
                continue
            symbols = tuple((alias.asname or alias.name).strip() for alias in node.names if alias.name.strip())
            target_module = _resolve_python_import_module_id(relative_path, node.module or "", node.level or 0)
            owner_path = _python_module_path(root, target_module)
            imports.append(
                InterfaceImport(
                    importer_path=relative_path,
                    module=("." * (node.level or 0)) + (node.module or ""),
                    symbols=symbols,
                    import_kind="python.import_from",
                    resolved_owner_path=owner_path,
                )
            )
            if target_module and owner_path:
                reexports.append(
                    _ReexportEdge(
                        specifier=target_module,
                        symbols={alias.name: alias.asname or alias.name for alias in node.names if alias.name != "*"},
                    )
                )
    return _dedupe_symbols(exports), imports, reexports, unknown_exports


def _ts_js_code_mask(text: str) -> list[bool]:
    """Mark TS/JS source positions that are executable code.

    Interface snapshots must not turn fixture strings into physical imports or
    exports. This deliberately treats full template literals as non-code; a
    missed dynamic edge is safer than a false cross-artifact contract failure.
    """

    source = str(text or "")
    mask = [True] * len(source)
    i = 0
    n = len(source)
    while i < n:
        char = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if char == "/" and nxt == "/":
            start = i
            i += 2
            while i < n and source[i] not in "\r\n":
                i += 1
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char == "/" and nxt == "*":
            start = i
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char in {"'", '"', "`"}:
            quote = char
            start = i
            i += 1
            escaped = False
            while i < n:
                current = source[i]
                if escaped:
                    escaped = False
                    i += 1
                    continue
                if current == "\\":
                    escaped = True
                    i += 1
                    continue
                i += 1
                if current == quote:
                    break
            for pos in range(start, i):
                mask[pos] = False
            continue
        i += 1
    return mask


def _match_starts_in_ts_js_code(mask: list[bool], start: int) -> bool:
    return 0 <= start < len(mask) and mask[start]


def _scan_ts_js_file(
    root: Path, relative_path: str, text: str
) -> tuple[list[InterfaceSymbol], list[InterfaceImport], list[_ReexportEdge], bool]:
    exports: list[InterfaceSymbol] = []
    imports: list[InterfaceImport] = []
    reexports: list[_ReexportEdge] = []
    unknown_exports = bool(_TS_UNKNOWABLE_EXPORT_RE.search(text))
    code_mask = _ts_js_code_mask(text)
    module_id = _ts_module_id(relative_path)
    for match in _TS_EXPORT_DECL_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        name = (
            match.group("fn")
            or match.group("cls")
            or match.group("ty")
            or match.group("cenum")
            or match.group("var")
            or ""
        )
        if not name:
            continue
        kind = (
            "function"
            if match.group("fn")
            else "class"
            if match.group("cls")
            else "type"
            if match.group("ty")
            else "value"
        )
        signature = f"{kind} {name}"
        if match.group("fn_params"):
            signature = f"function {name}{_compact_params(match.group('fn_params') or '')}"
        exports.append(_symbol(name, kind, relative_path, f"{module_id}.{name}", signature))
    if any(_match_starts_in_ts_js_code(code_mask, match.start()) for match in _TS_EXPORT_DEFAULT_RE.finditer(text)):
        exports.append(_symbol("default", "default", relative_path, f"{module_id}.default", "default"))
    for match in _TS_EXPORT_CLAUSE_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        mapping = _parse_ts_export_clause(match.group("inner"))
        specifier = str(match.group("spec") or "").strip()
        if specifier:
            owner = _resolve_ts_relative_path(root, relative_path, specifier)
            if owner:
                reexports.append(_ReexportEdge(specifier=owner, symbols=mapping))
            continue
        for _source, exported in mapping.items():
            exports.append(_symbol(exported, "value", relative_path, f"{module_id}.{exported}", "value"))
    for match in _TS_EXPORT_STAR_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        owner = _resolve_ts_relative_path(root, relative_path, str(match.group("spec") or ""))
        if owner:
            reexports.append(_ReexportEdge(specifier=owner, symbols={}, export_all=True))
        else:
            unknown_exports = True
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        if match.group("typeonly") or not _ts_symbol_coherence_enabled():
            continue
        specifier = str(match.group("spec") or "").strip()
        owner = _resolve_ts_relative_path(root, relative_path, specifier) if specifier.startswith(".") else ""
        names = tuple(_parse_ts_import_names(match.group("names")))
        if owner and names:
            imports.append(
                InterfaceImport(
                    importer_path=relative_path,
                    module=specifier,
                    symbols=names,
                    import_kind="typescript.named_import",
                    resolved_owner_path=owner,
                )
            )
    return _dedupe_symbols(exports), imports, reexports, unknown_exports


def _scan_go_file(relative_path: str, text: str) -> list[InterfaceSymbol]:
    exports: list[InterfaceSymbol] = []
    module_id = _module_id_from_path(relative_path)
    for match in _GO_EXPORT_DECL_RE.finditer(text):
        name = str(match.group("name") or "").strip()
        if name:
            exports.append(_symbol(name, "go_export", relative_path, f"{module_id}.{name}", "go_export"))
    return _dedupe_symbols(exports)


def _resolve_namespace_exports(
    root: Path,
    physical: Mapping[str, tuple[InterfaceSymbol, ...]],
    reexports: Mapping[str, tuple[_ReexportEdge, ...]],
    initial_unknown_export_paths: set[str],
) -> tuple[dict[str, tuple[InterfaceSymbol, ...]], set[str]]:
    cache: dict[str, tuple[InterfaceSymbol, ...]] = {}
    unknown_paths: set[str] = set(initial_unknown_export_paths)

    def resolve(path: str, seen: frozenset[str], inherited_unknown: set[str]) -> tuple[InterfaceSymbol, ...]:
        if path in cache:
            return cache[path]
        if path in seen:
            return physical.get(path, ())
        symbols = list(physical.get(path, ()))
        seen_next = frozenset((*seen, path))
        for edge in reexports.get(path, ()):
            source_path = edge.specifier
            if source_path not in physical and source_path not in reexports:
                source_path = _python_module_path(root, source_path) or source_path
            if source_path in inherited_unknown:
                unknown_paths.add(path)
                continue
            source_symbols = {symbol.name: symbol for symbol in resolve(source_path, seen_next, inherited_unknown)}
            if edge.export_all:
                if source_path not in physical and source_path not in reexports:
                    unknown_paths.add(path)
                    continue
                for symbol in source_symbols.values():
                    if symbol.name != "default":
                        symbols.append(_reexport_symbol(symbol, path))
                continue
            for source_name, exported_name in edge.symbols.items():
                source = source_symbols.get(source_name)
                if source is not None:
                    symbols.append(_reexport_symbol(source, path, exported_name=exported_name))
        resolved = tuple(_dedupe_symbols(symbols))
        cache[path] = resolved
        return resolved

    for path in sorted(set(physical) | set(reexports)):
        resolve(path, frozenset(), unknown_paths)
    return cache, unknown_paths


def _symbol(name: str, kind: str, owner_path: str, resolution_path: str, signature: str) -> InterfaceSymbol:
    normalized_signature = " ".join(str(signature or "").split())
    return InterfaceSymbol(
        name=name,
        symbol_kind=kind,
        owner_path=owner_path,
        resolution_path=resolution_path,
        signature=normalized_signature,
        signature_digest=_signature_digest(normalized_signature),
    )


def _reexport_symbol(symbol: InterfaceSymbol, owner_path: str, *, exported_name: str = "") -> InterfaceSymbol:
    name = exported_name or symbol.name
    module_id = _module_id_from_path(owner_path)
    return InterfaceSymbol(
        name=name,
        symbol_kind=symbol.symbol_kind,
        owner_path=owner_path,
        resolution_path=f"{module_id}.{name}",
        signature=symbol.signature,
        signature_digest=symbol.signature_digest,
        reexported_from=symbol.owner_path,
        semantic_role=symbol.semantic_role,
    )


def _signature_digest(signature: str) -> str:
    if not signature:
        return ""
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def _iter_source_files(root: Path) -> Iterable[str]:
    for current_root, dir_names, file_names in root.walk():
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIRS]
        for name in file_names:
            path = current_root / name
            if path.suffix.lower() in _SOURCE_EXTS:
                yield path.relative_to(root).as_posix()


def _path_in_scope(path: str, relative_paths: Iterable[str]) -> bool:
    normalized = _normalize_rel_path(path)
    for raw_scope in relative_paths:
        scope = _normalize_rel_path(raw_scope)
        if not scope:
            continue
        if normalized == scope or normalized.startswith(scope.rstrip("/") + "/"):
            return True
    return False


def _normalize_rel_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    parts = [part for part in path.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _module_id_from_path(path: str) -> str:
    normalized = _normalize_rel_path(path)
    if not normalized:
        return ""
    suffix = Path(normalized).suffix
    if suffix:
        normalized = normalized[: -len(suffix)]
    if normalized.endswith("/__init__"):
        normalized = normalized[: -len("/__init__")]
    return normalized.replace("/", ".")


def _python_module_id(relative_path: str) -> str:
    return _module_id_from_path(relative_path)


def _ts_module_id(relative_path: str) -> str:
    return _module_id_from_path(relative_path)


def _resolve_python_import_module_id(importer_path: str, module: str, level: int) -> str:
    module = str(module or "").strip(".")
    if level <= 0:
        return module
    importer_module = _python_module_id(importer_path)
    package_parts = importer_module.split(".")
    if not importer_path.endswith("__init__.py"):
        package_parts = package_parts[:-1]
    if level > 1:
        package_parts = package_parts[: -(level - 1)] if level - 1 <= len(package_parts) else []
    if module:
        package_parts.extend(module.split("."))
    return ".".join(part for part in package_parts if part)


def _python_module_path(root: Path, module_id: str) -> str:
    if not module_id:
        return ""
    relative_base = Path(*module_id.split("."))
    candidates = (relative_base.with_suffix(".py"), relative_base / "__init__.py")
    for candidate in candidates:
        full = root / candidate
        if full.is_file():
            return candidate.as_posix()
    return ""


def _python_submodule_owner_path(root: Path, owner_path: str, name: str) -> str:
    if not owner_path or not name:
        return ""
    owner = root / owner_path
    package_dir = owner.parent if owner.name == "__init__.py" else owner.with_suffix("")
    for candidate in (package_dir / f"{name}.py", package_dir / name / "__init__.py"):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved.relative_to(root).as_posix()
    return ""


def _resolve_ts_relative_path(root: Path, importer_path: str, specifier: str) -> str:
    if not specifier.startswith("."):
        return ""
    base = (root / importer_path).parent / specifier
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
        if base.suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            candidates.extend(base.with_suffix(ext) for ext in _TS_JS_RESOLUTION_EXTS if ext != base.suffix)
    else:
        candidates.extend(base.with_suffix(ext) for ext in _TS_JS_RESOLUTION_EXTS)
        candidates.extend(base / f"index{ext}" for ext in _TS_JS_RESOLUTION_EXTS)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved.relative_to(root).as_posix()
    return ""


def _python_assignment_names(targets: list[ast.expr]) -> list[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(item.id for item in target.elts if isinstance(item, ast.Name))
    return names


def _python_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f"{node.name}({_python_args_signature(node.args)})"


def _python_class_signature(node: ast.ClassDef) -> str:
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            return f"init({_python_args_signature(item.args, drop_self=True)})"
    return "class"


def _python_args_signature(args: ast.arguments, *, drop_self: bool = False) -> str:
    positional = [arg.arg for arg in [*args.posonlyargs, *args.args]]
    if drop_self and positional and positional[0] in {"self", "cls"}:
        positional = positional[1:]
    if args.vararg:
        positional.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        positional.append("*")
    positional.extend(arg.arg for arg in args.kwonlyargs)
    if args.kwarg:
        positional.append(f"**{args.kwarg.arg}")
    return ", ".join(positional)


def _parse_ts_import_names(inner: str) -> list[str]:
    names: list[str] = []
    for raw_part in _strip_ts_js_clause_comments(inner).split(","):
        token = raw_part.strip()
        if not token:
            continue
        if token.startswith("type "):
            continue
        source_name = token.split(" as ", 1)[0].strip()
        if source_name:
            names.append(source_name)
    return list(dict.fromkeys(names))


def _strip_ts_js_clause_comments(inner: str) -> str:
    clause = str(inner or "")
    mask = _ts_js_code_mask(clause)
    return "".join(char if mask[index] else " " for index, char in enumerate(clause))


def _ts_symbol_coherence_enabled() -> bool:
    return os.environ.get(_TS_SYMBOL_COHERENCE_FLAG, "1").strip().lower() not in {"0", "false", "no", "off"}


def _parse_ts_export_clause(inner: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for raw_part in _strip_ts_js_clause_comments(inner).split(","):
        token = raw_part.strip()
        if not token:
            continue
        token = re.sub(r"^type\s+", "", token).strip()
        if " as " in token:
            source_name, exported_name = [part.strip() for part in token.split(" as ", 1)]
        else:
            source_name = exported_name = token
        if source_name and exported_name:
            names[source_name] = exported_name
    return names


def _compact_params(params: str) -> str:
    return "(" + ", ".join(part.strip() for part in str(params or "").strip("()").split(",") if part.strip()) + ")"


def _dedupe_symbols(symbols: Iterable[InterfaceSymbol]) -> list[InterfaceSymbol]:
    seen: set[str] = set()
    unique: list[InterfaceSymbol] = []
    for symbol in symbols:
        if not symbol.name or symbol.name in seen:
            continue
        seen.add(symbol.name)
        unique.append(symbol)
    return unique


def _dedupe_issues(issues: Iterable[CrossArtifactConsistencyIssue]) -> list[CrossArtifactConsistencyIssue]:
    seen: set[str] = set()
    unique: list[CrossArtifactConsistencyIssue] = []
    for issue in issues:
        key = issue.message
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _dedupe_repair_plans(plans: Iterable[CrossArtifactRepairPlan]) -> list[CrossArtifactRepairPlan]:
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[CrossArtifactRepairPlan] = []
    for plan in plans:
        key = (
            plan.strategy,
            plan.authority,
            plan.owner_path,
            plan.symbol,
            plan.replacement_symbol,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(plan)
    return unique


def _closest_symbol(symbol: str, available: tuple[str, ...]) -> str:
    if not symbol or not available:
        return ""
    lowered = {item.lower(): item for item in available}
    direct = lowered.get(symbol.lower())
    if direct:
        return direct
    normalized_symbol = _symbol_similarity_key(symbol)
    for item in available:
        if _symbol_similarity_key(item) == normalized_symbol:
            return item
    matches = difflib.get_close_matches(symbol, available, n=1, cutoff=0.78)
    return matches[0] if matches else ""


def _symbol_similarity_key(symbol: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(symbol or "").lower())


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ContractAmendmentRequest",
    "CrossArtifactConsistencyIssue",
    "CrossArtifactInterfaceContract",
    "CrossArtifactInterfaceRequirement",
    "CrossArtifactRepairPlan",
    "InterfaceImport",
    "InterfaceSymbol",
    "SymbolIndexSnapshot",
    "build_contract_amendment_request",
    "build_symbol_index_snapshot",
    "plan_cross_artifact_repairs",
    "scan_cross_artifact_consistency",
    "scan_cross_artifact_consistency_errors",
]
