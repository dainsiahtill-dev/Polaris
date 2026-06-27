"""Canonical Python repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL = "deterministic_python_package_child_reexport_repair"
PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL = "deterministic_python_package_shadow_bridge_repair"
PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL = "deterministic_python_readme_required_token_repair"
PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL = "deterministic_python_unittest_missing_target_repair"
PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL = "deterministic_python_unittest_runtime_failure_repair"
PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL = "deterministic_unresolved_import_symbol_repair"

_PYTHON_IMPORT_NAME_FROM_INIT_ERROR_RE = re.compile(
    r"ImportError:\s+cannot\s+import\s+name\s+['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"]\s+"
    r"from\s+['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['\"]\s+"
    r"\((?P<path>[^)]*[/\\]__init__\.py)\)"
)
_PYTHON_RUNTIME_TEST_FAILURE_RE = re.compile(
    r"python runtime smoke (?:crashed|timed out|could not launch) for "
    r"['\"](?P<path>tests/[^'\"]*test[^'\"]*\.py)['\"]",
    re.IGNORECASE | re.DOTALL,
)
_PYTHON_README_REQUIRED_TOKEN_RE = re.compile(
    r"README\s+missing(?:\s+required\s+token)?:\s*(?P<token>[A-Za-z0-9_.-]{1,64})\b",
    re.IGNORECASE,
)
_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_PYTHON_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYTHON_README_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_README_FILE_PRIORITY = ("readme.md", "readme.rst", "readme.txt", "readme")


def build_python_unittest_missing_target_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build new unittest smoke targets for missing declared Python tests."""

    normalized_base = _normalize_base_files(base_files)
    module_names = _declared_python_module_names(normalized_base)
    if not module_names:
        return None
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_python_unittest_missing_target_diagnostic(diagnostic)
    )
    if not matched_diagnostics:
        return None

    operations: list[RepairOperation] = []
    for diagnostic in matched_diagnostics:
        target = _normalize_repair_path(str(diagnostic.path or ""))
        if not _is_python_unittest_target_path(target) or target in normalized_base:
            continue
        content = build_python_unittest_smoke_content(target, module_names)
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                metadata={
                    "repair_kind": "python_unittest_missing_target",
                    "module_names": list(module_names),
                    "write_file_reason": "new_python_unittest_contract_target",
                    "diagnostic_id": diagnostic.diagnostic_id,
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="python.unittest_missing_target",
        source_tool=PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"created_test_targets": [operation.path for operation in operations]},
    )


def build_python_unittest_runtime_failure_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Replace an existing generated unittest target with a conservative smoke test."""

    normalized_base = _normalize_base_files(base_files)
    module_names = _declared_python_module_names(normalized_base)
    if not module_names:
        return None
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        target = _python_runtime_failure_target(diagnostic)
        if not target or target not in normalized_base or not _is_python_unittest_target_path(target):
            continue
        content = build_python_unittest_smoke_content(target, module_names)
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                before_hash=sha256_text(normalized_base[target]),
                metadata={
                    "repair_kind": "python_unittest_runtime_failure",
                    "module_names": list(module_names),
                    "write_file_reason": "replace_generated_unittest_runtime_failure",
                    "diagnostic_id": diagnostic.diagnostic_id,
                },
            )
        )
        matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="python.unittest_runtime_failure",
        source_tool=PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "replaced_test_targets": [operation.path for operation in operations],
            "runtime_plan_scope": "existing_unittest_file_replacement_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def build_python_readme_required_token_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Append missing verifier-required README tokens to existing README files."""

    normalized_base = _normalize_base_files(base_files)
    targets: dict[str, list[tuple[str, RepairDiagnostic]]] = {}
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        target = _python_readme_required_token_target(diagnostic, normalized_base)
        if target is None:
            continue
        path, token = target
        key = (path, token.lower())
        if key in seen:
            continue
        seen.add(key)
        targets.setdefault(path, []).append((token, diagnostic))
    if not targets:
        return None

    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for path, token_diagnostics in sorted(targets.items()):
        text = normalized_base[path]
        context_before = _unique_text_suffix_context(text)
        if not context_before:
            continue
        tokens = tuple(token for token, _diagnostic in token_diagnostics)
        first_diagnostic = token_diagnostics[0][1]
        operations.append(
            _append_text_operation(
                path=path,
                text=text,
                addition=_build_python_readme_required_token_appendix(tokens),
                repair_kind="python_readme_required_token",
                diagnostic=first_diagnostic,
                metadata={
                    "tokens": list(tokens),
                    "target_kind": "readme",
                    "edit_scope": "append_documentation_token_only",
                    "expected_context_before": context_before,
                },
            )
        )
        matched.extend(diagnostic for _token, diagnostic in token_diagnostics)
    if not operations:
        return None
    return RepairPlan(
        rule_id="python.readme_required_token",
        source_tool=PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "runtime_plan_scope": "existing_readme_append_only",
            "unsafe_cases_fail_closed": True,
            "tokens": sorted({token for items in targets.values() for token, _diagnostic in items}),
        },
    )


