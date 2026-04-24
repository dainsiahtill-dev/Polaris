"""Tests for polaris.domain.models.resident."""

from __future__ import annotations

from polaris.domain.models.resident import (
    CapabilityGraphSnapshot,
    CapabilityNode,
    DecisionOption,
    DecisionRecord,
    DecisionVerdict,
    ExperimentRecord,
    ExperimentStatus,
    GoalProposal,
    GoalStatus,
    GoalType,
    ImprovementProposal,
    ImprovementStatus,
    MetaInsight,
    ResidentAgenda,
    ResidentIdentity,
    ResidentMode,
    ResidentRuntimeState,
    SkillArtifact,
    SkillProposal,
    SkillProposalStatus,
    coerce_float,
    coerce_mapping,
    coerce_str_list,
    new_id,
    utc_now_iso,
)


class TestUtcNowIso:
    def test_returns_string(self) -> None:
        result = utc_now_iso()
        assert isinstance(result, str)
        assert "T" in result


class TestNewId:
    def test_with_prefix(self) -> None:
        result = new_id("test")
        assert result.startswith("test-")
        assert len(result) > 5

    def test_empty_prefix(self) -> None:
        result = new_id("")
        assert result.startswith("resident-")

    def test_spaces_replaced(self) -> None:
        result = new_id("my prefix")
        assert result.startswith("my_prefix-")


class TestCoerceStrList:
    def test_string_input(self) -> None:
        assert coerce_str_list("hello") == ["hello"]

    def test_list_input(self) -> None:
        assert coerce_str_list(["a", "b"]) == ["a", "b"]

    def test_filters_empty(self) -> None:
        assert coerce_str_list(["a", "", "  ", "b"]) == ["a", "b"]

    def test_non_iterable(self) -> None:
        assert coerce_str_list(123) == []

    def test_none_items_filtered(self) -> None:
        assert coerce_str_list(["a", None, "b"]) == ["a", "b"]


class TestCoerceMapping:
    def test_dict_input(self) -> None:
        assert coerce_mapping({"a": 1}) == {"a": 1}

    def test_non_dict(self) -> None:
        assert coerce_mapping("not a dict") == {}

    def test_converts_keys_to_str(self) -> None:
        assert coerce_mapping({1: "value"}) == {"1": "value"}


class TestCoerceFloat:
    def test_valid_int(self) -> None:
        assert coerce_float(5) == 5.0

    def test_valid_string(self) -> None:
        assert coerce_float("3.14") == 3.14

    def test_invalid_uses_default(self) -> None:
        assert coerce_float("abc", default=1.0) == 1.0

    def test_minimum(self) -> None:
        assert coerce_float(-10, minimum=0.0) == 0.0

    def test_maximum(self) -> None:
        assert coerce_float(100, maximum=50.0) == 50.0


class TestResidentMode:
    def test_values(self) -> None:
        assert ResidentMode.OBSERVE.value == "observe"
        assert ResidentMode.PROPOSE.value == "propose"
        assert ResidentMode.ASSIST.value == "assist"


class TestGoalType:
    def test_values(self) -> None:
        assert GoalType.MAINTENANCE.value == "maintenance"
        assert GoalType.RELIABILITY.value == "reliability"


class TestGoalStatus:
    def test_values(self) -> None:
        assert GoalStatus.PENDING.value == "pending"
        assert GoalStatus.APPROVED.value == "approved"


class TestDecisionVerdict:
    def test_values(self) -> None:
        assert DecisionVerdict.SUCCESS.value == "success"
        assert DecisionVerdict.FAILURE.value == "failure"


class TestExperimentStatus:
    def test_values(self) -> None:
        assert ExperimentStatus.SIMULATED.value == "simulated"
        assert ExperimentStatus.APPROVED.value == "approved"


class TestImprovementStatus:
    def test_values(self) -> None:
        assert ImprovementStatus.PROPOSED.value == "proposed"
        assert ImprovementStatus.APPROVED.value == "approved"


class TestDecisionOption:
    def test_defaults(self) -> None:
        opt = DecisionOption()
        assert opt.label == ""
        assert opt.estimated_score == 0.0

    def test_to_dict(self) -> None:
        opt = DecisionOption(label="A", estimated_score=0.8)
        d = opt.to_dict()
        assert d["label"] == "A"
        assert d["estimated_score"] == 0.8

    def test_from_dict(self) -> None:
        opt = DecisionOption.from_dict({"label": "A", "estimated_score": "0.8"})
        assert opt.label == "A"
        assert opt.estimated_score == 0.8


