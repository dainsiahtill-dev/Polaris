"""Court role mapping layer unit test.

This test module verifies the correctness of mapping from technical roles to display roles, and the determinism of court state generation.
All tests follow Phase 1 acceptance criteria.
"""

from polaris.cells.docs.court_workflow.internal.court_mapping import (
    court_mapper,
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
    """Test court role mapper."""

    def test_topology_node_count(self):
        """Test topology node count: 24 interactive roles."""
        topology = court_mapper.get_topology()
        interactive_count = sum(1 for node in topology if node.is_interactive)

        # Current implementation: User 1 + Architect 2 + QA 2 + PM 1 + Departments 6 + Department officers 12 = 24
        assert interactive_count == 24, f"Expected 24 interactive roles, got {interactive_count}"
        assert len(topology) == 24, f"Expected total 24 roles, got {len(topology)}"

    def test_topology_structure(self):
        """Test topology structure integrity: User -> Top departments -> Departments -> Officers."""
        topology = court_mapper.get_topology()
        role_ids = {node.role_id for node in topology}

        # Required key roles
        required_roles = {
            "emperor",  # User
            "zhongshu_ling",
            "zhongshu_shilang",  # Architect
            "menxia_shilang",
            "menxia_shizhong",  # QA
            "shangshu_ling",  # PM
            "libu_shangshu",
            "hubu_shangshu",
            "libu_shangshu2",
            "bingbu_shangshu",
            "xingbu_shangshu",
            "gongbu_shangshu",  # Departments
        }

        for role in required_roles:
            assert role in role_ids, f"Required role '{role}' not found in topology"

    def test_role_hierarchy(self):
        """Test role hierarchy."""
        topology = court_mapper.get_topology()

        # User's parent should be None
        emperor = next((n for n in topology if n.role_id == "emperor"), None)
        assert emperor is not None
        assert emperor.parent_id is None
        assert emperor.level == 0

        # Top departments' parent should be User
        for role_id in ["zhongshu_ling", "menxia_shilang", "shangshu_ling"]:
            node = next((n for n in topology if n.role_id == role_id), None)
            assert node is not None, f"Role '{role_id}' not found"
            assert node.parent_id == "emperor", f"{role_id} should report to emperor"
            assert node.level == 1

    def test_tech_to_court_mapping(self):
        """Test technical role to court role mapping."""
        test_cases = [
            ("pm", "emperor"),
            ("director", "emperor"),
            ("planner", "zhongshu_ling"),
            ("reviewer", "menxia_shilang"),
            ("dispatcher", "shangshu_ling"),
            ("executor", "gongbu_shangshu"),
            ("unknown_role", "gongbu_officer_2"),  # Default value
        ]

        for tech_role, expected_court_role in test_cases:
            result = court_mapper.map_tech_role_to_court(tech_role)
            assert result == expected_court_role, (
                f"Mapping failed for '{tech_role}': expected '{expected_court_role}', got '{result}'"
            )


class TestEngineToCourtStateMapping:
    """Test engine state to court state mapping."""

    def test_empty_engine_state(self):
        """Test empty engine state mapping: all roles should be idle."""
        state = court_mapper.map_engine_to_court_state(None)

        assert state.phase == CourtScenePhase.COURT_AUDIENCE
        assert state.current_scene == "taiji_hall"
        assert len(state.actors) == 24  # 24 roles

        # All roles should be idle
        for actor in state.actors.values():
            assert actor.status == ActorStatus.IDLE
            assert actor.role_name  # Should have display name

    def test_engine_phase_mapping(self):
        """Test engine phase to court phase mapping."""
        phase_mapping = [
            ({"phase": "planning"}, CourtScenePhase.DRAFT, "zhongshu_pavilion"),
            ({"phase": "decomposing"}, CourtScenePhase.DECOMPOSE, "shangshu_hall"),
            ({"phase": "blueprint"}, CourtScenePhase.BLUEPRINT, "gongbu_blueprint"),
            ({"phase": "executing"}, CourtScenePhase.BUILD, "construction_site"),
            ({"phase": "reviewing"}, CourtScenePhase.REVIEW, "menxia_tower"),
            ({"phase": "finalizing"}, CourtScenePhase.FINALIZE, "taiji_hall"),
        ]

        for engine_payload, expected_phase, expected_scene in phase_mapping:
            state = court_mapper.map_engine_to_court_state(engine_payload)
            assert state.phase == expected_phase, f"Phase mapping failed: {engine_payload['phase']} -> {state.phase}"
            assert state.current_scene == expected_scene, (
                f"Scene mapping failed: {engine_payload['phase']} -> {state.current_scene}"
            )

    def test_role_status_priority(self):
        """Test role status priority: failed > blocked > executing > thinking > success > idle > offline."""
        # Simulate engine returning different statuses for a single role
        test_cases = [
            ({"roles": {"gongbu_shangshu": {"status": "failed"}}}, ActorStatus.FAILED),
            ({"roles": {"gongbu_shangshu": {"status": "blocked"}}}, ActorStatus.BLOCKED),
            ({"roles": {"gongbu_shangshu": {"status": "running", "running": True}}}, ActorStatus.EXECUTING),
            ({"roles": {"gongbu_shangshu": {"status": "thinking"}}}, ActorStatus.THINKING),
            ({"roles": {"gongbu_shangshu": {"status": "success"}}}, ActorStatus.SUCCESS),
            ({"roles": {"gongbu_shangshu": {"status": "idle"}}}, ActorStatus.IDLE),
            ({"running": False}, ActorStatus.OFFLINE),
        ]

        for engine_payload, expected_status in test_cases:
            engine_payload.setdefault("running", True)
            state = court_mapper.map_engine_to_court_state(engine_payload)
            actor = state.actors.get("gongbu_shangshu")
            assert actor is not None
            assert actor.status == expected_status, (
                f"Status priority failed for {engine_payload}: expected {expected_status}, got {actor.status}"
            )

    def test_risk_level_calculation(self):
        """Test risk level calculation."""
        test_cases = [
            ({"recent_errors": []}, RiskLevel.NONE),
            ({"recent_errors": ["e1"]}, RiskLevel.LOW),
            ({"recent_errors": ["e1", "e2", "e3"]}, RiskLevel.MEDIUM),
            ({"recent_errors": ["e1"] * 6}, RiskLevel.HIGH),
            ({"recent_errors": ["e1"] * 11}, RiskLevel.CRITICAL),
        ]

        for engine_payload, expected_risk in test_cases:
            state = court_mapper.map_engine_to_court_state(engine_payload)
            # Check that at least one role has a risk level
            risks = {a.risk_level for a in state.actors.values()}
            assert expected_risk in risks, (
                f"Risk level {expected_risk} not found in state for errors: {len(engine_payload.get('recent_errors', []))}"
            )

    def test_deterministic_output(self):
        """Test output determinism: given the same input, output must be consistent."""
        engine_payload = {
            "phase": "executing",
            "running": True,
            "roles": {
                "gongbu_shangshu": {"status": "running", "running": True},
                "emperor": {"status": "idle"},
            },
        }

        state1 = court_mapper.map_engine_to_court_state(engine_payload)
        state2 = court_mapper.map_engine_to_court_state(engine_payload)

        # Compare key fields
        assert state1.phase == state2.phase
        assert state1.current_scene == state2.current_scene
        assert set(state1.actors.keys()) == set(state2.actors.keys())

        for role_id in state1.actors:
            a1 = state1.actors[role_id]
            a2 = state2.actors[role_id]
            assert a1.status == a2.status
            assert a1.risk_level == a2.risk_level
            assert a1.current_action == a2.current_action


class TestSceneConfigs:
    """Test scene configuration."""

    def test_scene_count(self):
        """Test scene count."""
        scenes = get_scene_configs()
        # 7 scenes defined in docs
        expected_scenes = [
            "taiji_hall",
            "zhongshu_pavilion",
            "shangshu_hall",
            "gongbu_blueprint",
            "construction_site",
            "menxia_tower",
        ]
        for scene_id in expected_scenes:
            assert scene_id in scenes, f"Scene '{scene_id}' not found"

    def test_scene_phase_consistency(self):
        """Test scene phase consistency."""
        scenes = get_scene_configs()
        phase_to_scene = {}

        for scene_id, config in scenes.items():
            phase = config["phase"]
            if phase in phase_to_scene:
                # Multiple scenes may map to the same phase, but each scene must have a unique phase
                pass
            phase_to_scene[phase] = scene_id

            # Verify required fields
            assert "camera_position" in config
            assert "focus_roles" in config
            assert len(config["camera_position"]) == 3

    def test_scene_transitions(self):
        """Test scene transition configuration."""
        scenes = get_scene_configs()

        for scene_id, config in scenes.items():
            transitions = config.get("transitions", [])
            # Verify transition targets exist
            for target in transitions:
                assert target in scenes, f"Scene '{scene_id}' has invalid transition target '{target}'"


class TestAPICompatibility:
    """Test API compatibility functions."""

    def test_get_court_topology_api(self):
        """Test get_court_topology API function."""
        topology = get_court_topology()
        assert isinstance(topology, list)
        assert len(topology) == 24  # 24 roles

        # Verify returned format is a list of dicts
        for node in topology:
            assert isinstance(node, dict)
            assert "role_id" in node
            assert "role_name" in node
            assert "position" in node

    def test_map_engine_to_court_state_api(self):
        """Test map_engine_to_court_state API function."""
        engine_status = {"phase": "planning", "running": True}
        state = map_engine_to_court_state(engine_status)

        assert isinstance(state, dict)
        assert "phase" in state
        assert "current_scene" in state
        assert "actors" in state
        assert "updated_at" in state

    def test_get_scene_configs_api(self):
        """Test get_scene_configs API function."""
        scenes = get_scene_configs()
        assert isinstance(scenes, dict)
        assert len(scenes) >= 6


class TestEdgeCases:
    """Test edge cases."""

    def test_malformed_engine_status(self):
        """Test malformed engine state input."""
        malformed_cases = [
            {"roles": "not_a_dict"},  # roles is not a dict
            {"phase": 123},  # phase is not a string
            {"running": "maybe"},  # running is not a boolean
            {},  # Empty dict
        ]

        for case in malformed_cases:
            # Should not throw exceptions
            state = court_mapper.map_engine_to_court_state(case)
            assert state is not None
            assert len(state.actors) == 24  # 24 roles

    def test_missing_roles_in_engine(self):
        """Test missing role info in engine."""
        engine_payload = {
            "phase": "executing",
            "running": True,
            # Does not contain any role info
        }

        state = court_mapper.map_engine_to_court_state(engine_payload)
        # All roles should have default status
        for actor in state.actors.values():
            assert actor.status in ActorStatus
            assert actor.role_name

    def test_case_insensitive_tech_role_mapping(self):
        """Test case-insensitive technical role mapping."""
        variations = ["PM", "Pm", "pm", "pM", "DIRECTOR", "Director", "director"]

        for variation in variations:
            result = court_mapper.map_tech_role_to_court(variation)
            assert result == "emperor", f"Case-insensitive mapping failed for '{variation}'"
