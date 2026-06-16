"""H (2026-06-16 audit): emergency_truncate must GUARANTEE fit, never overflow.

The floor-trim loop floored every system plane at ``_SYSTEM_PLANE_FLOOR_CHARS``
(600) and then stopped — so many pinned planes on a small window could still
sum over budget. The gateway then raised ``BudgetExceededError``, aborting the
turn before any write (the #46 floor symptom: "Director barely ran / 0 files").
The fix drops whole lowest-priority planes (and, as a last resort, all of them)
so the assembly fits and the turn degrades instead of crashing — while always
preserving the final user turn.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.compression_engine import (
    CompressionEngine,
)
from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
    TokenEstimator,
)


def _engine() -> CompressionEngine:
    return CompressionEngine(
        max_context_tokens=100_000,
        compression_strategy="truncate",
        max_history_turns=10,
        token_estimator=TokenEstimator(),
        continuity_strategy=None,
        profile=None,
        workspace=None,
        reasoning_stripper=None,
    )


def _user_content(result: list[dict[str, object]]) -> str:
    return "".join(str(m.get("content") or "") for m in result if m.get("role") == "user")


class TestEmergencyTruncateGuaranteeFit:
    def test_many_floored_planes_still_fit_and_keep_user(self) -> None:
        engine = _engine()
        est = TokenEstimator()
        planes = [{"role": "system", "content": f"plane-{i} " + "x" * 2000} for i in range(12)]
        user = {"role": "user", "content": "do the task " + "y" * 500}
        result = engine.emergency_truncate([*planes, user], max_tokens=300)
        assert est.estimate(result) <= 300, "assembly must fit — gateway would otherwise raise"
        assert any(m.get("role") == "user" for m in result), "final user turn must survive"
        assert "do the task" in _user_content(result)

    def test_extreme_budget_drops_all_planes_keeps_user(self) -> None:
        engine = _engine()
        est = TokenEstimator()
        planes = [{"role": "system", "content": "x" * 5000} for _ in range(8)]
        user = {"role": "user", "content": "write the file"}
        result = engine.emergency_truncate([*planes, user], max_tokens=60)
        assert est.estimate(result) <= 60
        assert any(m.get("role") == "user" for m in result)

    def test_fitting_input_is_unchanged(self) -> None:
        engine = _engine()
        msgs = [
            {"role": "system", "content": "short system"},
            {"role": "user", "content": "hello"},
        ]
        result = engine.emergency_truncate(msgs, max_tokens=100_000)
        assert result == msgs

    @pytest.mark.parametrize("plane_count", [2, 5, 20, 64])
    def test_always_fits_across_plane_counts(self, plane_count: int) -> None:
        engine = _engine()
        est = TokenEstimator()
        planes = [{"role": "system", "content": "z" * 1500} for _ in range(plane_count)]
        user = {"role": "user", "content": "u" * 300}
        result = engine.emergency_truncate([*planes, user], max_tokens=200)
        assert est.estimate(result) <= 200, f"plane_count={plane_count}"
        assert any(m.get("role") == "user" for m in result)