class TestResidentIdentity:
    def test_defaults(self) -> None:
        ident = ResidentIdentity()
        assert ident.name == "Resident Engineer"
        assert ident.operating_mode == ResidentMode.OBSERVE

    def test_to_dict(self) -> None:
        ident = ResidentIdentity(name="Test")
        d = ident.to_dict()
        assert d["name"] == "Test"
        assert d["operating_mode"] == "observe"

    def test_from_dict(self) -> None:
        d = {
            "name": "Test",
            "operating_mode": "assist",
            "capability_profile": {"coding": 0.8},
        }
        ident = ResidentIdentity.from_dict(d)
        assert ident.name == "Test"
        assert ident.operating_mode == ResidentMode.ASSIST
        assert ident.capability_profile == {"coding": 0.8}

    def test_from_dict_invalid_mode(self) -> None:
        ident = ResidentIdentity.from_dict({"operating_mode": "invalid"})
        assert ident.operating_mode == ResidentMode.OBSERVE


class TestResidentAgenda:
    def test_defaults(self) -> None:
        agenda = ResidentAgenda()
        assert agenda.tick_count == 0

    def test_to_dict(self) -> None:
        agenda = ResidentAgenda(current_focus=["a"])
        d = agenda.to_dict()
        assert d["current_focus"] == ["a"]

    def test_from_dict(self) -> None:
        agenda = ResidentAgenda.from_dict({"tick_count": 5})
        assert agenda.tick_count == 5


class TestGoalProposal:
    def test_defaults(self) -> None:
        goal = GoalProposal()
        assert goal.goal_type == GoalType.MAINTENANCE
        assert goal.status == GoalStatus.PENDING

    def test_to_dict(self) -> None:
        goal = GoalProposal(title="T")
        d = goal.to_dict()
        assert d["title"] == "T"
        assert d["goal_type"] == "maintenance"

    def test_from_dict(self) -> None:
        goal = GoalProposal.from_dict({"goal_id": "g1", "title": "T", "goal_type": "reliability"})
        assert goal.goal_id == "g1"
        assert goal.title == "T"
        assert goal.goal_type == GoalType.RELIABILITY

    def test_from_dict_invalid_type(self) -> None:
        goal = GoalProposal.from_dict({"goal_id": "g1", "goal_type": "invalid_type"})
        assert goal.goal_id == "g1"
        assert goal.goal_type == GoalType.MAINTENANCE


class TestDecisionRecord:
    def test_defaults(self) -> None:
        rec = DecisionRecord()
        assert rec.verdict == DecisionVerdict.UNKNOWN

    def test_to_dict(self) -> None:
        rec = DecisionRecord(verdict=DecisionVerdict.SUCCESS)
        d = rec.to_dict()
        assert d["verdict"] == "success"

    def test_from_dict(self) -> None:
        rec = DecisionRecord.from_dict({"decision_id": "d1", "verdict": "success"})
        assert rec.decision_id == "d1"
        assert rec.verdict == DecisionVerdict.SUCCESS

    def test_from_dict_invalid_verdict(self) -> None:
        rec = DecisionRecord.from_dict({"decision_id": "d1", "verdict": "invalid_verdict"})
        assert rec.decision_id == "d1"
        assert rec.verdict == DecisionVerdict.UNKNOWN

    def test_from_dict_with_options(self) -> None:
        rec = DecisionRecord.from_dict(
            {
                "decision_id": "d1",
                "options": [{"label": "A"}],
            }
        )
        assert rec.decision_id == "d1"
        assert len(rec.options) == 1
        assert rec.options[0].label == "A"

    def test_phase_1_1_fields(self) -> None:
        rec = DecisionRecord.from_dict(
            {
                "decision_id": "d1",
                "evidence_bundle_id": "eb1",
                "parent_decision_id": "pd1",
                "affected_files": ["a.py"],
            }
        )
        assert rec.decision_id == "d1"
        assert rec.evidence_bundle_id == "eb1"
        assert rec.parent_decision_id == "pd1"
        assert rec.affected_files == ["a.py"]


class TestMetaInsight:
    def test_defaults(self) -> None:
        insight = MetaInsight()
        assert insight.confidence == 0.0

    def test_from_dict(self) -> None:
        insight = MetaInsight.from_dict({"confidence": 0.9})
        assert insight.confidence == 0.9


