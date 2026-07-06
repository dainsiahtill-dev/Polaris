"""Architecture fence for ContextOS final-request hash projection ownership."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"

OWNED_CONTEXT_HASH_REGEXES = {
    (
        "polaris/kernelone/events/final_request_evidence.py",
        "_CONTEXT_SNAPSHOT_HASH_RE",
    ): "(?<![0-9A-Fa-f])([0-9A-Fa-f]{24})(?![0-9A-Fa-f])",
    (
        "polaris/kernelone/llm/engine/internal/context_hash.py",
        "CONTEXT_HASH_PATTERN",
    ): "^[0-9a-f]{24}$",
}


@dataclass(frozen=True)
class RegexDefinition:
    """Compiled regex discovered by the AST scanner."""

    path: str
    name: str
    line: int
    pattern: str


def _production_python_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(part in {"tests", "generated", "__pycache__"} for part in path.parts)
    ]


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str:
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if node.targets else None
    return target.id if isinstance(target, ast.Name) else ""


def _is_re_compile_call(node: ast.Call) -> bool:
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "compile"
        and isinstance(function.value, ast.Name)
        and function.value.id == "re"
    )


def _string_constant(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _looks_like_context_hash_regex(pattern: str) -> bool:
    normalized = pattern.casefold()
    return "{24}" in normalized and ("0-9" in normalized or "a-f" in normalized)


def _compiled_context_hash_regexes(root: Path) -> list[RegexDefinition]:
    definitions: list[RegexDefinition] = []
    for path in _production_python_files(root):
        tree = _parse_python(path)
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call) or not _is_re_compile_call(value):
                continue
            pattern = _string_constant(value.args[0] if value.args else None)
            if not _looks_like_context_hash_regex(pattern):
                continue
            definitions.append(
                RegexDefinition(
                    path=relative,
                    name=_assigned_name(node),
                    line=node.lineno,
                    pattern=pattern,
                )
            )
    return definitions


def test_context_snapshot_hash_regexes_are_owned_by_projection_helpers() -> None:
    """Final-request hash extraction must not grow new local regex owners."""

    actual = {
        (definition.path, definition.name): definition.pattern
        for definition in _compiled_context_hash_regexes(POLARIS_ROOT)
    }

    assert actual == OWNED_CONTEXT_HASH_REGEXES
