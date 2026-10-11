"""Read-only, conservative module binding proof over KernelOne's AST ABI.

Facts are per-source derived values, never execution or receipt authority. Only
module-root declarations can prove exports; descendants merely validate parsing
and spans. Unsupported imports remain visible so consumers cannot prove a subset.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.context.repo_intelligence.tags import (
    _get_ts_parser,
    _ts_byte_span,
    _ts_call,
    _ts_node_children,
    _ts_node_kind,
    _ts_root_node,
)

from .constants import _TS_IDENTIFIER_RE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportBindingFact:
    imported: str
    local: str
    type_only: bool


@dataclass(frozen=True)
class ImportStatementFact:
    specifier: str
    bindings: tuple[ImportBindingFact, ...]
    specifier_span: tuple[int, int]
    supported: bool


@dataclass(frozen=True)
class ModuleBindingFacts:
    imports: tuple[ImportStatementFact, ...]
    runtime_exports: frozenset[str]


class _SourceView:
    """Strict UTF-8 boundary map built once, with O(1) node span lookup."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.byte_to_char = {0: 0}
        offset = 0
        for index, character in enumerate(source, 1):
            offset += len(character.encode("utf-8"))
            self.byte_to_char[offset] = index

    def span(self, node: Any) -> tuple[int, int]:
        # KernelOne adapter supports both Rust methods and C properties. Require
        # actual accessors; its compatibility default (0, 0) is not proof.
        if getattr(node, "start_byte", None) is None or getattr(node, "end_byte", None) is None:
            raise ValueError("AST byte span accessor unavailable")
        start, end = _ts_byte_span(node)
        if start > end or start not in self.byte_to_char or end not in self.byte_to_char:
            raise ValueError("AST span outside UTF-8 source boundaries")
        return self.byte_to_char[start], self.byte_to_char[end]

    def text(self, node: Any) -> str:
        return self.source[slice(*self.span(node))]

    def identifier(self, node: Any) -> str:
        if node is None or _ts_node_kind(node) not in {"identifier", "type_identifier"}:
            return ""
        text = self.text(node)
        return text if _TS_IDENTIFIER_RE.fullmatch(text) else ""


def _validated_nodes(root: Any, view: _SourceView) -> tuple[Any, ...]:
    """Refuse incomplete ABI, recovery/missing nodes and inconsistent spans."""
    if root is None or _ts_node_kind(root) != "program":
        raise ValueError("AST module root unavailable")
    stack = [(root, (0, len(view.source)))]
    nodes = []
    while stack:
        node, parent_span = stack.pop()
        kind = _ts_node_kind(node)
        flags = [_ts_call(getattr(node, name, None)) for name in ("has_error", "is_error", "is_missing")]
        if not kind or any(type(flag) is not bool for flag in flags):
            raise ValueError("AST error/missing accessor unavailable")
        if kind == "ERROR" or any(flags):
            raise ValueError("AST contains error recovery or missing nodes")
        span = view.span(node)
        if span[0] < parent_span[0] or span[1] > parent_span[1] or (span[0] == span[1] and kind != "program"):
            raise ValueError("AST span is stale or inconsistent")
        if getattr(node, "children", None) is None and getattr(node, "child_count", None) is None:
            raise ValueError("AST child accessor unavailable")
        children = _ts_node_children(node)
        previous_end = span[0]
        for child in children:
            child_span = view.span(child)
            if child_span[0] < previous_end:
                raise ValueError("AST child spans overlap")
            previous_end = child_span[1]
        nodes.append(node)
        stack.extend((child, span) for child in reversed(children))
    return tuple(nodes)


def _binding_pair(node: Any, view: _SourceView, *, type_only: bool = False) -> tuple[str, str] | None:
    children = _ts_node_children(node)
    if type_only and children and _ts_node_kind(children[0]) == "type":
        children = children[1:]
    kinds = tuple(_ts_node_kind(child) for child in children)
    if kinds == ("identifier",):
        name = view.identifier(children[0])
        return (name, name) if name else None
    if kinds == ("identifier", "as", "identifier"):
        first, last = view.identifier(children[0]), view.identifier(children[-1])
        return (first, last) if first and last else None
    return None


def _import_fact(node: Any, view: _SourceView) -> ImportStatementFact:
    children = _ts_node_children(node)
    strings = [child for child in children if _ts_node_kind(child) == "string"]
    if len(strings) != 1:
        raise ValueError("Import source string cannot be identified")
    string = strings[0]
    start, end = view.span(string)
    raw = view.text(string)
    if len(raw) < 2 or raw[0] not in "\"'" or raw[-1] != raw[0]:
        raise ValueError("Import quote span cannot be verified")
    specifier = raw[1:-1]
    bindings: list[ImportBindingFact] = []
    kinds = tuple(_ts_node_kind(child) for child in children)
    whole_type = "type" in kinds
    supported = all(kind in {"import", "import_clause", "from", "string", ";"} for kind in kinds)
    supported = supported and "\\" not in specifier
    clauses = [child for child in children if _ts_node_kind(child) == "import_clause"]
    if len(clauses) != 1:
        supported = False
    else:
        for child in _ts_node_children(clauses[0]):
            kind = _ts_node_kind(child)
            if kind == "identifier":
                name = view.identifier(child)
                supported = supported and bool(name)
                bindings.append(ImportBindingFact("default", name, whole_type))
            elif kind == "named_imports":
                for item in _ts_node_children(child):
                    item_kind = _ts_node_kind(item)
                    if item_kind in {"{", "}", ","}:
                        continue
                    inline_type = any(_ts_node_kind(part) == "type" for part in _ts_node_children(item))
                    pair = _binding_pair(item, view, type_only=inline_type) if item_kind == "import_specifier" else None
                    if pair is None:
                        supported = False
                    else:
                        bindings.append(ImportBindingFact(*pair, whole_type or inline_type))
                        supported = supported and not inline_type
            elif kind != ",":
                supported = False
    locals_ = [binding.local for binding in bindings]
    supported = supported and bool(bindings) and len(set(locals_)) == len(locals_)
    return ImportStatementFact(specifier, tuple(bindings), (start + 1, end - 1), supported)


