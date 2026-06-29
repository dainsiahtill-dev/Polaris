from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.artifact_quality_diagnostics import (
    _missing_unresolved_relative_import_target_files,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _ordered_materialization_quality_repair_target_candidates,
)


def test_typescript_source_diagnostics_precede_generated_dist_targets() -> None:
    ordered = _ordered_materialization_quality_repair_target_candidates(
        missing_target_files=["dist/index.js"],
        runtime_smoke_target_files=[],
        semantic_quality_target_files=["src/web.ts", "src/models/Plane.ts"],
        explicit_quality_target_files=["src/web.ts"],
        should_merge_missing_targets=True,
    )

    assert ordered[:2] == ["src/web.ts", "src/models/Plane.ts"]
    assert ordered[-1] == "dist/index.js"


def test_typescript_unresolved_js_import_targets_source_ts_file(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.ts").write_text('import { Market } from "./models/Market.js";\n', encoding="utf-8")
    (src / "models").mkdir()

    targets = _missing_unresolved_relative_import_target_files(
        ["Artifact quality scan failed: unresolved relative import './models/Market.js' in src/main.ts"],
        str(tmp_path),
    )

    assert targets == ["src/models/Market.ts"]
