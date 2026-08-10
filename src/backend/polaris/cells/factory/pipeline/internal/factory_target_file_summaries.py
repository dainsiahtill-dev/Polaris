"""Pure target-file export-summary extractors for cross-file coherence.

Extracted from ``OrchestrationStageExecutor``. These functions scan a
workspace for source files that a task depends on (but does not own) and
extract their compact export signatures so the Director's imports stay
coherent with reality. The JS/TS extractor uses regex; the Python extractor
uses ``ast`` with a regex fallback.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

_EXISTING_SUMMARY_SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".js",
    ".ts",
    ".mjs",
    ".cjs",
    ".jsx",
    ".tsx",
)
_EXISTING_SUMMARY_MAX_FILES = 24

_EXCLUDED_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".polaris",
        "runtime",
        "node_modules",
        "__pycache__",
        ".git",
        "dist",
        "build",
    }
)


def read_existing_target_file_summaries(
    workspace: Path,
    task: dict[str, Any],
    *,
    max_chars_per_file: int = 1500,
) -> list[dict[str, str]]:
    """Summarize the export API of files this task depends on but does NOT own.

    A later task (e.g. the one writing ``main.py``) imports symbols from files
    an earlier task already created (e.g. ``src/models/mood.py``). Those
    dependency files are NOT in this task's own ``target_files``, so the
    Director would otherwise have to guess their API — and guessing wrong is
    exactly how ``main.py`` ended up calling ``Mood(mood=..., intensity=...)``
    on an ``enum`` (live L1-03: cross-file coherence break, entrypoint smoke
    TypeError). We therefore scan the workspace for already-existing source
    files OUTSIDE this task's targets and inject their compact export
    signatures so the Director's imports stay coherent with reality.

    The task's own existing targets are also summarized (harmless re-edit
    context); both sets are returned, de-duplicated, capped, and path-sorted
    for deterministic context.
    """

    own_targets: set[str] = set()
    raw_targets = task.get("target_files")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if isinstance(item, str) and item.strip():
                own_targets.add(item.strip().replace("\\", "/").lstrip("./"))

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(rel: str) -> None:
        norm = rel.replace("\\", "/")
        if norm and norm not in seen:
            seen.add(norm)
            candidates.append(norm)

    for rel in sorted(own_targets):
        if (workspace / rel).is_file():
            _add(rel)

    workspace_root = workspace.resolve()
    if workspace_root.is_dir():
        for suffix in _EXISTING_SUMMARY_SOURCE_SUFFIXES:
            for full_path in sorted(workspace_root.rglob(f"*{suffix}")):
                if not full_path.is_file():
                    continue
                parts = set(full_path.relative_to(workspace_root).parts)
                if parts & _EXCLUDED_DIR_PARTS:
                    continue
                try:
                    rel = str(full_path.relative_to(workspace_root))
                except ValueError:
                    continue
                norm = rel.replace("\\", "/")
                if norm in own_targets:
                    continue
                _add(rel)
                if len(candidates) >= _EXISTING_SUMMARY_MAX_FILES:
                    break
            if len(candidates) >= _EXISTING_SUMMARY_MAX_FILES:
                break

    summaries: list[dict[str, str]] = []
    for rel_path in candidates[:_EXISTING_SUMMARY_MAX_FILES]:
        full_path = workspace / rel_path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.strip():
            continue
        suffix = full_path.suffix.lower()
        if suffix in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
            summary = extract_js_export_summary(content)
        elif suffix == ".py":
            summary = extract_py_export_summary(content)
        else:
            summary = content[:max_chars_per_file]
        summaries.append({"path": rel_path, "exports": summary})
    return summaries


def extract_js_export_summary(content: str) -> str:
    """Extract JS/TS export signatures so dependent files reference real symbols.

    Captures classes, functions, const/let/var, TS enums (with members),
    interfaces, types, ``export { ... }`` lists, and CommonJS exports. Mirrors
    the Python extractor's enum-member coverage: a dependent TS file's Director
    must see enum members (e.g. ``SkyCondition.CALM``), not just the enum name,
    or it invents non-existent members — the cross-file coherence wall L4-L8
    React/Express projects hit.
    """

    lines: list[str] = []

    # TS enums (incl. ``const enum``) with their members — the JS analog of the
    # Python enum-member gap. ``[^{}]`` spans newlines, so multi-line bodies match.
    for match in re.finditer(r"(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)\s*\{([^{}]*)\}", content):
        name = match.group(1)
        members: list[str] = []
        seen_member: set[str] = set()
        for member in re.findall(r"([A-Za-z_$][\w$]*)\s*(?==|,|\Z)", match.group(2)):
            if member not in seen_member:
                seen_member.add(member)
                members.append(member)
        lines.append(f"enum {name} {{ {', '.join(members[:40])} }}" if members else f"enum {name}")

    for raw_line in content.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue
        if (
            re.match(r"module\.exports\s*=", stripped)
            or re.match(r"exports\.[A-Za-z_$]", stripped)
            or re.match(r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+[A-Za-z_$]", stripped)
            or re.match(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*[A-Za-z_$]", stripped)
            or re.match(r"(?:export\s+)?(?:const|let|var)\s+(?!enum\b)[A-Za-z_$]", stripped)
            or re.match(r"(?:export\s+)?interface\s+[A-Za-z_$]", stripped)
            or re.match(r"(?:export\s+)?type\s+[A-Za-z_$][\w$]*\s*=", stripped)
            or re.match(r"export\s+\{", stripped)
            or re.match(r"export\s+default\s+", stripped)
        ):
            lines.append(stripped[:200])

    deduped: list[str] = []
    seen_line: set[str] = set()
    for line in lines:
        if line not in seen_line:
            seen_line.add(line)
            deduped.append(line)
    if not deduped:
        for raw_line in content.split("\n"):
            if raw_line.strip():
                deduped.append(raw_line.strip()[:200])
            if len(deduped) >= 30:
                break
    return "\n".join(deduped[:60])


def extract_py_export_summary(content: str) -> str:
    """Extract Python export signatures so a dependent file's Director sees the
    *valid* cross-file symbols, not just declaration names.

    Includes enum members and class attributes alongside class/function
    signatures. Without enum members, the Director receives only
    ``class SkyCondition(Enum):`` and guesses non-existent members like
    ``SkyCondition.CLEAR`` — the factory-bench L1-03 entrypoint crash
    (``AttributeError: type object 'SkyCondition' has no attribute 'CLEAR'``).
    Falls back to a line scan when the source does not parse.
    """

    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return extract_py_export_summary_fallback(content)

    enum_bases = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
    lines: list[str] = []

    def _base_names(class_node: ast.ClassDef) -> list[str]:
        names: list[str] = []
        for base in class_node.bases:
            if isinstance(base, ast.Name):
                names.append(base.id)
            elif isinstance(base, ast.Attribute):
                names.append(base.attr)
        return names

    def _func_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        params: list[str] = [a.arg for a in fn.args.posonlyargs] + [a.arg for a in fn.args.args]
        if fn.args.vararg is not None:
            params.append("*" + fn.args.vararg.arg)
        params.extend(a.arg for a in fn.args.kwonlyargs)
        if fn.args.kwarg is not None:
            params.append("**" + fn.args.kwarg.arg)
        keyword = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
        return f"{keyword} {fn.name}({', '.join(params)})"

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = _base_names(node)
            header = f"class {node.name}({', '.join(bases)}):" if bases else f"class {node.name}:"
            is_enum = any(base in enum_bases for base in bases)
            members: list[str] = []
            methods: list[str] = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    members.extend(tgt.id for tgt in item.targets if isinstance(tgt, ast.Name))
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    members.append(item.target.id)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
            if is_enum and members:
                lines.append(f"{header} members: {', '.join(members[:40])}")
            else:
                detail: list[str] = []
                if members:
                    detail.append("attrs: " + ", ".join(members[:24]))
                if methods:
                    detail.append("methods: " + ", ".join(methods[:24]))
                lines.append(f"{header} {' | '.join(detail)}" if detail else header)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_func_signature(node))
        elif isinstance(node, ast.Assign):
            lines.extend(f"{tgt.id} = ..." for tgt in node.targets if isinstance(tgt, ast.Name) and tgt.id.isupper())

    if not lines:
        return extract_py_export_summary_fallback(content)
    return "\n".join(lines[:60])


def extract_py_export_summary_fallback(content: str) -> str:
    """Line-scan fallback when the dependency source does not parse as Python."""

    lines: list[str] = []
    for raw_line in content.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"(?:class|def|async def)\s+\w+", stripped):
            lines.append(stripped[:200])
    if not lines:
        for raw_line in content.split("\n"):
            if raw_line.strip():
                lines.append(raw_line.strip()[:200])
            if len(lines) >= 30:
                break
    return "\n".join(lines[:50])
