"""Pure, evidence-driven Go repair planning for Director execution.

The functions in this module never mutate a workspace and never spawn a
process. They convert authoritative quality-gate diagnostics plus UTF-8 source
content into deterministic write plans. The Director adapter remains the only
component allowed to execute those plans through its governed tool chain.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .go_syntax import (
    GoDeclaration,
    iter_go_import_literals,
    iter_go_top_level_declarations,
)

_GO_MOD_MODULE_RE = re.compile(r"(?m)^\s*module\s+(?P<module>\S+)\s*(?://.*)?$")
_GO_VERSION_RE = re.compile(r"^v\d+(?:\.\d+){0,2}(?:[-+].*)?$")
_GO_IMPORT_DIAGNOSTIC_PATTERNS = (
    re.compile(
        r"\bno required module provides package\s+"
        r"(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcannot find module providing package\s+"
        r"(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpackage\s+(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)"
        r"\s+is not in std\b",
        re.IGNORECASE,
    ),
)
_GO_DUPLICATE_NAME_PATTERNS = (
    re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s+redeclared\b", re.IGNORECASE),
    re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s+already declared\b", re.IGNORECASE),
    re.compile(
        r"\bmethod\s+(?:\([^)]*\)|[A-Za-z_]\w*)\.(?P<name>[A-Za-z_]\w*)"
        r"\s+already declared\b",
        re.IGNORECASE,
    ),
)
_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".polaris",
        "node_modules",
        "playwright-report",
        "runtime",
        "test-results",
        "vendor",
    }
)


@dataclass(frozen=True, slots=True)
class GoImportReplacement:
    """One exact import literal replacement supported by compiler evidence."""

    before: str
    after: str
    reason: str


@dataclass(frozen=True, slots=True)
class GoFileRepairPlan:
    """Complete UTF-8 file content plus audit evidence for a governed write."""

    file: str
    content: str
    repair_kinds: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoRepairBlocker:
    """A quality defect that the deterministic planner refused to mutate."""

    code: str
    message: str
    evidence: tuple[str, ...]
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GoRepairPlan:
    """All safe writes and explicit blockers found in one planning pass."""

    writes: tuple[GoFileRepairPlan, ...]
    blockers: tuple[GoRepairBlocker, ...]


@dataclass(frozen=True, slots=True)
class _TextEdit:
    start: int
    end: int
    replacement: str
    repair_kind: str
    evidence: str


def extract_go_import_paths_from_errors(errors: Sequence[str]) -> frozenset[str]:
    """Extract exact missing package paths from supported Go diagnostics."""

    paths: set[str] = set()
    for error in errors:
        text = str(error or "")
        for pattern in _GO_IMPORT_DIAGNOSTIC_PATTERNS:
            for match in pattern.finditer(text):
                paths.add(match.group("path").rstrip(".,:;"))
    return frozenset(paths)


def extract_go_duplicate_names_from_errors(errors: Sequence[str]) -> frozenset[str]:
    """Extract declaration names from Go redeclaration diagnostics."""

    names: set[str] = set()
    for error in errors:
        text = str(error or "")
        for pattern in _GO_DUPLICATE_NAME_PATTERNS:
            names.update(match.group("name") for match in pattern.finditer(text))
    return frozenset(names)


def _has_duplicate_diagnostic(errors: Sequence[str]) -> bool:
    return any(
        "redeclared" in str(error or "").lower()
        or "already declared" in str(error or "").lower()
        for error in errors
    )


def _parse_go_module_path(go_mod_text: str) -> str:
    match = _GO_MOD_MODULE_RE.search(go_mod_text)
    return match.group("module").strip() if match else ""


def _is_module_path_token(token: str) -> bool:
    return (
        "/" in token
        and not token.startswith(("./", "../"))
        and not _GO_VERSION_RE.fullmatch(token)
    )


def _declared_module_paths(go_mod_text: str) -> frozenset[str]:
    """Return remote module paths explicitly named by go.mod directives."""

    declared: set[str] = set()
    block_directive = ""
    for raw_line in go_mod_text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if line == ")":
            block_directive = ""
            continue
        block_match = re.fullmatch(r"(require|replace|exclude)\s*\(", line)
        if block_match:
            block_directive = block_match.group(1)
            continue

        tokens = line.replace("=>", " ").split()
        if not tokens:
            continue
        directive = block_directive
        if tokens[0] in {"exclude", "module", "replace", "require", "retract"}:
            directive = tokens.pop(0)
        if directive == "module" or not directive:
            continue
        declared.update(token for token in tokens if _is_module_path_token(token))
    return frozenset(declared)


def _is_ignored_go_file(workspace: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return True
    return any(part in _IGNORED_DIRECTORY_NAMES for part in relative.parts[:-1])


def _iter_go_files(workspace: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(workspace.rglob("*.go"), key=lambda item: item.as_posix())
        if path.is_file() and not _is_ignored_go_file(workspace, path)
    )


def _read_utf8(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _local_package_dirs(workspace: Path) -> frozenset[str]:
    dirs: set[str] = set()
    for go_file in _iter_go_files(workspace):
        if go_file.name.endswith("_test.go"):
            continue
        relative = go_file.parent.relative_to(workspace).as_posix()
        if relative != ".":
            dirs.add(relative)
    return frozenset(dirs)


def _is_declared_dependency(import_path: str, declared_modules: Iterable[str]) -> bool:
    return any(
        import_path == module or import_path.startswith(f"{module}/")
        for module in declared_modules
    )


def _unique_longest_suffix(path: str, package_dirs: Iterable[str]) -> str | None:
    matches = {
        package_dir
        for package_dir in package_dirs
        if path == package_dir or path.endswith(f"/{package_dir}")
    }
    if not matches:
        return None
    longest_length = max(len(match) for match in matches)
    longest = sorted(match for match in matches if len(match) == longest_length)
    return longest[0] if len(longest) == 1 else None


def _replacement_for_import(
    *,
    module: str,
    import_path: str,
    declared_modules: frozenset[str],
    package_dirs: frozenset[str],
) -> GoImportReplacement | None:
    if _is_declared_dependency(import_path, declared_modules) or import_path == module:
        return None
    if import_path.startswith(f"{module}/"):
        current_suffix = import_path[len(module) + 1 :]
        if current_suffix in package_dirs:
            return None
        corrected_suffix = _unique_longest_suffix(current_suffix, package_dirs)
        if corrected_suffix and corrected_suffix != current_suffix:
            return GoImportReplacement(
                before=import_path,
                after=f"{module}/{corrected_suffix}",
                reason="compiler_diagnostic_unique_local_subpath",
            )
        return None

    parts = tuple(part for part in import_path.split("/") if part)
    module_leaf = module.rsplit("/", 1)[-1]
    candidates: set[str] = set()
    for index in range(1, len(parts)):
        wrong_prefix = "/".join(parts[:index])
        suffix = "/".join(parts[index:])
        if wrong_prefix.rsplit("/", 1)[-1] == module_leaf and suffix in package_dirs:
            candidates.add(f"{module}/{suffix}")
    if len(candidates) != 1:
        return None
    return GoImportReplacement(
        before=import_path,
        after=next(iter(candidates)),
        reason="compiler_diagnostic_module_leaf_and_local_package_match",
    )


def _comment_duplicate_declaration(
    source: str,
    declaration: GoDeclaration,
) -> str | None:
    line_end = source.find("\n", declaration.end)
    trailing = source[declaration.end : None if line_end < 0 else line_end]
    if trailing.strip():
        return None
    original = source[declaration.start : declaration.end]
    marker = (
        f"// [polaris deterministic repair] exact duplicate "
        f"{declaration.kind} {declaration.name}: "
    )
    return "".join(f"{marker}{line}" for line in original.splitlines(keepends=True))


def _duplicate_edits(
    *,
    sources: dict[str, str],
    duplicate_names: frozenset[str],
    has_duplicate_diagnostic: bool,
) -> tuple[dict[str, list[_TextEdit]], list[GoRepairBlocker]]:
    edits: dict[str, list[_TextEdit]] = {}
    blockers: list[GoRepairBlocker] = []
    if not has_duplicate_diagnostic:
        return edits, blockers
    if not duplicate_names:
        blockers.append(
            GoRepairBlocker(
                code="go_duplicate_diagnostic_unparsed",
                message=(
                    "Go reported a redeclaration, but the deterministic planner "
                    "could not extract an exact declaration name."
                ),
                evidence=("artifact_quality_errors:duplicate_without_name",),
            )
        )
        return edits, blockers

    declarations_by_name: dict[str, list[GoDeclaration]] = {}
    for file, source in sources.items():
        for declaration in iter_go_top_level_declarations(file, source):
            if declaration.name in duplicate_names:
                declarations_by_name.setdefault(declaration.name, []).append(
                    declaration
                )

    for name in sorted(duplicate_names):
        declarations = sorted(
            declarations_by_name.get(name, []),
            key=lambda declaration: (declaration.file, declaration.start),
        )
        if len(declarations) < 2:
            blockers.append(
                GoRepairBlocker(
                    code="go_duplicate_declaration_not_located",
                    message=(
                        f"Go reported duplicate declaration {name!r}, but fewer "
                        "than two safely parseable top-level declarations were found."
                    ),
                    evidence=(f"duplicate_name:{name}",),
                    files=tuple(declaration.file for declaration in declarations),
                )
            )
            continue

        signatures = {declaration.signature for declaration in declarations}
        if len(signatures) != 1:
            blockers.append(
                GoRepairBlocker(
                    code="go_duplicate_declarations_differ",
                    message=(
                        f"Duplicate declaration {name!r} is not token-identical; "
                        "automatic merge or deletion is intentionally blocked."
                    ),
                    evidence=tuple(
                        f"{declaration.file}:{declaration.line}"
                        for declaration in declarations
                    ),
                    files=tuple(
                        dict.fromkeys(declaration.file for declaration in declarations)
                    ),
                )
            )
            continue

        canonical = declarations[0]
        for duplicate in declarations[1:]:
            replacement = _comment_duplicate_declaration(
                sources[duplicate.file], duplicate
            )
            if replacement is None:
                blockers.append(
                    GoRepairBlocker(
                        code="go_duplicate_declaration_shared_line",
                        message=(
                            f"Duplicate declaration {name!r} shares a source line "
                            "with other code; line-comment repair was blocked."
                        ),
                        evidence=(f"{duplicate.file}:{duplicate.line}",),
                        files=(duplicate.file,),
                    )
                )
                continue
            edits.setdefault(duplicate.file, []).append(
                _TextEdit(
                    start=duplicate.start,
                    end=duplicate.end,
                    replacement=replacement,
                    repair_kind="exact_duplicate_declaration",
                    evidence=(
                        f"duplicate:{duplicate.file}:{duplicate.line};"
                        f"canonical:{canonical.file}:{canonical.line};name:{name}"
                    ),
                )
            )
    return edits, blockers


def _apply_edits(
    *,
    file: str,
    source: str,
    edits: Sequence[_TextEdit],
) -> GoFileRepairPlan | GoRepairBlocker:
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    previous_end = -1
    for edit in ordered:
        if edit.start < 0 or edit.end > len(source) or edit.start >= edit.end:
            return GoRepairBlocker(
                code="go_repair_edit_out_of_bounds",
                message=f"Planned Go repair for {file} had an invalid source span.",
                evidence=(f"span:{edit.start}:{edit.end};length:{len(source)}",),
                files=(file,),
            )
        if edit.start < previous_end:
            return GoRepairBlocker(
                code="go_repair_edit_overlap",
                message=f"Planned Go repairs for {file} overlap; mutation was blocked.",
                evidence=tuple(item.evidence for item in ordered),
                files=(file,),
            )
        previous_end = edit.end

    updated = source
    for edit in reversed(ordered):
        updated = updated[: edit.start] + edit.replacement + updated[edit.end :]
    return GoFileRepairPlan(
        file=file,
        content=updated,
        repair_kinds=tuple(dict.fromkeys(edit.repair_kind for edit in ordered)),
        evidence=tuple(edit.evidence for edit in ordered),
    )


def plan_go_repairs(
    workspace: Path,
    *,
    artifact_quality_errors: Sequence[str],
) -> GoRepairPlan:
    """Plan safe Go repairs from authoritative quality-gate diagnostics.

    No mutation is planned merely because source text looks suspicious. An
    import edit requires an exact missing-package diagnostic and a unique local
    package match. A duplicate declaration edit requires an explicit
    redeclaration diagnostic and token-identical declarations. Every ambiguous
    or destructive case becomes a blocker instead of a silent fallback.
    """

    workspace_root = workspace.resolve()
    go_files = _iter_go_files(workspace_root)
    if not go_files:
        return GoRepairPlan(writes=(), blockers=())

    sources: dict[str, str] = {}
    unreadable: list[str] = []
    for path in go_files:
        relative = path.relative_to(workspace_root).as_posix()
        source = _read_utf8(path)
        if source is None:
            unreadable.append(relative)
        else:
            sources[relative] = source

    blockers: list[GoRepairBlocker] = []
    if unreadable:
        blockers.append(
            GoRepairBlocker(
                code="go_source_not_utf8_readable",
                message="One or more Go source files could not be read as UTF-8.",
                evidence=tuple(f"unreadable:{file}" for file in unreadable),
                files=tuple(unreadable),
            )
        )

    edits_by_file: dict[str, list[_TextEdit]] = {}
    import_suspects = extract_go_import_paths_from_errors(artifact_quality_errors)
    if import_suspects:
        go_mod_text = _read_utf8(workspace_root / "go.mod")
        module = _parse_go_module_path(go_mod_text or "")
        if not module:
            blockers.append(
                GoRepairBlocker(
                    code="go_module_path_unavailable",
                    message=(
                        "Missing-package diagnostics were present, but go.mod did "
                        "not provide a readable canonical module path."
                    ),
                    evidence=tuple(
                        f"missing_package:{path}" for path in sorted(import_suspects)
                    ),
                    files=("go.mod",),
                )
            )
        else:
            declared_modules = _declared_module_paths(go_mod_text or "") - {module}
            package_dirs = _local_package_dirs(workspace_root)
            observed_suspects: set[str] = set()
            safely_repaired_originals: set[str] = set()
            for file, source in sources.items():
                for literal in iter_go_import_literals(source):
                    if literal.path not in import_suspects:
                        continue
                    observed_suspects.add(literal.path)
                    replacement = _replacement_for_import(
                        module=module,
                        import_path=literal.path,
                        declared_modules=declared_modules,
                        package_dirs=package_dirs,
                    )
                    if replacement is None:
                        continue
                    safely_repaired_originals.add(literal.path)
                    edits_by_file.setdefault(file, []).append(
                        _TextEdit(
                            start=literal.start,
                            end=literal.end,
                            replacement=replacement.after,
                            repair_kind="go_import_path",
                            evidence=(
                                f"{replacement.reason}:{replacement.before}"
                                f"->{replacement.after}"
                            ),
                        )
                    )

            for import_path in sorted(observed_suspects - safely_repaired_originals):
                reason = (
                    "declared dependency"
                    if _is_declared_dependency(import_path, declared_modules)
                    else "no unique local package mapping"
                )
                blockers.append(
                    GoRepairBlocker(
                        code="go_import_repair_not_safe",
                        message=(
                            f"Import {import_path!r} matched a compiler diagnostic, "
                            f"but deterministic repair was blocked: {reason}."
                        ),
                        evidence=(f"missing_package:{import_path}", f"reason:{reason}"),
                    )
                )

    duplicate_edits, duplicate_blockers = _duplicate_edits(
        sources=sources,
        duplicate_names=extract_go_duplicate_names_from_errors(artifact_quality_errors),
        has_duplicate_diagnostic=_has_duplicate_diagnostic(artifact_quality_errors),
    )
    for file, edits in duplicate_edits.items():
        edits_by_file.setdefault(file, []).extend(edits)
    blockers.extend(duplicate_blockers)

    writes: list[GoFileRepairPlan] = []
    for file in sorted(edits_by_file):
        planned = _apply_edits(
            file=file, source=sources[file], edits=edits_by_file[file]
        )
        if isinstance(planned, GoRepairBlocker):
            blockers.append(planned)
        elif planned.content != sources[file]:
            writes.append(planned)

    return GoRepairPlan(writes=tuple(writes), blockers=tuple(blockers))
