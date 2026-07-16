from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field, fields
from pathlib import Path
from textwrap import dedent

import pytest
from polaris.cells.runtime.task_runtime import public as task_runtime_public
from polaris.cells.runtime.task_runtime.internal import (
    directed_effect_operation as deo_internal,
    service as runtime_service_internal,
)
from polaris.cells.runtime.task_runtime.public import (
    contracts as runtime_public_contracts,
    service as runtime_public_service,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    DirectedEffectParentReadinessProjectionV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentReadinessStateCountV1,
)

_DEO_INTERNAL_MODULE = "polaris.cells.runtime.task_runtime.internal.directed_effect_operation"
_DEO_INTERNAL_PREFIX = "polaris.cells.runtime.task_runtime.internal"
_DEO_REPOSITORY = f"{_DEO_INTERNAL_MODULE}.DirectedEffectOperationRepository"
_FACT_STREAM_PUBLIC = "polaris.cells.events.fact_stream.public"
_REPOSITORY_INVENTORY_TARGETS = {
    f"{_DEO_REPOSITORY}.seal_inventory",
    f"{_DEO_REPOSITORY}.finalize_inventory",
}
_FACT_STREAM_WRITER_TARGETS = {
    f"{_FACT_STREAM_PUBLIC}.append_fact_event",
    f"{_FACT_STREAM_PUBLIC}.service.append_fact_event",
    f"{_FACT_STREAM_PUBLIC}.AppendFactEventCommandV1",
    f"{_FACT_STREAM_PUBLIC}.contracts.AppendFactEventCommandV1",
    f"{_FACT_STREAM_PUBLIC}.append_if_guarded_snapshot",
    f"{_FACT_STREAM_PUBLIC}.service.append_if_guarded_snapshot",
    f"{_FACT_STREAM_PUBLIC}.GuardedFactEventV1",
    f"{_FACT_STREAM_PUBLIC}.contracts.GuardedFactEventV1",
}
_REPOSITORY_FORBIDDEN_CALL_TARGETS = {
    f"{_FACT_STREAM_PUBLIC}.append_fact_event",
    f"{_FACT_STREAM_PUBLIC}.service.append_fact_event",
    f"{_FACT_STREAM_PUBLIC}.enroll_fact_stream_streams",
    f"{_FACT_STREAM_PUBLIC}.service.enroll_fact_stream_streams",
    f"{_DEO_REPOSITORY}.close_parent",
    f"{_DEO_REPOSITORY}.enroll_operation_stream",
    f"{_DEO_REPOSITORY}.enroll_parent_registry_stream",
    f"{_DEO_REPOSITORY}.persist_receipt",
    f"{_DEO_REPOSITORY}.settlement_pre_barrier",
    "polaris.cells.runtime.task_runtime.public.settle_task_runtime_execution_attempt",
    "polaris.cells.runtime.task_runtime.public.service.settle_task_runtime_execution_attempt",
}


@dataclass(frozen=True, order=True)
class _QualifiedReference:
    owner: str
    target: str
    kind: str
    node_id: int = field(default=0, compare=False, repr=False)
    owner_node_id: int = field(default=0, compare=False, repr=False)


def _method_tree(name: str) -> ast.Module:
    method = getattr(deo_internal.DirectedEffectOperationRepository, name)
    return ast.parse(dedent(inspect.getsource(method)))


def _reachable_repository_methods(root: str) -> set[str]:
    analysis = _analyze_source(
        inspect.getsource(deo_internal),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_FACT_STREAM_WRITER_TARGETS | _REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC, _DEO_REPOSITORY},
    )
    return _reachable_function_owners(
        analysis,
        roots={f"DirectedEffectOperationRepository.{root}"},
    )


def _called_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def _call_owners(tree: ast.AST, target: str) -> list[str]:
    owners: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute):
                called = node.func.attr
            elif isinstance(node.func, ast.Name):
                called = node.func.id
            else:
                called = None
            if called == target:
                owners.append(self.functions[-1] if self.functions else "<module>")
            self.generic_visit(node)

    Visitor().visit(tree)
    return owners


def _function_local_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    bindings = {
        argument.arg
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
    }
    if node.args.vararg is not None:
        bindings.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        bindings.add(node.args.kwarg.arg)

    class Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            bindings.add(child.name)

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_ClassDef(self, child: ast.ClassDef) -> None:
            bindings.add(child.name)

        def visit_Lambda(self, child: ast.Lambda) -> None:
            return

        def visit_ListComp(self, child: ast.ListComp) -> None:
            return

        visit_SetComp = visit_ListComp  # noqa: N815
        visit_DictComp = visit_ListComp  # noqa: N815
        visit_GeneratorExp = visit_ListComp  # noqa: N815

        def visit_Name(self, child: ast.Name) -> None:
            if isinstance(child.ctx, (ast.Store, ast.Del)):
                bindings.add(child.id)

        def visit_Import(self, child: ast.Import) -> None:
            bindings.update(alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in child.names)

        def visit_ImportFrom(self, child: ast.ImportFrom) -> None:
            bindings.update(alias.asname or alias.name for alias in child.names if alias.name != "*")

        def visit_ExceptHandler(self, child: ast.ExceptHandler) -> None:
            if child.name:
                bindings.add(child.name)
            self.generic_visit(child)

    collector = Collector()
    for statement in node.body:
        collector.visit(statement)
    return bindings


