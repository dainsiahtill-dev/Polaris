"""Fitness (architecture) tests for the capability-dispatch decomposition.

These guard the structural invariants established when the 1,839-line
``execute_role_capability_invocation`` god-function was decomposed into a typed
``CapabilityHandler`` registry (design:
``docs/superpowers/specs/2026-06-20-capability-dispatcher-decomposition-design.md``).
They fail fast if a future change regrows the dispatcher into a switch statement,
reintroduces ``Any`` on its public interface, drifts the registry from the
historical branch set, or pulls an owner-cell import back into the dispatcher.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from polaris.cells.roles.runtime.internal.capability import CapabilityHandler
from polaris.cells.roles.runtime.internal.capability._oracle import (
    CAPABILITY_FAMILY_COUNT,
    CAPABILITY_IDENTITY_TUPLES,
)
from polaris.cells.roles.runtime.internal.capability.registry_default import (
    default_capability_registry,
)
from polaris.cells.roles.runtime.public import capability_commands

# Owner cells whose public contracts the migrated handlers own — none of these
# may be imported inside the dispatcher function any more (each handler owns its
# function-local owner-cell import).
_OWNER_CELLS = (
    "director.execution",
    "qa.audit_verdict",
    "chief_engineer.blueprint",
    "code_intelligence.engine",
    "runtime.task_market",
    "runtime.projection",
    "finops.budget_guard",
    "policy.workspace_guard",
    "architect.design",
    "factory.verification_guard",
    "llm.control_plane",
)

# Per-family identity-flag names that lived ONLY in the deleted if/elif ladder.
# (The QA role-denial prelude flags ``is_qa_pytest_verification`` /
# ``is_qa_visual_audit_verdict`` are intentionally retained in the prelude and
# are NOT listed here.)
_MIGRATED_SWITCH_FLAGS = (
    "is_pm_critical_path",
    "is_pm_runtime_projection",
    "is_blueprint_generation",
    "is_ce_ast_dependency",
    "is_qa_audit_verdict",
    "is_qa_traceback_parse",
    "is_architect_budget_reservation",
    "is_not_task_market_dispatch",
)


def _dispatcher_funcdef() -> ast.FunctionDef:
    """Return the parsed AST of ``execute_role_capability_invocation``."""
    source = inspect.getsource(capability_commands.execute_role_capability_invocation)
    module = ast.parse(source)
    funcdef = module.body[0]
    assert isinstance(funcdef, ast.FunctionDef)
    return funcdef


def test_registry_identities_match_oracle() -> None:
    # Arrange / Act
    registry_identities = set(default_capability_registry().identities())

    # Assert: the live registry covers EXACTLY the historical branch identity set.
    assert registry_identities == set(CAPABILITY_IDENTITY_TUPLES)


def test_every_identity_resolves_to_a_capability_handler() -> None:
    # Arrange
    registry = default_capability_registry()

    # Act
    handlers = [registry.lookup(*identity) for identity in CAPABILITY_IDENTITY_TUPLES]

    # Assert: every identity resolves, and each handler satisfies the protocol.
    assert all(handler is not None for handler in handlers)
    assert all(isinstance(handler, CapabilityHandler) for handler in handlers)
    # 14 identity tuples collapse onto 13 distinct handler instances (the
    # blueprint family answers two capability ids with one shared handler).
    distinct_instances = {id(handler) for handler in handlers}
    assert len(distinct_instances) == CAPABILITY_FAMILY_COUNT


def test_dispatcher_exposes_no_any_typed_service_kwargs() -> None:
    # Arrange
    funcdef = _dispatcher_funcdef()
    all_args = [*funcdef.args.args, *funcdef.args.kwonlyargs]

    # Act / Assert: every ``*_service`` parameter is typed (zero ``Any``).
    service_args = [arg for arg in all_args if arg.arg.endswith("_service")]
    assert service_args, "expected the dispatcher to still take typed service ports"
    for arg in service_args:
        assert arg.annotation is not None, f"{arg.arg} is unannotated"
        annotation = ast.unparse(arg.annotation)
        assert "Any" not in annotation, f"{arg.arg} regrew an Any annotation: {annotation}"


def test_dispatcher_no_longer_hosts_the_per_family_switch() -> None:
    # Arrange
    source = inspect.getsource(capability_commands.execute_role_capability_invocation)

    # Assert: none of the deleted if/elif identity flags reappear.
    for flag in _MIGRATED_SWITCH_FLAGS:
        assert flag not in source, f"dispatcher regrew the per-family switch flag {flag!r}"


def test_dispatcher_owns_no_per_capability_cross_cell_import() -> None:
    # Arrange
    funcdef = _dispatcher_funcdef()

    # Act
    imported_modules = [node.module or "" for node in ast.walk(funcdef) if isinstance(node, ast.ImportFrom)]

    # Assert: no owner-cell import remains in the dispatcher (handlers own them).
    for module in imported_modules:
        for owner in _OWNER_CELLS:
            assert owner not in module, f"dispatcher must not import owner cell {owner!r}; found {module!r}"


def test_dispatcher_is_no_longer_a_god_function() -> None:
    # Arrange
    source_lines = inspect.getsource(capability_commands.execute_role_capability_invocation).splitlines()

    # Assert: the function shrank by an order of magnitude from its 1,839-line
    # origin (verbatim prelude + delegation + single fallback only).
    assert len(source_lines) < 400, f"dispatcher is {len(source_lines)} lines; expected < 400"


# ── architect.design acyclicity (CYCLE-15) ───────────────────────────────────
# The boundary-validation handler reaches ``architect.design`` ONLY through the
# typed ``CapabilityDeps`` invoker port supplied by the composition root. No
# module under ``roles.runtime/internal/capability/**`` may import
# ``architect.design`` at RUNTIME scope (module-level OR function-local — a
# deferred import is still a real cell edge). A ``TYPE_CHECKING``-guarded import
# (erased at runtime) is allowed for typing only.
_CAPABILITY_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "internal" / "capability"
_ARCHITECT_DESIGN_PREFIX = "polaris.cells.architect.design"


def _is_type_checking_guard(node: ast.If) -> bool:
    """Return True when ``node`` is an ``if TYPE_CHECKING:`` block."""
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _runtime_scope_import_modules(tree: ast.Module) -> set[str]:
    """Collect import module names reachable at runtime (TYPE_CHECKING erased)."""
    modules: set[str] = set()

    def _visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_guard(node):
                # Skip the TYPE_CHECKING body (erased at runtime); still descend
                # into the ``else`` branch, which DOES execute at runtime.
                _visit(node.orelse)
                continue
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            # Descend into nested scopes (functions/classes/try/if/with/loops).
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    _visit([child])

    _visit(tree.body)
    return modules


def test_capability_package_never_imports_architect_design_at_runtime() -> None:
    # Arrange
    offenders: dict[str, set[str]] = {}

    # Act
    for module_path in sorted(_CAPABILITY_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        hits = {
            module
            for module in _runtime_scope_import_modules(tree)
            if module == _ARCHITECT_DESIGN_PREFIX or module.startswith(f"{_ARCHITECT_DESIGN_PREFIX}.")
        }
        if hits:
            offenders[str(module_path.relative_to(_CAPABILITY_PACKAGE_ROOT))] = hits

    # Assert: the only architect.design reference may be TYPE_CHECKING-guarded.
    assert offenders == {}, (
        "roles.runtime/internal/capability/** must not import architect.design at "
        f"runtime scope (deferred imports are real cell edges); offenders: {offenders}"
    )