class TestSkillArtifact:
    def test_defaults(self) -> None:
        skill = SkillArtifact()
        assert skill.version == 1

    def test_from_dict(self) -> None:
        skill = SkillArtifact.from_dict({"skill_id": "s1", "version": 3, "name": "Test"})
        assert skill.skill_id == "s1"
        assert skill.version == 3
        assert skill.name == "Test"


class TestSkillProposalStatus:
    def test_values(self) -> None:
        assert SkillProposalStatus.PENDING_REVIEW.value == "pending_review"
        assert SkillProposalStatus.APPROVED.value == "approved"


class TestSkillProposal:
    def test_defaults(self) -> None:
        prop = SkillProposal()
        assert prop.status == SkillProposalStatus.PENDING_REVIEW.value

    def test_from_dict(self) -> None:
        prop = SkillProposal.from_dict({"name": "P", "confidence": 0.8})
        assert prop.name == "P"
        assert prop.confidence == 0.8


class TestCapabilityNode:
    def test_defaults(self) -> None:
        node = CapabilityNode()
        assert node.score == 0.0
        assert node.attempts == 0

    def test_from_dict(self) -> None:
        node = CapabilityNode.from_dict({"capability_id": "c1", "name": "N", "score": 0.75})
        assert node.capability_id == "c1"
        assert node.name == "N"
        assert node.score == 0.75

    def test_from_dict_defaults(self) -> None:
        node = CapabilityNode.from_dict({"capability_id": "c1"})
        assert node.capability_id == "c1"
        assert node.name == ""
        assert node.score == 0.0


class TestCapabilityGraphSnapshot:
    def test_defaults(self) -> None:
        snap = CapabilityGraphSnapshot()
        assert snap.capabilities == []

    def test_to_dict(self) -> None:
        snap = CapabilityGraphSnapshot(capabilities=[CapabilityNode(name="N")])
        d = snap.to_dict()
        assert len(d["capabilities"]) == 1

    def test_from_dict(self) -> None:
        snap = CapabilityGraphSnapshot.from_dict(
            {
                "capabilities": [{"capability_id": "c1", "name": "N", "score": 0.5}],
                "gaps": ["g1"],
            }
        )
        assert len(snap.capabilities) == 1
        assert snap.capabilities[0].capability_id == "c1"
        assert snap.capabilities[0].name == "N"
        assert snap.gaps == ["g1"]


class TestExperimentRecord:
    def test_defaults(self) -> None:
        exp = ExperimentRecord()
        assert exp.status == ExperimentStatus.SIMULATED

    def test_to_dict(self) -> None:
        exp = ExperimentRecord(status=ExperimentStatus.APPROVED)
        d = exp.to_dict()
        assert d["status"] == "approved"

    def test_from_dict(self) -> None:
        exp = ExperimentRecord.from_dict({"experiment_id": "e1", "status": "promoted"})
        assert exp.experiment_id == "e1"
        assert exp.status == ExperimentStatus.PROMOTED

    def test_from_dict_invalid_status(self) -> None:
        exp = ExperimentRecord.from_dict({"experiment_id": "e1", "status": "invalid_status"})
        assert exp.experiment_id == "e1"
        assert exp.status == ExperimentStatus.SIMULATED


class TestImprovementProposal:
    def test_defaults(self) -> None:
        imp = ImprovementProposal()
        assert imp.status == ImprovementStatus.PROPOSED

    def test_from_dict(self) -> None:
        imp = ImprovementProposal.from_dict({"improvement_id": "i1", "status": "approved", "title": "T"})
        assert imp.improvement_id == "i1"
        assert imp.status == ImprovementStatus.APPROVED

    def test_from_dict_invalid_status(self) -> None:
        imp = ImprovementProposal.from_dict({"improvement_id": "i1", "status": "invalid_status", "title": "T"})
        assert imp.improvement_id == "i1"
        assert imp.status == ImprovementStatus.PROPOSED


class TestResidentRuntimeState:
    def test_defaults(self) -> None:
        state = ResidentRuntimeState()
        assert state.active is True
        assert state.mode == ResidentMode.OBSERVE

    def test_to_dict(self) -> None:
        state = ResidentRuntimeState(mode=ResidentMode.ASSIST)
        d = state.to_dict()
        assert d["mode"] == "assist"

    def test_from_dict(self) -> None:
        state = ResidentRuntimeState.from_dict({"mode": "propose", "tick_count": 10})
        assert state.mode == ResidentMode.PROPOSE
        assert state.tick_count == 10

    def test_from_dict_invalid_mode(self) -> None:
        state = ResidentRuntimeState.from_dict({"mode": "invalid"})
        assert state.mode == ResidentMode.OBSERVE
