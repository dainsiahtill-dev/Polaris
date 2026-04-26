from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path("src/backend")
ORCH_ROOT = BACKEND_ROOT / "app" / "orchestration"
SCAN_ROOTS = [ORCH_ROOT, BACKEND_ROOT / "scripts" / "pm"]


def _iter_python_files(root: Path):
    return sorted(root.rglob("*.py"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.AST:
    return ast.parse(_read_text(path))


def _module_name_from_path(path: Path) -> str:
    rel = path.relative_to(BACKEND_ROOT).with_suffix("")
    return ".".join(rel.parts)


def test_no_duplicate_top_level_defs_or_classes_in_orchestration_layers() -> None:
    duplicates: dict[str, dict[str, list[int]]] = {}
    for root in SCAN_ROOTS:
        for path in _iter_python_files(root):
            counts = defaultdict(list)
            tree = _parse(path)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    counts[node.name].append(node.lineno)
            dupes = {name: lines for name, lines in counts.items() if len(lines) > 1}
            if dupes:
                duplicates[path.as_posix()] = dupes
    assert duplicates == {}


def test_no_old_emit_event_keyword_contracts() -> None:
    problems: list[str] = []
    for root in SCAN_ROOTS:
        for path in _iter_python_files(root):
            tree = _parse(path)
            imports_emit = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "io_utils":
                    if any(alias.name == "emit_event" for alias in node.names):
                        imports_emit = True
                        break
            if not imports_emit:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "emit_event":
                    continue
                keys = [kw.arg for kw in node.keywords if kw.arg is not None]
                stale = [key for key in keys if key in {"event", "role", "data"}]
                if stale:
                    problems.append(f"{path.as_posix()}:{node.lineno}:{stale}")
    assert problems == []


def test_no_orchestration_keyword_signature_drift() -> None:
    defs: dict[tuple[str, str], dict[str, object]] = {}
    for path in _iter_python_files(ORCH_ROOT):
        tree = _parse(path)
        module = _module_name_from_path(path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                accepted = {arg.arg for arg in node.args.args}
                accepted.update(arg.arg for arg in node.args.kwonlyargs)
                accepted.update(arg.arg for arg in getattr(node.args, "posonlyargs", []))
                defs[(module, node.name)] = {
                    "accepted": accepted,
                    "has_kwargs": node.args.kwarg is not None,
                    "path": path.as_posix(),
                    "line": node.lineno,
                }

    problems: list[str] = []
    for root in SCAN_ROOTS:
        for path in _iter_python_files(root):
            tree = _parse(path)
            imported = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.orchestration."):
                    for alias in node.names:
                        imported[alias.asname or alias.name] = (node.module, alias.name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id not in imported:
                    continue
                module, original = imported[node.func.id]
                signature = defs.get((module, original))
                if not signature or bool(signature["has_kwargs"]):
                    continue
                passed = {kw.arg for kw in node.keywords if kw.arg is not None}
                unknown = sorted(passed - set(signature["accepted"]))
                if unknown:
                    problems.append(f"{path.as_posix()}:{node.lineno}:{node.func.id}->{module}.{original}:{unknown}")
    assert problems == []


def test_no_dynamic_module_loading_or_private_orchestration_imports() -> None:
    problems: list[str] = []
    for path in _iter_python_files(ORCH_ROOT):
        text = _read_text(path)
        if "spec_from_file_location" in text or "module_from_spec" in text or "exec_module" in text:
            problems.append(f"{path.as_posix()}:dynamic_import")
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pm.orchestration_engine":
                private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
                if private_names:
                    problems.append(f"{path.as_posix()}:{node.lineno}:private_import:{private_names}")
    assert problems == []
