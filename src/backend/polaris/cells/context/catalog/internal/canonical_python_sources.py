"""Read-only Python source binding and C3 resolution for declared descriptors.

This deliberately does not import or execute the inspected source. Only literal
imports/aliases, concrete class definitions and runtime TYPE_CHECKING branches
are resolved. Unknown required bindings fail before any descriptor is published.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem


class CanonicalSourceError(ValueError):
    """A required descriptor class or method cannot be proven from source."""


@dataclass(frozen=True)
class ResolvedMethod:
    name: str
    path: Path
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class ResolvedClassSurface:
    name: str
    path: Path
    node: ast.ClassDef
    methods: tuple[ResolvedMethod, ...]
    source_files: tuple[Path, ...]


@dataclass(frozen=True)
class _Reference:
    module: str
    name: str


@dataclass(frozen=True)
class _Module:
    name: str


@dataclass(frozen=True)
class _Attribute:
    owner: object
    name: str


@dataclass(frozen=True)
class _Unavailable:
    reason: str


@dataclass(eq=False)
class _Class:
    module: str
    path: Path
    node: ast.ClassDef
    namespace: dict[str, object]


class CanonicalPythonSources:
    """Resolve only requested class surfaces, without treating ownership as API."""

    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root.resolve(strict=True)
        self._filesystem = KernelFileSystem(str(self.root), LocalFileSystemAdapter())
        self._scopes: dict[str, tuple[Path, dict[str, object]]] = {}
        self._used: set[Path] = set()

    def _module_path(self, module: str) -> Path:
        parts = module.split(".")
        if not module or not all(part.isidentifier() for part in parts):
            raise CanonicalSourceError(f"required module is invalid: {module!r}")
        directory = self.root
        selected = directory
        for index, part in enumerate(parts):
            stem = directory / part
            package, regular = stem / "__init__.py", stem.with_suffix(".py")
            if package.is_file():
                selected = package
            elif regular.is_file():
                if index != len(parts) - 1:
                    raise CanonicalSourceError(f"required module is shadowed by a concrete module: {module}")
                selected = regular
            elif stem.is_dir():
                selected = stem
            else:
                raise CanonicalSourceError(f"required module does not resolve: {module}")
            try:
                selected.resolve(strict=True).relative_to(self.root)
            except (OSError, RuntimeError, ValueError) as error:
                raise CanonicalSourceError(f"required module escapes source root: {module}") from error
            directory = stem
        return selected.resolve(strict=True)

    @staticmethod
    def _value(expression: ast.expr, namespace: dict[str, object]) -> object:
        if isinstance(expression, ast.Name):
            return namespace.get(expression.id, _Unavailable(f"unbound name {expression.id}"))
        if isinstance(expression, ast.Attribute):
            return _Attribute(CanonicalPythonSources._value(expression.value, namespace), expression.attr)
        if isinstance(expression, (ast.Tuple, ast.List)):
            values: list[object] = []
            for item in expression.elts:
                if isinstance(item, ast.Starred):
                    expanded = CanonicalPythonSources._value(item.value, namespace)
                    if not isinstance(expanded, tuple):
                        return _Unavailable("unknown unpacked expression")
                    values.extend(expanded)
                else:
                    values.append(CanonicalPythonSources._value(item, namespace))
            return tuple(values)
        if isinstance(expression, ast.Constant) and type(expression.value) is bool:
            return expression.value
        return _Unavailable("dynamic or non-class binding")

    @staticmethod
    def _condition(expression: ast.expr, namespace: dict[str, object]) -> bool | None:
        value = CanonicalPythonSources._value(expression, namespace)
        if type(value) is bool:
            return value
        if isinstance(value, _Attribute) and value.owner == _Module("typing") and value.name == "TYPE_CHECKING":
            return False
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            nested = CanonicalPythonSources._condition(expression.operand, namespace)
            return None if nested is None else not nested
        return None

    def _assign_target(self, target: ast.expr, value: object, namespace: dict[str, object]) -> None:
        """Bind every unpacking leaf; unknown values invalidate previous bindings."""
        if isinstance(target, ast.Name):
            namespace[target.id] = value
        elif isinstance(target, ast.Starred):
            self._assign_target(target.value, value, namespace)
        elif isinstance(target, (ast.Tuple, ast.List)):
            stars = [index for index, item in enumerate(target.elts) if isinstance(item, ast.Starred)]
            if isinstance(value, tuple) and not stars and len(value) == len(target.elts):
                for item, member in zip(target.elts, value, strict=True):
                    self._assign_target(item, member, namespace)
            elif isinstance(value, tuple) and len(stars) == 1 and len(value) >= len(target.elts) - 1:
                index = stars[0]
                trailing = len(target.elts) - index - 1
                for position, item in enumerate(target.elts):
                    member = (
                        value[position]
                        if position < index
                        else value[len(value) - (len(target.elts) - position)]
                        if position > index
                        else value[index : len(value) - trailing if trailing else len(value)]
                    )
                    self._assign_target(item, member, namespace)
            else:
                for item in target.elts:
                    self._assign_target(item, _Unavailable("unknown or mismatched unpacking binding"), namespace)

    def _delete_target(self, target: ast.expr, namespace: dict[str, object], *, strict_local: bool = False) -> None:
        if isinstance(target, ast.Name):
            if target.id in namespace:
                del namespace[target.id]
            elif strict_local:
                # Class DELETE_NAME consults class locals, never globals/MRO.
                # An invalid deletion aborts construction and cannot be undone
                # by a second deletion or inherit a formerly visible function.
                raise CanonicalSourceError(f"required class deletion of unbound local binding: {target.id}")
            else:
                namespace[target.id] = _Unavailable("deletion of an unbound local binding")
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._delete_target(item, namespace, strict_local=strict_local)

    def _bind(
        self,
        nodes: list[ast.stmt],
        module: str,
        path: Path,
        namespace: dict[str, object],
        fallback: dict[str, object] | None = None,
    ) -> None:
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in nodes:
            lookup = {**(fallback or {}), **namespace}
            if isinstance(node, ast.ImportFrom):
                try:
                    target = (
                        resolve_name("." * node.level + (node.module or ""), package)
                        if node.level
                        else node.module or ""
                    )
                except (ImportError, ValueError) as error:
                    raise CanonicalSourceError(f"required relative import does not resolve in {module}") from error
                for alias in node.names:
                    name = alias.asname or alias.name
                    namespace[name] = (
                        False
                        if target == "typing" and alias.name == "TYPE_CHECKING"
                        else _Reference(target, alias.name)
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    namespace[alias.asname or alias.name.split(".")[0]] = _Module(
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(node, ast.ClassDef):
                namespace[node.name] = _Class(module, path, node, lookup)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                namespace[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Annotation-only statements have no runtime binding effect.
                if node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = self._value(node.value, lookup)
                for binding_target in targets:
                    self._assign_target(binding_target, value, namespace)
            elif isinstance(node, ast.Delete):
                for deletion_target in node.targets:
                    self._delete_target(deletion_target, namespace, strict_local=fallback is not None)
            elif isinstance(node, ast.AugAssign):
                self._assign_target(node.target, _Unavailable("unknown augmented binding"), namespace)
            elif isinstance(node, ast.If):
                condition = self._condition(node.test, lookup)
                if condition is not None:
                    self._bind(node.body if condition else node.orelse, module, path, namespace, fallback)
                else:
                    before = dict(namespace)
                    changed: set[str] = set()
                    for branch in (node.body, node.orelse):
                        outcome = dict(before)
                        self._bind(branch, module, path, outcome, fallback)
                        changed.update(
                            name
                            for name in before.keys() | outcome.keys()
                            if name not in before or name not in outcome or before[name] is not outcome[name]
                        )
                    namespace.update({name: _Unavailable("unknown runtime conditional") for name in changed})

    def _scope(self, module: str) -> dict[str, object]:
        if module not in self._scopes:
            path = self._module_path(module)
            namespace: dict[str, object] = {}
            if path.is_file():
                try:
                    text = self._filesystem.workspace_read_text(str(path), encoding="utf-8")
                    tree = ast.parse(text, filename=str(path))
                except (OSError, SyntaxError, UnicodeError) as error:
                    raise CanonicalSourceError(f"required source cannot be parsed: {module}: {error}") from error
                self._bind(tree.body, module, path, namespace)
            self._scopes[module] = path, namespace
        path, namespace = self._scopes[module]
        if path.is_file():
            self._used.add(path)
        return namespace

    def _symbol(self, module: str, name: str, trail: frozenset[tuple[str, str]]) -> object:
        key = module, name
        if key in trail:
            raise CanonicalSourceError(f"required source alias cycle: {module}.{name}")
        namespace = self._scope(module)
        if name not in namespace:
            try:
                self._module_path(module + "." + name)
            except CanonicalSourceError as error:
                raise CanonicalSourceError(f"required class/base symbol does not resolve: {module}.{name}") from error
            return _Module(module + "." + name)
        return self._follow(namespace[name], trail | {key})

    def _follow(self, value: object, trail: frozenset[tuple[str, str]]) -> object:
        if isinstance(value, _Reference):
            return self._symbol(value.module, value.name, trail)
        if isinstance(value, _Attribute):
            owner = self._follow(value.owner, trail)
            if not isinstance(owner, _Module):
                raise CanonicalSourceError("required attribute has no static module binding")
            namespace = self._scope(owner.name)
            if value.name in namespace:
                return self._symbol(owner.name, value.name, trail)
            self._module_path(owner.name + "." + value.name)
            return _Module(owner.name + "." + value.name)
        if isinstance(value, _Unavailable):
            raise CanonicalSourceError(f"required source binding is unresolved: {value.reason}")
        return value

    def _mro(self, cls: _Class, trail: frozenset[_Class], cache: dict[_Class, list[_Class]]) -> list[_Class]:
        if cls in trail:
            raise CanonicalSourceError(f"required class inheritance cycle: {cls.module}.{cls.node.name}")
        if cls in cache:
            return cache[cls]
        bases: list[_Class] = []
        for expression in cls.node.bases:
            if isinstance(expression, ast.Name) and expression.id == "object" and "object" not in cls.namespace:
                continue
            resolved = self._follow(self._value(expression, cls.namespace), frozenset())
            if not isinstance(resolved, _Class):
                raise CanonicalSourceError(f"required base is not a concrete class: {cls.module}.{cls.node.name}")
            bases.append(resolved)
        sequences = [list(self._mro(base, trail | {cls}, cache)) for base in bases] + [list(bases)]
        ordered = [cls]
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            candidate = next(
                (sequence[0] for sequence in sequences if not any(sequence[0] in other[1:] for other in sequences)),
                None,
            )
            if candidate is None:
                raise CanonicalSourceError(f"required class has inconsistent C3 MRO: {cls.module}.{cls.node.name}")
            ordered.append(candidate)
            for sequence in sequences:
                if sequence[0] is candidate:
                    sequence.pop(0)
        cache[cls] = ordered
        return ordered

    def resolve_class(self, module: str, name: str, methods: tuple[str, ...]) -> ResolvedClassSurface:
        """Resolve every explicitly required method or reject the entire surface."""
        if not name.isidentifier() or not methods or any(not method.isidentifier() for method in methods):
            raise CanonicalSourceError(f"required descriptor class/method names are invalid: {name}")
        self._used = set()
        resolved = self._symbol(module, name, frozenset())
        if not isinstance(resolved, _Class):
            raise CanonicalSourceError(f"required symbol is not a concrete class: {module}.{name}")
        ordered = self._mro(resolved, frozenset(), {})
        selected: list[ResolvedMethod] = []
        for method in methods:
            for cls in ordered:
                namespace: dict[str, object] = {}
                self._bind(cls.node.body, cls.module, cls.path, namespace, cls.namespace)
                if method not in namespace:
                    continue
                member = namespace[method]
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    raise CanonicalSourceError(f"required method is shadowed or unresolved: {module}.{name}.{method}")
                selected.append(ResolvedMethod(method, cls.path, member))
                break
            else:
                raise CanonicalSourceError(f"required method does not resolve: {module}.{name}.{method}")
        files = self._used | {cls.path for cls in ordered}
        return ResolvedClassSurface(name, resolved.path, resolved.node, tuple(selected), tuple(sorted(files)))
