from polaris.cells.roles.runtime.internal.engineering_phase_policy import (
    EngineeringPhase,
    allowed_tools,
    phase_for,
)


def test_director_phase_policy_preserves_prepare_and_verify_channels() -> None:
    assert (
        phase_for(
            "director",
            verification="not_run",
            first_verification_due=True,
            preparation_pending=False,
            qa_report_required=False,
        )
        is EngineeringPhase.VERIFY
    )
    assert (
        phase_for(
            "director",
            verification="failed",
            first_verification_due=False,
            preparation_pending=True,
            qa_report_required=False,
        )
        is EngineeringPhase.PREPARE
    )
    assert (
        phase_for(
            "director",
            verification="failed",
            first_verification_due=False,
            preparation_pending=False,
            qa_report_required=False,
        )
        is EngineeringPhase.REPAIR
    )
    assert "execute_command" in (allowed_tools(EngineeringPhase.VERIFY) or frozenset())
    assert "write_file" not in (allowed_tools(EngineeringPhase.VERIFY) or frozenset())


def test_qa_report_phase_admits_only_structured_verdict() -> None:
    phase = phase_for(
        "qa", verification="passed", first_verification_due=False, preparation_pending=False, qa_report_required=True
    )
    assert phase is EngineeringPhase.REPORT
    assert allowed_tools(phase) == frozenset({"report_review"})
    assert (
        phase_for(
            "qa",
            verification="passed",
            first_verification_due=False,
            preparation_pending=False,
            qa_report_required=False,
        )
        is EngineeringPhase.QA_PROBE
    )
