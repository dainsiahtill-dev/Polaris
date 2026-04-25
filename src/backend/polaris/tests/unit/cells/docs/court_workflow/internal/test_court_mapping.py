"""Unit tests for court_mapping module."""

from __future__ import annotations

import pytest
from polaris.cells.docs.court_workflow.internal.court_mapping import (
    COURT_SCENE_CONFIGS,
    COURT_TOPOLOGY,
    TECH_TO_COURT_ROLE_MAPPING,
    CourtRoleMapping,
    get_court_topology,
    get_scene_configs,
    map_engine_to_court_state,
)
from polaris.cells.docs.court_workflow.internal.models.court import (
    ActorStatus,
    CourtScenePhase,
    RiskLevel,
)


class TestCourtRoleMapping:
    """Tests for CourtRoleMapping."""

    @pytest.fixture
    def mapper(self) -> CourtRoleMapping:
        return CourtRoleMapping()

    def test_get_topology(self, mapper: CourtRoleMapping) -> None:
        topology = mapper.get_topology()
        assert len(topology) == len(COURT_TOPOLOGY)
        assert topology[0].role_id == "emperor"

    def test_get_role_state_valid(self, mapper: CourtRoleMapping) -> None:
        state = mapper.get_role_state("emperor")
        assert state is not None
        assert state.role_id == "emperor"
        assert state.role_name == "天子"
        assert state.status == ActorStatus.IDLE

    def test_get_role_state_invalid(self, mapper: CourtRoleMapping) -> None:
        assert mapper.get_role_state("nonexistent") is None

    def test_map_tech_role_to_court_pm(self, mapper: CourtRoleMapping) -> None:
        assert mapper.map_tech_role_to_court("pm") == "emperor"

    def test_map_tech_role_to_court_architect(self, mapper: CourtRoleMapping) -> None:
        assert mapper.map_tech_role_to_court("architect") == "zhongshu_shilang"

    def test_map_tech_role_to_court_auditor(self, mapper: CourtRoleMapping) -> None:
        assert mapper.map_tech_role_to_court("auditor") == "menxia_shizhong"

    def test_map_tech_role_to_court_default(self, mapper: CourtRoleMapping) -> None:
        assert mapper.map_tech_role_to_court("unknown") == "gongbu_officer_2"

    def test_map_tech_role_case_insensitive(self, mapper: CourtRoleMapping) -> None:
        assert mapper.map_tech_role_to_court("PM") == "emperor"
        assert mapper.map_tech_role_to_court("Architect") == "zhongshu_shilang"

    def test_determine_status_from_engine_failed(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "failed"}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.FAILED

    def test_determine_status_from_engine_blocked(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "blocked"}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.BLOCKED

    def test_determine_status_from_engine_running(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "running", "running": True}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.EXECUTING

    def test_determine_status_from_engine_thinking(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "thinking"}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.THINKING

    def test_determine_status_from_engine_success(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "success"}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.SUCCESS

    def test_determine_status_from_engine_idle(self, mapper: CourtRoleMapping) -> None:
        payload = {"roles": {"emperor": {"status": "idle"}}}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.IDLE

    def test_determine_status_offline(self, mapper: CourtRoleMapping) -> None:
        payload = {"phase": "idle", "running": False}
        status = mapper._determine_status_from_engine(payload, "emperor")
        assert status == ActorStatus.OFFLINE

    def test_determine_status_unknown_role(self, mapper: CourtRoleMapping) -> None:
        payload = {"phase": "planning", "running": True}
        status = mapper._determine_status_from_engine(payload, "unknown_role")
        assert status == ActorStatus.IDLE

    def test_determine_risk_level_none(self, mapper: CourtRoleMapping) -> None:
        payload: dict[str, object] = {}
        assert mapper._determine_risk_level(payload, "emperor") == RiskLevel.NONE

    def test_determine_risk_level_low(self, mapper: CourtRoleMapping) -> None:
        payload = {"recent_errors": ["e1"]}
        assert mapper._determine_risk_level(payload, "emperor") == RiskLevel.LOW

    def test_determine_risk_level_medium(self, mapper: CourtRoleMapping) -> None:
        payload = {"recent_errors": ["e1", "e2", "e3"]}
        assert mapper._determine_risk_level(payload, "emperor") == RiskLevel.MEDIUM

    def test_determine_risk_level_high(self, mapper: CourtRoleMapping) -> None:
        payload = {"recent_errors": ["e"] * 6}
        assert mapper._determine_risk_level(payload, "emperor") == RiskLevel.HIGH

    def test_determine_risk_level_critical(self, mapper: CourtRoleMapping) -> None:
        payload = {"recent_errors": ["e"] * 11}
        assert mapper._determine_risk_level(payload, "emperor") == RiskLevel.CRITICAL

    def test_map_engine_to_court_state_planning(self, mapper: CourtRoleMapping) -> None:
        engine = {"phase": "planning", "running": True}
        state = mapper.map_engine_to_court_state(engine)
        assert state.phase == CourtScenePhase.DRAFT
        assert state.current_scene == "zhongshu_pavilion"
        assert "emperor" in state.actors

    def test_map_engine_to_court_state_building(self, mapper: CourtRoleMapping) -> None:
        engine = {"phase": "building", "running": True}
        state = mapper.map_engine_to_court_state(engine)
        assert state.phase == CourtScenePhase.BUILD
        assert state.current_scene == "construction_site"

    def test_map_engine_to_court_state_reviewing(self, mapper: CourtRoleMapping) -> None:
        engine = {"phase": "reviewing", "running": True}
        state = mapper.map_engine_to_court_state(engine)
        assert state.phase == CourtScenePhase.REVIEW
        assert state.current_scene == "menxia_tower"

    def test_map_engine_to_court_state_finalizing(self, mapper: CourtRoleMapping) -> None:
        engine = {"phase": "finalizing", "running": True}
        state = mapper.map_engine_to_court_state(engine)
        assert state.phase == CourtScenePhase.FINALIZE
        assert state.current_scene == "taiji_hall"

    def test_map_engine_to_court_state_default(self, mapper: CourtRoleMapping) -> None:
        state = mapper.map_engine_to_court_state(None)
        assert state.phase == CourtScenePhase.COURT_AUDIENCE
        assert state.current_scene == "taiji_hall"

    def test_map_engine_to_court_state_actor_actions(self, mapper: CourtRoleMapping) -> None:
        engine = {"phase": "planning", "running": True}
        state = mapper.map_engine_to_court_state(engine)
        emperor = state.actors["emperor"]
        assert emperor.current_action == "颁诏"
        zhongshu = state.actors["zhongshu_ling"]
        assert zhongshu.current_action == "草拟圣旨"
        shangshu = state.actors["shangshu_ling"]
        assert shangshu.current_action == "统筹"

    def test_get_scene_configs(self, mapper: CourtRoleMapping) -> None:
        configs = mapper.get_scene_configs()
        assert "taiji_hall" in configs
        assert configs["taiji_hall"]["scene_name"] == "太极殿"


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_get_court_topology(self) -> None:
        topology = get_court_topology()
        assert isinstance(topology, list)
        assert len(topology) == len(COURT_TOPOLOGY)
        assert topology[0]["role_id"] == "emperor"

    def test_map_engine_to_court_state_function(self) -> None:
        result = map_engine_to_court_state({"phase": "building", "running": True})
        assert isinstance(result, dict)
        assert result["phase"] == "build"
        assert result["current_scene"] == "construction_site"
        assert "actors" in result

    def test_get_scene_configs_function(self) -> None:
        configs = get_scene_configs()
        assert isinstance(configs, dict)
        assert "taiji_hall" in configs


