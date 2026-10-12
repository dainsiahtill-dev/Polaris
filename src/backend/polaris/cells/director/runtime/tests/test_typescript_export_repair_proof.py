"""Missing-export repair must use current bindings, not name resemblance."""

import pytest
from polaris.cells.director.runtime.public.contracts import QueryDirectorRepairPlanProbeV1
from polaris.cells.director.runtime.public.service import query_director_repair_plan_probe


@pytest.mark.parametrize("case", ["similar_error_factory", "already_exported", "stale_import_edge"])
def test_public_missing_export_probe_declines_unproven_or_stale_alias(case: str) -> None:
    producer = "export function createWidgetError(value: number): Error { return new Error(`invalid ${value}`); }\n"
    importer = "import { createWidget } from './domain.js';\nexport const widget = createWidget(1);\n"
    if case == "already_exported":
        producer += "export { createWidgetError as createWidget };\n"
    elif case == "stale_import_edge":
        importer = "import { createWidget } from './runtime.js';\nexport const widget = createWidget(1);\n"
    files = {
        "src/main.ts": importer,
        "src/domain.ts": producer,
    }
    if case == "stale_import_edge":
        files["src/runtime.ts"] = "export function createWidget(value: number): number { return value * 2; }\n"
    # The current source already points elsewhere or proves an export; stale
    # feedback must never create another alias on the former source module.
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            ("src/main.ts(1,10): error TS2305: Module '\"./domain.js\"' has no exported member 'createWidget'.",),
            base_files=files,
            source_tools=("deterministic_typescript_missing_export_repair",),
        )
    )
    assert probe.plannable_source_tools == ()
    assert probe.status == "coverage_matched_but_unplannable"
