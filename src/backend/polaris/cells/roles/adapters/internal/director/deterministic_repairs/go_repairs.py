"""Pure Go import-drift analysis for deterministic Director repairs.

This module deliberately plans edits but never writes files. The Director
adapter must execute every planned edit through ``DirectorToolExecutor`` so
workspace policy, path guards, runtime events, and effect receipts remain the
source of truth for mutations.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GO_MOD_MODULE_RE = re.compile(r"(?m)^\s*module\s+(?P<module>\S+)\s*(?://.*)?$")
_GO_VERSION_RE = re.compile(r"^v\d+(?:\.\d+){0,2}(?:[-+].*)?$")
_GO_DIAGNOSTIC_PATTERNS = (
    re.compile(
        r"\bno required module provides package\s+(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcannot find module providing package\s+(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpackage\s+(?P<path>[A-Za-z0-9._~+@/-]+/[A-Za-z0-9._~+@/-]+)\s+is not in std\b",
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
    """One exact import path replacement planned for a Go source file."""

    before: str
    after: str


@dataclass(frozen=True, slots=True)
class GoFileRepairPlan:
    """Complete UTF-8 file content plus the evidence that produced it."""

    file: str
    content: str
    replacements: tuple[GoImportReplacement, ...]


@dataclass(frozen=True, slots=True)
class _ImportLiteral:
    start: int
    end: int
    path: str


@dataclass(frozen=True, slots=True)
class _GoToken:
    kind: str
    value: str
    start: int
    end: int
    content_start: int | None = None
    content_end: int | None = None


def extract_go_import_paths_from_errors(errors: Sequence[str]) -> frozenset[str]:
    """Extract exact missing Go package paths from compiler diagnostics."""

    paths: set[str] = set()
    for error in errors:
        text = str(error or "")
        for pattern in _GO_DIAGNOSTIC_PATTERNS:
            for match in pattern.finditer(text):
                paths.add(match.group("path").rstrip(".,:;"))
    return frozenset(paths)


def _parse_go_module_path(go_mod_text: str) -> str:
    match = _GO_MOD_MODULE_RE.search(go_mod_text)
    return match.group("module").strip() if match else ""


def _declared_module_paths(go_mod_text: str) -> frozenset[str]:
    """Return module paths explicitly named by go.mod directives."""

    declared: set[str] = set()
    for raw_line in go_mod_text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line or line in {"(", ")", "require (", "replace (", "exclude ("}:
            continue
        tokens = line.replace("=>", " ").split()
        if tokens and tokens[0] in {
            "exclude",
            "module",
            "replace",
            "require",
            "retract",
        }:
            tokens = tokens[1:]
        for token in tokens:
            if "/" not in token or token.startswith(("./", "../")):
                continue
            if _GO_VERSION_RE.fullmatch(token):
                continue
            declared.add(token)
    return frozenset(declared)


def _iter_go_tokens(source: str) -> tuple[_GoToken, ...]:
    """Tokenize enough Go syntax to identify import declarations safely."""

    tokens: list[_GoToken] = []
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            closing = source.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if character in {'"', "`"}:
            quote = character
            token_start = index
            index += 1
            content_start = index
            while index < length:
                if quote == '"' and source[index] == "\\":
                    index = min(length, index + 2)
                    continue
                if source[index] == quote:
                    content_end = index
                    index += 1
                    tokens.append(
                        _GoToken(
                            kind="string",
                            value=source[content_start:content_end],
                            start=token_start,
                            end=index,
                            content_start=content_start,
                            content_end=content_end,
                        )
                    )
                    break
                index += 1
            continue
        if character.isalpha() or character == "_":
            token_start = index
            index += 1
            while index < length and (source[index].isalnum() or source[index] == "_"):
                index += 1
            tokens.append(
                _GoToken(
                    kind="identifier",
                    value=source[token_start:index],
                    start=token_start,
                    end=index,
                )
            )
            continue
        tokens.append(
            _GoToken(
                kind="punctuation",
                value=character,
                start=index,
                end=index + 1,
            )
        )
        index += 1
    return tuple(tokens)


def _literal_from_token(token: _GoToken) -> _ImportLiteral | None:
    if (
        token.kind != "string"
        or token.content_start is None
        or token.content_end is None
        or "\\" in token.value
    ):
        return None
    return _ImportLiteral(
        start=token.content_start,
        end=token.content_end,
        path=token.value,
    )


def _iter_import_literals(source: str) -> tuple[_ImportLiteral, ...]:
    tokens = _iter_go_tokens(source)
    literals: list[_ImportLiteral] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "identifier" or token.value != "import":
            index += 1
            continue

        cursor = index + 1
        if cursor >= len(tokens):
            break
        if tokens[cursor].value == "(":
            cursor += 1
            depth = 1
            while cursor < len(tokens) and depth > 0:
                current = tokens[cursor]
                if current.value == "(":
                    depth += 1
                elif current.value == ")":
                    depth -= 1
                elif depth == 1:
                    literal = _literal_from_token(current)
                    if literal is not None:
                        literals.append(literal)
                cursor += 1
            index = cursor
            continue

        for candidate in tokens[cursor : min(len(tokens), cursor + 3)]:
            literal = _literal_from_token(candidate)
            if literal is not None:
                literals.append(literal)
                break
            if candidate.value in {";", "(", ")"}:
                break
        index = cursor + 1

    return tuple(sorted(literals, key=lambda literal: literal.start))


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


def _local_go_package_exists(workspace: Path, suffix: str) -> bool:
    workspace_root = workspace.resolve()
    candidate = (workspace_root / suffix).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        return False
    return candidate.is_dir() and any(path.is_file() for path in candidate.glob("*.go"))


def _replacement_for_import(
    *,
    workspace: Path,
    module: str,
    import_path: str,
    declared_modules: frozenset[str],
) -> str | None:
    if not module or import_path == module or import_path.startswith(f"{module}/"):
        return None

    parts = tuple(part for part in import_path.split("/") if part)
    if len(parts) < 2:
        return None
    module_leaf = module.rsplit("/", 1)[-1]
    for index in range(1, len(parts)):
        wrong_prefix = "/".join(parts[:index])
        if wrong_prefix.rsplit("/", 1)[-1] != module_leaf:
            continue
        if wrong_prefix in declared_modules:
            continue
        suffix = "/".join(parts[index:])
        if _local_go_package_exists(workspace, suffix):
            return f"{module}/{suffix}"
    return None


def _read_utf8(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def plan_go_module_import_repairs(
    workspace: Path,
    *,
    artifact_quality_errors: Sequence[str],
) -> list[GoFileRepairPlan]:
    """Plan diagnostic-backed local import repairs without mutating the workspace.

    A repair is considered only when all of the following evidence agrees:
    the Go compiler named the exact missing import, the stale module prefix has
    the same leaf name as the active module, the stale prefix is not declared as
    a dependency in ``go.mod``, and the suffix resolves to a real local Go
    package. This conservative policy prefers a blocked repair over corrupting a
    legitimate external import.
    """

    workspace_root = workspace.resolve()
    go_mod_path = workspace_root / "go.mod"
    go_mod_text = _read_utf8(go_mod_path)
    if go_mod_text is None:
        return []
    module = _parse_go_module_path(go_mod_text)
    if not module:
        return []

    suspects = extract_go_import_paths_from_errors(artifact_quality_errors)
    if not suspects:
        return []
    declared_modules = _declared_module_paths(go_mod_text) - {module}
    replacements_by_import: dict[str, str] = {}
    for suspect in sorted(suspects):
        replacement = _replacement_for_import(
            workspace=workspace_root,
            module=module,
            import_path=suspect,
            declared_modules=declared_modules,
        )
        if replacement is not None:
            replacements_by_import[suspect] = replacement
    if not replacements_by_import:
        return []

    plans: list[GoFileRepairPlan] = []
    for go_file in _iter_go_files(workspace_root):
        source = _read_utf8(go_file)
        if source is None:
            continue
        literals = tuple(
            literal
            for literal in _iter_import_literals(source)
            if literal.path in replacements_by_import
        )
        if not literals:
            continue
        updated = source
        for literal in reversed(literals):
            updated = (
                updated[: literal.start]
                + replacements_by_import[literal.path]
                + updated[literal.end :]
            )
        replacements = tuple(
            dict.fromkeys(
                GoImportReplacement(
                    before=literal.path,
                    after=replacements_by_import[literal.path],
                )
                for literal in literals
            )
        )
        plans.append(
            GoFileRepairPlan(
                file=go_file.relative_to(workspace_root).as_posix(),
                content=updated,
                replacements=replacements,
            )
        )
    return plans
