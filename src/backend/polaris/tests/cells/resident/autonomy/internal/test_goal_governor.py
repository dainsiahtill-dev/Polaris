"""Tests for polaris.cells.resident.autonomy.internal.goal_governor.

Mock strategies:
- ResidentStorage is fully mocked (all file I/O bypassed).
- GoalProposal/related models are used directly (no mocking needed).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from polaris.cells.resident.autonomy.internal.goal_governor import (
    GoalGovernor,
    _scope_from_evidence,
    _stable_goal_fingerprint,
)
from polaris.domain.models.resident import (
    CapabilityGraphSnapshot,
    GoalProposal,
    GoalStatus,
    GoalType,
    ImprovementProposal,
    ImprovementStatus,
    MetaInsight,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_goal(goal_id: str = "g1", status: GoalStatus = GoalStatus.PENDING, **kwargs) -> GoalProposal:
    return GoalProposal(
        goal_id=goal_id,
        goal_type=kwargs.get("goal_type", GoalType.MAINTENANCE),
        title=kwargs.get("title", "Title"),
        motivation=kwargs.get("motivation", ""),
        source=kwargs.get("source", "manual"),
        expected_value=kwargs.get("expected_value", 0.5),
        risk_score=kwargs.get("risk_score", 0.2),
        scope=kwargs.get("scope", []),
        budget=kwargs.get("budget", {"max_tasks": 2}),
        evidence_refs=kwargs.get("evidence_refs", []),
        status=status,
        fingerprint=kwargs.get("fingerprint", ""),
        derived_from=kwargs.get("derived_from", []),
    )


@pytest.fixture
def storage() -> MagicMock:
    return MagicMock(spec=["load_goals", "save_goals"])


@pytest.fixture
def governor(storage: MagicMock) -> GoalGovernor:
    return GoalGovernor(storage=storage)


# ---------------------------------------------------------------------------
# _stable_goal_fingerprint
# ---------------------------------------------------------------------------

class TestStableGoalFingerprint:
    def test_deterministic(self) -> None:
        fp1 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "Title", "src", ["a", "b"])
        fp2 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "Title", "src", ["a", "b"])
        assert fp1 == fp2

    def test_case_insensitive(self) -> None:
        fp1 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "Title", "Src", ["A", "B"])
        fp2 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "title", "src", ["a", "b"])
        assert fp1 == fp2

    def test_different_inputs_different_fp(self) -> None:
        fp1 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "A", "src", ["x"])
        fp2 = _stable_goal_fingerprint(GoalType.RELIABILITY, "A", "src", ["x"])
        assert fp1 != fp2

    def test_sorts_scope(self) -> None:
        fp1 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "T", "src", ["b", "a"])
        fp2 = _stable_goal_fingerprint(GoalType.MAINTENANCE, "T", "src", ["a", "b"])
        assert fp1 == fp2

    def test_empty_scope(self) -> None:
        fp = _stable_goal_fingerprint(GoalType.MAINTENANCE, "T", "src", [])
        assert isinstance(fp, str)
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# _scope_from_evidence
# ---------------------------------------------------------------------------

class TestScopeFromEvidence:
    def test_typical_paths(self) -> None:
        refs = ["src/backend/main.py", "docs/readme.md", "tests/unit/test_x.py"]
        scope = _scope_from_evidence(refs)
        assert "src/backend" in scope
        assert "docs/readme.md" in scope
        assert "tests/unit" in scope

    def test_windows_backslashes(self) -> None:
        refs = ["src\\backend\\main.py"]
        scope = _scope_from_evidence(refs)
        assert "src/backend" in scope

    def test_short_paths(self) -> None:
        refs = ["main.py", "docs"]
        scope = _scope_from_evidence(refs)
        assert "main.py" in scope
        assert "docs" in scope

    def test_empty_refs(self) -> None:
        assert _scope_from_evidence([]) == []

    def test_duplicates_removed(self) -> None:
        refs = ["src/backend/a.py", "src/backend/b.py"]
        scope = _scope_from_evidence(refs)
        assert scope == ["src/backend"]

    def test_max_six_items(self) -> None:
        refs = [f"mod{i}/file.py" for i in range(10)]
        scope = _scope_from_evidence(refs)
        assert len(scope) == 6

    def test_empty_strings_skipped(self) -> None:
        refs = ["", "   ", "src/a.py"]
        scope = _scope_from_evidence(refs)
        assert scope == ["src/a.py"]


# ---------------------------------------------------------------------------
# GoalGovernor.list_goals
# ---------------------------------------------------------------------------

class TestListGoals:
    def test_list_all(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = [
            _make_goal("g1", GoalStatus.PENDING),
            _make_goal("g2", GoalStatus.APPROVED),
        ]
        goals = governor.list_goals()
        assert len(goals) == 2

    def test_list_filtered_by_status(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = [
            _make_goal("g1", GoalStatus.PENDING),
            _make_goal("g2", GoalStatus.APPROVED),
        ]
        goals = governor.list_goals(status="approved")
        assert len(goals) == 1
        assert goals[0].goal_id == "g2"

    def test_list_filtered_no_match(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = [_make_goal("g1", GoalStatus.PENDING)]
        goals = governor.list_goals(status="archived")
        assert goals == []

    def test_empty_storage(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        assert governor.list_goals() == []


# ---------------------------------------------------------------------------
# GoalGovernor.create_manual_proposal
# ---------------------------------------------------------------------------

class TestCreateManualProposal:
    def test_create_basic(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        payload = {
            "title": "Fix bug",
            "motivation": "It crashes",
            "goal_type": "reliability",
            "expected_value": 0.9,
            "risk_score": 0.1,
            "scope": ["src/backend"],
        }
        proposal = governor.create_manual_proposal(payload)
        assert proposal.title == "Fix bug"
        assert proposal.goal_type == GoalType.RELIABILITY
        assert proposal.expected_value == 0.9
        assert proposal.risk_score == 0.1
        assert proposal.status == GoalStatus.PENDING
        assert proposal.fingerprint != ""
        storage.save_goals.assert_called_once()

    def test_create_with_evidence_scope_fallback(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        payload = {
            "title": "Refactor",
            "evidence_refs": ["src/backend/core.py"],
        }
        proposal = governor.create_manual_proposal(payload)
        assert "src/backend" in proposal.scope

    def test_duplicate_fingerprint_returns_existing(self, governor: GoalGovernor, storage: MagicMock) -> None:
        existing = _make_goal("g1", GoalStatus.PENDING, title="Dup", source="manual", scope=["src"])
        existing.fingerprint = _stable_goal_fingerprint(GoalType.MAINTENANCE, "Dup", "manual", ["src"])
        storage.load_goals.return_value = [existing]
        payload = {"title": "Dup", "source": "manual", "scope": ["src"]}
        proposal = governor.create_manual_proposal(payload)
        assert proposal.goal_id == "g1"
        storage.save_goals.assert_not_called()

    def test_defaults_for_missing_fields(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        proposal = governor.create_manual_proposal({})
        assert proposal.goal_type == GoalType.MAINTENANCE
        assert proposal.source == "manual"
        assert proposal.expected_value == 0.6
        assert proposal.risk_score == 0.2
        assert proposal.budget == {"max_tasks": 2, "max_parallel_tasks": 1}

    def test_invalid_goal_type_falls_back(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        proposal = governor.create_manual_proposal({"goal_type": "nonsense"})
        assert proposal.goal_type == GoalType.MAINTENANCE

    def test_budget_mapping_coercion(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        proposal = governor.create_manual_proposal({"budget": {"max_tasks": 5}})
        assert proposal.budget == {"max_tasks": 5}


# ---------------------------------------------------------------------------
# GoalGovernor.generate
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_generate_from_insights(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        insights = [
            MetaInsight(insight_type="strategy_risk", summary="Risky", confidence=0.8, evidence_refs=["src/a.py"]),
        ]
        new_goals = governor.generate(insights=insights, capability_graph=None, improvements=[])
        assert len(new_goals) == 1
        assert new_goals[0].goal_type == GoalType.RELIABILITY
        storage.save_goals.assert_called_once()

    def test_generate_prediction_gap(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        insights = [
            MetaInsight(insight_type="prediction_gap", summary="Gap", confidence=0.7, evidence_refs=[]),
        ]
        new_goals = governor.generate(insights=insights, capability_graph=None, improvements=[])
        assert new_goals[0].goal_type == GoalType.CAPABILITY

    def test_generate_strategy_strength(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        insights = [
            MetaInsight(insight_type="strategy_strength", summary="Strong", confidence=0.9, evidence_refs=[]),
        ]
        new_goals = governor.generate(insights=insights, capability_graph=None, improvements=[])
        assert new_goals[0].goal_type == GoalType.KNOWLEDGE
        assert new_goals[0].risk_score == 0.15

    def test_generate_deduplication(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        insights = [
            MetaInsight(insight_type="failure_cluster", summary="Fail", confidence=0.5, evidence_refs=["src/x.py"]),
            MetaInsight(insight_type="failure_cluster", summary="Fail", confidence=0.5, evidence_refs=["src/x.py"]),
        ]
        new_goals = governor.generate(insights=insights, capability_graph=None, improvements=[])
        assert len(new_goals) == 1

    def test_generate_max_new_limits(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        insights = [MetaInsight(insight_type="strategy_risk", summary=f"R{i}", confidence=0.5, evidence_refs=[]) for i in range(10)]
        new_goals = governor.generate(insights=insights, capability_graph=None, improvements=[], max_new=3)
        assert len(new_goals) == 3

    def test_generate_from_capability_graph(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        graph = CapabilityGraphSnapshot(gaps=["gap1", "gap2"])
        new_goals = governor.generate(insights=[], capability_graph=graph, improvements=[])
        assert len(new_goals) == 2
        assert all(g.goal_type == GoalType.CAPABILITY for g in new_goals)

    def test_generate_from_improvements(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        improvements = [
            ImprovementProposal(
                title="Perf",
                description="Faster",
                target_surface="src/backend",
                confidence=0.8,
                status=ImprovementStatus.PROPOSED,
            ),
        ]
        new_goals = governor.generate(insights=[], capability_graph=None, improvements=improvements)
        assert len(new_goals) == 1
        assert new_goals[0].goal_type == GoalType.MAINTENANCE
        assert "Perf" in new_goals[0].title

    def test_generate_skips_non_proposed_improvements(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        improvements = [
            ImprovementProposal(
                title="Perf",
                description="Faster",
                target_surface="src/backend",
                confidence=0.8,
                status=ImprovementStatus.APPROVED,
            ),
        ]
        new_goals = governor.generate(insights=[], capability_graph=None, improvements=improvements)
        assert new_goals == []

    def test_generate_no_new_goals_no_save(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        new_goals = governor.generate(insights=[], capability_graph=None, improvements=[])
        assert new_goals == []
        storage.save_goals.assert_not_called()


# ---------------------------------------------------------------------------
# GoalGovernor.approve_goal
# ---------------------------------------------------------------------------

class TestApproveGoal:
    def test_approve_existing(self, governor: GoalGovernor, storage: MagicMock) -> None:
        goal = _make_goal("g1", GoalStatus.PENDING)
        storage.load_goals.return_value = [goal]
        result = governor.approve_goal("g1", note="LGTM")
        assert result is not None
        assert result.status == GoalStatus.APPROVED
        assert result.approval_note == "LGTM"
        assert result.pm_contract_outline != {}
        storage.save_goals.assert_called_once()

    def test_approve_missing_returns_none(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        assert governor.approve_goal("missing") is None
        storage.save_goals.assert_not_called()


# ---------------------------------------------------------------------------
# GoalGovernor.reject_goal
# ---------------------------------------------------------------------------

class TestRejectGoal:
    def test_reject_existing(self, governor: GoalGovernor, storage: MagicMock) -> None:
        goal = _make_goal("g1", GoalStatus.PENDING)
        storage.load_goals.return_value = [goal]
        result = governor.reject_goal("g1", note="Nope")
        assert result is not None
        assert result.status == GoalStatus.REJECTED
        assert result.approval_note == "Nope"
        storage.save_goals.assert_called_once()

    def test_reject_missing_returns_none(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        assert governor.reject_goal("missing") is None
        storage.save_goals.assert_not_called()


# ---------------------------------------------------------------------------
# GoalGovernor.materialize_goal
# ---------------------------------------------------------------------------

class TestMaterializeGoal:
    def test_materialize_approved(self, governor: GoalGovernor, storage: MagicMock) -> None:
        goal = _make_goal("g1", GoalStatus.APPROVED)
        storage.load_goals.return_value = [goal]
        contract = governor.materialize_goal("g1")
        assert contract is not None
        assert isinstance(contract, dict)
        assert goal.status == GoalStatus.MATERIALIZED
        storage.save_goals.assert_called_once()

    def test_materialize_already_materialized(self, governor: GoalGovernor, storage: MagicMock) -> None:
        goal = _make_goal("g1", GoalStatus.MATERIALIZED)
        storage.load_goals.return_value = [goal]
        contract = governor.materialize_goal("g1")
        assert contract is not None

    def test_materialize_pending_raises(self, governor: GoalGovernor, storage: MagicMock) -> None:
        goal = _make_goal("g1", GoalStatus.PENDING)
        storage.load_goals.return_value = [goal]
        with pytest.raises(ValueError, match="approved before materialization"):
            governor.materialize_goal("g1")

    def test_materialize_missing_returns_none(self, governor: GoalGovernor, storage: MagicMock) -> None:
        storage.load_goals.return_value = []
        assert governor.materialize_goal("missing") is None
        storage.save_goals.assert_not_called()


# ---------------------------------------------------------------------------
# GoalGovernor._title_from_insight
# ---------------------------------------------------------------------------

class TestTitleFromInsight:
    def test_strategy_strength(self, governor: GoalGovernor) -> None:
        insight = MetaInsight(insight_type="strategy_strength", strategy_tag="tag1")
        assert "Codify successful strategy" in governor._title_from_insight(insight)
        assert "tag1" in governor._title_from_insight(insight)

    def test_prediction_gap(self, governor: GoalGovernor) -> None:
        insight = MetaInsight(insight_type="prediction_gap", strategy_tag="tag2")
        assert "Reduce prediction gap" in governor._title_from_insight(insight)

    def test_failure_cluster(self, governor: GoalGovernor) -> None:
        insight = MetaInsight(insight_type="failure_cluster", strategy_tag="tag3")
        assert "Stabilize failure cluster" in governor._title_from_insight(insight)

    def test_default(self, governor: GoalGovernor) -> None:
        insight = MetaInsight(insight_type="other", strategy_tag="")
        assert "Harden strategy" in governor._title_from_insight(insight)


# ---------------------------------------------------------------------------
# GoalGovernor._build_pm_contract
# ---------------------------------------------------------------------------

class TestBuildPmContract:
    def test_contract_structure(self, governor: GoalGovernor) -> None:
        goal = _make_goal("g1", GoalStatus.APPROVED, title="Test Goal", scope=["src/backend"])
        contract = governor._build_pm_contract(goal)
        assert contract["focus"] == "resident_goal_materialization"
        assert contract["overall_goal"] == "Test Goal"
        assert contract["metadata"]["resident_goal_id"] == "g1"
        assert len(contract["tasks"]) == 2
        assert contract["tasks"][0]["phase"] == "analysis"
        assert contract["tasks"][1]["phase"] == "implementation"
        assert contract["tasks"][1]["depends_on"] == ["g1-A"]

    def test_contract_escapes_backticks(self, governor: GoalGovernor) -> None:
        goal = _make_goal("g1", title="`dangerous`", scope=[])
        contract = governor._build_pm_contract(goal)
        assert "`" not in contract["overall_goal"]

    def test_contract_default_scope(self, governor: GoalGovernor) -> None:
        goal = _make_goal("g1", title="No Scope", scope=[])
        contract = governor._build_pm_contract(goal)
        assert contract["tasks"][0]["scope_paths"] == ["src/backend", "docs"]
