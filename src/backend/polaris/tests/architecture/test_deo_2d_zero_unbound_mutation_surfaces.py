"""DEO-2D repository fence for zero unbound Director mutation surfaces."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from textwrap import dedent

import pytest
from polaris.cells.runtime.task_runtime.tests.test_directed_effect_operation_guarded_fence import (
    _analyze_source,
    _module_context_for_path,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
ADAPTERS_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters"
ADAPTER_DIRECTOR_ROOT = ADAPTERS_ROOT / "internal" / "director"
DIRECTOR_CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells" / "director"
MUTATION_PORT = "polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py"
POLICY_SNAPSHOT = "polaris/cells/roles/adapters/internal/director/directed_effect_policy_snapshot.py"
PHYSICAL_TOOL_EXECUTOR = "polaris/cells/roles/adapters/internal/director/execution_tools.py"
PRIVATE_EXECUTOR_FACTORY = "_create_director_tool_executor"
PLATFORM_PROGRESS_WRITER = "polaris/cells/director/tasking/internal/workspace_probe.py"
EXECUTOR_MODULE = "polaris.cells.roles.adapters.internal.director.execution_tools"
EXECUTOR_CLASS = f"{EXECUTOR_MODULE}.DirectorToolExecutor"
EXECUTOR_FACTORY = f"{EXECUTOR_MODULE}.{PRIVATE_EXECUTOR_FACTORY}"
EXECUTOR_AUTHORITY = f"{EXECUTOR_MODULE}._DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY"
EXECUTOR_INSTANCE_REGISTRY = f"{EXECUTOR_MODULE}._DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES"
EXECUTOR_EXECUTE = f"{EXECUTOR_CLASS}.execute_tool"
POLICY_SNAPSHOT_MODULE = "polaris.cells.roles.adapters.internal.director.directed_effect_policy_snapshot"
POLICY_SNAPSHOT_EXECUTOR_ALIAS = f"{POLICY_SNAPSHOT_MODULE}._DirectorToolExecutor"
_EXECUTOR_TARGETS = {
    EXECUTOR_MODULE,
    EXECUTOR_CLASS,
    EXECUTOR_FACTORY,
    EXECUTOR_AUTHORITY,
    EXECUTOR_INSTANCE_REGISTRY,
    EXECUTOR_EXECUTE,
    POLICY_SNAPSHOT_EXECUTOR_ALIAS,
}
_EXECUTOR_PROTECTED_OBJECTS = _EXECUTOR_TARGETS - {EXECUTOR_EXECUTE}
_EXECUTOR_MARKERS = (
    "execution_tools",
    "DirectorToolExecutor",
    PRIVATE_EXECUTOR_FACTORY,
    "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_",
)


def _physical_executor_analysis(
    source: str,
    *,
    current_module: str,
    current_is_package: bool = False,
):
    return _analyze_source(
        source,
        current_module=current_module,
        current_is_package=current_is_package,
        targets=set(_EXECUTOR_TARGETS),
        protected_objects=set(_EXECUTOR_PROTECTED_OBJECTS),
    )


def _physical_executor_escape_findings(
    source: str,
    *,
    current_module: str,
    current_is_package: bool = False,
) -> tuple[tuple[str, str, str], ...]:
    analysis = _physical_executor_analysis(
        source,
        current_module=current_module,
        current_is_package=current_is_package,
    )
    findings = {
        ("import", "<module>", imported)
        for imported in analysis.imports
        if imported in _EXECUTOR_TARGETS
        or imported.startswith(f"{EXECUTOR_MODULE}.")
        or imported == POLICY_SNAPSHOT_EXECUTOR_ALIAS
    }
    findings.update(
        (reference.kind, reference.owner, reference.target)
        for reference in analysis.references
        if reference.target in _EXECUTOR_TARGETS or reference.target.startswith(f"{EXECUTOR_MODULE}.")
    )
    findings.update(
        ("dynamic_module_literal", "<module>", node.value)
        for node in ast.walk(analysis.tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == EXECUTOR_MODULE
    )
    return tuple(sorted(findings))


@pytest.mark.parametrize(
    "source",
    (
        """
        import polaris.cells.roles.adapters.internal.director.execution_tools as physical
        executor = physical._create_director_tool_executor("/workspace")
        executor.execute_tool("read_file", {"path": "README.md"})
        """,
        """
        import polaris.cells.roles.adapters.internal.director.execution_tools as physical
        constructor = physical.DirectorToolExecutor
        authority = physical._DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY
        executor = constructor("/workspace", _physical_execution_authority=authority)
        run = executor.execute_tool
        run("read_file", {"path": "README.md"})
        """,
        """
        import polaris.cells.roles.adapters.internal.director.execution_tools as physical
        name = "_create_director_tool_executor"
        creator = getattr(physical, name)
        creator("/workspace")
        """,
        """
        from polaris.cells.roles.adapters.internal.director.execution_tools import *
        _create_director_tool_executor("/workspace")
        """,
        """
        import polaris.cells.roles.adapters.internal.director.execution_tools as physical
        creator = vars(physical)["_create_director_tool_executor"]
        creator("/workspace")
        """,
        """
        import polaris.cells.roles.adapters.internal.director.execution_tools as physical
        creator = physical.__dict__["_create_director_tool_executor"]
        creator("/workspace")
        """,
        """
        from polaris.cells.roles.adapters.internal.director.directed_effect_policy_snapshot import (
            _DirectorToolExecutor as constructor,
        )
        constructor("/workspace")
        """,
        """
        import importlib
        physical = importlib.import_module(
            "polaris.cells.roles.adapters.internal.director.execution_tools"
        )
        getattr(physical, "_create_director_tool_executor")("/workspace")
        """,
    ),
)
def test_physical_executor_fence_detects_adversarial_alias_and_taint_escapes(source: str) -> None:
    findings = _physical_executor_escape_findings(
        dedent(source),
        current_module="fixture.adversarial_physical_executor",
    )

    assert findings


def test_physical_executor_self_fence_detects_private_factory_alias_reuse() -> None:
    source = (BACKEND_ROOT / PHYSICAL_TOOL_EXECUTOR).read_text(encoding="utf-8")
    analysis = _physical_executor_analysis(
        source
        + """
