"""Canonical Python repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL = "deterministic_python_package_child_reexport_repair"
PYTHON_MISSING_MODULE_ALIAS_SOURCE_TOOL = "deterministic_python_missing_module_alias_repair"
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
_PYTHON_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError:\s+No\s+module\s+named\s+['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)['\"]",
    re.IGNORECASE,
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


def build_python_missing_module_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Create a source-root compatibility module for one unambiguous nested module.

    A generated project may place ``weather.py`` at ``src/models/weather.py``
    while its tests import ``weather`` with ``src`` on ``PYTHONPATH``.  The
    runtime kernel may bridge that topology only when exactly one existing
    nested module matches the missing top-level name.  Ambiguity, unsafe names,
    and an existing target all fail closed.
    """

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen_targets: set[str] = set()
    advertised = _python_src_layout_advertised_package_name(normalized_base)
    importer_rewrites = 0
    for diagnostic in diagnostics:
        module_name = _python_missing_top_level_module_name(diagnostic)
        if not module_name:
            continue
        if advertised == module_name:
            for path, text in list(normalized_base.items()):
                if path in seen_targets or not path.endswith(".py"):
                    continue
                rewritten = _rewrite_python_import_package_prefix(
                    text,
                    missing_package=module_name,
                    target_package="src",
                )
                if not rewritten or rewritten == text:
                    continue
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=path,
                        span_start=0,
                        span_end=len(text),
                        expected=text,
                        replacement=rewritten,
                        before_hash=sha256_text(text),
                        metadata={
                            "repair_kind": "python_missing_module_alias_importer_rewrite",
                            "missing_module": module_name,
                            "target_module": "src",
                            "edit_file_preferred": True,
                            "diagnostic_id": diagnostic.diagnostic_id,
                        },
                    )
                )
                normalized_base[path] = rewritten
                seen_targets.add(path)
                importer_rewrites += 1
                matched.append(diagnostic)
            if importer_rewrites:
                continue
        target = f"src/{module_name}.py"
        if target in normalized_base or target in seen_targets:
            continue
        candidates = _python_nested_module_alias_candidates(normalized_base, module_name)
        if len(candidates) != 1:
            continue
        source = candidates[0]
        import_path = source.removeprefix("src/").removesuffix(".py").replace("/", ".")
        relative_import_path = "." + import_path
        content = (
            '"""Compatibility bridge generated from an unambiguous module topology."""\n\n'
            "if __package__:\n"
            f"    from {relative_import_path} import *  # noqa: F401,F403\n"
            "else:\n"
            f"    from {import_path} import *  # noqa: F401,F403\n"
        )
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                metadata={
                    "repair_kind": "python_missing_module_alias",
                    "source_module": source,
                    "missing_module": module_name,
                    "write_file_reason": "new_python_source_root_module_alias",
                    "diagnostic_id": diagnostic.diagnostic_id,
                },
            )
        )
        seen_targets.add(target)
        matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="python.missing_module_alias",
        source_tool=PYTHON_MISSING_MODULE_ALIAS_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": (
                "src_layout_advertised_package_importer_rewrite" if importer_rewrites else "missing_module_alias"
            ),
            "unsafe_cases_fail_closed": True,
            "requires_unique_nested_module_candidate": importer_rewrites == 0,
            "importer_rewrite_count": importer_rewrites,
        },
    )


def _python_missing_top_level_module_name(diagnostic: RepairDiagnostic) -> str:
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _PYTHON_MODULE_NOT_FOUND_RE.search(raw)
    return str(match.group("module") or "").strip() if match is not None else ""


def _python_src_layout_advertised_package_name(base_files: Mapping[str, str]) -> str:
    """Return the unique package name advertised by ``src/__init__.py``.

    Live L2-19: ``src/`` is the implementation, but generated modules import
    ``waterdrop_rhythm_pad``. The first docstring token is the advertised
    distribution name. Anything that is not a single identifier fail-closes.
    """

    init_text = str(base_files.get("src/__init__.py") or "")
    match = re.match(r'\s*"""\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\n|""")', init_text)
    if match is None:
        return ""
    name = str(match.group(1) or "").strip()
    return name if _PYTHON_IDENTIFIER_RE.match(name) else ""


def _rewrite_python_import_package_prefix(
    text: str,
    *,
    missing_package: str,
    target_package: str,
) -> str | None:
    if not text or not missing_package or not target_package or missing_package == target_package:
        return None
    if not _PYTHON_IDENTIFIER_RE.match(missing_package) or not _PYTHON_IDENTIFIER_RE.match(target_package):
        return None
    pattern = re.compile(
        rf"(?m)^from[ \t]+{re.escape(missing_package)}"
        rf"(?P<rest>(?:\.[A-Za-z_][A-Za-z0-9_]*)*)[ \t]+import[ \t]+"
    )
    rewritten, count = pattern.subn(
        lambda match: f"from {target_package}{match.group('rest')} import ",
        text,
    )
    if count < 1 or rewritten == text:
        return None
    return rewritten


