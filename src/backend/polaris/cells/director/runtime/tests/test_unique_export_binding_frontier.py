"""Public shadow routing must preserve every used binding and real export evidence."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    normalize_director_repair_diagnostics,
    normalize_director_repair_issue_diagnostics,
    plan_director_repair,
    query_director_repair_materialization_plan_probe,
)

SOURCE_TOOL = "deterministic_typescript_unique_export_import_repair"
IMPORTER = "tests/current.ts"
ERROR = "unresolved relative import '../dist/missing.js' in tests/current.ts"


def _check_public_routes(
    clause: str, usage: str, candidates: dict[str, str], *, plannable: bool, typed: bool = False
) -> None:
    content = f'import {clause} from "../dist/missing.js";\nconsole.log({usage});\n'
    files = {IMPORTER: content, "tests/stale.ts": content, **candidates}
    issue = {
        "source": "artifact_quality",
        "code": "unresolved_relative_import",
        "message": "Relative import target is unresolved.",
        "path": IMPORTER,
        "metadata": {"specifier": "../dist/missing.js"},
        "raw": "unresolved relative import '../dist/missing.js' in tests/stale.ts",
    }
    diagnostics = (
        normalize_director_repair_issue_diagnostics((issue,))
        if typed
        else normalize_director_repair_diagnostics((ERROR,))
    )
    command = PlanDirectorRepairCommandV1(
        source_tool=SOURCE_TOOL, base_files=files, diagnostics=diagnostics, mode="shadow"
    )
    query = QueryDirectorRepairMaterializationPlanProbeV1(
        artifact_quality_errors=() if typed else (ERROR,),
        artifact_quality_issues=(issue,) if typed else (),
        base_files=files,
        source_tools=(SOURCE_TOOL,),
        mode="shadow",
    )
    before = deepcopy((command, query))
    direct = plan_director_repair(command)
    probe = query_director_repair_materialization_plan_probe(query)
    assert (command, query) == before
    assert direct.error_code == (None if plannable else "repair_not_planned")
    summary = direct.composition_summary.to_dict()
    assert summary["patch_count"] == int(plannable)
    assert (SOURCE_TOOL in probe.plannable_source_tools) is plannable
    assert probe.plan_probe_result is not None
    selected = next(item for item in probe.plan_probe_result.items if item.source_tool == SOURCE_TOOL)
    assert selected.patch_count == int(plannable)
    if plannable:
        expected = content.replace("../dist/missing.js", "../src/candidate.ts")
        assert summary["changed_paths"] == [IMPORTER]
        assert summary["patches"][0]["content_after"] == expected
        assert selected.planning_result.composition_summary.to_dict()["patches"][0]["content_after"] == expected
    else:
        assert summary["changed_paths"] == []
        assert probe.status != "covered_plannable"


def test_used_default_binding_without_default_export_refuses_whole_import_rewrite() -> None:
    _check_public_routes(
        "DefaultThing, { Foo }", "DefaultThing, Foo", {"src/candidate.ts": "export const Foo = 1;\n"}, plannable=False
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "/* export const Foo = 1; */\n",
        "// export const Foo = 1;\n",
        'const text = "export const Foo = 1;";\n',
        "const text = 'export const Foo = 1;';\n",
        "const text = `export const Foo = 1;`;\n",
        "const text = `line one\nexport const Foo = 1;\n`;\n",
        "const text = `export { Local as Foo };`;\n",
    ],
)
def test_export_shaped_non_code_refuses_candidate(candidate: str) -> None:
    _check_public_routes("{ Foo }", "Foo", {"src/candidate.ts": candidate}, plannable=False)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    ("clause", "usage", "candidate"),
    [
        ("{ Foo }", "Foo", "export const Foo = 1;\n"),
        ("{ Foo as Renamed }", "Renamed", "const Local = 1; export { Local as Foo };\n"),
        ("DefaultThing, { Foo }", "DefaultThing, Foo", "export const Foo = 1; export default function Thing() {}\n"),
        (
            "DefaultThing, { Foo }",
            "DefaultThing, Foo",
            "const Local = 1; const Thing = 2; export { Local as Foo, Thing as default };\n",
        ),
        ("DefaultThing", "DefaultThing", "export default class Thing {}\n"),
    ],
)
def test_real_exports_preserve_bindings_and_canonical_importer(
    clause: str, usage: str, candidate: str, typed: bool
) -> None:
    _check_public_routes(clause, usage, {"src/candidate.ts": candidate}, plannable=True, typed=typed)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_or_ambiguous_candidate_refuses_proposal(ambiguous: bool) -> None:
    candidates = {"src/candidate.ts": "export const Other = 1;\n"}
    if ambiguous:
        candidates = {"src/candidate.ts": "export const Foo = 1;\n", "src/other.ts": "export const Foo = 2;\n"}
    _check_public_routes("{ Foo }", "Foo", candidates, plannable=False)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    ("clause", "usage", "candidate"),
    [
        ("$DefaultThing, { Foo }", "$DefaultThing, Foo", "export const Foo = 1;\n"),
        ("{ Foo }", "Foo", "const rx = /export const Foo = 1/;\n"),
        ("{ Foo }", "Foo", "const text = `before ${`export const Foo = 1`} after`;\n"),
    ],
    ids=["dollar-default", "regex-literal", "nested-template"],
)
def test_review_residual_refuses_unproven_whole_import_rewrite(
    clause: str, usage: str, candidate: str, typed: bool
) -> None:
    _check_public_routes(clause, usage, {"src/candidate.ts": candidate}, plannable=False, typed=typed)


@pytest.mark.parametrize("typed", [False, True])
def test_review_residual_real_dollar_default_export_preserves_bindings(typed: bool) -> None:
    _check_public_routes(
        "$DefaultThing, { Foo }",
        "$DefaultThing, Foo",
        {"src/candidate.ts": "export const Foo = 1; export default function Thing() {}\n"},
        plannable=True,
        typed=typed,
    )


# Identifier-end residual: a declaration must prove the entire imported name.
@pytest.mark.parametrize("typed", [False, True])
def test_identifier_end_dollar_suffix_refuses_prefix_export(typed: bool) -> None:
    _check_public_routes(
        "{ Foo }", "Foo", {"src/candidate.ts": "export const Foo$ = 1;\n"}, plannable=False, typed=typed
    )


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(("clause", "usage"), [("{ Foo$ }", "Foo$"), ("{ Foo$ as Renamed }", "Renamed")])
def test_identifier_end_exact_dollar_export_preserves_named_and_alias_binding(
    clause: str, usage: str, typed: bool
) -> None:
    _check_public_routes(clause, usage, {"src/candidate.ts": "export const Foo$ = 1;\n"}, plannable=True, typed=typed)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "clause",
    [
        "{ Foo, /* preserve */ Missing }",
        "{ Foo, // preserve\n Missing }",
        '{ Foo, "string-name" as Missing }',
        "{ Foo, 'string-name' as Missing }",
        "{ Foo, Missing\\u0024 }",
        "{ Foo, Missing\u200c }",
        "{ Foo, type Missing }",
        "type { Foo }",
        "DefaultThing, * as Missing",
        "{ Foo,, Missing }",
        "{ Foo, Missing AS Alias }",
    ],
    ids=[
        "E1-block-comment",
        "E1-line-comment",
        "E2-double-quoted",
        "E2-single-quoted",
        "escaped-import",
        "joiner-import",
        "inline-type",
        "whole-type",
        "namespace",
        "empty-middle",
        "uppercase-as",
    ],
)
def test_incomplete_or_unsupported_clause_refuses_entire_public_proposal(clause: str, typed: bool) -> None:
    # Dropping any unparsed part would falsely prove Foo while preserving Missing.
    _check_public_routes(
        clause,
        "Foo",
        {"src/candidate.ts": "export const Foo = 1; export default 1;\n"},
        plannable=False,
        typed=typed,
    )


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("suffix", [r"\u0024", r"\u{24}", "\u200c", "\u200d", "\u0301"])
def test_e3_complete_identifier_never_proves_prefix(suffix: str, typed: bool) -> None:
    _check_public_routes(
        "{ Foo }",
        "Foo",
        {"src/candidate.ts": f"export const Foo{suffix} = 1;\n"},
        plannable=False,
        typed=typed,
    )


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "candidate",
    [
        "export interface Foo { value: number }\n",
        "export type Foo = number;\n",
        "interface Foo { value: number }; export { Foo };\n",
        "type Local = number; export { Local as Foo };\n",
        "const Local = 1; export type { Local as Foo };\n",
        "const Local = 1; export { type Local as Foo };\n",
        "namespace Nested { const Local = 1; }; export { Local as Foo };\n",
        'export { Foo } from "./unknown";\n',
        "export default interface Foo { value: number }\n",
        "interface Local { value: number }; export default Local;\n",
        "declare const Local: number; export { Local as Foo };\n",
        "export default class$;\n",
        "export default function$;\n",
        "export default true$;\n",
        "interface Foo { value: number }; export default (Foo);\n",
    ],
    ids=[
        "E4-interface",
        "E4-type",
        "E4-interface-list",
        "E4-type-alias-list",
        "E4-export-type-list",
        "E4-inline-type-list",
        "nested-local-alias",
        "unproven-reexport",
        "E4-default-interface",
        "E4-default-type-reference",
        "E4-ambient-alias",
        "default-class-prefix",
        "default-function-prefix",
        "default-literal-prefix",
        "default-parenthesized-type",
    ],
)
def test_e4_runtime_import_requires_value_export(candidate: str, typed: bool) -> None:
    clause, usage = ("DefaultThing", "DefaultThing") if "export default" in candidate else ("{ Foo }", "Foo")
    _check_public_routes(clause, usage, {"src/candidate.ts": candidate}, plannable=False, typed=typed)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    ("clause", "usage", "candidate"),
    [
        ("{ Foo, Unused }", "Foo", "export const Foo = 1; export const Unused = 2;\n"),
        ("{ Foo as Renamed, Unused, }", "Renamed", "export const Foo = 1; export let Unused = 2;\n"),
        ("DefaultThing,\n{ Foo }", "DefaultThing, Foo", "export const Foo = 1; export default function Thing() {}\n"),
        ("{ Foo }", "Foo", "export function Foo() {}\n"),
        ("{ Foo }", "Foo", "export class Foo {}\n"),
        ("{ Foo }", "Foo", "export enum Foo { Value }\n"),
        ("{ Foo }", "Foo", "const Local$ = 1; export { Local$ as Foo };\n"),
        ("DefaultThing", "DefaultThing", "const Local = 1; export default Local;\n"),
    ],
)
def test_supported_complete_value_bindings_remain_available(
    clause: str, usage: str, candidate: str, typed: bool
) -> None:
    _check_public_routes(clause, usage, {"src/candidate.ts": candidate}, plannable=True, typed=typed)


def _check_ast_public_content(content: str, candidate: str, expected: str | None, typed: bool) -> None:
    """Exercise both public projections, including exact multi-statement content."""
    files = {IMPORTER: content, "tests/stale.ts": content, "src/candidate.ts": candidate}
    issue = {
        "source": "artifact_quality",
        "code": "unresolved_relative_import",
        "path": IMPORTER,
        "message": "Unresolved target.",
        "metadata": {"specifier": "../dist/missing.js"},
        "raw": "unresolved relative import '../dist/missing.js' in tests/stale.ts",
    }
    diagnostics = (
        normalize_director_repair_issue_diagnostics((issue,))
        if typed
        else normalize_director_repair_diagnostics((ERROR,))
    )
    command = PlanDirectorRepairCommandV1(
        source_tool=SOURCE_TOOL, base_files=files, diagnostics=diagnostics, mode="shadow"
    )
    query = QueryDirectorRepairMaterializationPlanProbeV1(
        artifact_quality_errors=() if typed else (ERROR,),
        artifact_quality_issues=(issue,) if typed else (),
        base_files=files,
        source_tools=(SOURCE_TOOL,),
        mode="shadow",
    )
    before = deepcopy((files, command, query))
    direct = plan_director_repair(command)
    probe = query_director_repair_materialization_plan_probe(query)
    assert (files, command, query) == before
    assert direct.planned is (expected is not None)
    assert direct.error_code == (None if expected is not None else "repair_not_planned")
    assert (SOURCE_TOOL in probe.plannable_source_tools) is (expected is not None)
    summary = direct.composition_summary.to_dict()
    assert summary["changed_paths"] == ([IMPORTER] if expected is not None else [])
    if expected is None:
        assert summary["patch_count"] == 0
        assert probe.status != "covered_plannable"
    else:
        assert summary["patch_count"] == 1
        assert summary["patches"][0]["content_after"] == expected
        assert probe.plan_probe_result is not None
        selected = next(item for item in probe.plan_probe_result.items if item.source_tool == SOURCE_TOOL)
        assert selected.planning_result.composition_summary.to_dict()["patches"][0]["content_after"] == expected


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("expression", ["class Hidden {}", "function Hidden() {}"])
@pytest.mark.parametrize("shadow", ["", "interface Hidden {}\n", "type Hidden = number;\n"])
@pytest.mark.parametrize("default", [False, True])
def test_ast_scope_expression_name_cannot_prove_module_value(
    expression: str, shadow: str, default: bool, typed: bool
) -> None:
    candidate = shadow + f"const Wrapper = (\n{expression}\n); "
    candidate += "export default Hidden;\n" if default else "export { Hidden as Foo };\n"
    content = 'import Seed from "../dist/missing.js";\n' if default else 'import { Foo } from "../dist/missing.js";\n'
    _check_ast_public_content(content, candidate, None, typed)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("separator", ["\n", " ", "\n// 中😀\n"])
def test_ast_multi_all_real_specifiers_rewritten(separator: str, typed: bool) -> None:
    content = '// 汉😀\nimport Seed, { Foo as Local } from "../dist/missing.js";' + separator
    content += "import { Second, Unused } from '../dist/missing.js';\nconsole.log(Seed,Local,Second);\n"
    candidate = "export const Foo = 1; export const Second = 2; export const Unused = 3; export default 4;\n"
    _check_ast_public_content(content, candidate, content.replace("../dist/missing.js", "../src/candidate.ts"), typed)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "second", ["{ Missing }", '{ Foo, "odd-name" as Missing }', "* as Missing", "type { Foo }", "{ type Foo }"]
)
def test_ast_multi_second_unproven_binding_refuses_whole_proposal(second: str, typed: bool) -> None:
    content = 'import { Foo } from "../dist/missing.js";\n'
    content += f'import {second} from "../dist/missing.js";\n'
    _check_ast_public_content(content, "export const Foo = 1;\n", None, typed)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("template", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_ast_noncode_import_is_untouched_and_real_import_is_proved(template: bool, valid: bool, typed: bool) -> None:
    fake = 'import { Foo } from "../dist/missing.js";\n'
    prefix = "const text = `\n" + fake + "`;\n" if template else "/*\n" + fake + "*/\n"
    real = 'import { Foo } from "../dist/missing.js";\n' if valid else 'import { Missing } from "../dist/missing.js";\n'
    expected = prefix + real.replace("../dist/missing.js", "../src/candidate.ts") if valid else None
    _check_ast_public_content(prefix + real, "export const Foo = 1;\n", expected, typed)


@pytest.mark.parametrize("typed", [False, True])
def test_ast_multi_intervening_unicode_template_and_unrelated_path_are_preserved(typed: bool) -> None:
    first = 'import { Foo } from "../dist/missing.js";\n'
    middle = 'const note = `中😀\nimport { Fake } from "../dist/missing.js";\n`;\n'
    middle += 'import { Other } from "./other.js";\n'
    last = "import Seed from '../dist/missing.js';\nconsole.log(Foo,Seed,note,Other);\n"
    expected = first.replace("../dist/missing.js", "../src/candidate.ts")
    expected += middle + last.replace("../dist/missing.js", "../src/candidate.ts")
    _check_ast_public_content(first + middle + last, "export const Foo = 1; export default 2;\n", expected, typed)