class _QualifiedReferenceResolver(ast.NodeVisitor):
    def __init__(
        self,
        tree: ast.AST,
        *,
        targets: set[str],
        protected_objects: set[str],
        current_module: str,
        current_is_package: bool,
    ) -> None:
        self.targets = targets
        self.protected_objects = protected_objects
        self.current_module = current_module
        self.current_is_package = current_is_package
        self.references: list[_QualifiedReference] = []
        self.resolved_calls: list[_QualifiedReference] = []
        self.lexical_owners: list[tuple[str, str]] = []
        self.definition_owners: list[str] = []
        self.definition_node_ids: list[int] = []
        self.class_qnames: list[str] = []
        self.function_node_ids: list[int] = []
        self.function_nodes: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.function_owners_by_id: dict[int, str] = {}
        self.function_qnames_by_id: dict[int, str] = {}
        self.known_callable_targets = {
            f"{current_module}.{node.name}"
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        } | {
            f"{current_module}.{node.name}.{member.name}"
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for member in node.body
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.scopes: list[dict[str, str | frozenset[str] | None]] = [{}]
        self.scopes[0].update(
            {
                node.name: f"{current_module}.{node.name}"
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        )
        self.direct_call_nodes = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}

    @property
    def aliases(self) -> dict[str, str | frozenset[str] | None]:
        return self.scopes[-1]

    @property
    def owner(self) -> str:
        if self.definition_owners:
            return self.definition_owners[-1]
        return self.lexical_owners[-1][1] if self.lexical_owners else "<module>"

    @property
    def owner_node_id(self) -> int:
        if self.definition_node_ids:
            return self.definition_node_ids[-1]
        return self.function_node_ids[-1] if self.function_node_ids else 0

    def _nested_owner(self, name: str) -> str:
        if not self.lexical_owners:
            return name
        parent_kind, parent_owner = self.lexical_owners[-1]
        separator = "." if parent_kind == "class" else ".<locals>."
        return f"{parent_owner}{separator}{name}"

    def _resolve(self, node: ast.AST | None) -> str | None:
        if isinstance(node, (ast.IfExp, ast.BoolOp, ast.NamedExpr)):
            possible = self._possible_resolutions(node)
            return next(iter(possible)) if len(possible) == 1 else None
        if isinstance(node, ast.Name):
            bound = self.aliases.get(node.id, node.id)
            if isinstance(bound, frozenset):
                return next(iter(bound)) if len(bound) == 1 else None
            return bound
        if isinstance(node, ast.Attribute):
            prefix = self._resolve(node.value)
            return f"{prefix}.{node.attr}" if prefix else None
        if isinstance(node, ast.Call):
            function = self._resolve(node.func)
            if function in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                object_name = self._resolve(node.args[0])
                attribute = node.args[1]
                if object_name and isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
                    return f"{object_name}.{attribute.value}"
                return None
            if function and function.rsplit(".", maxsplit=1)[-1][:1].isupper():
                return function
            return None
        return None

    def _possible_resolutions(self, node: ast.AST | None) -> set[str]:
        if isinstance(node, ast.Name):
            bound = self.aliases.get(node.id, node.id)
            if isinstance(bound, frozenset):
                return set(bound)
            return {bound} if bound is not None else set()
        if isinstance(node, ast.Attribute):
            return {f"{prefix}.{node.attr}" for prefix in self._possible_resolutions(node.value)}
        if isinstance(node, ast.IfExp):
            return self._possible_resolutions(node.body) | self._possible_resolutions(node.orelse)
        if isinstance(node, ast.BoolOp):
            return {resolution for value in node.values for resolution in self._possible_resolutions(value)}
        if isinstance(node, ast.NamedExpr):
            return self._possible_resolutions(node.value)
        if isinstance(node, ast.Call):
            resolved = self._resolve(node)
            return {resolved} if resolved is not None else set()
        resolved = self._resolve(node)
        return {resolved} if resolved is not None else set()

    def _record(self, node: ast.AST, target: str, kind: str) -> None:
        if target in self.targets:
            self.references.append(
                _QualifiedReference(
                    self.owner,
                    target,
                    kind,
                    id(node),
                    self.owner_node_id,
                )
            )

    def _is_protected(self, target: str) -> bool:
        return any(target == protected or target.startswith(f"{protected}.") for protected in self.protected_objects)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", maxsplit=1)[0]
            self.aliases[bound] = alias.name if alias.asname else bound

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _absolute_import_from(
            node,
            current_module=self.current_module,
            current_is_package=self.current_is_package,
        )
        if not module:
            return
        for alias in node.names:
            if alias.name == "*":
                if self._is_protected(module):
                    self.references.append(
                        _QualifiedReference(
                            self.owner,
                            module,
                            "protected_star_import",
                            id(node),
                            self.owner_node_id,
                        )
                    )
                continue
            self.aliases[alias.asname or alias.name] = f"{module}.{alias.name}"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        function_owner = self._nested_owner(node.name)
        function_qname = (
            f"{self.class_qnames[-1]}.{node.name}"
            if self.lexical_owners and self.lexical_owners[-1][0] == "class"
            else f"{self.current_module}.{function_owner}"
        )
        self.function_nodes[id(node)] = node
        self.function_owners_by_id[id(node)] = function_owner
        self.function_qnames_by_id[id(node)] = function_qname
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        decorator_names = {
            resolved for decorator in node.decorator_list if (resolved := self._resolve(decorator)) is not None
        }
        self.definition_owners.append(f"{function_owner}.<definition>")
        self.definition_node_ids.append(id(node))
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        argument_annotations = {argument.arg: self._resolve(argument.annotation) for argument in arguments}
        for argument in arguments:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        vararg_annotation = self._resolve(node.args.vararg.annotation) if node.args.vararg is not None else None
        kwarg_annotation = self._resolve(node.args.kwarg.annotation) if node.args.kwarg is not None else None
        if node.returns is not None:
            self.visit(node.returns)
        self.definition_node_ids.pop()
        self.definition_owners.pop()
        self.scopes.append(dict(self.aliases))
        bound_names = _function_local_bindings(node)
        self.aliases.update(dict.fromkeys(bound_names))
        for argument in arguments:
            self.aliases[argument.arg] = argument_annotations[argument.arg]
        if node.args.vararg is not None:
            self.aliases[node.args.vararg.arg] = vararg_annotation
        if node.args.kwarg is not None:
            self.aliases[node.args.kwarg.arg] = kwarg_annotation
        is_staticmethod = any(decorator.rsplit(".", maxsplit=1)[-1] == "staticmethod" for decorator in decorator_names)
        if self.class_qnames and arguments and not is_staticmethod:
            self.aliases[arguments[0].arg] = self.class_qnames[-1]
        self.lexical_owners.append(("function", function_owner))
        self.function_node_ids.append(id(node))
        for statement in node.body:
            self.visit(statement)
        self.function_node_ids.pop()
        self.lexical_owners.pop()
        self.scopes.pop()
        self.aliases[node.name] = function_qname

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_owner = self._nested_owner(node.name)
        class_name = f"{self.current_module}.{class_owner}"
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self.scopes.append(dict(self.aliases))
        self.aliases[node.name] = class_name
        self.lexical_owners.append(("class", class_owner))
        self.class_qnames.append(class_name)
        for statement in node.body:
            self.visit(statement)
        self.class_qnames.pop()
        self.lexical_owners.pop()
        self.scopes.pop()
        self.aliases[node.name] = class_name

    def _shadow_target(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self.aliases[node.id] = None
        elif isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                self._shadow_target(element)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        lambda_owner = self._nested_owner(f"<lambda>@{node.lineno}:{node.col_offset}")
        self.scopes.append(dict(self.aliases))
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            self.aliases[argument.arg] = None
        if node.args.vararg is not None:
            self.aliases[node.args.vararg.arg] = None
        if node.args.kwarg is not None:
            self.aliases[node.args.kwarg.arg] = None
        self.lexical_owners.append(("function", lambda_owner))
        self.visit(node.body)
        self.lexical_owners.pop()
        self.scopes.pop()

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.AST, ...],
    ) -> None:
        if not generators:
            return
        self.visit(generators[0].iter)
        self.scopes.append(dict(self.aliases))
        for index, generator in enumerate(generators):
            if index:
                self.visit(generator.iter)
            self._shadow_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _bind_assignment(self, target: ast.AST, value: ast.AST) -> bool:
        if isinstance(target, ast.Name):
            possible = self._possible_resolutions(value)
            self.aliases[target.id] = (
                next(iter(possible)) if len(possible) == 1 else frozenset(possible) if possible else None
            )
            return len(possible) == 1
        if isinstance(target, ast.Starred):
            if isinstance(value, (ast.Tuple, ast.List)) and isinstance(target.value, ast.Name):
                possible = {resolution for element in value.elts for resolution in self._possible_resolutions(element)}
                self.aliases[target.value.id] = (
                    next(iter(possible)) if len(possible) == 1 else frozenset(possible) if possible else None
                )
                return len(possible) == 1
            return self._bind_assignment(target.value, value)
        if isinstance(target, (ast.Tuple, ast.List)):
            if not isinstance(value, (ast.Tuple, ast.List)):
                self._shadow_target(target)
                return False
            starred = [index for index, element in enumerate(target.elts) if isinstance(element, ast.Starred)]
            if not starred:
                if len(target.elts) != len(value.elts):
                    self._shadow_target(target)
                    return False
                pairs = tuple(zip(target.elts, value.elts, strict=True))
            elif len(starred) == 1 and len(value.elts) >= len(target.elts) - 1:
                index = starred[0]
                suffix_count = len(target.elts) - index - 1
                middle_end = len(value.elts) - suffix_count
                middle = ast.Tuple(elts=value.elts[index:middle_end], ctx=ast.Load())
                pairs = (
                    *zip(target.elts[:index], value.elts[:index], strict=True),
                    (target.elts[index], middle),
                    *zip(
                        target.elts[index + 1 :],
                        value.elts[middle_end:],
                        strict=True,
                    ),
                )
            else:
                self._shadow_target(target)
                return False
            resolved_all = True
            for target_element, value_element in pairs:
                resolved_all = self._bind_assignment(target_element, value_element) and resolved_all
            return resolved_all
        return False

    def _record_unresolved_protected_assignment(
        self,
        node: ast.AST,
        references_before: int,
    ) -> None:
        protected_references = [
            reference
            for reference in self.references[references_before:]
            if reference.kind
            in {
                "attribute_load",
                "constant_getattr",
                "dynamic_getattr",
                "name_load",
            }
            and (reference.target in self.targets or self._is_protected(reference.target))
        ]
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) else None
        protected_callable_targets = self._protected_callable_load_targets(value) if value is not None else set()
        if protected_references or protected_callable_targets:
            sentinel_target = (
                protected_references[0].target if protected_references else sorted(protected_callable_targets)[0]
            )
            self.references.append(
                _QualifiedReference(
                    self.owner,
                    sentinel_target,
                    "unresolved_protected_assignment",
                    id(node),
                    self.owner_node_id,
                )
            )

    def _protected_callable_load_targets(self, tree: ast.AST) -> set[str]:
        resolved_targets_by_call_id: dict[int, set[str]] = {}
        for reference in self.resolved_calls:
            resolved_targets_by_call_id.setdefault(reference.node_id, set()).add(reference.target)

        protected_targets: set[str] = set()

        def bound_names(node: ast.AST) -> set[str]:
            if isinstance(node, ast.Name):
                return {node.id}
            if isinstance(node, ast.Starred):
                return bound_names(node.value)
            if isinstance(node, (ast.Tuple, ast.List)):
                return {name for element in node.elts for name in bound_names(element)}
            return set()

        def possible_resolutions(node: ast.AST | None, shadowed: set[str]) -> set[str]:
            if isinstance(node, ast.Name):
                if node.id in shadowed:
                    return set()
                bound = self.aliases.get(node.id, node.id)
                if isinstance(bound, frozenset):
                    return set(bound)
                return {bound} if bound is not None else set()
            if isinstance(node, ast.Attribute):
                return {f"{prefix}.{node.attr}" for prefix in possible_resolutions(node.value, shadowed)}
            if isinstance(node, ast.IfExp):
                return possible_resolutions(node.body, shadowed) | possible_resolutions(node.orelse, shadowed)
            if isinstance(node, ast.BoolOp):
                return {resolution for value in node.values for resolution in possible_resolutions(value, shadowed)}
            if isinstance(node, ast.NamedExpr):
                return possible_resolutions(node.value, shadowed)
            if (
                isinstance(node, ast.Call)
                and resolved_targets_by_call_id.get(id(node), set()) & {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                return {
                    f"{object_name}.{node.args[1].value}"
                    for object_name in possible_resolutions(node.args[0], shadowed)
                }
            return set()

        def record(node: ast.AST, shadowed: set[str]) -> None:
            for resolution in possible_resolutions(node, shadowed):
                if self._is_protected(resolution) and (
                    resolution in self.known_callable_targets or resolution in self.targets
                ):
                    protected_targets.add(resolution)

        def visit_comprehension(
            generators: list[ast.comprehension],
            results: tuple[ast.AST, ...],
            shadowed: set[str],
        ) -> None:
            comprehension_shadowed = set(shadowed)
            for generator in generators:
                visit(generator.iter, comprehension_shadowed)
                comprehension_shadowed.update(bound_names(generator.target))
                for condition in generator.ifs:
                    visit(condition, comprehension_shadowed)
            for result in results:
                visit(result, comprehension_shadowed)

        def visit(node: ast.AST, shadowed: set[str]) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.ListComp):
                visit_comprehension(node.generators, (node.elt,), shadowed)
                return
            if isinstance(node, ast.SetComp):
                visit_comprehension(node.generators, (node.elt,), shadowed)
                return
            if isinstance(node, ast.DictComp):
                visit_comprehension(node.generators, (node.key, node.value), shadowed)
                return
            if isinstance(node, ast.GeneratorExp):
                visit_comprehension(node.generators, (node.elt,), shadowed)
                return
            if (
                isinstance(node, (ast.Name, ast.Attribute))
                and isinstance(node.ctx, ast.Load)
                and id(node) not in self.direct_call_nodes
            ) or isinstance(node, ast.Call):
                record(node, shadowed)
            for child in ast.iter_child_nodes(node):
                visit(child, shadowed)

        visit(tree, set())
        return protected_targets

    def visit_Assign(self, node: ast.Assign) -> None:
        references_before = len(self.references)
        self.visit(node.value)
        resolved_all = True
        for target in node.targets:
            resolved_all = self._bind_assignment(target, node.value) and resolved_all
        if not resolved_all:
            self._record_unresolved_protected_assignment(node, references_before)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        references_before = len(self.references)
        if node.value is not None:
            self.visit(node.value)
            resolved = self._bind_assignment(node.target, node.value)
        else:
            resolved = self._bind_assignment(node.target, node.annotation)
        if not resolved:
            self._record_unresolved_protected_assignment(node, references_before)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        references_before = len(self.references)
        self.visit(node.value)
        if not self._bind_assignment(node.target, node.value):
            self._record_unresolved_protected_assignment(node, references_before)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            resolved = self._resolve(node)
            if resolved and id(node) not in self.direct_call_nodes:
                self._record(node, resolved, "name_load")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            resolved = self._resolve(node)
            if resolved and id(node) not in self.direct_call_nodes:
                self._record(node, resolved, "attribute_load")
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        functions = self._possible_resolutions(node.func)
        for function in functions:
            self.resolved_calls.append(
                _QualifiedReference(
                    self.owner,
                    function,
                    "call",
                    id(node),
                    self.owner_node_id,
                )
            )
            self._record(node, function, "call")
        if functions & {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            object_name = self._resolve(node.args[0])
            attribute = node.args[1]
            if object_name and isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
                self._record(node, f"{object_name}.{attribute.value}", "constant_getattr")
            elif object_name and object_name in self.protected_objects:
                self.references.append(
                    _QualifiedReference(
                        self.owner,
                        object_name,
                        "dynamic_getattr",
                        id(node),
                        self.owner_node_id,
                    )
                )
        self.generic_visit(node)


def _qualified_references(
    tree: ast.AST,
    *,
    targets: set[str],
    protected_objects: set[str],
    current_module: str,
    current_is_package: bool = False,
) -> tuple[_QualifiedReference, ...]:
    resolver = _QualifiedReferenceResolver(
        tree,
        targets=targets,
        protected_objects=protected_objects,
        current_module=current_module,
        current_is_package=current_is_package,
    )
    resolver.visit(tree)
    return tuple(resolver.references)


def _absolute_import_from(
    node: ast.ImportFrom,
    *,
    current_module: str,
    current_is_package: bool,
) -> str:
    if node.level == 0:
        return node.module or ""
    package = current_module if current_is_package else current_module.rpartition(".")[0]
    package_parts = package.split(".") if package else []
    keep = max(0, len(package_parts) - (node.level - 1))
    base_parts = package_parts[:keep]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _imported_qualified_names(
    tree: ast.AST,
    *,
    current_module: str,
    current_is_package: bool = False,
) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_import_from(
                node,
                current_module=current_module,
                current_is_package=current_is_package,
            )
            if module:
                imported.add(module)
                imported.update(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return imported


@dataclass(frozen=True)
class _SourceAnalysis:
    tree: ast.Module
    references: tuple[_QualifiedReference, ...]
    resolved_calls: tuple[_QualifiedReference, ...]
    imports: frozenset[str]
    calls_by_id: dict[int, ast.Call]
    functions: dict[tuple[str, int], ast.FunctionDef | ast.AsyncFunctionDef]
    function_owners_by_id: dict[int, str]
    function_targets: dict[str, frozenset[tuple[str, int]]]


def _analyze_source(
    source: str,
    *,
    current_module: str,
    current_is_package: bool = False,
    targets: set[str],
    protected_objects: set[str],
) -> _SourceAnalysis:
    tree = ast.parse(source)
    resolver = _QualifiedReferenceResolver(
        tree,
        targets=targets,
        protected_objects=protected_objects,
        current_module=current_module,
        current_is_package=current_is_package,
    )
    resolver.visit(tree)
    return _SourceAnalysis(
        tree=tree,
        references=tuple(resolver.references),
        resolved_calls=tuple(resolver.resolved_calls),
        imports=frozenset(
            _imported_qualified_names(
                tree,
                current_module=current_module,
                current_is_package=current_is_package,
            )
        ),
        calls_by_id={id(node): node for node in ast.walk(tree) if isinstance(node, ast.Call)},
        functions={
            (owner, node_id): resolver.function_nodes[node_id]
            for node_id, owner in resolver.function_owners_by_id.items()
        },
        function_owners_by_id=dict(resolver.function_owners_by_id),
        function_targets={
            target: frozenset(
                (resolver.function_owners_by_id[node_id], node_id)
                for node_id, candidate in resolver.function_qnames_by_id.items()
                if candidate == target
            )
            for target in set(resolver.function_qnames_by_id.values())
        },
    )


def _reachable_function_owners(
    analysis: _SourceAnalysis,
    *,
    roots: set[str],
) -> set[str]:
    pending = {key for key in analysis.functions if key[0] in roots}
    reached: set[tuple[str, int]] = set()
    while pending:
        owner_key = pending.pop()
        if owner_key in reached:
            continue
        reached.add(owner_key)
        _owner, owner_node_id = owner_key
        for call in analysis.resolved_calls:
            if call.owner_node_id != owner_node_id:
                continue
            pending.update(analysis.function_targets.get(call.target, ()))
        function = analysis.functions[owner_key]
        for node in _current_lexical_nodes(function):
            if not isinstance(node, ast.Call):
                continue
            candidates: set[tuple[str, int]] = set()
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and "." in owner_key[0]
            ):
                class_owner = owner_key[0].rsplit(".", maxsplit=1)[0]
                candidates = {key for key in analysis.functions if key[0] == f"{class_owner}.{node.func.attr}"}
            if len(candidates) == 1:
                pending.update(candidates)
    return {owner for owner, _node_id in reached}


_LEXICAL_SCOPE_BARRIERS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _current_lexical_nodes(tree: ast.AST) -> tuple[ast.AST, ...]:
    roots = tuple(tree.body) if isinstance(tree, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) else (tree,)
    observed: list[ast.AST] = []
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        if isinstance(node, _LEXICAL_SCOPE_BARRIERS):
            continue
        observed.append(node)
        pending.extend(reversed(tuple(ast.iter_child_nodes(node))))
    return tuple(observed)


def _loaded_tokens(tree: ast.AST) -> set[str]:
    nodes = _current_lexical_nodes(tree)
    return (
        {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        | {node.attr for node in nodes if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)}
        | {node.value for node in nodes if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    )


def _tainted_assignment_aliases(tree: ast.AST, forbidden: set[str]) -> set[str]:
    assignments: list[ast.Assign | ast.AnnAssign] = []

    class Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_ListComp(self, node: ast.ListComp) -> None:
            return

        visit_SetComp = visit_ListComp  # noqa: N815
        visit_DictComp = visit_ListComp  # noqa: N815
        visit_GeneratorExp = visit_ListComp  # noqa: N815

        def visit_Assign(self, node: ast.Assign) -> None:
            assignments.append(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            assignments.append(node)

    Collector().visit(tree)
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            else:
                continue
            if _loaded_tokens(value).isdisjoint(forbidden | tainted):
                continue
            aliases = {
                child.id
                for target in targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }
            new_aliases = aliases - tainted
            if new_aliases:
                tainted.update(new_aliases)
                changed = True
    return tainted


def _local_tainted_assignment_aliases(tree: ast.AST, forbidden: set[str]) -> set[str]:
    assignments = [
        node
        for node in _current_lexical_nodes(tree)
        if isinstance(node, ast.Assign) or (isinstance(node, ast.AnnAssign) and node.value is not None)
    ]
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            else:
                assert node.value is not None
                value = node.value
                targets = [node.target]
            if _loaded_tokens(value).isdisjoint(forbidden | tainted):
                continue
            aliases = {
                child.id
                for target in targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }
            new_aliases = aliases - tainted
            if new_aliases:
                tainted.update(new_aliases)
                changed = True
    return tainted


def _expression_is_tainted(
    analysis: _SourceAnalysis,
    expression: ast.AST,
    *,
    direct_seeds: set[str],
    tainted_functions: set[tuple[str, int]],
) -> bool:
    if not _loaded_tokens(expression).isdisjoint(direct_seeds):
        return True
    expression_node_ids = {id(node) for node in _current_lexical_nodes(expression)}
    return any(
        call.node_id in expression_node_ids
        and not analysis.function_targets.get(call.target, frozenset()).isdisjoint(tainted_functions)
        for call in analysis.resolved_calls
    )


def _qualified_local_tainted_aliases(
    analysis: _SourceAnalysis,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    direct_seeds: set[str],
    tainted_functions: set[tuple[str, int]],
) -> set[str]:
    assignments = [
        node
        for node in _current_lexical_nodes(function)
        if isinstance(node, (ast.Assign, ast.NamedExpr)) or (isinstance(node, ast.AnnAssign) and node.value is not None)
    ]
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in assignments:
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not _expression_is_tainted(
                analysis,
                value,
                direct_seeds=direct_seeds | tainted,
                tainted_functions=tainted_functions,
            ):
                continue
            aliases = {
                child.id
                for target in targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }
            new_aliases = aliases - tainted
            if new_aliases:
                tainted.update(new_aliases)
                changed = True
    return tainted


def _writer_taint_violations(
    analysis: _SourceAnalysis,
    *,
    restricted_states: set[str],
    owners: set[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    module_aliases = _tainted_assignment_aliases(analysis.tree, restricted_states)
    tainted_returns: set[tuple[str, int]] = set()
    changed = True
    while changed:
        changed = False
        for function_key, function in analysis.functions.items():
            local_aliases = _qualified_local_tainted_aliases(
                analysis,
                function,
                direct_seeds=restricted_states | module_aliases,
                tainted_functions=tainted_returns,
            )
            if (
                any(
                    _expression_is_tainted(
                        analysis,
                        node.value,
                        direct_seeds=restricted_states | module_aliases | local_aliases,
                        tainted_functions=tainted_returns,
                    )
                    for node in _current_lexical_nodes(function)
                    if isinstance(node, ast.Return) and node.value is not None
                )
                and function_key not in tainted_returns
            ):
                tainted_returns.add(function_key)
                changed = True

    violations: list[tuple[str, str]] = []
    for reference in analysis.references:
        if reference.kind != "call" or reference.target not in _FACT_STREAM_WRITER_TARGETS:
            continue
        if owners is not None and reference.owner not in owners:
            continue
        call = analysis.calls_by_id[reference.node_id]
        function = analysis.functions.get((reference.owner, reference.owner_node_id))
        local_aliases = (
            _qualified_local_tainted_aliases(
                analysis,
                function,
                direct_seeds=restricted_states | module_aliases,
                tainted_functions=tainted_returns,
            )
            if function is not None
            else set()
        )
        if any(
            _expression_is_tainted(
                analysis,
                argument,
                direct_seeds=restricted_states | module_aliases | local_aliases,
                tainted_functions=tainted_returns,
            )
            for argument in (*call.args, *(keyword.value for keyword in call.keywords))
        ):
            violations.append((reference.owner, reference.target))
    return tuple(violations)


def _forbidden_closure_references(
    analysis: _SourceAnalysis,
    *,
    owners: set[str],
) -> tuple[_QualifiedReference, ...]:
    forbidden_names = {target.rsplit(".", maxsplit=1)[-1] for target in _REPOSITORY_FORBIDDEN_CALL_TARGETS}
    resolved_targets_by_call_id: dict[int, set[str]] = {}
    for reference in analysis.resolved_calls:
        resolved_targets_by_call_id.setdefault(reference.node_id, set()).add(reference.target)

    def constant_forbidden_getattr(node: ast.AST) -> str | None:
        if (
            not isinstance(node, ast.Call)
            or not resolved_targets_by_call_id.get(id(node), set()) & {"getattr", "builtins.getattr"}
            or len(node.args) < 2
            or not isinstance(node.args[1], ast.Constant)
            or node.args[1].value not in forbidden_names
        ):
            return None
        return str(node.args[1].value)

    violations = {
        reference
        for reference in (*analysis.resolved_calls, *analysis.references)
        if reference.owner in owners
        and (
            (reference.kind == "call" and reference.target.rsplit(".", maxsplit=1)[-1] in forbidden_names)
            or (reference.target == _DEO_REPOSITORY and reference.kind == "dynamic_getattr")
        )
    }
    for (owner, owner_node_id), function in analysis.functions.items():
        if owner not in owners:
            continue
        lexical_nodes = _current_lexical_nodes(function)
        forbidden_aliases: set[str] = set()
        changed = True
        while changed:
            changed = False
            for node in lexical_nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    continue
                if isinstance(node, ast.AnnAssign) and node.value is None:
                    continue
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value_tokens = _loaded_tokens(value)
                has_forbidden_attribute = any(
                    (
                        isinstance(child, ast.Attribute)
                        and isinstance(child.ctx, ast.Load)
                        and child.attr in forbidden_names
                    )
                    or constant_forbidden_getattr(child) is not None
                    for child in _current_lexical_nodes(value)
                )
                if not has_forbidden_attribute and value_tokens.isdisjoint(forbidden_aliases):
                    continue
                new_aliases = {
                    child.id
                    for target in targets
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                } - forbidden_aliases
                if new_aliases:
                    forbidden_aliases.update(new_aliases)
                    changed = True

        for node in lexical_nodes:
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in forbidden_names:
                violations.add(
                    _QualifiedReference(
                        owner,
                        node.attr,
                        "conservative_forbidden_attribute_load",
                        id(node),
                        owner_node_id,
                    )
                )
            forbidden_getattr = constant_forbidden_getattr(node)
            if forbidden_getattr is not None:
                violations.add(
                    _QualifiedReference(
                        owner,
                        forbidden_getattr,
                        "conservative_forbidden_getattr",
                        id(node),
                        owner_node_id,
                    )
                )
            if not isinstance(node, ast.Call):
                if (
                    isinstance(node, ast.Return)
                    and node.value is not None
                    and not _loaded_tokens(node.value).isdisjoint(forbidden_aliases)
                ):
                    violations.add(
                        _QualifiedReference(
                            owner,
                            "forbidden_alias",
                            "forbidden_alias_return",
                            id(node),
                            owner_node_id,
                        )
                    )
                continue
            called_tail = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if called_tail in forbidden_names or called_tail in forbidden_aliases:
                violations.add(
                    _QualifiedReference(
                        owner,
                        called_tail,
                        "conservative_forbidden_call" if called_tail in forbidden_names else "forbidden_alias_call",
                        id(node),
                        owner_node_id,
                    )
                )
    return tuple(sorted(violations))


def _module_context_for_path(path: Path, *, polaris_root: Path) -> tuple[str, bool]:
    relative = path.relative_to(polaris_root)
    parts = list(relative.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(("polaris", *parts)), is_package


def _production_python_files() -> tuple[Path, ...]:
    cell_root = Path(inspect.getfile(deo_internal)).resolve().parents[1]
    return tuple(path for path in sorted(cell_root.rglob("*.py")) if "tests" not in path.relative_to(cell_root).parts)


def _polaris_production_python_files() -> tuple[Path, ...]:
    polaris_root = Path(inspect.getfile(deo_internal)).resolve().parents[4]
    return tuple(
        path
        for path in sorted(polaris_root.rglob("*.py"))
        if "tests" not in path.relative_to(polaris_root).parts and path.name != "conftest.py"
    )


def _service_method_tree(name: str) -> ast.Module:
    method = getattr(runtime_service_internal.TaskRuntimeService, name)
    return ast.parse(dedent(inspect.getsource(method)))


def test_qualified_resolver_catches_repository_alias_and_getattr_escapes() -> None:
    tree = ast.parse(
        dedent(
            f"""
            from {_DEO_INTERNAL_MODULE} import DirectedEffectOperationRepository as Repository

            def escape(method_name):
                repository = Repository()
                seal = repository.seal_inventory
                seal(None)
                getattr(repository, "finalize_inventory")
                return getattr(repository, method_name)
            """
        )
    )

    references = set(
        _qualified_references(
            tree,
            targets=_REPOSITORY_INVENTORY_TARGETS,
            protected_objects={_DEO_REPOSITORY, _DEO_INTERNAL_MODULE},
            current_module="fixture.repository_consumer",
        )
    )

    assert _QualifiedReference("escape", f"{_DEO_REPOSITORY}.seal_inventory", "attribute_load") in references
    assert _QualifiedReference("escape", f"{_DEO_REPOSITORY}.seal_inventory", "call") in references
    assert _QualifiedReference("escape", f"{_DEO_REPOSITORY}.finalize_inventory", "constant_getattr") in references
    assert _QualifiedReference("escape", _DEO_REPOSITORY, "dynamic_getattr") in references


def test_qualified_resolver_catches_factstream_from_import_module_alias_and_getattr() -> None:
    tree = ast.parse(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as append
            import {_FACT_STREAM_PUBLIC} as fact_stream

            def write(field):
                append(None)
                guarded = fact_stream.append_if_guarded_snapshot
                guarded(None)
                return getattr(fact_stream, field)
            """
        )
    )

    references = set(
        _qualified_references(
            tree,
            targets=_FACT_STREAM_WRITER_TARGETS,
            protected_objects={_FACT_STREAM_PUBLIC},
            current_module="fixture.fact_stream_consumer",
        )
    )

    assert _QualifiedReference("write", f"{_FACT_STREAM_PUBLIC}.service.append_fact_event", "call") in references
    assert (
        _QualifiedReference("write", f"{_FACT_STREAM_PUBLIC}.append_if_guarded_snapshot", "attribute_load")
        in references
    )
    assert _QualifiedReference("write", f"{_FACT_STREAM_PUBLIC}.append_if_guarded_snapshot", "call") in references
    assert _QualifiedReference("write", _FACT_STREAM_PUBLIC, "dynamic_getattr") in references


def test_import_detector_expands_from_import_module_path() -> None:
    tree = ast.parse(f"from {_DEO_INTERNAL_PREFIX} import directed_effect_operation as deo")

    assert _imported_qualified_names(
        tree,
        current_module="fixture.import_consumer",
    ) >= {
        _DEO_INTERNAL_PREFIX,
        _DEO_INTERNAL_MODULE,
    }


def test_source_analysis_resolves_relative_package_alias_and_nested_getattr() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from .. import internal as package_internal
            from ..internal import directed_effect_operation as deo
            import {_FACT_STREAM_PUBLIC} as fact_stream

            def write():
                nested = getattr(fact_stream, "service").append_fact_event
                nested(None)
            """
        ),
        current_module="polaris.cells.runtime.task_runtime.public.fixture",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert analysis.imports >= {
        _DEO_INTERNAL_PREFIX,
        _DEO_INTERNAL_MODULE,
    }
    assert (
        _QualifiedReference(
            "write",
            f"{_FACT_STREAM_PUBLIC}.service.append_fact_event",
            "attribute_load",
        )
        in analysis.references
    )
    assert (
        _QualifiedReference(
            "write",
            f"{_FACT_STREAM_PUBLIC}.service.append_fact_event",
            "call",
        )
        in analysis.references
    )


def test_source_analysis_traverses_definition_expressions_but_honors_parameter_shadow() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as writer

            @writer
            def decorated(writer=writer) -> writer:
                writer(None)
            """
        ),
        current_module="fixture.definition_expressions",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    definition_references = [
        reference
        for reference in analysis.references
        if reference.owner == "decorated.<definition>"
        and reference.target == f"{_FACT_STREAM_PUBLIC}.service.append_fact_event"
    ]
    assert len(definition_references) == 3
    assert {reference.kind for reference in definition_references} == {"name_load"}


def test_source_analysis_detects_writer_alias_return_taint_and_self_dynamic_getattr() -> None:
    restricted_states = {"CLOSED_BY_PARENT"}
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.contracts import GuardedFactEventV1 as Event
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as emit

            STATE = "CLOSED_BY_PARENT"

            def tainted_value():
                return STATE

            def write():
                event = Event(state=tainted_value())
                emit(event)

            class DirectedEffectOperationRepository:
                def dynamic(self, method_name):
                    return getattr(self, method_name)

                @staticmethod
                def inspect_receipt(receipt, field_name):
                    return getattr(receipt, field_name)
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_FACT_STREAM_WRITER_TARGETS | _REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC, _DEO_REPOSITORY},
    )

    assert set(
        _writer_taint_violations(
            analysis,
            restricted_states=restricted_states,
        )
    ) == {
        ("write", f"{_FACT_STREAM_PUBLIC}.contracts.GuardedFactEventV1"),
        ("write", f"{_FACT_STREAM_PUBLIC}.service.append_fact_event"),
    }
    assert _forbidden_closure_references(
        analysis,
        owners={"DirectedEffectOperationRepository.dynamic"},
    ) == (
        _QualifiedReference(
            "DirectedEffectOperationRepository.dynamic",
            _DEO_REPOSITORY,
            "dynamic_getattr",
        ),
    )
    assert (
        _forbidden_closure_references(
            analysis,
            owners={"DirectedEffectOperationRepository.inspect_receipt"},
        )
        == ()
    )


def test_source_analysis_rejects_protected_star_import() -> None:
    analysis = _analyze_source(
        f"from {_FACT_STREAM_PUBLIC} import *",
        current_module="fixture.star_import",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert analysis.references == (
        _QualifiedReference(
            "<module>",
            _FACT_STREAM_PUBLIC,
            "protected_star_import",
        ),
    )


def test_source_analysis_binds_destructured_aliases_and_rejects_unresolved_protected_assignment() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            import {_FACT_STREAM_PUBLIC} as fact_stream

            emit, Event = (
                fact_stream.service.append_fact_event,
                fact_stream.contracts.GuardedFactEventV1,
            )
            unresolved = choose(fact_stream.service.append_fact_event)

            def write():
                emit(Event(state="INTENT_COMMITTED"))
            """
        ),
        current_module="fixture.destructuring",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert {
        (reference.owner, reference.target, reference.kind)
        for reference in analysis.references
        if reference.kind == "call"
    } >= {
        ("write", f"{_FACT_STREAM_PUBLIC}.service.append_fact_event", "call"),
        ("write", f"{_FACT_STREAM_PUBLIC}.contracts.GuardedFactEventV1", "call"),
    }
    assert any(
        reference.kind == "unresolved_protected_assignment"
        and reference.target == f"{_FACT_STREAM_PUBLIC}.service.append_fact_event"
        for reference in analysis.references
    )


def test_source_analysis_uses_lexical_owners_and_keeps_comprehension_target_local() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as writer

            class First:
                def rogue(self):
                    writer(None)

            class Second:
                def rogue(self):
                    writer(None)

            def outer():
                def inner():
                    writer(None)
                [writer for writer in ()]
                writer(None)
            """
        ),
        current_module="fixture.lexical",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    call_owners = {
        reference.owner
        for reference in analysis.references
        if reference.kind == "call" and reference.target == f"{_FACT_STREAM_PUBLIC}.service.append_fact_event"
    }
    assert call_owners == {
        "First.rogue",
        "Second.rogue",
        "outer",
        "outer.<locals>.inner",
    }
    assert {owner for owner, _node_id in analysis.functions} >= call_owners


def test_source_analysis_reachability_covers_bound_getattr_and_generic_helper() -> None:
    analysis = _analyze_source(
        dedent(
            """
            def close_helper(repository):
                repository.close_parent()

            class DirectedEffectOperationRepository:
                def root(self):
                    bound = self.helper
                    bound()

                def helper(self):
                    return getattr(self, "leaf")()

                def leaf(self):
                    close_helper(self)
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    reached = _reachable_function_owners(
        analysis,
        roots={"DirectedEffectOperationRepository.root"},
    )
    assert reached == {
        "DirectedEffectOperationRepository.root",
        "DirectedEffectOperationRepository.helper",
        "DirectedEffectOperationRepository.leaf",
        "close_helper",
    }
    violations = _forbidden_closure_references(analysis, owners=reached)
    assert any(
        reference.owner == "close_helper"
        and reference.kind == "conservative_forbidden_call"
        and reference.target == "close_parent"
        for reference in violations
    )


def test_source_analysis_skips_nested_return_taint_but_catches_definition_time_writer() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as emit

            STATE = "CLOSED_BY_PARENT"

            def safe():
                def nested():
                    return STATE
                emit(None)

            @emit(STATE)
            def decorated():
                pass
            """
        ),
        current_module="fixture.lexical_taint",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert _writer_taint_violations(
        analysis,
        restricted_states={"CLOSED_BY_PARENT"},
    ) == (
        (
            "decorated.<definition>",
            f"{_FACT_STREAM_PUBLIC}.service.append_fact_event",
        ),
    )


def test_source_analysis_handles_conditional_named_and_boxed_protected_assignments() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            import {_FACT_STREAM_PUBLIC} as fact_stream

            conditional = (
                fact_stream.service.append_fact_event
                if enabled
                else fact_stream.service.append_fact_event
            )
            (named := fact_stream.service.append_fact_event)
            box[0] = fact_stream.service.append_fact_event

            def write():
                conditional(None)
                named(None)
            """
        ),
        current_module="fixture.conditional_assignment",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert {
        reference.owner
        for reference in analysis.references
        if reference.kind == "call" and reference.target == f"{_FACT_STREAM_PUBLIC}.service.append_fact_event"
    } == {"write"}
    assert any(
        reference.kind == "unresolved_protected_assignment" and reference.owner == "<module>"
        for reference in analysis.references
    )


def test_source_analysis_propagates_qualified_method_return_taint_to_writer() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.contracts import GuardedFactEventV1 as Event

            class Helper:
                @staticmethod
                def state():
                    return "DEAD_LETTER"

                @staticmethod
                def chained():
                    return Helper.state()

            def write():
                Event(state=Helper.chained())
            """
        ),
        current_module="fixture.qualified_return_taint",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert _writer_taint_violations(
        analysis,
        restricted_states={"DEAD_LETTER"},
    ) == (
        (
            "write",
            f"{_FACT_STREAM_PUBLIC}.contracts.GuardedFactEventV1",
        ),
    )


def test_source_analysis_reaches_late_helper_and_rejects_forbidden_alias_use() -> None:
    analysis = _analyze_source(
        dedent(
            """
            class DirectedEffectOperationRepository:
                def root(self):
                    late_helper(self)

            def late_helper(repository):
                closer = repository.close_parent
                closer()
                return closer
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    reached = _reachable_function_owners(
        analysis,
        roots={"DirectedEffectOperationRepository.root"},
    )
    assert reached == {
        "DirectedEffectOperationRepository.root",
        "late_helper",
    }
    violation_kinds = {
        reference.kind
        for reference in _forbidden_closure_references(analysis, owners=reached)
        if reference.owner == "late_helper"
    }
    assert violation_kinds >= {
        "conservative_forbidden_attribute_load",
        "forbidden_alias_call",
        "forbidden_alias_return",
    }


def test_source_analysis_keeps_class_state_out_of_module_taint() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.service import append_fact_event as emit

            class Holder:
                STATE = "DEAD_LETTER"

            def safe():
                emit(None)
            """
        ),
        current_module="fixture.class_state",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert (
        _writer_taint_violations(
            analysis,
            restricted_states={"DEAD_LETTER"},
        )
        == ()
    )


def test_source_analysis_propagates_every_conditional_bound_method_edge() -> None:
    analysis = _analyze_source(
        dedent(
            """
            class DirectedEffectOperationRepository:
                def root(self, flag):
                    bridge = self._one if flag else self._two
                    bridge()

                def _one(self):
                    self.close_parent()

                def _two(self):
                    self.persist_receipt()
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    reached = _reachable_function_owners(
        analysis,
        roots={"DirectedEffectOperationRepository.root"},
    )
    assert reached == {
        "DirectedEffectOperationRepository.root",
        "DirectedEffectOperationRepository._one",
        "DirectedEffectOperationRepository._two",
    }
    assert {reference.owner for reference in _forbidden_closure_references(analysis, owners=reached)} >= {
        "DirectedEffectOperationRepository._one",
        "DirectedEffectOperationRepository._two",
    }


def test_source_analysis_prefers_exact_late_module_binding_over_same_leaf_method() -> None:
    analysis = _analyze_source(
        dedent(
            """
            class DirectedEffectOperationRepository:
                def root(self):
                    late_helper(self)

            class Rogue:
                def late_helper(self):
                    self.persist_receipt()

            def late_helper(repository):
                repository.close_parent()
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    reached = _reachable_function_owners(
        analysis,
        roots={"DirectedEffectOperationRepository.root"},
    )
    assert reached == {
        "DirectedEffectOperationRepository.root",
        "late_helper",
    }
    assert "Rogue.late_helper" not in reached
    assert any(
        reference.owner == "late_helper" and reference.target == "close_parent"
        for reference in _forbidden_closure_references(analysis, owners=reached)
    )


def test_source_analysis_taints_writer_from_late_module_helper_return() -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            from {_FACT_STREAM_PUBLIC}.contracts import GuardedFactEventV1 as Event

            def write():
                Event(state=late_state())

            def late_state():
                return "DEAD_LETTER"
            """
        ),
        current_module="fixture.late_return_taint",
        targets=_FACT_STREAM_WRITER_TARGETS,
        protected_objects={_FACT_STREAM_PUBLIC},
    )

    assert _writer_taint_violations(
        analysis,
        restricted_states={"DEAD_LETTER"},
    ) == (
        (
            "write",
            f"{_FACT_STREAM_PUBLIC}.contracts.GuardedFactEventV1",
        ),
    )


@pytest.mark.parametrize(
    ("helper_body", "terminal_kind"),
    (
        (
            'closer = getattr(repository, "close_parent")\ncloser()',
            "forbidden_alias_call",
        ),
        (
            'get = getattr\ncloser = get(repository, "close_parent")\ncloser()',
            "forbidden_alias_call",
        ),
        (
            'import builtins\ncloser = builtins.getattr(repository, "close_parent")\nreturn closer',
            "forbidden_alias_return",
        ),
    ),
    ids=("direct", "local-alias", "builtins"),
)
def test_source_analysis_rejects_constant_getattr_forbidden_alias(
    helper_body: str,
    terminal_kind: str,
) -> None:
    indented_helper_body = "\n".join(f"    {line}" for line in helper_body.splitlines())
    analysis = _analyze_source(
        (
            "class DirectedEffectOperationRepository:\n"
            "    def root(self):\n"
            "        helper(self)\n\n"
            "def helper(repository):\n"
            f"{indented_helper_body}\n"
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    reached = _reachable_function_owners(
        analysis,
        roots={"DirectedEffectOperationRepository.root"},
    )
    assert reached == {
        "DirectedEffectOperationRepository.root",
        "helper",
    }
    violation_kinds = {
        reference.kind
        for reference in _forbidden_closure_references(analysis, owners=reached)
        if reference.owner == "helper"
    }
    assert violation_kinds >= {
        "conservative_forbidden_getattr",
        terminal_kind,
    }


@pytest.mark.parametrize(
    "assignment",
    (
        "bridges = [self._one, self._two]",
        "ordinary, *bridges = (object(), self._one, self._two)",
    ),
    ids=("container", "starred-container"),
)
def test_source_analysis_fails_closed_for_protected_callable_container_subscript(
    assignment: str,
) -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            class DirectedEffectOperationRepository:
                def root(self):
                    {assignment}
                    bridges[0]()

                def _one(self):
                    pass

                def _two(self):
                    pass

            def benign(items):
                values = [items[0], items[1]]
                return values[0]
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    sentinels = {
        reference.owner for reference in analysis.references if reference.kind == "unresolved_protected_assignment"
    }
    assert sentinels == {"DirectedEffectOperationRepository.root"}
    assert "benign" not in sentinels


@pytest.mark.parametrize(
    "assignment",
    (
        "bridges = [bridge for bridge in (self._one, self._two)]",
        "bridges = (bridge for bridge in (self._one, self._two))",
    ),
    ids=("list-comprehension", "generator-expression"),
)
def test_source_analysis_fails_closed_for_comprehended_protected_callable_container(
    assignment: str,
) -> None:
    analysis = _analyze_source(
        dedent(
            f"""
            class DirectedEffectOperationRepository:
                def root(self):
                    {assignment}
                    next(iter(bridges))()

                def _one(self):
                    pass

                def _two(self):
                    pass

                def shadowed(self, items):
                    values = [self._one for self in items]
                    return values

            def benign(items):
                values = [item.value for item in items]
                generated = (item.value for item in items)
                return values, generated
            """
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    sentinels = {
        reference.owner for reference in analysis.references if reference.kind == "unresolved_protected_assignment"
    }
    assert sentinels == {"DirectedEffectOperationRepository.root"}
    assert "DirectedEffectOperationRepository.shadowed" not in sentinels
    assert "benign" not in sentinels


@pytest.mark.parametrize(
    ("setup", "helper"),
    (
        ("", "getattr"),
        ("get = getattr", "get"),
        ("import builtins", "builtins.getattr"),
    ),
    ids=("direct", "local-alias", "builtins"),
)
def test_source_analysis_fails_closed_for_constant_getattr_callable_container(
    setup: str,
    helper: str,
) -> None:
    setup_line = f"        {setup}\n" if setup else ""
    analysis = _analyze_source(
        (
            "class DirectedEffectOperationRepository:\n"
            "    data = object()\n\n"
            "    def root(self):\n"
            f"{setup_line}"
            f'        bridges = [{helper}(self, "_one")]\n'
            "        bridges[0]()\n\n"
            "    def benign(self):\n"
            '        values = [getattr(self, "data")]\n'
            "        return values[0]\n\n"
            "    def _one(self):\n"
            "        pass\n"
        ),
        current_module=_DEO_INTERNAL_MODULE,
        targets=_REPOSITORY_FORBIDDEN_CALL_TARGETS,
        protected_objects={_DEO_REPOSITORY},
    )

    sentinels = {
        reference.owner for reference in analysis.references if reference.kind == "unresolved_protected_assignment"
    }
    assert sentinels == {"DirectedEffectOperationRepository.root"}
    assert "DirectedEffectOperationRepository.benign" not in sentinels


def test_child_mutation_has_one_guarded_path_and_no_direct_append() -> None:
    calls = _called_names(_method_tree("_mutate"))

    assert "append_if_guarded_snapshot" in calls
    assert "append_fact_event" not in calls
    assert "_reconcile_operation_append" in calls
    assert calls.count("_guarded_attempt_failure") >= 4


def test_reprepare_policy_is_bounded_to_normative_drift_codes() -> None:
    assert deo_internal._MAX_GUARDED_ATTEMPTS == 3
    assert len(deo_internal._GUARDED_REPREPARE_DRIFT_CODES) == 2
    assert "target_snapshot_drift" in deo_internal._GUARDED_REPREPARE_DRIFT_CODES
    assert "guard_snapshot_drift" in deo_internal._GUARDED_REPREPARE_DRIFT_CODES
    constants = {
        node.value
        for node in ast.walk(_method_tree("_mutate"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required_evidence = {
        "guarded_reprepare_exhausted",
        "attempts_total",
        "reprepare_count",
        "drift_codes",
        "target_head_seq",
        "guard_head_seq",
        "operation_identity",
        "parent_binding_id",
    }
    assert constants >= required_evidence


def test_repository_has_no_disk_snapshot_side_effect_api() -> None:
    repository = deo_internal.DirectedEffectOperationRepository

    assert not hasattr(repository, "_persist_snapshot")
    assert not hasattr(repository, "_snapshot_path")


def test_parent_admission_has_one_service_owned_writer_and_no_recursive_validation() -> None:
    module_tree = ast.parse(inspect.getsource(deo_internal))
    append_owners: set[str] = set()
    for node in ast.walk(module_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "append_fact_event" in _called_names(node):
            append_owners.add(node.name)

    repository_calls = _called_names(_method_tree("admit_parent_with_validated_authority"))
    public_calls = _called_names(
        ast.parse(dedent(inspect.getsource(runtime_public_service.admit_directed_effect_parent)))
    )
    service_calls = _called_names(_service_method_tree("admit_directed_effect_parent"))
    assert append_owners == {"admit_parent_with_validated_authority"}
    assert "validate_attempt" not in repository_calls
    assert "validate_execution_attempt" not in repository_calls
    assert "_get_session_lock" not in repository_calls
    assert "TaskRuntimeService" in public_calls
    assert "admit_directed_effect_parent" in public_calls
    assert "_get_session_lock" in service_calls
    assert "_session_file_lock_path" in service_calls


def test_all_active_to_inactive_writers_reach_the_common_pre_barrier() -> None:
    direct_methods = {
        "_settle_execution_attempt_locked",
        "fence_expired_factory_run_sessions",
        "_suspend_active_session_for_run_locked",
        "cancel_task_row_for_deduplication",
        "fail_task_row_from_role_adapter",
        "_reopen_with_execution_event",
    }
    for method_name in direct_methods:
        calls = _called_names(_service_method_tree(method_name))
        assert "_directed_effect_inactive_pre_barrier_locked" in calls

    rework_calls = _called_names(_service_method_tree("fail_task_row_after_rework_exhausted"))
    reopen_calls = _called_names(_service_method_tree("reopen_task_row"))
    assert "_reopen_with_execution_event" in rework_calls
    assert "_reopen_with_execution_event" in reopen_calls


def test_guarded_success_validates_all_public_receipt_fields_via_exact_replay() -> None:
    helper_name = "_guarded_receipt_proof_drift"
    assert hasattr(deo_internal.DirectedEffectOperationRepository, helper_name)
    helper_tree = _method_tree(helper_name)
    receipt_fields = {
        "event_id",
        "workspace",
        "stream",
        "storage_path",
        "appended_at",
        "appended_seq",
        "semantic_digest",
    }
    helper_constants = {
        node.value for node in ast.walk(helper_tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert helper_constants >= receipt_fields
    for result_method in ("_confirmed_inventory_result", "_confirmed_mutation_result"):
        result_tree = _method_tree(result_method)
        assert helper_name in _called_names(result_tree)
        result_constants = {
            node.value
            for node in ast.walk(result_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert result_constants.isdisjoint(receipt_fields)
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_confirm_guarded_append"))
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_confirm_inventory_append"))
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_reconcile_operation_append"))
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_reconcile_inventory_append"))


def test_parent_readiness_has_only_the_read_only_repository_path() -> None:
    public_tree = ast.parse(dedent(inspect.getsource(runtime_public_service.get_directed_effect_parent_readiness)))
    repository_calls = _called_names(_method_tree("get_parent_readiness"))
    reducer_calls = _called_names(_method_tree("_reduce_operation_stream"))

    assert set(_called_names(public_tree)) == {
        "isinstance",
        "TypeError",
        "DirectedEffectOperationRepository",
        "_directed_effect_authority_failure",
        "DirectedEffectParentReadinessResultV1",
        "get_parent_readiness",
    }
    assert set(repository_calls) >= {
        "_validated_parent_binding",
        "_read_stream",
        "_reduce_operation_stream",
        "DirectedEffectParentReadinessProjectionV1",
        "DirectedEffectParentReadinessStateCountV1",
        "DirectedEffectParentReadinessResultV1",
    }
    forbidden_calls = {
        "admit",
        "claim",
        "abort",
        "admit_parent_with_validated_authority",
        "append_fact_event",
        "append_if_guarded_snapshot",
        "enroll_fact_stream_streams",
        "enroll_operation_stream",
        "enroll_parent_registry_stream",
        "settle_task_runtime_execution_attempt",
        "_directed_effect_inactive_pre_barrier_locked",
    }
    assert not (set(repository_calls) & forbidden_calls)
    assert not (set(reducer_calls) & forbidden_calls)
    reachable_names = {
        owner.rsplit(".", maxsplit=1)[-1] for owner in _reachable_repository_methods("get_parent_readiness")
    }
    assert reachable_names.isdisjoint(forbidden_calls)


def test_parent_readiness_contract_has_no_authority_or_settlement_shape() -> None:
    forbidden = (
        "ready",
        "eligible",
        "authority",
        "authorization",
        "authorized",
        "receipt",
        "close",
        "settle",
        "settlement",
        "terminal",
    )
    for contract in (
        DirectedEffectParentReadinessProjectionV1,
        DirectedEffectParentReadinessResultV1,
        DirectedEffectParentReadinessStateCountV1,
    ):
        assert not any(token in field.name.lower() for field in fields(contract) for token in forbidden)
    assert "get_directed_effect_parent_readiness" in task_runtime_public.__all__
    assert "DirectedEffectParentReadinessResultV1" in task_runtime_public.__all__


def test_parent_readiness_failure_evidence_cannot_change_result_invariants() -> None:
    failure = DirectedEffectParentReadinessResultV1(
        ok=False,
        code="session_not_active",
        evidence={
            "non_authoritative_description": "No authority or settlement verdict is emitted.",
            "settings_snapshot": {"settings_mode": "diagnostic"},
            "authority_granted": False,
        },
    )
    assert failure.ok is False
    assert failure.projection is None
    assert failure.evidence["non_authoritative_description"].endswith("emitted.")
    assert failure.evidence["settings_snapshot"]["settings_mode"] == "diagnostic"


def test_parent_readiness_imports_do_not_connect_to_forbidden_cells() -> None:
    forbidden_modules = ("factory", "roles", "delivery", "frontend", "run_ledger")
    for module in (runtime_public_service, deo_internal):
        tree = ast.parse(inspect.getsource(module))
        imported_modules = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            forbidden_module in imported_module.lower()
            for imported_module in imported_modules
            for forbidden_module in forbidden_modules
        )
        assert not any("ui" in imported_module.lower().split(".") for imported_module in imported_modules)


def test_inventory_repository_entrypoints_have_only_public_service_callers() -> None:
    polaris_root = Path(inspect.getfile(deo_internal)).resolve().parents[4]
    taskruntime_root = Path(inspect.getfile(deo_internal)).resolve().parents[1]
    observed: list[tuple[str, str, str, str]] = []
    cross_cell_internal_imports: list[tuple[str, str]] = []
    for path in _polaris_production_python_files():
        relative_path = path.relative_to(polaris_root).as_posix()
        current_module, current_is_package = _module_context_for_path(
            path,
            polaris_root=polaris_root,
        )
        analysis = _analyze_source(
            path.read_text(encoding="utf-8"),
            current_module=current_module,
            current_is_package=current_is_package,
            targets=_REPOSITORY_INVENTORY_TARGETS,
            protected_objects={_DEO_REPOSITORY, _DEO_INTERNAL_MODULE},
        )
        observed.extend(
            (relative_path, reference.owner, reference.target, reference.kind) for reference in analysis.references
        )
        if not path.is_relative_to(taskruntime_root):
            cross_cell_internal_imports.extend(
                (relative_path, imported_name)
                for imported_name in analysis.imports
                if imported_name == _DEO_INTERNAL_PREFIX or imported_name.startswith(f"{_DEO_INTERNAL_PREFIX}.")
            )

    assert sorted(observed) == [
        (
            "cells/runtime/task_runtime/public/service.py",
            "finalize_directed_effect_inventory_admission",
            f"{_DEO_REPOSITORY}.finalize_inventory",
            "call",
        ),
        (
            "cells/runtime/task_runtime/public/service.py",
            "seal_directed_effect_inventory",
            f"{_DEO_REPOSITORY}.seal_inventory",
            "call",
        ),
    ]
    assert cross_cell_internal_imports == []


def test_deo_symbol_surface_has_no_forbidden_imports_or_implicit_enrollment() -> None:
    forbidden_module_segments = {
        "adapters",
        "bench",
        "director",
        "factory",
        "factory_bench",
        "kernelone",
        "qa",
        "roles",
        "run_ledger",
    }
    deo_symbol_surface = (
        runtime_public_contracts,
        task_runtime_public,
        runtime_public_service,
        deo_internal,
    )
    for module in deo_symbol_surface:
        analysis = _analyze_source(
            inspect.getsource(module),
            current_module=module.__name__,
            current_is_package=Path(inspect.getfile(module)).name == "__init__.py",
            targets=set(),
            protected_objects=set(),
        )
        forbidden_imports = {
            imported_name
            for imported_name in analysis.imports
            if set(imported_name.lower().replace("-", "_").split(".")) & forbidden_module_segments
        }
        assert forbidden_imports == set(), module.__name__

    enrollment_calls = {
        "enroll_fact_stream_streams",
        "enroll_operation_stream",
        "enroll_parent_registry_stream",
    }
    for writer in ("seal_inventory", "finalize_inventory"):
        reachable_names = {owner.rsplit(".", maxsplit=1)[-1] for owner in _reachable_repository_methods(writer)}
        assert reachable_names.isdisjoint(enrollment_calls)


def test_inventory_and_operation_writer_paths_are_closed_to_deo3_states() -> None:
    class_tree = ast.parse(dedent(inspect.getsource(deo_internal.DirectedEffectOperationRepository)))
    assert sorted(_call_owners(class_tree, "GuardedFactEventV1")) == [
        "_mutate",
        "finalize_inventory",
        "seal_inventory",
    ]
    assert sorted(_call_owners(class_tree, "_mutate")) == ["abort", "admit", "claim"]

    allowed_targets = {
        "abort": {"ABORTED"},
        "admit": {"INTENT_COMMITTED"},
        "claim": {"EFFECT_STARTED"},
    }
    forbidden_writer_constants = {
        "RECEIPT_COMMITTED",
        "RECOVERY_PENDING",
        "CLOSED_BY_PARENT",
        "DEAD_LETTER",
    }
    for method_name, expected_targets in allowed_targets.items():
        method_tree = _method_tree(method_name)
        constants = {
            node.value
            for node in ast.walk(method_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        mutation_targets = {
            keyword.value.value
            for node in ast.walk(method_tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_mutate"
            for keyword in node.keywords
            if keyword.arg == "target"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        }
        assert mutation_targets == expected_targets
        assert constants.isdisjoint(forbidden_writer_constants)

    for writer in ("seal_inventory", "finalize_inventory", "_mutate"):
        calls = set(_called_names(_method_tree(writer)))
        assert "append_fact_event" not in calls
        assert "append_if_guarded_snapshot" in calls
        assert calls.isdisjoint(
            {
                "close_parent",
                "persist_receipt",
                "settle_task_runtime_execution_attempt",
                "settlement_pre_barrier",
            }
        )


def test_all_taskruntime_factstream_writer_references_have_closed_owners() -> None:
    cell_root = Path(inspect.getfile(deo_internal)).resolve().parents[1]
    polaris_root = Path(inspect.getfile(deo_internal)).resolve().parents[4]
    expected_owners = {
        "append_fact_event": {
            (
                "internal/directed_effect_operation.py",
                "DirectedEffectOperationRepository.admit_parent_with_validated_authority",
            ),
            ("internal/service.py", "TaskRuntimeService._append_execution_fact_with_cas"),
            ("internal/task_board.py", "TaskBoard._append_terminal_event"),
        },
        "AppendFactEventCommandV1": {
            (
                "internal/directed_effect_operation.py",
                "DirectedEffectOperationRepository.admit_parent_with_validated_authority",
            ),
            ("internal/service.py", "TaskRuntimeService._append_execution_fact_with_cas"),
            ("internal/task_board.py", "TaskBoard._append_terminal_event"),
        },
        "append_if_guarded_snapshot": {
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository._confirm_guarded_append"),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository._confirm_inventory_append"),
            (
                "internal/directed_effect_operation.py",
                "DirectedEffectOperationRepository._confirm_inventory_ready_append",
            ),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository._mutate"),
            (
                "internal/directed_effect_operation.py",
                "DirectedEffectOperationRepository._reconcile_inventory_append",
            ),
            (
                "internal/directed_effect_operation.py",
                "DirectedEffectOperationRepository._reconcile_operation_append",
            ),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository.finalize_inventory"),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository.seal_inventory"),
        },
        "GuardedFactEventV1": {
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository._mutate"),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository.finalize_inventory"),
            ("internal/directed_effect_operation.py", "DirectedEffectOperationRepository.seal_inventory"),
        },
    }
    restricted_writer_constants = {
        "RECEIPT_COMMITTED",
        "RECOVERY_PENDING",
        "CLOSED_BY_PARENT",
        "DEAD_LETTER",
        "task_runtime.deo_parent_registry.v1.closed",
        "_PARENT_CLOSED_EVENT_TYPE",
    }
    observed = {target: set() for target in expected_owners}
    dynamic_writer_getattrs: list[tuple[str, str, str]] = []
    protected_sentinels: list[tuple[str, str, str]] = []
    analyses: dict[str, _SourceAnalysis] = {}
    for path in _production_python_files():
        relative_path = path.relative_to(cell_root).as_posix()
        current_module, current_is_package = _module_context_for_path(
            path,
            polaris_root=polaris_root,
        )
        analysis = _analyze_source(
            path.read_text(encoding="utf-8"),
            current_module=current_module,
            current_is_package=current_is_package,
            targets=_FACT_STREAM_WRITER_TARGETS | _REPOSITORY_FORBIDDEN_CALL_TARGETS,
            protected_objects={
                _FACT_STREAM_PUBLIC,
                f"{_FACT_STREAM_PUBLIC}.service",
                f"{_FACT_STREAM_PUBLIC}.contracts",
                _DEO_REPOSITORY,
            },
        )
        analyses[relative_path] = analysis
        for reference in analysis.references:
            if reference.kind in {
                "protected_star_import",
                "unresolved_protected_assignment",
            }:
                protected_sentinels.append((relative_path, reference.owner, reference.kind))
                continue
            if reference.kind == "dynamic_getattr" and reference.target.startswith(_FACT_STREAM_PUBLIC):
                dynamic_writer_getattrs.append((relative_path, reference.owner, reference.target))
                continue
            if reference.target not in _FACT_STREAM_WRITER_TARGETS:
                continue
            target = reference.target.rsplit(".", maxsplit=1)[-1]
            observed[target].add((relative_path, reference.owner))

    assert observed == expected_owners
    assert dynamic_writer_getattrs == []
    assert protected_sentinels == []
    for relative_path, analysis in analyses.items():
        assert (
            _writer_taint_violations(
                analysis,
                restricted_states=restricted_writer_constants,
            )
            == ()
        ), relative_path

    repository_analysis = analyses["internal/directed_effect_operation.py"]
    allowed_guarded_owners = {
        owner
        for path, owner in expected_owners["append_if_guarded_snapshot"]
        if path == "internal/directed_effect_operation.py"
    }
    for root in ("seal_inventory", "finalize_inventory", "_mutate"):
        reached = _reachable_repository_methods(root)
        guarded_owners = {
            reference.owner
            for reference in repository_analysis.references
            if reference.owner in reached
            and reference.kind == "call"
            and reference.target.rsplit(".", maxsplit=1)[-1] == "append_if_guarded_snapshot"
        }
        assert (
            _forbidden_closure_references(
                repository_analysis,
                owners=reached,
            )
            == ()
        ), root
        assert (
            _writer_taint_violations(
                repository_analysis,
                restricted_states=restricted_writer_constants,
                owners=reached,
            )
            == ()
        ), root
        assert guarded_owners <= allowed_guarded_owners, root


def test_inventory_facts_use_only_factstream_public_guarded_and_strict_api() -> None:
    tree = ast.parse(inspect.getsource(deo_internal))
    fact_stream_imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and "fact_stream" in (node.module or "")
    }
    assert fact_stream_imports == {"polaris.cells.events.fact_stream.public"}
    for writer in ("seal_inventory", "finalize_inventory"):
        writer_calls = set(_called_names(_method_tree(writer)))
        assert "_prepare_inventory_guarded_snapshot" in writer_calls
        assert "append_if_guarded_snapshot" in writer_calls
        assert "append_fact_event" not in writer_calls
    prepare_calls = set(_called_names(_method_tree("_prepare_inventory_guarded_snapshot")))
    assert "read_guarded_fact_snapshot" in prepare_calls
