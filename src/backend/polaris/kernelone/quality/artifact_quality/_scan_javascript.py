"""Static ESM named-import vs export scans for JavaScript artifacts."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, cast

from polaris.kernelone.quality.artifact_quality._issues import (
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    _FileArtifactQualityEvidence,
)
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

_JS_NAMED_IMPORT_RE = re.compile(
    r"\bimport\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<specifier>\.\.?/[^'\"]+)['\"]"
)
_JS_NAMESPACE_IMPORT_RE = re.compile(
    r"\bimport\s*\*\s*as\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s*['\"](?P<specifier>\.\.?/[^'\"]+)['\"]"
)
_JS_RELATIVE_FROM_RE = re.compile(r"\bfrom\s*['\"](?P<specifier>\.\.?/[^'\"]+)['\"]")
_JS_MEMBER_ACCESS_RE = re.compile(r"(?<![./])\b(?P<alias>[A-Za-z_$][\w$]*)\.(?P<member>[A-Za-z_$][\w$]*)")
_JS_CATALOG_FIXTURE_RE = re.compile(r"\b(?P<name>DEFAULT_[A-Z][A-Z0-9_]*)\b")
_JS_LOCAL_BINDING_RE = re.compile(r"\b(?:const|let|var|function)\s+(?P<name>DEFAULT_[A-Z][A-Z0-9_]*)\b")
_JS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_JS_SUFFIXES = (".js", ".mjs", ".cjs")
_JS_MEMBER_SKIP = frozenset(
    {
        "default",
        "length",
        "name",
        "prototype",
        "then",
        "catch",
        "finally",
        "toString",
        "valueOf",
        "keys",
        "values",
        "entries",
        "seeds",
        "domains",
        "version",
        "js",
        "mjs",
        "cjs",
        "ts",
        "tsx",
        "json",
    }
)


def _scan_javascript_named_export_evidence(
    root_full: Path,
    relative_paths: list[str],
    *,
    reader: WorkspaceReadSnapshot | None = None,
    source_paths: tuple[str, ...] | None = None,
    source_inventory_complete: bool = True,
) -> _FileArtifactQualityEvidence:
    """Catch ESM named-import holes that ``node --check`` cannot see.

    ``node --check`` is parse-only. Live L2-11 failed QA on
    ``does not provide an export named 'computeVerdict'`` after Director
    settle reported zero diagnostics. Static importer/exporter matching
    keeps this on the Director repair path without executing CLI entrypoints.
    """

    if reader is not None and reader.root != root_full.absolute():
        raise ValueError("specialist reader workspace binding mismatch")
    importers = [
        str(Path(path).as_posix()).lstrip("./")
        for path in relative_paths
        if Path(str(path)).suffix.lower() in _JS_SUFFIXES
    ]
    if not importers:
        return _FileArtifactQualityEvidence()

    errors: list[str] = []
    issues: list[Any] = []
    seen: set[str] = set()
    incomplete_reasons: list[str] = []
    if reader is not None and source_paths is None:
        raise ValueError("controlled JavaScript specialist requires original source inventory")
    inventory = frozenset(source_paths) if source_paths is not None else None
    workspace_exports = _workspace_javascript_named_exports(
        root_full, reader=reader, source_paths=source_paths, incomplete_reasons=incomplete_reasons
    )
    exports_complete = source_inventory_complete and not incomplete_reasons

    def evidence() -> _FileArtifactQualityEvidence:
        return _FileArtifactQualityEvidence(
            errors=tuple(errors), issues=tuple(issues), incomplete_reasons=tuple(incomplete_reasons)
        )

    for importer in importers:
        importer_text = _javascript_specialist_text(root_full, importer, reader, incomplete_reasons)
        if importer_text is None:
            continue
        for spec_match in _JS_RELATIVE_FROM_RE.finditer(importer_text):
            specifier = str(spec_match.group("specifier") or "")
            previous_unknowns = len(incomplete_reasons)
            if (
                _resolve_relative_js_module(
                    root_full,
                    importer,
                    specifier,
                    reader=reader,
                    source_paths=inventory,
                    incomplete_reasons=incomplete_reasons,
                )
                is not None
                or len(incomplete_reasons) != previous_unknowns
            ):
                continue
            raw = f"{importer}: Error [ERR_MODULE_NOT_FOUND]: Cannot find module '{specifier}'"
            if raw in seen:
                continue
            seen.add(raw)
            errors.append(raw)
            issues.append(
                _file_artifact_quality_issue(
                    raw,
                    importer,
                    code="javascript_missing_relative_module",
                    source="javascript_named_export_scanner",
                    metadata={
                        "language": "javascript",
                        "diagnostic_kind": "missing_relative_module",
                        "module": specifier,
                    },
                )
            )
            if len(errors) >= 20:
                return evidence()
        for match in _JS_NAMED_IMPORT_RE.finditer(importer_text):
            exporter = _resolve_relative_js_module(
                root_full,
                importer,
                str(match.group("specifier") or ""),
                reader=reader,
                source_paths=inventory,
                incomplete_reasons=incomplete_reasons,
            )
            if exporter is None:
                continue
            exporter_text = _javascript_specialist_text(root_full, exporter, reader, incomplete_reasons)
            if exporter_text is None:
                continue
            for raw_symbol in str(match.group("symbols") or "").split(","):
                symbol = raw_symbol.split(" as ", 1)[0].strip()
                if not _JS_IDENTIFIER_RE.fullmatch(symbol):
                    continue
                if _javascript_module_exports_symbol(exporter_text, symbol):
                    continue
                specifier = str(match.group("specifier") or "")
                raw = (
                    f"{importer}: SyntaxError: The requested module '{specifier}' "
                    f"does not provide an export named '{symbol}'"
                )
                if raw in seen:
                    continue
                seen.add(raw)
                errors.append(raw)
                issues.append(
                    _file_artifact_quality_issue(
                        raw,
                        importer,
                        code="javascript_missing_named_export",
                        source="javascript_named_export_scanner",
                        metadata={
                            "language": "javascript",
                            "diagnostic_kind": "missing_named_export",
                            "symbol": symbol,
                            "module": specifier,
                            "exporter": exporter,
                        },
                    )
                )
                if len(errors) >= 20:
                    return evidence()
        _append_javascript_namespace_member_issues(
            root_full,
            importer=importer,
            importer_text=importer_text,
            errors=errors,
            issues=issues,
            seen=seen,
            reader=reader,
            source_paths=inventory,
            incomplete_reasons=incomplete_reasons,
        )
        if len(errors) >= 20:
            return evidence()
        if exports_complete:
            _append_javascript_missing_catalog_fixture_issues(
                importer=importer,
                importer_text=importer_text,
                workspace_exports=workspace_exports,
                errors=errors,
                issues=issues,
                seen=seen,
            )
        if len(errors) >= 20:
            return evidence()
    return evidence()


def _append_javascript_namespace_member_issues(
    root_full: Path,
    *,
    importer: str,
    importer_text: str,
    errors: list[str],
    issues: list[Any],
    seen: set[str],
    reader: WorkspaceReadSnapshot | None = None,
    source_paths: frozenset[str] | None = None,
    incomplete_reasons: list[str] | None = None,
) -> None:
    """Flag ``import * as ns`` member access that sibling modules do not export.

    Live L2-11: tests remapped named imports, then kept ``lost.DEFAULT_LOST_ITEMS``.
    ``node --check`` and named-import scan stayed green; ``npm test`` failed.
    """

    reasons = incomplete_reasons if incomplete_reasons is not None else []
    for match in _JS_NAMESPACE_IMPORT_RE.finditer(importer_text):
        alias = str(match.group("alias") or "")
        specifier = str(match.group("specifier") or "")
        exporter = _resolve_relative_js_module(
            root_full, importer, specifier, reader=reader, source_paths=source_paths, incomplete_reasons=reasons
        )
        if not alias or exporter is None:
            continue
        exporter_text = _javascript_specialist_text(root_full, exporter, reader, reasons)
        if exporter_text is None:
            continue
        exported = set(_javascript_named_exports(exporter_text))
        for member_match in _JS_MEMBER_ACCESS_RE.finditer(importer_text):
            if str(member_match.group("alias") or "") != alias:
                continue
            member = str(member_match.group("member") or "")
            if not member or member in _JS_MEMBER_SKIP or member in exported:
                continue
            if not _JS_IDENTIFIER_RE.fullmatch(member):
                continue
            # Live L2-11: `lost.js` path and `clue.weight` entity fields are
            # not module exports. Only flag CONSTANT_CASE catalog bindings.
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", member):
                continue
            raw = f"{importer}: TypeError: namespace '{alias}' from '{specifier}' has no exported member '{member}'"
            if raw in seen:
                continue
            seen.add(raw)
            errors.append(raw)
            issues.append(
                _file_artifact_quality_issue(
                    raw,
                    importer,
                    code="javascript_missing_namespace_export",
                    source="javascript_named_export_scanner",
                    metadata={
                        "language": "javascript",
                        "diagnostic_kind": "missing_namespace_export",
                        "symbol": member,
                        "alias": alias,
                        "module": specifier,
                        "exporter": exporter,
                    },
                )
            )
            if len(errors) >= 20:
                return


def _append_javascript_missing_catalog_fixture_issues(
    *,
    importer: str,
    importer_text: str,
    workspace_exports: set[str],
    errors: list[str],
    issues: list[Any],
    seen: set[str],
) -> None:
    """Flag DEFAULT_* catalogs that tests require but no sibling exports.

    Live L2-11 QA: after import remap, tests asserted populated
    ``DEFAULT_LOST_ITEMS`` seeds. Domain modules never exported those names.
    Inventing them is forbidden; tests must use existing create* factories.
    """

    if not _is_javascript_test_path(importer):
        return
    local_names = {str(match.group("name") or "") for match in _JS_LOCAL_BINDING_RE.finditer(importer_text)}
    for match in _JS_CATALOG_FIXTURE_RE.finditer(importer_text):
        name = str(match.group("name") or "")
        if not name or name in local_names or name in workspace_exports:
            continue
        raw = (
            f"{importer}: unresolved catalog fixture '{name}' "
            "(sibling modules do not export it; construct fixtures with existing create* factories; "
            "do not invent DEFAULT_* domain exports)"
        )
        if raw in seen:
            continue
        seen.add(raw)
        errors.append(raw)
        issues.append(
            _file_artifact_quality_issue(
                raw,
                importer,
                code="javascript_missing_catalog_fixture",
                source="javascript_named_export_scanner",
                metadata={
                    "language": "javascript",
                    "diagnostic_kind": "missing_catalog_fixture",
                    "symbol": name,
                },
            )
        )
        if len(errors) >= 20:
            return


def _javascript_specialist_text(
    root_full: Path, relative: str, reader: WorkspaceReadSnapshot | None, incomplete_reasons: list[str]
) -> str | None:
    """Use the existing scan snapshot, never an independent controlled reopen."""
    if reader is not None:
        if reader.root != root_full.absolute():
            raise ValueError("specialist reader workspace binding mismatch")
        read = reader.read(root_full / relative)
        if read.status == "readable":
            return cast("str | None", (read.text))
        incomplete_reasons.append(f"javascript_specialist_source_unknown:{relative}:{read.status}:{read.detail}")
        return None
    try:
        return (root_full / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _workspace_javascript_named_exports(
    root_full: Path,
    *,
    reader: WorkspaceReadSnapshot | None = None,
    source_paths: tuple[str, ...] | None = None,
    incomplete_reasons: list[str] | None = None,
) -> set[str]:
    names: set[str] = set()
    reasons = incomplete_reasons if incomplete_reasons is not None else []
    if reader is not None and source_paths is None:
        raise ValueError("controlled JavaScript export inventory is required")
    try:
        if reader is not None:
            assert source_paths is not None  # The explicit guard above fails closed.
            candidates = [root_full / path for path in source_paths]
        else:
            candidates = list(root_full.rglob("*"))
    except OSError:
        return names
    for path in candidates:
        if path.suffix.lower() not in _JS_SUFFIXES or (reader is None and not path.is_file()):
            continue
        if any(part in {".git", "node_modules", "dist", "build"} for part in path.parts):
            continue
        text = _javascript_specialist_text(root_full, path.relative_to(root_full).as_posix(), reader, reasons)
        if text is not None:
            names.update(_javascript_named_exports(text))
    return names


def _is_javascript_test_path(relative_path: str) -> bool:
    posix = str(relative_path or "").replace("\\", "/").lstrip("./")
    name = Path(posix).name.lower()
    return (
        "/tests/" in f"/{posix}/"
        or "/test/" in f"/{posix}/"
        or name.endswith(".test.js")
        or name.endswith(".spec.js")
        or name.startswith("test_")
    )


def _javascript_named_exports(text: str) -> tuple[str, ...]:
    """Return declared ESM named exports. Live L2-11 tests imported CLUE_KINDS
    while the sibling module exported CLUE_KIND.
    """

    names: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?m)^\s*export\s+(?:async\s+)?(?:class|function|const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)",
        text,
    ):
        name = str(match.group("name") or "")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    for match in re.finditer(r"(?m)^\s*export\s*\{(?P<body>[^}]+)\}", text):
        for raw in str(match.group("body") or "").split(","):
            token = raw.split(" as ", 1)[-1].strip()
            if _JS_IDENTIFIER_RE.fullmatch(token) and token not in seen:
                seen.add(token)
                names.append(token)
    return tuple(names)


def _javascript_module_exports_symbol(text: str, symbol: str) -> bool:
    return symbol in set(_javascript_named_exports(text))


def _resolve_relative_js_module(
    root_full: Path,
    importer: str,
    specifier: str,
    *,
    reader: WorkspaceReadSnapshot | None = None,
    source_paths: frozenset[str] | None = None,
    incomplete_reasons: list[str] | None = None,
) -> str | None:
    token = str(specifier or "").strip()
    if not token.startswith("."):
        return None
    base_dir = Path(importer).parent
    raw = (base_dir / token).as_posix()
    while raw.startswith("./"):
        raw = raw[2:]
    candidates = [raw]
    if not Path(raw).suffix:
        candidates.extend([f"{raw}{suffix}" for suffix in _JS_SUFFIXES])
        candidates.extend([f"{raw}/index{suffix}" for suffix in _JS_SUFFIXES])
    for candidate in candidates:
        if reader is not None:
            # Preserve lexical ancestry checks before normalizing '..', but do
            # not consult live metadata to resolve an already witnessed owner.
            normalized = Path(os.path.normpath(candidate)).as_posix()
            lexical_parts = Path(candidate).parts
            if ".." not in lexical_parts and source_paths is not None and normalized in source_paths:
                return normalized
            try:
                full, witness = reader.confined_path(root_full / candidate)
                normalized = full.relative_to(root_full).as_posix()
                if source_paths is not None and normalized in source_paths:
                    return cast("str | None", (normalized))
                if witness is not None and stat.S_ISREG(witness.st_mode):
                    if incomplete_reasons is not None:
                        incomplete_reasons.append(f"javascript_module_inventory_unknown:{candidate}")
                    return None
            except (OSError, RuntimeError, ValueError) as exc:
                if incomplete_reasons is not None:
                    incomplete_reasons.append(f"javascript_module_resolution_unknown:{candidate}:{exc}")
                return None
            continue
        normalized = str(Path(candidate).as_posix()).lstrip("./")
        if (root_full / normalized).is_file():
            return normalized
    return None
