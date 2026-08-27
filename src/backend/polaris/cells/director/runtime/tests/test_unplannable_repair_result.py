from __future__ import annotations

from polaris.cells.director.runtime.public import PlanDirectorRepairCommandV1
from polaris.cells.director.runtime.public.service import plan_director_repair


def test_registered_cpp_repair_without_effect_plan_reports_not_planned() -> None:
    """Exact L3-24 r88: metadata coverage must not become a silent false green."""

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_cpp_post_repair",
            base_files={"src/diary_cli.cpp": '#include "diary_render.hpp"\nint main() { return 0; }\n'},
            artifact_quality_errors=(
                "src/diary_cli.cpp:11:10: fatal error: diary_render.hpp: No such file or directory",
            ),
            mode="shadow",
        )
    )

    assert result.ok is False
    assert result.planned is False
    assert result.effect_plan is None
    assert result.error_code == "repair_not_planned"
    assert result.error_message == (
        "Registered repair source_tool='deterministic_cpp_post_repair' produced no effect plan."
    )