class TestConstants:
    """Tests for module-level constants."""

    def test_court_topology_length(self) -> None:
        assert len(COURT_TOPOLOGY) == 24

    def test_court_topology_emperor_first(self) -> None:
        assert COURT_TOPOLOGY[0].role_id == "emperor"
        assert COURT_TOPOLOGY[0].parent_id is None

    def test_tech_to_court_role_mapping(self) -> None:
        assert TECH_TO_COURT_ROLE_MAPPING["pm"] == "emperor"
        assert TECH_TO_COURT_ROLE_MAPPING["director"] == "emperor"
        assert TECH_TO_COURT_ROLE_MAPPING["architect"] == "zhongshu_shilang"
        assert TECH_TO_COURT_ROLE_MAPPING["auditor"] == "menxia_shizhong"
        assert TECH_TO_COURT_ROLE_MAPPING["default"] == "gongbu_officer_2"

    def test_court_scene_configs(self) -> None:
        assert "taiji_hall" in COURT_SCENE_CONFIGS
        assert "zhongshu_pavilion" in COURT_SCENE_CONFIGS
        assert "shangshu_hall" in COURT_SCENE_CONFIGS
        assert "gongbu_blueprint" in COURT_SCENE_CONFIGS
        assert "construction_site" in COURT_SCENE_CONFIGS
        assert "menxia_tower" in COURT_SCENE_CONFIGS
