"""Unit tests for court_workflow internal models."""

from __future__ import annotations

from polaris.cells.docs.court_workflow.internal.models.court import (
    ActorStatus,
    CourtActionEvent,
    CourtActorState,
    CourtEvidenceRef,
    CourtRole,
    CourtSceneConfig,
    CourtScenePhase,
    CourtState,
    CourtTopologyNode,
    RiskLevel,
)


class TestCourtRole:
    """Tests for CourtRole enum."""

    def test_members(self) -> None:
        assert CourtRole.EMPEROR.value == "emperor"
        assert CourtRole.ZHONGSHU_LING.value == "zhongshu_ling"
        assert CourtRole.GONGBU_OFFICER_2.value == "gongbu_officer_2"


class TestCourtScenePhase:
    """Tests for CourtScenePhase enum."""

    def test_members(self) -> None:
        assert CourtScenePhase.COURT_AUDIENCE.value == "court_audience"
        assert CourtScenePhase.BUILD.value == "build"
        assert CourtScenePhase.FINALIZE.value == "finalize"


class TestActorStatus:
    """Tests for ActorStatus enum."""

    def test_members(self) -> None:
        assert ActorStatus.OFFLINE.value == "offline"
        assert ActorStatus.IDLE.value == "idle"
        assert ActorStatus.EXECUTING.value == "executing"
        assert ActorStatus.FAILED.value == "failed"


class TestRiskLevel:
    """Tests for RiskLevel enum."""

    def test_members(self) -> None:
        assert RiskLevel.NONE.value == "none"
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.CRITICAL.value == "critical"


class TestCourtEvidenceRef:
    """Tests for CourtEvidenceRef dataclass."""

    def test_to_dict(self) -> None:
        ref = CourtEvidenceRef(path="/evidence/1.txt", channel="audit", run_id="r1")
        d = ref.to_dict()
        assert d["path"] == "/evidence/1.txt"
        assert d["channel"] == "audit"
        assert d["run_id"] == "r1"
        assert d["task_id"] is None


class TestCourtActorState:
    """Tests for CourtActorState dataclass."""

    def test_defaults(self) -> None:
        state = CourtActorState(role_id="emperor", role_name="天子", status=ActorStatus.IDLE)
        assert state.current_action == ""
        assert state.task_id is None
        assert state.risk_level == RiskLevel.NONE
        assert state.evidence_refs == []
        assert state.metadata == {}
        assert state.updated_at > 0

    def test_to_dict(self) -> None:
        state = CourtActorState(
            role_id="emperor",
            role_name="天子",
            status=ActorStatus.EXECUTING,
            current_action="听政",
            risk_level=RiskLevel.LOW,
        )
        d = state.to_dict()
        assert d["role_id"] == "emperor"
        assert d["status"] == "executing"
        assert d["current_action"] == "听政"
        assert d["risk_level"] == "low"


class TestCourtTopologyNode:
    """Tests for CourtTopologyNode dataclass."""

    def test_defaults(self) -> None:
        node = CourtTopologyNode(role_id="test", role_name="测试")
        assert node.position == [0.0, 0.0, 0.0]
        assert node.department == ""
        assert node.level == 0
        assert node.is_interactive is True

    def test_to_dict(self) -> None:
        node = CourtTopologyNode(
            role_id="emperor",
            role_name="天子",
            parent_id=None,
            position=[0, 2, 0],
            department="imperial",
            level=0,
        )
        d = node.to_dict()
        assert d["role_id"] == "emperor"
        assert d["position"] == [0, 2, 0]
        assert d["parent_id"] is None


class TestCourtActionEvent:
    """Tests for CourtActionEvent dataclass."""

    def test_defaults(self) -> None:
        event = CourtActionEvent(action_type="dispatch", from_role="shangshu_ling")
        assert event.to_role is None
        assert event.payload == {}
        assert event.ts > 0
        assert event.evidence_refs == []

    def test_to_dict(self) -> None:
        event = CourtActionEvent(
            action_type="review",
            from_role="menxia_shilang",
            to_role="gongbu_shangshu",
            payload={"task_id": "t1"},
        )
        d = event.to_dict()
        assert d["action_type"] == "review"
        assert d["to_role"] == "gongbu_shangshu"
        assert d["payload"] == {"task_id": "t1"}


class TestCourtSceneConfig:
    """Tests for CourtSceneConfig dataclass."""

    def test_defaults(self) -> None:
        config = CourtSceneConfig(scene_id="hall", scene_name="大殿", phase=CourtScenePhase.COURT_AUDIENCE)
        assert config.description == ""
        assert config.camera_position == [0.0, 5.0, 10.0]
        assert config.focus_roles == []
        assert config.transitions == []

    def test_to_dict(self) -> None:
        config = CourtSceneConfig(
            scene_id="taiji",
            scene_name="太极殿",
            phase=CourtScenePhase.COURT_AUDIENCE,
            focus_roles=["emperor"],
        )
        d = config.to_dict()
        assert d["scene_id"] == "taiji"
        assert d["phase"] == "court_audience"
        assert d["focus_roles"] == ["emperor"]


class TestCourtState:
    """Tests for CourtState dataclass."""

    def test_defaults(self) -> None:
        state = CourtState(
            phase=CourtScenePhase.COURT_AUDIENCE,
            current_scene="taiji_hall",
            actors={},
        )
        assert state.topology is None
        assert state.recent_events == []
        assert state.updated_at > 0

    def test_to_dict(self) -> None:
        actor = CourtActorState(
            role_id="emperor",
            role_name="天子",
            status=ActorStatus.IDLE,
        )
        state = CourtState(
            phase=CourtScenePhase.DRAFT,
            current_scene="zhongshu_pavilion",
            actors={"emperor": actor},
            topology=[CourtTopologyNode("emperor", "天子")],
        )
        d = state.to_dict()
        assert d["phase"] == "draft"
        assert d["current_scene"] == "zhongshu_pavilion"
        assert "emperor" in d["actors"]
        assert d["topology"] is not None
