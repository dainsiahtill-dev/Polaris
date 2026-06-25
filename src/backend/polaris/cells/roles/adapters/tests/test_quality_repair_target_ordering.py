from __future__ import annotations

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