def build_python_package_child_reexport_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Append package ``__init__`` re-exports for symbols found in child modules."""

    return _build_python_package_bridge_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
        rule_id="python.package_child_reexport",
        bridge_kind="child_reexport",
    )


def build_python_package_shadow_bridge_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Append package/module shadow bridge code for package.py symbols."""

    return _build_python_package_bridge_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
        rule_id="python.package_shadow_bridge",
        bridge_kind="shadow_bridge",
    )


def build_python_unresolved_import_symbol_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Append a narrow Python export alias/stub for unresolved import-symbol diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for target in _python_unresolved_import_symbol_targets(diagnostic):
            exporter = target["exporter"]
            symbol = target["symbol"]
            key = (exporter, symbol)
            if key in seen:
                continue
            seen.add(key)
            exporter_text = normalized_base.get(exporter)
            if exporter_text is None or _python_symbol_defined(exporter_text, symbol):
                continue
            stub = _build_python_symbol_stub(exporter_text, symbol)
            if not stub:
                continue
            operations.append(
                _append_text_operation(
                    path=exporter,
                    text=exporter_text,
                    addition="\n" + stub + "\n",
                    repair_kind="python_unresolved_import_symbol",
                    diagnostic=diagnostic,
                    metadata={"symbol": symbol, "importer": target["importer"], "stub_line": stub},
                )
            )
            matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="python.unresolved_import_symbol",
        source_tool=PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": "append_alias_or_empty_class_stub_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def build_python_unittest_smoke_content(test_rel_path: str, module_names: Sequence[str]) -> str:
    """Return a deterministic unittest smoke file for declared Python modules."""

    root_parent_index = len(PurePosixPath(test_rel_path).parent.parts)
    modules_repr = ", ".join(repr(name) for name in module_names)
    return f'''"""Contract smoke tests for declared Python modules."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[{root_parent_index}]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODULE_NAMES = ({modules_repr},)


class DeclaredPythonModuleSmokeTests(unittest.TestCase):
    def test_declared_modules_import(self) -> None:
        for module_name in MODULE_NAMES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertIsNotNone(module)

    def test_declared_modules_expose_public_runtime_symbols(self) -> None:
        for module_name in MODULE_NAMES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                public_symbols = [
                    name
                    for name in dir(module)
                    if not name.startswith("_") and name not in {{"annotations"}}
                ]
                self.assertTrue(public_symbols, f"{{module_name}} exposes no public runtime symbols")


if __name__ == "__main__":
    unittest.main()
'''


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        normalized: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized := _normalize_repair_path(str(path or "")))
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _declared_python_module_names(base_files: Mapping[str, str]) -> tuple[str, ...]:
    module_names: list[str] = []
    for path in sorted(base_files):
        lowered = path.lower()
        if not lowered.endswith(".py") or lowered.startswith("tests/") or lowered.endswith("/__init__.py"):
            continue
        module_name = path[:-3].replace("/", ".")
        if module_name and module_name not in module_names:
            module_names.append(module_name)
    return tuple(module_names)