def _declaration_values(node: Any, view: _SourceView) -> tuple[str, ...]:
    kind = _ts_node_kind(node)
    if kind == "enum_declaration" and any(_ts_node_kind(child) == "const" for child in _ts_node_children(node)):
        return ()
    if kind in {"class_declaration", "function_declaration", "generator_function_declaration", "enum_declaration"}:
        name = view.identifier(node.child_by_field_name("name"))
        return (name,) if name else ()
    if kind in {"lexical_declaration", "variable_declaration"}:
        names = []
        for child in _ts_node_children(node):
            if _ts_node_kind(child) == "variable_declarator":
                name = view.identifier(child.child_by_field_name("name"))
                if not name:
                    return ()
                names.append(name)
        return tuple(names)
    return ()


def _runtime_exports(roots: list[Any], nodes: tuple[Any, ...], view: _SourceView) -> frozenset[str]:
    # Preserve the established conservative candidate subset. Identifier escapes,
    # templates and division are availability refusals, not language-invalidity.
    if any(_ts_node_kind(node) in {"template_string", "/", "/="} for node in nodes):
        return frozenset()
    identifiers = [node for node in nodes if _ts_node_kind(node) in {"identifier", "type_identifier"}]
    if any(not view.identifier(node) for node in identifiers):
        return frozenset()
    local_values: list[str] = []
    for root in roots:
        if _ts_node_kind(root) == "export_statement":
            children = _ts_node_children(root)
            # A named default expression is not a local declaration.
            for child in children:
                local_values.extend(_declaration_values(child, view))
        else:
            local_values.extend(_declaration_values(root, view))
    if len(local_values) != len(set(local_values)):
        return frozenset()
    values = frozenset(local_values)
    exports: list[str] = []
    for root in roots:
        if _ts_node_kind(root) != "export_statement":
            continue
        children = _ts_node_children(root)
        kinds = tuple(_ts_node_kind(child) for child in children)
        if any(kind in {"from", "type", "ambient_declaration", "comment"} for kind in kinds):
            continue
        if "default" in kinds:
            payload = [child for child in children if _ts_node_kind(child) not in {"export", "default", ";"}]
            if len(payload) != 1:
                continue
            value = payload[0]
            kind = _ts_node_kind(value)
            if kind in {
                "class",
                "class_declaration",
                "function_expression",
                "function_declaration",
                "number",
                "object",
                "array",
                "true",
                "false",
                "null",
            } or (kind == "identifier" and view.identifier(value) in values):
                exports.append("default")
            continue
        clauses = [child for child in children if _ts_node_kind(child) == "export_clause"]
        if clauses:
            pairs = []
            valid = True
            for item in _ts_node_children(clauses[0]):
                if _ts_node_kind(item) in {"{", "}", ","}:
                    continue
                pair = _binding_pair(item, view) if _ts_node_kind(item) == "export_specifier" else None
                if pair is None:
                    valid = False
                else:
                    pairs.append(pair)
            if valid:
                exports.extend(exported for local, exported in pairs if local in values)
        else:
            for child in children:
                exports.extend(_declaration_values(child, view))
    return frozenset(exports) if len(exports) == len(set(exports)) else frozenset()


def parse_module_binding_facts(source: str) -> ModuleBindingFacts | None:
    """Parse locally; refuse missing provider/ABI/errors without textual fallback."""
    try:
        provider = _get_ts_parser("typescript")
        if provider is None:
            logger.warning("TypeScript binding proof unavailable: no grammar provider")
            return None
        parser = provider("typescript")
        # KernelOne supports both provider ABIs; TypeError selects input ABI only.
        try:
            tree = parser.parse(source)
        except TypeError:
            tree = parser.parse(source.encode("utf-8"))
        view = _SourceView(source)
        root = _ts_root_node(tree)
        nodes = _validated_nodes(root, view)
        roots = _ts_node_children(root)
        imports = tuple(_import_fact(node, view) for node in roots if _ts_node_kind(node) == "import_statement")
        return ModuleBindingFacts(imports, _runtime_exports(roots, nodes, view))
    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        UnicodeError,
        LookupError,
        OSError,
    ) as exc:
        logger.warning("TypeScript binding proof refused: %s", exc)
        return None