_FACTORY_ALIAS = _create_director_tool_executor

def unauthorized_internal_factory_call(workspace):
    return _FACTORY_ALIAS(workspace)
""",
        current_module=EXECUTOR_MODULE,
    )

    assert {
        (reference.owner, reference.target, reference.kind)
        for reference in analysis.references
        if reference.target == EXECUTOR_FACTORY
    } == {
        ("<module>", EXECUTOR_FACTORY, "name_load"),
        ("unauthorized_internal_factory_call", EXECUTOR_FACTORY, "call"),
    }


def _sources() -> tuple[Path, ...]:
    sources = set(ADAPTERS_ROOT.rglob("*.py")) | set(DIRECTOR_CELLS_ROOT.rglob("*.py"))
    return tuple(sorted(path for path in sources if "tests" not in path.parts))


def _production_sources() -> tuple[Path, ...]:
    return tuple(sorted(path for path in POLARIS_ROOT.rglob("*.py") if "tests" not in path.parts))


def _site(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _qualified_call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        return f"{function.value.id}.{function.attr}"
    if isinstance(function, ast.Name):
        return function.id
    return ""


def test_only_mutation_port_constructs_and_calls_physical_director_executor() -> None:
    observed_findings: set[tuple[str, str, str, str]] = set()

    for path in _production_sources():
        site = _site(path)
        if site == PHYSICAL_TOOL_EXECUTOR:
            continue
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in _EXECUTOR_MARKERS):
            continue
        current_module, current_is_package = _module_context_for_path(path, polaris_root=POLARIS_ROOT)
        observed_findings.update(
            (site, kind, owner, target)
            for kind, owner, target in _physical_executor_escape_findings(
                source,
                current_module=current_module,
                current_is_package=current_is_package,
            )
        )

    assert observed_findings == {
        (MUTATION_PORT, "import", "<module>", EXECUTOR_MODULE),
        (MUTATION_PORT, "import", "<module>", EXECUTOR_FACTORY),
        (
            MUTATION_PORT,
            "call",
            "_DirectorDirectedEffectMutationPort._execute_physical",
            EXECUTOR_FACTORY,
        ),
        (POLICY_SNAPSHOT, "import", "<module>", EXECUTOR_MODULE),
        (POLICY_SNAPSHOT, "import", "<module>", EXECUTOR_CLASS),
        (
            POLICY_SNAPSHOT,
            "name_load",
            "_DirectorEffectPolicySnapshotPort._validate_write_policy",
            EXECUTOR_CLASS,
        ),
    }

    executor_source = (BACKEND_ROOT / PHYSICAL_TOOL_EXECUTOR).read_text(encoding="utf-8")
    executor_tree = ast.parse(executor_source, filename=str(BACKEND_ROOT / PHYSICAL_TOOL_EXECUTOR))
    executor_analysis = _physical_executor_analysis(
        executor_source,
        current_module=EXECUTOR_MODULE,
    )
    constructor_calls = [
        node
        for node in ast.walk(executor_tree)
        if isinstance(node, ast.Call) and _call_name(node) == "DirectorToolExecutor"
    ]
    mutation_tree = _tree(BACKEND_ROOT / MUTATION_PORT)
    factory_calls = [
        node
        for node in ast.walk(mutation_tree)
        if isinstance(node, ast.Call) and _call_name(node) == PRIVATE_EXECUTOR_FACTORY
    ]
    execute_calls = [
        node
        for node in ast.walk(mutation_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "executor"
        and node.func.attr == "execute_tool"
    ]
    factory_definitions = [
        node
        for node in ast.walk(executor_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == PRIVATE_EXECUTOR_FACTORY
    ]
    execute_definitions = [
        node
        for node in ast.walk(executor_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute_tool"
    ]
    executor_internal_execute_calls = [
        node for node in ast.walk(executor_tree) if isinstance(node, ast.Call) and _call_name(node) == "execute_tool"
    ]
    registry_add_calls = [
        node
        for node in ast.walk(executor_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES"
        and node.func.attr == "add"
    ]
    protected_identity_loads = Counter(
        node.id
        for node in ast.walk(executor_tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id
        in {
            "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY",
            "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES",
        }
    )
    executor_factory_references = [
        (reference.owner, reference.target, reference.kind)
        for reference in executor_analysis.references
        if reference.target == EXECUTOR_FACTORY
    ]

    assert len(constructor_calls) == 1
    assert len(factory_calls) == 1
    assert len(execute_calls) == 1
    assert len(factory_definitions) == 1
    assert len(execute_definitions) == 1
    assert executor_internal_execute_calls == []
    assert len(registry_add_calls) == 1
    assert protected_identity_loads == Counter(
        {
            "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY": 2,
            "_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES": 2,
        }
    )
    assert executor_factory_references == []


def test_no_executor_factory_or_direct_repair_callback_authority_remains() -> None:
    executor_factory_sites: list[str] = []
    direct_callback_sites: list[str] = []

    for path in _sources():
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            if any(
                keyword.arg == "executor_factory"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "DirectorToolExecutor"
                for keyword in node.keywords
            ):
                executor_factory_sites.append(f"{_site(path)}:{node.lineno}")
            if _call_name(node) == "run_director_repair" and any(
                keyword.arg in {"writer", "editor", "deleter"} for keyword in node.keywords
            ):
                direct_callback_sites.append(f"{_site(path)}:{node.lineno}")

    assert executor_factory_sites == []
    assert direct_callback_sites == []


def test_text_patch_fallback_has_no_private_physical_applicator() -> None:
    path = ADAPTER_DIRECTOR_ROOT / "execution.py"
    source = path.read_text(encoding="utf-8")
    method_names = {
        node.name for node in ast.walk(_tree(path)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "_execute_patch_file_format" not in method_names
    assert "_apply_protocol_operations" not in method_names
    assert "_apply_single_patch" not in method_names
    assert "StrictOperationApplier" not in source


def test_director_cells_expose_no_raw_text_physical_apply_surface() -> None:
    forbidden_definitions = {"apply_operation", "apply_all_operations", "apply_operations_strict"}
    definition_sites: list[str] = []
    raw_apply_sites: list[str] = []

    for path in _sources():
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in forbidden_definitions:
                definition_sites.append(f"{_site(path)}:{node.lineno}:{node.name}")
            if isinstance(node, ast.Call) and _call_name(node) == "apply_protocol_output":
                raw_apply_sites.append(f"{_site(path)}:{node.lineno}")

    assert definition_sites == []
    assert raw_apply_sites == []


def test_director_cells_do_not_spawn_processes_outside_directed_effect_authority() -> None:
    """Commands/verifiers must become governed ToolInvocation effects, never local subprocesses."""

    forbidden_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.system",
    }
    process_sites: list[str] = []

    for path in _sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and _qualified_call_name(node) in forbidden_calls:
                process_sites.append(f"{_site(path)}:{node.lineno}:{_qualified_call_name(node)}")

    assert process_sites == []


def test_physical_process_and_workspace_effect_apis_stay_behind_canonical_executor() -> None:
    """Legacy public agents/services may block effects, never own their executors."""

    command_service_imports: set[str] = set()
    command_service_constructors: set[str] = set()
    patch_broadcast_sites: list[str] = []
    workspace_effect_sites: dict[str, set[str]] = {}
    workspace_effect_calls = {"workspace_write_text", "workspace_write_bytes", "workspace_remove"}

    for path in _sources():
        site = _site(path)
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "CommandExecutionService" for alias in node.names
            ):
                command_service_imports.add(site)
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            if call_name == "CommandExecutionService":
                command_service_constructors.add(site)
            if call_name == "apply_patch_with_broadcast":
                patch_broadcast_sites.append(f"{site}:{node.lineno}")
            if call_name in workspace_effect_calls:
                workspace_effect_sites.setdefault(site, set()).add(call_name)

    assert command_service_imports == {PHYSICAL_TOOL_EXECUTOR}
    assert command_service_constructors == {PHYSICAL_TOOL_EXECUTOR}
    assert patch_broadcast_sites == []
    assert set(workspace_effect_sites) <= {PHYSICAL_TOOL_EXECUTOR, PLATFORM_PROGRESS_WRITER}