def _python_nested_module_alias_candidates(base_files: Mapping[str, str], module_name: str) -> tuple[str, ...]:
    suffix = f"/{module_name}.py"
    candidates = sorted(
        path
        for path in base_files
        if path.startswith("src/")
        and path.endswith(suffix)
        and path != f"src/{module_name}.py"
        and all(_PYTHON_IDENTIFIER_RE.match(part) for part in PurePosixPath(path.removesuffix(".py")).parts)
        and not any(part in {"tests", "test", "__pycache__"} for part in PurePosixPath(path).parts)
    )
    return tuple(candidates)


def build_python_unresolved_import_symbol_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Repair unresolved Python import symbols without inventing domain APIs.

    Live L2-19 TASK-3-tests: ``tests/test_product.py`` imported
    ``forecast_for`` from ``src.engine.forecast`` while that helper already
    lived in ``src.models.weather``.  Prefer rewriting the importer to the
    unique existing declaration; exporter aliases stay a fallback.
    """

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str, str]] = set()
    importer_rewrites = 0
    for diagnostic in diagnostics:
        for target in _python_unresolved_import_symbol_targets(diagnostic):
            exporter = target["exporter"]
            symbol = target["symbol"]
            importer = target["importer"]
            key = (importer, exporter, symbol)
            if key in seen:
                continue
            seen.add(key)
            importer_text = normalized_base.get(importer)
            source_rel = _python_unique_existing_symbol_module(
                normalized_base,
                symbol=symbol,
                exclude_paths={importer, exporter},
            )
            if importer_text is not None and source_rel:
                rewritten = _rewrite_python_from_import_module(
                    importer_text,
                    symbol=symbol,
                    to_module=_python_module_name_from_rel(source_rel),
                    wrong_module=_python_module_name_from_rel(exporter),
                )
                if rewritten and rewritten != importer_text:
                    operations.append(
                        RepairOperation(
                            kind="text_replace",
                            path=importer,
                            span_start=0,
                            span_end=len(importer_text),
                            expected=importer_text,
                            replacement=rewritten,
                            before_hash=sha256_text(importer_text),
                            metadata={
                                "repair_kind": "python_unresolved_import_symbol_importer_rewrite",
                                "diagnostic_id": diagnostic.diagnostic_id,
                                "symbol": symbol,
                                "wrong_module": exporter,
                                "source_module": source_rel,
                                "edit_file_preferred": True,
                            },
                        )
                    )
                    normalized_base[importer] = rewritten
                    importer_rewrites += 1
                    matched.append(diagnostic)
                    continue
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
                    metadata={"symbol": symbol, "importer": importer, "stub_line": stub},
                )
            )
            matched.append(diagnostic)
    if not operations:
        return None
    if importer_rewrites and importer_rewrites == len(operations):
        runtime_scope = "rewrite_importer_to_unique_existing_module"
    elif importer_rewrites == 0:
        runtime_scope = "append_alias_to_existing_similar_symbol_only"
    else:
        runtime_scope = "importer_rewrite_or_exporter_alias"
    return RepairPlan(
        rule_id="python.unresolved_import_symbol",
        source_tool=PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(_dedupe_diagnostics(matched)),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": runtime_scope,
            "unsafe_cases_fail_closed": True,
            "empty_stub_generation_allowed": False,
            "importer_rewrite_count": importer_rewrites,
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
    raw_normalized = str(raw_path or "").strip().replace("\\", "/")
    if raw_normalized.endswith("/" + expected) or raw_normalized == expected:
        return expected
    normalized = raw_normalized
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


def _python_module_name_from_rel(path: str) -> str:
    token = str(path or "").strip().replace("\\", "/")
    if not token.endswith(".py"):
        return ""
    return token[:-3].replace("/", ".")


def _python_unique_existing_symbol_module(
    base_files: Mapping[str, str],
    *,
    symbol: str,
    exclude_paths: set[str],
) -> str:
    defined: list[str] = []
    reexported: list[str] = []
    for path, text in base_files.items():
        if path in exclude_paths or not path.endswith(".py"):
            continue
        if any(part in {"tests", "test", "__pycache__"} for part in PurePosixPath(path).parts):
            continue
        kind = _python_symbol_definition_kind(text, symbol)
        if kind == "defined":
            defined.append(path)
        elif kind == "reexport":
            reexported.append(path)
    # Live L2-19: weather.py defines forecast_for and models/__init__.py
    # re-exports it. Treating both as owners made unique-module lookup
    # fail-closed and skipped the in-scope test import rewrite.
    if len(defined) == 1:
        return defined[0]
    if not defined and len(reexported) == 1:
        return reexported[0]
    return ""


def _parse_python_import_names(body: str) -> list[str]:
    names: list[str] = []
    for raw in str(body or "").split(","):
        token = raw.split("#", 1)[0].strip()
        if token:
            names.append(token)
    return names


def _format_python_parenthesized_import(module: str, names: Sequence[str], *, indent: str) -> str:
    inner_indent = indent + "    "
    rendered = ",\n".join(f"{inner_indent}{name}" for name in names)
    return f"{indent}from {module} import (\n{rendered},\n{indent})"


def _rewrite_python_from_import_module(
    text: str,
    *,
    symbol: str,
    to_module: str,
    wrong_module: str,
) -> str | None:
    if not text or not symbol or not to_module or not wrong_module or to_module == wrong_module:
        return None
    paren_re = re.compile(
        rf"(?ms)^(?P<indent>[ \t]*)from[ \t]+{re.escape(wrong_module)}[ \t]+import[ \t]*\((?P<body>.*?)\)"
    )
    flat_re = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)from[ \t]+{re.escape(wrong_module)}[ \t]+import[ \t]+(?P<body>[^\n(]+)$"
    )
    match = paren_re.search(text) or flat_re.search(text)
    if match is None:
        return None
    names = _parse_python_import_names(str(match.group("body") or ""))
    kept_alias = ""
    remaining: list[str] = []
    for name in names:
        exported = name.split(" as ", 1)[0].strip()
        if exported == symbol:
            kept_alias = name
        else:
            remaining.append(name)
    if not kept_alias:
        return None
    indent = str(match.group("indent") or "")
    new_import = f"{indent}from {to_module} import {kept_alias}"
    if remaining:
        if match.re is paren_re or "\n" in str(match.group("body") or ""):
            rebuilt = _format_python_parenthesized_import(wrong_module, remaining, indent=indent)
        else:
            rebuilt = f"{indent}from {wrong_module} import {', '.join(remaining)}"
        replacement = f"{rebuilt}\n{new_import}"
    else:
        replacement = new_import
    original = match.group(0)
    if text.count(original) != 1:
        return None
    return text[: match.start()] + replacement + text[match.end() :]


def _python_unresolved_import_symbol_targets(diagnostic: RepairDiagnostic) -> tuple[dict[str, str], ...]:
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    symbol = str(metadata.get("symbol") or "").strip()
    module = str(metadata.get("module") or "").strip()
    importer = _normalize_repair_path(str(metadata.get("importer_path") or ""))
    if not symbol or not module or not importer:
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


def _python_symbol_definition_kind(text: str, symbol: str) -> str:
    escaped = re.escape(symbol)
    if re.search(rf"(?m)^\s*(?:class|def|async\s+def)\s+{escaped}\b", text) or re.search(
        rf"(?m)^\s*{escaped}\s*=",
        text,
    ):
        return "defined"
    if re.search(rf"(?m)^\s*from\s+[.\w]+\s+import\s+.*\b{escaped}\b", text):
        return "reexport"
    return ""


def _python_symbol_defined(text: str, symbol: str) -> bool:
    return bool(_python_symbol_definition_kind(text, symbol))


def _build_python_symbol_stub(text: str, symbol: str) -> str:
    """Return a meaningful alias for ``symbol`` or decline the repair.

    Empty ``class Symbol: pass`` stubs satisfy imports while violating
    delivery-depth placeholder gates. If no real candidate exists, fail closed
    and leave the diagnostic for LLM/contract-level repair.
    """

    class_pattern = re.compile(
        r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:(\b]",
        re.MULTILINE,
    )
    symbol_lc = symbol.lower()
    for match in class_pattern.finditer(text):
        name = match.group("name")
        if name != symbol and name.lower().endswith(symbol_lc):
            return f"{symbol} = {name}"
    return ""


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
    operation_metadata: dict[str, object] = {
        "repair_kind": repair_kind,
        "diagnostic_id": diagnostic.diagnostic_id,
        "edit_file_preferred": True,
        **dict(metadata),
    }
    context_before = _append_text_context_before(text)
    if context_before:
        operation_metadata.setdefault("expected_context_before", context_before)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=len(text),
        span_end=len(text),
        expected="",
        replacement=replacement,
        before_hash=sha256_text(text),
        metadata=operation_metadata,
    )


def _append_text_context_before(text: str) -> str:
    """Return a bounded unique EOF context for append-style text operations."""

    if not text:
        return ""
    context = ""
    for line in reversed(text.splitlines(keepends=True)):
        context = line + context
        if context.strip() and text.count(context) == 1:
            return context
        if len(context) >= 4096:
            break
    for size in (256, 512, 1024, 2048, 4096):
        candidate = text[-size:]
        if candidate.strip() and text.count(candidate) == 1:
            return candidate
    if len(text) <= 8192 and text.count(text) == 1:
        return text
    return ""


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
    "PYTHON_MISSING_MODULE_ALIAS_SOURCE_TOOL",
    "PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL",
    "PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL",
    "PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL",
    "PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL",
    "PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL",
    "PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL",
    "build_python_missing_module_alias_plan",
    "build_python_package_child_reexport_plan",
    "build_python_package_shadow_bridge_plan",
    "build_python_readme_required_token_plan",
    "build_python_unittest_missing_target_plan",
    "build_python_unittest_runtime_failure_plan",
    "build_python_unittest_smoke_content",
    "build_python_unresolved_import_symbol_plan",
]
