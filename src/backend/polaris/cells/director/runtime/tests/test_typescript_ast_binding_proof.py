"""AST proof tests use the deployed grammar; doubles only exercise refusal paths."""

from __future__ import annotations

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import module_binding_proof as proof


def test_real_abi_unicode_spans_and_all_root_imports() -> None:
    source = (
        "// 汉😀\nimport Seed, { Foo as Local } from \"../lost.js\"; /* 中 */ import { Other } from '../lost.js';\n"
    )
    facts = proof.parse_module_binding_facts(source)
    assert facts is not None
    assert len(facts.imports) == 2
    assert all(item.supported for item in facts.imports)
    assert [(b.imported, b.local) for b in facts.imports[0].bindings] == [("default", "Seed"), ("Foo", "Local")]
    assert [source[slice(*item.specifier_span)] for item in facts.imports] == ["../lost.js", "../lost.js"]


@pytest.mark.parametrize("expression", ["class Hidden {}", "function Hidden() {}"])
def test_expression_private_name_and_type_shadow_never_prove_alias(expression: str) -> None:
    facts = proof.parse_module_binding_facts(
        f"interface Hidden {{}}\nconst Wrapper = (\n{expression}\n); export {{ Hidden as Foo }}; export default Hidden;"
    )
    assert facts is not None
    assert facts.runtime_exports == frozenset()


def test_root_values_aliases_default_and_multiple_variable_names() -> None:
    facts = proof.parse_module_binding_facts(
        "const Local = 1; export { Local as Foo }; export default Local; export const First = 1, Second = 2;"
    )
    assert facts is not None
    assert facts.runtime_exports == frozenset({"Foo", "default", "First", "Second"})


@pytest.mark.parametrize("source", ["export default class {}", "export default function() {}", "export default 7;"])
def test_direct_default_runtime_values(source: str) -> None:
    facts = proof.parse_module_binding_facts(source)
    assert facts is not None
    assert facts.runtime_exports == frozenset({"default"})


def test_comment_template_dynamic_import_and_nested_names_are_not_root_facts() -> None:
    source = '/*\nimport { Fake } from "../lost.js";\n*/\nconst text = `\nimport { Fake } from "../lost.js";\n`;\n'
    source += 'import { Real } from "../lost.js"; import("../dynamic.js"); namespace Nested { export const Fake=1; }'
    facts = proof.parse_module_binding_facts(source)
    assert facts is not None
    assert len(facts.imports) == 1
    assert facts.imports[0].bindings[0].imported == "Real"
    assert facts.runtime_exports == frozenset()


@pytest.mark.parametrize(
    "clause", ["* as Foo", "type { Foo }", "{ type Foo }", '{ "odd-name" as Foo }', "{ Foo, /* retained */ Missing }"]
)
def test_unsupported_statement_stays_visible_for_complete_proof(clause: str) -> None:
    facts = proof.parse_module_binding_facts(f'import {{ Foo }} from "../lost.js"; import {clause} from "../lost.js";')
    assert facts is not None
    assert len(facts.imports) == 2
    assert facts.imports[0].supported
    assert not facts.imports[1].supported


@pytest.mark.parametrize(
    "source", ["export const Foo = ;", 'import { Foo from "../lost.js";', "export function Foo( {}"]
)
def test_error_recovery_and_missing_nodes_refuse_source(source: str) -> None:
    assert proof.parse_module_binding_facts(source) is None


def test_missing_grammar_refuses_without_regex_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proof, "_get_ts_parser", lambda _language: None)
    assert proof.parse_module_binding_facts("export const Foo = 1;") is None


@pytest.mark.parametrize("clause", ["type { Foo }", "{ type Foo }"])
def test_type_only_fact_preserves_identity_but_cannot_prove_runtime(clause: str) -> None:
    facts = proof.parse_module_binding_facts(f'import {clause} from "../lost.js";')
    assert facts is not None
    assert not facts.imports[0].supported
    assert facts.imports[0].bindings[0].imported == "Foo"
    assert facts.imports[0].bindings[0].type_only is True


@pytest.mark.parametrize("failure", [KeyError("typescript"), OSError("grammar unavailable")])
def test_grammar_lookup_failure_refuses_without_effects(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    def unavailable(_language: str) -> object:
        raise failure

    monkeypatch.setattr(proof, "_get_ts_parser", unavailable)
    assert proof.parse_module_binding_facts("export const Foo=1;") is None


def test_incompatible_abi_refuses_without_regex_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenParser:
        def parse(self, _source: str) -> object:
            return object()

    monkeypatch.setattr(proof, "_get_ts_parser", lambda _language: lambda _name: BrokenParser())
    assert proof.parse_module_binding_facts("export const Foo = 1;") is None


@pytest.mark.parametrize("span", [(0, 1), (-1, 20), (0, 1000)])
def test_stale_or_non_utf8_boundary_spans_refuse(monkeypatch: pytest.MonkeyPatch, span: tuple[int, int]) -> None:
    monkeypatch.setattr(proof, "_ts_byte_span", lambda _node: span)
    assert proof.parse_module_binding_facts("// 中😀\nexport const Foo = 1;") is None


@pytest.mark.parametrize(
    "source",
    [
        "export interface Foo {}",
        "export type Foo = number;",
        "declare const Foo: number; export { Foo };",
        'export { Foo } from "./unknown";',
        "namespace N { export const Foo=1; }",
        "const Local=1; export default (Local);",
        "const Local=1; export type { Local as Foo };",
        "const Local=1; export { type Local as Foo };",
        "export const Foo$=1;",
    ],
)
def test_unproven_forms_never_advertise_requested_value(source: str) -> None:
    facts = proof.parse_module_binding_facts(source)
    assert facts is not None
    assert "Foo" not in facts.runtime_exports
    assert "default" not in facts.runtime_exports


@pytest.mark.parametrize(
    "source", ["export const enum Foo { Value }", "const enum Local { Value }; export { Local as Foo };"]
)
def test_erased_const_enum_cannot_prove_runtime_object(source: str) -> None:
    facts = proof.parse_module_binding_facts(source)
    assert facts is not None
    assert "Foo" not in facts.runtime_exports
