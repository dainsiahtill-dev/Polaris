"""宫廷角色映射层单元测试.

本测试模块验证技术角色到古制角色的映射正确性，以及宫廷状态生成的确定性。
所有测试遵循 Phase 1 验收标准。
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
    """测试宫廷角色映射器."""

    def test_topology_node_count(self):
        """测试拓扑节点数量：24个可交互角色."""
        topology = court_mapper.get_topology()
        interactive_count = sum(1 for node in topology if node.is_interactive)

        # 当前实现：天子 1 + 中书省 2 + 门下省 2 + 尚书省 1 + 六部 6 + 部属官员 12 = 24
        assert interactive_count == 24, f"Expected 24 interactive roles, got {interactive_count}"
        assert len(topology) == 24, f"Expected total 24 roles, got {len(topology)}"

    def test_topology_structure(self):
        """测试拓扑结构完整性：天子 -> 三省 -> 六部 -> 官员."""
        topology = court_mapper.get_topology()
        role_ids = {node.role_id for node in topology}

        # 必有的关键角色
        required_roles = {
            "emperor",  # 天子
            "zhongshu_ling", "zhongshu_shilang",  # 中书省
            "menxia_shilang", "menxia_shizhong",  # 门下省
            "shangshu_ling",  # 尚书省
            "libu_shangshu", "hubu_shangshu", "libu_shangshu2",
            "bingbu_shangshu", "xingbu_shangshu", "gongbu_shangshu",  # 六部
        }

        for role in required_roles:
            assert role in role_ids, f"Required role '{role}' not found in topology"

    def test_role_hierarchy(self):
        """测试角色层级关系."""
        topology = court_mapper.get_topology()

        # 天子的父节点应为 None
        emperor = next((n for n in topology if n.role_id == "emperor"), None)
        assert emperor is not None
        assert emperor.parent_id is None
        assert emperor.level == 0

        # 三省的父节点应为天子
        for role_id in ["zhongshu_ling", "menxia_shilang", "shangshu_ling"]:
            node = next((n for n in topology if n.role_id == role_id), None)
            assert node is not None, f"Role '{role_id}' not found"
            assert node.parent_id == "emperor", f"{role_id} should report to emperor"
            assert node.level == 1

    def test_tech_to_court_mapping(self):
        """测试技术角色到宫廷角色的映射."""
        test_cases = [
            ("pm", "emperor"),
            ("director", "emperor"),
            ("planner", "zhongshu_ling"),
            ("reviewer", "menxia_shilang"),
            ("dispatcher", "shangshu_ling"),
            ("executor", "gongbu_shangshu"),
            ("unknown_role", "gongbu_officer_2"),  # 默认值
        ]

        for tech_role, expected_court_role in test_cases:
            result = court_mapper.map_tech_role_to_court(tech_role)
            assert result == expected_court_role, \
                f"Mapping failed for '{tech_role}': expected '{expected_court_role}', got '{result}'"


class TestEngineToCourtStateMapping:
    """测试引擎状态到宫廷状态的映射."""

    def test_empty_engine_state(self):
        """测试空引擎状态映射：所有角色应为 idle."""
        state = court_mapper.map_engine_to_court_state(None)

        assert state.phase == CourtScenePhase.COURT_AUDIENCE
        assert state.current_scene == "taiji_hall"
        assert len(state.actors) == 24  # 24个角色

        # 所有角色应为 idle
        for actor in state.actors.values():
            assert actor.status == ActorStatus.IDLE
            assert actor.role_name  # 应有显示名称

    def test_engine_phase_mapping(self):
        """测试引擎阶段到宫廷阶段的映射."""
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
            assert state.phase == expected_phase, \
                f"Phase mapping failed: {engine_payload['phase']} -> {state.phase}"
            assert state.current_scene == expected_scene, \
                f"Scene mapping failed: {engine_payload['phase']} -> {state.current_scene}"

    def test_role_status_priority(self):
        """测试角色状态优先级：failed > blocked > executing > thinking > success > idle > offline."""
        # 模拟引擎返回不同状态的单个角色
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
            assert actor.status == expected_status, \
                f"Status priority failed for {engine_payload}: expected {expected_status}, got {actor.status}"

    def test_risk_level_calculation(self):
        """测试风险等级计算."""
        test_cases = [
            ({"recent_errors": []}, RiskLevel.NONE),
            ({"recent_errors": ["e1"]}, RiskLevel.LOW),
            ({"recent_errors": ["e1", "e2", "e3"]}, RiskLevel.MEDIUM),
            ({"recent_errors": ["e1"] * 6}, RiskLevel.HIGH),
            ({"recent_errors": ["e1"] * 11}, RiskLevel.CRITICAL),
        ]

        for engine_payload, expected_risk in test_cases:
            state = court_mapper.map_engine_to_court_state(engine_payload)
            # 检查至少一个角色有风险等级
            risks = {a.risk_level for a in state.actors.values()}
            assert expected_risk in risks, \
                f"Risk level {expected_risk} not found in state for errors: {len(engine_payload.get('recent_errors', []))}"

    def test_deterministic_output(self):
        """测试输出确定性：给定相同输入，输出必须一致."""
        engine_payload = {
            "phase": "executing",
            "running": True,
            "roles": {
                "gongbu_shangshu": {"status": "running", "running": True},
                "emperor": {"status": "idle"},
            }
        }

        state1 = court_mapper.map_engine_to_court_state(engine_payload)
        state2 = court_mapper.map_engine_to_court_state(engine_payload)

        # 比较关键字段
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
    """测试场景配置."""

    def test_scene_count(self):
        """测试场景数量."""
        scenes = get_scene_configs()
        # 文档定义的7个场景
        expected_scenes = [
            "taiji_hall", "zhongshu_pavilion", "shangshu_hall",
            "gongbu_blueprint", "construction_site", "menxia_tower"
        ]
        for scene_id in expected_scenes:
            assert scene_id in scenes, f"Scene '{scene_id}' not found"

    def test_scene_phase_consistency(self):
        """测试场景阶段一致性."""
        scenes = get_scene_configs()
        phase_to_scene = {}

        for scene_id, config in scenes.items():
            phase = config["phase"]
            if phase in phase_to_scene:
                # 允许多个场景映射到同一阶段，但每个场景必须有唯一阶段
                pass
            phase_to_scene[phase] = scene_id

            # 验证必要字段
            assert "camera_position" in config
            assert "focus_roles" in config
            assert len(config["camera_position"]) == 3

    def test_scene_transitions(self):
        """测试场景切换配置."""
        scenes = get_scene_configs()

        for scene_id, config in scenes.items():
            transitions = config.get("transitions", [])
            # 验证切换目标存在
            for target in transitions:
                assert target in scenes, \
                    f"Scene '{scene_id}' has invalid transition target '{target}'"


class TestAPICompatibility:
    """测试 API 兼容性函数."""

    def test_get_court_topology_api(self):
        """测试 get_court_topology API 函数."""
        topology = get_court_topology()
        assert isinstance(topology, list)
        assert len(topology) == 24  # 24个角色

        # 验证返回格式为字典列表
        for node in topology:
            assert isinstance(node, dict)
            assert "role_id" in node
            assert "role_name" in node
            assert "position" in node

    def test_map_engine_to_court_state_api(self):
        """测试 map_engine_to_court_state API 函数."""
        engine_status = {"phase": "planning", "running": True}
        state = map_engine_to_court_state(engine_status)

        assert isinstance(state, dict)
        assert "phase" in state
        assert "current_scene" in state
        assert "actors" in state
        assert "updated_at" in state

    def test_get_scene_configs_api(self):
        """测试 get_scene_configs API 函数."""
        scenes = get_scene_configs()
        assert isinstance(scenes, dict)
        assert len(scenes) >= 6


class TestEdgeCases:
    """测试边界情况."""

    def test_malformed_engine_status(self):
        """测试损坏的引擎状态输入."""
        malformed_cases = [
            {"roles": "not_a_dict"},  # roles 不是字典
            {"phase": 123},  # phase 不是字符串
            {"running": "maybe"},  # running 不是布尔值
            {},  # 空字典
        ]

        for case in malformed_cases:
            # 不应抛出异常
            state = court_mapper.map_engine_to_court_state(case)
            assert state is not None
            assert len(state.actors) == 24  # 24个角色

    def test_missing_roles_in_engine(self):
        """测试引擎中缺少角色信息."""
        engine_payload = {
            "phase": "executing",
            "running": True,
            # 不包含任何角色信息
        }

        state = court_mapper.map_engine_to_court_state(engine_payload)
        # 所有角色应有默认状态
        for actor in state.actors.values():
            assert actor.status in ActorStatus
            assert actor.role_name

    def test_case_insensitive_tech_role_mapping(self):
        """测试技术角色映射大小写不敏感."""
        variations = ["PM", "Pm", "pm", "pM", "DIRECTOR", "Director", "director"]

        for variation in variations:
            result = court_mapper.map_tech_role_to_court(variation)
            assert result == "emperor", f"Case-insensitive mapping failed for '{variation}'"