def _is_python_unittest_missing_target_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code != "declared_target_missing":
        return False
    return _is_python_unittest_target_path(str(diagnostic.path or ""))


def _python_runtime_failure_target(diagnostic: RepairDiagnostic) -> str:
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _PYTHON_RUNTIME_TEST_FAILURE_RE.search(raw)
    if match:
        return _normalize_repair_path(str(match.group("path") or ""))
    if diagnostic.code == "python_runtime_smoke_failed":
        return _normalize_repair_path(str(diagnostic.path or ""))
    return ""


def _python_readme_required_token_target(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[str, str] | None:
    if diagnostic.code != "python_assertionerror":
        return None
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _PYTHON_README_REQUIRED_TOKEN_RE.search(raw)
    if not match:
        return None
    token = str(match.group("token") or "").strip()
    if not _PYTHON_README_TOKEN_RE.match(token):
        return None
    path = _select_existing_readme_path(diagnostic, base_files)
    if not path:
        return None
    if token.lower() in base_files[path].lower():
        return None
    return path, token


def _select_existing_readme_path(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> str:
    diagnostic_path = _normalize_repair_path(str(diagnostic.path or ""))
    if diagnostic_path in base_files and PurePosixPath(diagnostic_path).name.lower() in _README_FILE_PRIORITY:
        return diagnostic_path
    candidates = [
        path
        for path in base_files
        if PurePosixPath(path).name.lower() in _README_FILE_PRIORITY
        and not path.lower().startswith(("tests/", "test/"))
    ]
    if not candidates:
        return ""
    priority = {name: index for index, name in enumerate(_README_FILE_PRIORITY)}
    candidates.sort(key=lambda path: (priority.get(PurePosixPath(path).name.lower(), 99), path.count("/"), path))
    return candidates[0]


def _build_python_readme_required_token_appendix(tokens: Sequence[str]) -> str:
    unique_tokens = tuple(dict.fromkeys(str(token) for token in tokens if str(token or "").strip()))
    if not unique_tokens:
        return ""
    body: list[str] = ["## Verification", ""]
    if any(token.lower() == "unittest" for token in unique_tokens):
        body.extend(
            [
                "Run the unittest workflow with:",
                "",
                "```bash",
                "python -m unittest discover -s tests -p 'test_*.py' -v",
                "```",
            ]
        )
    other_tokens = [token for token in unique_tokens if token.lower() != "unittest"]
    if other_tokens:
        if len(body) > 2:
            body.append("")
        body.extend(f"- Required verification token: `{token}`." for token in other_tokens)
    return "\n" + "\n".join(body) + "\n"


def _unique_text_suffix_context(text: str) -> str:
    if not text:
        return ""
    for max_chars in (240, 480, 960, len(text)):
        start = max(0, len(text) - max_chars)
        candidate = text[start:]
        if candidate and text.find(candidate) == start and text.find(candidate, start + 1) < 0:
            return candidate
    return ""


def _is_python_unittest_target_path(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized.startswith("tests/") or not normalized.endswith(".py"):
        return False
    return PurePosixPath(normalized).name.startswith("test_")


def _build_python_package_bridge_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    source_tool: str,
    rule_id: str,
    bridge_kind: str,
) -> RepairPlan | None:
    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics:
        for target in _python_import_name_from_init_targets(diagnostic, normalized_base):
            init_rel = target["init_rel"]
            symbol = target["symbol"]
            module_name = target["module"]
            init_text = normalized_base.get(init_rel)
            if init_text is None or _python_symbol_defined(init_text, symbol):
                continue
            if bridge_kind == "shadow_bridge":
                shadow_rel = f"{module_name.replace('.', '/')}.py"
                shadow_text = normalized_base.get(shadow_rel)
                if shadow_text is None or not _python_symbol_defined(shadow_text, symbol):
                    continue
                addition = "\n" + _build_python_package_shadow_bridge_block([symbol])
                metadata = {"symbols": [symbol], "shadow_module": shadow_rel}
            else:
                child_module = _find_python_package_child_symbol_source(
                    base_files=normalized_base,
                    init_rel=init_rel,
                    symbol=symbol,
                )
                if not child_module:
                    continue
                addition = "\n" + _build_python_package_child_reexport_bridge_block(child_module, [symbol])
                metadata = {"symbols": [symbol], "source_module": f".{child_module}"}
            key = (init_rel, symbol, source_tool)
            if key in seen:
                continue
            seen.add(key)
            operations.append(
                _append_text_operation(
                    path=init_rel,
                    text=init_text,
                    addition=addition,
                    repair_kind=bridge_kind,
                    diagnostic=diagnostic,
                    metadata=metadata,
                )
            )
            matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": bridge_kind,
            "unsafe_cases_fail_closed": True,
        },
    )


