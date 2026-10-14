"""Quality repair and semantic QA use distinct bounded grants on one live stage.

These are real admission/port guards; source resolver/fact stream are the existing
unit seams. Native HTTP capture is covered by the separate producer integration.
"""

from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
    FactoryRoleEvidenceAuthorityError,
)
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import _authority


@pytest.mark.parametrize("first_role", ["director", "qa"])
def test_quality_roles_have_independent_grant_slots(tmp_path: Path, first_role: str) -> None:
    port, _admission, _resolver, _facts = _authority(tmp_path=tmp_path, stage="quality_gate")
    other_role = "qa" if first_role == "director" else "director"
    first = port.mint_authority_binding(first_role)
    other = port.mint_authority_binding(other_role)
    assert (first.role, other.role) == (first_role, other_role)
    assert first.execution_authority_hash != other.execution_authority_hash
    assert first.attempt_budget == other.attempt_budget == FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET
    for role in ("director",):
        with pytest.raises(FactoryRoleEvidenceAuthorityError, match="cardinality_exceeded"):
            port.mint_authority_binding(role)
        with pytest.raises(FactoryRoleEvidenceAuthorityError, match="cardinality_exceeded"):
            port.require_grant_capacity(role, 1)


@pytest.mark.parametrize("role", ["pm", "architect", "chief_engineer"])
def test_quality_stage_rejects_other_roles(tmp_path: Path, role: str) -> None:
    port, _admission, _resolver, _facts = _authority(tmp_path=tmp_path, stage="quality_gate")
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="stage_role_mismatch"):
        port.mint_authority_binding(role)


def test_revocation_never_frees_quality_slot(tmp_path: Path) -> None:
    port, _admission, _resolver, _facts = _authority(tmp_path=tmp_path, stage="quality_gate")
    director = port.mint_authority_binding("director")
    port.revoke_authority_binding(director)
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="cardinality_exceeded"):
        port.mint_authority_binding("director")
    assert port.mint_authority_binding("qa").role == "qa"


def test_closed_quality_port_never_mints_either_role(tmp_path: Path) -> None:
    port, _admission, _resolver, _facts = _authority(tmp_path=tmp_path, stage="quality_gate")
    port.close_authority()
    for role in ("director", "qa"):
        with pytest.raises(FactoryRoleEvidenceAuthorityError, match="authority_closed"):
            port.mint_authority_binding(role)


def test_quality_qa_children_get_fresh_grants_without_refunding_retired_slots(tmp_path: Path) -> None:
    port, *_ = _authority(tmp_path=tmp_path, stage="quality_gate")
    port.limit_quality_qa_children(4)
    grants = [port.mint_authority_binding("qa") for _ in range(4)]
    assert len({g.execution_authority_hash for g in grants}) == 4
    assert all(port._grants[g.execution_authority_hash].revoked for g in grants[:-1])
    assert not port._grants[grants[-1].execution_authority_hash].revoked
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="cardinality_exceeded"):
        port.mint_authority_binding("qa")
    assert len(port._grants) == 4


def test_quality_qa_mint_unsettled_child_has_no_effect(tmp_path: Path) -> None:
    from polaris.cells.factory.pipeline.tests.test_factory_physical_attempt_control import _command

    port, *_ = _authority(tmp_path=tmp_path, stage="quality_gate")
    first = port.mint_authority_binding("qa")
    control = first.physical_attempt_control_port
    control.reserve(
        _command(
            factory_run_id=first.factory_run_id, role="qa", execution_authority_hash=first.execution_authority_hash
        )
    )
    before = control.budget_state(first.execution_authority_hash)
    with pytest.raises(Exception, match="previous_child_unsettled"):
        port.mint_authority_binding("qa")
    assert control.budget_state(first.execution_authority_hash) == before
    assert not port._grants[first.execution_authority_hash].revoked
    assert len(port._grants) == 1


@pytest.mark.asyncio
async def test_no_source_diagnostic_does_not_require_or_mint_provider_authority():
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_03 import _Mixin03
    from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import _run

    class EmptyWorkspace(_Mixin03):
        def _workspace_quality_repair_changed_files(self):
            return []

        def _factory_role_evidence_cutoff_port(self, _ctx):
            raise AssertionError("no-source diagnostic must not mint or dispatch")

    results, summary = await EmptyWorkspace()._apply_workspace_quality_llm_repairs(
        run=_run(stage="quality_gate"), context={}, artifact_quality_errors=["missing source"], repair_attempt=1
    )
    assert results == [] and summary["attempted"] is False
    assert summary["reason"] == "no_workspace_source_files_for_repair"
