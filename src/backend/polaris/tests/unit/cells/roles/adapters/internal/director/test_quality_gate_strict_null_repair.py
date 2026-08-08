"""Round B amplification: materialization repair must guide weak Directors to a
buildable tsconfig when strict-null-check errors (TS18048/TS2322) dominate.

L1-01 m03-r18 (MiniMax-M3) produced ~521 TS18048/TS2322 strict-null errors
(``dewPoint?: number`` cascading). The brief does NOT mandate ``strict`` mode;
the CE model added it. A weak Director cannot make every nullable field
null-safe, so the build dead-letters. The repair re-prompt must advise relaxing
``tsconfig`` compiler strictness so the real product builds and runs. This is
system-side (Director guidance), not gauge-side (bench_gates unchanged).
"""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_repair_message,
)


def _msg(quality_errors: list[str]) -> str:
    return _build_materialization_quality_repair_message(
        original_message="implement the garden simulator",
        artifact_quality_errors=quality_errors,
        changed_files=["src/models/Humidity.ts"],
        missing_target_files=None,
        repair_target_files=["src/models/Humidity.ts"],
    )


def test_repair_message_adds_strict_null_relaxation_for_ts18048() -> None:
    msg = _msg(
        [
            "src/models/Humidity.ts(12,7): error TS18048: 'dewPoint' is possibly 'undefined'.",
            "src/models/Humidity.ts(20,9): error TS2322: Type 'number | undefined' is not assignable to type 'number'.",
        ]
    )
    assert "STRICT-NULL" in msg.upper() or "strictNullChecks" in msg
    assert "tsconfig" in msg.lower()


def test_repair_message_no_strict_null_guidance_when_absent() -> None:
    msg = _msg(["src/main.ts: some unrelated quality error (no type codes)"])
    assert "STRICT-NULL" not in msg.upper()