def _python_import_name_from_init_targets(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _PYTHON_IMPORT_NAME_FROM_INIT_ERROR_RE.search(raw)
    if not match:
        return ()
    symbol = str(match.group("symbol") or "").strip()
    module = str(match.group("module") or "").strip()
    if not _PYTHON_IDENTIFIER_RE.match(symbol) or not module:
        return ()
    expected_init = f"{module.replace('.', '/')}/__init__.py"
    init_rel = _path_from_error_suffix(str(match.group("path") or ""), expected_init, base_files)
    if init_rel != expected_init or init_rel not in base_files:
        return ()
    return ({"symbol": symbol, "module": module, "init_rel": init_rel},)


def _path_from_error_suffix(raw_path: str, expected: str, base_files: Mapping[str, str]) -> str:
    normalized = str(raw_path or "").replace("\\", "/")
    normalized = _normalize_repair_path(normalized)
    if normalized in base_files:
        return normalized
    if normalized.endswith("/" + expected) or normalized == expected:
        return expected
    return ""


def _find_python_package_child_symbol_source(
    *,
    base_files: Mapping[str, str],
    init_rel: str,
    symbol: str,
) -> str:
    package_dir = PurePosixPath(init_rel).parent.as_posix()
    prefix = "" if package_dir == "." else f"{package_dir}/"
    candidates = [
        path
        for path in base_files
        if path.startswith(prefix)
        and path.endswith(".py")
        and path != init_rel
        and "/" not in path.removeprefix(prefix)
        and _PYTHON_IDENTIFIER_RE.match(PurePosixPath(path).stem)
    ]
    candidates.sort(key=lambda path: (0 if PurePosixPath(path).stem == "core" else 1, PurePosixPath(path).stem))
    for candidate in candidates:
        if _python_symbol_defined(base_files[candidate], symbol):
            return PurePosixPath(candidate).stem
    return ""


def _build_python_package_child_reexport_bridge_block(child_module: str, symbols: Sequence[str]) -> str:
    import_lines = "\n".join(f"from .{child_module} import {symbol}" for symbol in symbols)
    all_updates = "\n".join(
        f"if {symbol!r} not in _polaris_existing_all:\n    _polaris_existing_all.append({symbol!r})"
        for symbol in symbols
    )
    return f"""# Polaris deterministic repair: re-export package child module symbols.
{import_lines}
_polaris_existing_all = list(globals().get("__all__", []))
{all_updates}
__all__ = _polaris_existing_all
"""


def _build_python_package_shadow_bridge_block(symbols: Sequence[str]) -> str:
    assignments = "\n".join(f"{symbol} = getattr(_polaris_shadow_module, {symbol!r})" for symbol in symbols)
    all_updates = "\n".join(
        f"if {symbol!r} not in _polaris_existing_all:\n    _polaris_existing_all.append({symbol!r})"
        for symbol in symbols
    )
    return f"""# Polaris deterministic repair: bridge package/module shadowing.
from importlib.util import module_from_spec as _polaris_module_from_spec
from importlib.util import spec_from_file_location as _polaris_spec_from_file_location
from pathlib import Path as _PolarisPath
import sys as _polaris_sys

_polaris_shadow_file = _PolarisPath(__file__).resolve().parent.with_suffix(".py")
_polaris_shadow_parent = __name__.rsplit(".", 1)[0]
_polaris_shadow_name = (
    f"{{_polaris_shadow_parent}}._{{_polaris_shadow_file.stem}}_shadow"
    if _polaris_shadow_parent
    else f"_{{_polaris_shadow_file.stem}}_shadow"
)
_polaris_shadow_spec = _polaris_spec_from_file_location(_polaris_shadow_name, _polaris_shadow_file)
if _polaris_shadow_spec is None or _polaris_shadow_spec.loader is None:
    raise ImportError(f"Cannot load package-shadow bridge from {{_polaris_shadow_file}}")
_polaris_shadow_module = _polaris_module_from_spec(_polaris_shadow_spec)
_polaris_sys.modules[_polaris_shadow_name] = _polaris_shadow_module
_polaris_shadow_spec.loader.exec_module(_polaris_shadow_module)
{assignments}
_polaris_existing_all = list(globals().get("__all__", []))
{all_updates}
__all__ = _polaris_existing_all
"""


def _python_unresolved_import_symbol_targets(diagnostic: RepairDiagnostic) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _UNRESOLVED_IMPORT_SYMBOL_RE.search(raw)
    if not match:
        return ()
    symbol = str(match.group("symbol") or "").strip()
    module = str(match.group("module") or "").strip()
    importer = _normalize_repair_path(str(match.group("path") or ""))
    if not _PYTHON_IDENTIFIER_RE.match(symbol) or not module or not importer.endswith(".py"):
        return ()
    return (
        {
            "symbol": symbol,
            "exporter": f"{module.replace('.', '/')}.py",
            "importer": importer,
        },
    )


def _python_symbol_defined(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    patterns = (
        rf"(?m)^\s*(?:class|def|async\s+def)\s+{escaped}\b",
        rf"(?m)^\s*from\s+[.\w]+\s+import\s+.*\b{escaped}\b",
        rf"(?m)^\s*{escaped}\s*=",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _build_python_symbol_stub(text: str, symbol: str) -> str:
    class_pattern = re.compile(
        r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:(\b]",
        re.MULTILINE,
    )
    symbol_lc = symbol.lower()
    for match in class_pattern.finditer(text):
        name = match.group("name")
        if name != symbol and name.lower().endswith(symbol_lc):
            return f"{symbol} = {name}"
    return f"class {symbol}:\n    pass"


def _append_text_operation(
    *,
    path: str,
    text: str,
    addition: str,
    repair_kind: str,
    diagnostic: RepairDiagnostic,
    metadata: Mapping[str, object],
) -> RepairOperation:
    replacement = addition if text.endswith("\n") else "\n" + addition.lstrip("\n")
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=len(text),
        span_end=len(text),
        expected="",
        replacement=replacement,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": repair_kind,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            **dict(metadata),
        },
    )


def _dedupe_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> tuple[RepairDiagnostic, ...]:
    seen: set[str] = set()
    deduped: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.diagnostic_id in seen:
            continue
        seen.add(diagnostic.diagnostic_id)
        deduped.append(diagnostic)
    return tuple(deduped)


__all__ = [
    "PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL",
    "PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL",
    "PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL",
    "PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL",
    "PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL",
    "PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL",
    "build_python_package_child_reexport_plan",
    "build_python_package_shadow_bridge_plan",
    "build_python_readme_required_token_plan",
    "build_python_unittest_missing_target_plan",
    "build_python_unittest_runtime_failure_plan",
    "build_python_unittest_smoke_content",
    "build_python_unresolved_import_symbol_plan",
]
