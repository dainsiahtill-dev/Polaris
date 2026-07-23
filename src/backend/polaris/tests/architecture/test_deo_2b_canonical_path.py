"""Architecture fences for the selected DEO-2B canonical execution path."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
)
from polaris.kernelone.llm.contracts.tool import CellToolExecutorPort

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
DIRECTOR_RUNTIME_ROOT = POLARIS_ROOT / "cells" / "director" / "runtime"
ROLES_KERNEL_ROOT = POLARIS_ROOT / "cells" / "roles" / "kernel"
ROLES_RUNTIME_ROOT = POLARIS_ROOT / "cells" / "roles" / "runtime"
ADAPTER_DIRECTOR_ROOT = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "director"
ADAPTER_PUBLIC_ROOT = POLARIS_ROOT / "cells" / "roles" / "adapters" / "public"
COMMAND_CAPABILITY = POLARIS_ROOT / "kernelone" / "llm" / "toolkit" / "executor" / "command_capability.py"
COMMAND_HANDLER = POLARIS_ROOT / "kernelone" / "llm" / "toolkit" / "executor" / "handlers" / "command.py"


def _production_sources(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in root.rglob("*.py")
        if "tests" not in path.parts and not path.name.startswith("test_") and "__pycache__" not in path.parts
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return tuple(modules)


def _import_offenders(root: Path, prefixes: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for path in _production_sources(root):
        for module in _imported_modules(path):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes):
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{module}")
    return sorted(offenders)


def test_cell_dependencies_keep_deo_2b_on_one_directional_ports() -> None:
    assert (
        _import_offenders(
            DIRECTOR_RUNTIME_ROOT,
            ("polaris.cells.roles.kernel", "polaris.cells.roles.adapters"),
        )
        == []
    )
    assert _import_offenders(ROLES_KERNEL_ROOT, ("polaris.cells.roles.adapters",)) == []
    assert _import_offenders(ROLES_RUNTIME_ROOT, ("polaris.cells.roles.adapters",)) == []

    private_execution_prefixes = (
        "polaris.cells.roles.adapters.internal.director.execution_tools",
        "polaris.kernelone.llm.toolkit.executor.handlers.command",
    )
    assert _import_offenders(DIRECTOR_RUNTIME_ROOT, private_execution_prefixes) == []
    assert _import_offenders(ROLES_KERNEL_ROOT, private_execution_prefixes) == []


def test_policy_snapshot_uses_only_stable_command_capability_and_write_policy_symbol() -> None:
    path = ADAPTER_DIRECTOR_ROOT / "directed_effect_policy_snapshot.py"
    source = path.read_text(encoding="utf-8")
    tree = _tree(path)

    assert "polaris.kernelone.llm.toolkit.executor.command_capability" in _imported_modules(path)
    assert "polaris.kernelone.llm.toolkit.executor.handlers.command" not in _imported_modules(path)
    assert "AgentAccelToolExecutor(" not in source

    director_tool_imports = [
        tuple(alias.name for alias in node.names)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "execution_tools"
    ]
    assert director_tool_imports == [("DirectorToolExecutor",)]
    private_attributes = sorted(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_DirectorToolExecutor"
    )
    assert private_attributes == ["_validate_director_policy_for_write"]


def test_command_capability_is_pure_and_handler_is_only_compatibility_wrapper() -> None:
    forbidden_import_prefixes = (
        "multiprocessing",
        "polaris.cells",
        "polaris.kernelone.fs",
        "polaris.kernelone.process",
        "polaris.kernelone.single_agent",
        "subprocess",
    )
    imported = _imported_modules(COMMAND_CAPABILITY)
    assert [
        module
        for module in imported
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_import_prefixes)
    ] == []

    capability_source = COMMAND_CAPABILITY.read_text(encoding="utf-8")
    forbidden_symbols = (
        "AgentAccelToolExecutor",
        "CommandExecutionService",
        "KernelFileSystem",
        "install_default_skills",
        "Path(",
        "open(",
        "os.",
    )
    assert [token for token in forbidden_symbols if token in capability_source] == []

    handler_source = COMMAND_HANDLER.read_text(encoding="utf-8")
    assert "from polaris.kernelone.llm.toolkit.executor.command_capability import (" in handler_source
    wrapper = handler_source[
        handler_source.index("def _validate_command_capability(") : handler_source.index(
            "def _attach_command_effect_receipt("
        )
    ]
    assert wrapper.count("validate_command_capability(") == 2
    assert "CommandCapabilityValidationInputV1(" in wrapper


def test_public_adapter_hides_physical_executor_and_composition_uses_one_bundle() -> None:
    public_offenders = [
        str(path.relative_to(BACKEND_ROOT))
        for path in _production_sources(ADAPTER_PUBLIC_ROOT)
        if "DirectorToolExecutor" in path.read_text(encoding="utf-8")
    ]
    assert public_offenders == []

    adapter_source = (ADAPTER_DIRECTOR_ROOT / "adapter.py").read_text(encoding="utf-8")
    assert adapter_source.count("DirectedEffectRuntimeDependenciesV1(") == 1
    assert "directed_effect_runtime=directed_effect_runtime" in adapter_source


def test_mutation_path_has_no_raw_executor_edge_and_is_dominated_by_three_gates() -> None:
    mutation_source = (ADAPTER_DIRECTOR_ROOT / "directed_effect_mutation_port.py").read_text(encoding="utf-8")
    method_start = mutation_source.index("    async def execute_mutation(")
    method_end = mutation_source.index("\n\ndef create_director_directed_effect_mutation_port(")
    method = mutation_source[method_start:method_end]

    prepare_at = method.index("_prepare_mutation(")
    revalidate_at = method.index("await self._revalidate_policy(")
    consume_at = method.index("self._consume_once(prepared)")
    physical_at = method.index("self._execute_physical(prepared)")
    assert prepare_at < revalidate_at < consume_at < physical_at

    prepare_helper = mutation_source[
        mutation_source.index("def _prepare_mutation(") : mutation_source.index("\n\ndef _policy_request(")
    ]
    revalidate_helper = mutation_source[
        mutation_source.index("    async def _revalidate_policy(") : mutation_source.index("\n    def _consume_once(")
    ]
    consume_helper = mutation_source[
        mutation_source.index("    def _consume_once(") : mutation_source.index("\n    def _execute_physical(")
    ]
    physical_helper = mutation_source[
        mutation_source.index("    def _execute_physical(") : mutation_source.index(
            "\n    async def _observe_post_state("
        )
    ]
    assert "validate_directed_effect_execution(" in prepare_helper
    assert "await self._policy.revalidate(" in revalidate_helper
    assert "self._consume.consume(prepared.context)" in consume_helper
    assert "executor = _create_director_tool_executor(self._workspace)" in physical_helper
    assert "executor.execute_tool(" in physical_helper
    assert "self._executor" not in mutation_source

    mutation_runtime_source = inspect.getsource(ToolBatchRuntime._execute_directed_effect)
    read_runtime_source = inspect.getsource(ToolBatchRuntime._execute_single)
    assert "self.executor(" not in mutation_runtime_source
    assert "runtime.mutation_port.execute_mutation(" in mutation_runtime_source
    assert "return await self.executor(tool_name, arguments)" in read_runtime_source


def test_generic_tool_ports_carry_no_deo_capability_field() -> None:
    context_fields = {field.name for field in fields(ToolExecutionContext)}
    protocol_parameters = set(inspect.signature(CellToolExecutorPort.execute).parameters)
    forbidden_fragments = ("claim_grant", "deo", "directed_effect")

    assert not {
        name
        for name in context_fields | protocol_parameters
        if any(fragment in name.lower() for fragment in forbidden_fragments)
    }


def test_new_deo_execution_modules_import_no_transport_or_terminal_authority() -> None:
    modules = (
        ROLES_KERNEL_ROOT / "internal" / "directed_effect_dispatch.py",
        ROLES_KERNEL_ROOT / "internal" / "directed_effect_lifecycle.py",
        ADAPTER_DIRECTOR_ROOT / "directed_effect_mutation_port.py",
    )
    forbidden_fragments = (
        ".events",
        ".nats",
        "parent_close",
        "receipt_store",
        "recovery",
        "settlement",
        "terminal_admission",
        "transport",
    )
    offenders = [
        f"{path.relative_to(BACKEND_ROOT)}:{module}"
        for path in modules
        for module in _imported_modules(path)
        if any(fragment in module for fragment in forbidden_fragments)
    ]
    assert offenders == []


def test_taskruntime_issuer_and_selected_deo_consumers_are_singletons() -> None:
    taskruntime_internal = (
        POLARIS_ROOT / "cells" / "runtime" / "task_runtime" / "internal" / "directed_effect_operation.py"
    ).read_text(encoding="utf-8")
    lifecycle = (ROLES_KERNEL_ROOT / "internal" / "directed_effect_lifecycle.py").read_text(encoding="utf-8")
    mutation = (ADAPTER_DIRECTOR_ROOT / "directed_effect_mutation_port.py").read_text(encoding="utf-8")
    batch_runtime = (ROLES_KERNEL_ROOT / "internal" / "tool_batch_runtime.py").read_text(encoding="utf-8")

    assert taskruntime_internal.count("return DirectedEffectClaimGrantV1(") == 1
    assert lifecycle.count("self._ports.claim_operation(") == 1
    assert lifecycle.count("context = DirectedEffectExecutionContextV1(") == 1
    assert mutation.count("validate_directed_effect_execution(") == 1
    assert mutation.count("executor.execute_tool(") == 1
    assert mutation.count("_create_director_tool_executor(self._workspace)") == 1
    assert batch_runtime.count("runtime.mutation_port.execute_mutation(") == 1

    lifecycle_imports = _imported_modules(ROLES_KERNEL_ROOT / "internal" / "directed_effect_lifecycle.py")
    assert not any(module.startswith("polaris.cells.runtime.task_runtime.internal") for module in lifecycle_imports)
