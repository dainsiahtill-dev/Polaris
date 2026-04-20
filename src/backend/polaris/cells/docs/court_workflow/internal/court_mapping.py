"""宫廷角色映射服务.

本模块实现技术角色到古制角色的映射逻辑，是宫廷投影系统的核心映射层。
遵循"纯古制展示优先"原则，技术角色在映射层吸收，不直接暴露给最终用户。
"""

from datetime import datetime
from typing import Any

from .models.court import (
    ActorStatus,
    CourtActorState,
    CourtScenePhase,
    CourtState,
    CourtTopologyNode,
    RiskLevel,
)

# 固定宫廷拓扑定义
COURT_TOPOLOGY: list[CourtTopologyNode] = [
    # 天子
    CourtTopologyNode("emperor", "天子", None, [0, 2, 0], "imperial", 0, True),
    # 三省（中书省、门下省、尚书省）
    CourtTopologyNode("zhongshu_ling", "中书令", "emperor", [-4, 1, 2], "zhongshu", 1, True),
    CourtTopologyNode("zhongshu_shilang", "中书侍郎", "zhongshu_ling", [-5, 0.5, 3], "zhongshu", 2, True),
    CourtTopologyNode("menxia_shilang", "门下侍郎", "emperor", [4, 1, 2], "menxia", 1, True),
    CourtTopologyNode("menxia_shizhong", "门下侍中", "menxia_shilang", [5, 0.5, 3], "menxia", 2, True),
    CourtTopologyNode("shangshu_ling", "尚书令", "emperor", [0, 1, 4], "shangshu", 1, True),
    # 六部
    CourtTopologyNode("libu_shangshu", "吏部尚书", "shangshu_ling", [-6, 0.5, 6], "libu", 2, True),
    CourtTopologyNode("hubu_shangshu", "户部尚书", "shangshu_ling", [-3.6, 0.5, 6], "hubu", 2, True),
    CourtTopologyNode("libu_shangshu2", "礼部尚书", "shangshu_ling", [-1.2, 0.5, 6], "libu2", 2, True),
    CourtTopologyNode("bingbu_shangshu", "兵部尚书", "shangshu_ling", [1.2, 0.5, 6], "bingbu", 2, True),
    CourtTopologyNode("xingbu_shangshu", "刑部尚书", "shangshu_ling", [3.6, 0.5, 6], "xingbu", 2, True),
    CourtTopologyNode("gongbu_shangshu", "工部尚书", "shangshu_ling", [6, 0.5, 6], "gongbu", 2, True),
    # 部属官员（每部2名）
    CourtTopologyNode("libu_officer_1", "吏部员外郎", "libu_shangshu", [-7, 0, 8], "libu", 3, True),
    CourtTopologyNode("libu_officer_2", "吏部主事", "libu_shangshu", [-5, 0, 8], "libu", 3, True),
    CourtTopologyNode("hubu_officer_1", "户部员外郎", "hubu_shangshu", [-4.6, 0, 8], "hubu", 3, True),
    CourtTopologyNode("hubu_officer_2", "户部主事", "hubu_shangshu", [-2.6, 0, 8], "hubu", 3, True),
    CourtTopologyNode("libu2_officer_1", "礼部员外郎", "libu_shangshu2", [-2.2, 0, 8], "libu2", 3, True),
    CourtTopologyNode("libu2_officer_2", "礼部主事", "libu_shangshu2", [-0.2, 0, 8], "libu2", 3, True),
    CourtTopologyNode("bingbu_officer_1", "兵部员外郎", "bingbu_shangshu", [0.2, 0, 8], "bingbu", 3, True),
    CourtTopologyNode("bingbu_officer_2", "兵部主事", "bingbu_shangshu", [2.2, 0, 8], "bingbu", 3, True),
    CourtTopologyNode("xingbu_officer_1", "刑部员外郎", "xingbu_shangshu", [2.6, 0, 8], "xingbu", 3, True),
    CourtTopologyNode("xingbu_officer_2", "刑部主事", "xingbu_shangshu", [4.6, 0, 8], "xingbu", 3, True),
    CourtTopologyNode("gongbu_officer_1", "工部员外郎", "gongbu_shangshu", [5, 0, 8], "gongbu", 3, True),
    CourtTopologyNode("gongbu_officer_2", "工部主事", "gongbu_shangshu", [7, 0, 8], "gongbu", 3, True),
]


# 技术角色到宫廷角色的映射表
TECH_TO_COURT_ROLE_MAPPING: dict[str, str] = {
    # PM/Director 映射到天子（统筹全局）
    "pm": "emperor",
    "director": "emperor",
    "orchestrator": "emperor",
    # 规划类角色映射到中书省
    "planner": "zhongshu_ling",
    "architect": "zhongshu_shilang",
    # 审核类角色映射到门下省
    "reviewer": "menxia_shilang",
    "auditor": "menxia_shizhong",
    # 任务编排映射到尚书省
    "dispatcher": "shangshu_ling",
    "coordinator": "shangshu_ling",
    # 执行类角色映射到工部
    "executor": "gongbu_shangshu",
    "builder": "gongbu_shangshu",
    "worker": "gongbu_officer_1",
    # 默认映射
    "default": "gongbu_officer_2",
}


# 场景配置定义
COURT_SCENE_CONFIGS: dict[str, Any] = {
    "taiji_hall": {
        "scene_id": "taiji_hall",
        "scene_name": "太极殿",
        "phase": CourtScenePhase.COURT_AUDIENCE,
        "description": "天子听政之所，全局总览中心",
        "camera_position": [0, 8, 15],
        "focus_roles": ["emperor"],
        "transitions": ["zhongshu_pavilion", "shangshu_hall", "menxia_tower"],
    },
    "zhongshu_pavilion": {
        "scene_id": "zhongshu_pavilion",
        "scene_name": "中书省·制诏阁",
        "phase": CourtScenePhase.DRAFT,
        "description": "中书令草拟圣旨之处",
        "camera_position": [-6, 5, 8],
        "focus_roles": ["zhongshu_ling", "zhongshu_shilang"],
        "transitions": ["taiji_hall", "shangshu_hall"],
    },
    "shangshu_hall": {
        "scene_id": "shangshu_hall",
        "scene_name": "尚书省·政务厅",
        "phase": CourtScenePhase.DECOMPOSE,
        "description": "尚书令拆解任务、派发令牌之所",
        "camera_position": [0, 5, 10],
        "focus_roles": ["shangshu_ling"],
        "transitions": ["taiji_hall", "gongbu_blueprint"],
    },
    "gongbu_blueprint": {
        "scene_id": "gongbu_blueprint",
        "scene_name": "工部·蓝图台",
        "phase": CourtScenePhase.BLUEPRINT,
        "description": "工部尚书绘制施工图之所",
        "camera_position": [6, 4, 8],
        "focus_roles": ["gongbu_shangshu", "gongbu_officer_1", "gongbu_officer_2"],
        "transitions": ["shangshu_hall", "construction_site"],
    },
    "construction_site": {
        "scene_id": "construction_site",
        "scene_name": "营造司·施工现场",
        "phase": CourtScenePhase.BUILD,
        "description": "工匠施工之地，代码即建筑",
        "camera_position": [8, 3, 6],
        "focus_roles": ["gongbu_officer_1", "gongbu_officer_2"],
        "transitions": ["gongbu_blueprint", "menxia_tower"],
    },
    "menxia_tower": {
        "scene_id": "menxia_tower",
        "scene_name": "门下省·审议台",
        "phase": CourtScenePhase.REVIEW,
        "description": "门下侍郎初审、侍中终审之所",
        "camera_position": [6, 5, 8],
        "focus_roles": ["menxia_shilang", "menxia_shizhong"],
        "transitions": ["taiji_hall", "construction_site"],
    },
}


class CourtRoleMapping:
    """宫廷角色映射器.

    负责将技术运行态数据映射为古制宫廷状态，是投影系统的核心逻辑层。
    """

    def __init__(self) -> None:
        self._topology_map: dict[str, CourtTopologyNode] = {node.role_id: node for node in COURT_TOPOLOGY}
        self._role_display_names: dict[str, str] = {node.role_id: node.role_name for node in COURT_TOPOLOGY}

    def get_topology(self) -> list[CourtTopologyNode]:
        """获取完整宫廷拓扑."""
        return COURT_TOPOLOGY.copy()

    def get_role_state(self, role_id: str) -> CourtActorState | None:
        """获取指定角色的默认状态."""
        if role_id not in self._topology_map:
            return None
        return CourtActorState(
            role_id=role_id,
            role_name=self._role_display_names.get(role_id, role_id),
            status=ActorStatus.IDLE,
            current_action="待机中",
        )

    def map_tech_role_to_court(self, tech_role: str) -> str:
        """将技术角色映射到宫廷角色.

        Args:
            tech_role: 技术角色标识

        Returns:
            宫廷角色ID
        """
        return TECH_TO_COURT_ROLE_MAPPING.get(tech_role.lower(), "gongbu_officer_2")

    def _determine_status_from_engine(self, engine_payload: dict[str, Any], role_id: str) -> ActorStatus:
        """根据引擎状态确定角色状态.

        优先级: failed > blocked > executing > thinking > success > idle > offline
        """
        roles = engine_payload.get("roles", {})
        if role_id in roles:
            role_data = roles[role_id]
            status_str = str(role_data.get("status", "")).lower()
            running = bool(role_data.get("running", False))

            if status_str in {"failed", "error"}:
                return ActorStatus.FAILED
            elif status_str == "blocked":
                return ActorStatus.BLOCKED
            elif running or status_str == "running":
                return ActorStatus.EXECUTING
            elif status_str in {"planning", "thinking"}:
                return ActorStatus.THINKING
            elif status_str in {"success", "completed"}:
                return ActorStatus.SUCCESS
            elif status_str == "idle":
                return ActorStatus.IDLE

        # 检查引擎整体状态
        phase = str(engine_payload.get("phase", "")).lower()
        running = bool(engine_payload.get("running", False))

        if not running:
            return ActorStatus.OFFLINE
        if phase == "failed":
            return ActorStatus.FAILED

        return ActorStatus.IDLE

    def _determine_risk_level(self, engine_payload: dict[str, Any], role_id: str) -> RiskLevel:
        """根据引擎数据确定风险等级."""
        error_count = 0
        recent_errors = engine_payload.get("recent_errors", [])
        if isinstance(recent_errors, list):
            error_count = len(recent_errors)

        if error_count > 10:
            return RiskLevel.CRITICAL
        elif error_count > 5:
            return RiskLevel.HIGH
        elif error_count > 2:
            return RiskLevel.MEDIUM
        elif error_count > 0:
            return RiskLevel.LOW
        return RiskLevel.NONE

    def map_engine_to_court_state(
        self,
        engine_status: dict[str, Any] | None,
        pm_status: dict[str, Any] | None = None,
        director_status: dict[str, Any] | None = None,
    ) -> CourtState:
        """将引擎状态映射为完整宫廷状态.

        Args:
            engine_status: 引擎状态JSON
            pm_status: PM状态
            director_status: Director状态

        Returns:
            完整宫廷状态对象
        """
        # 确定当前阶段
        phase = CourtScenePhase.COURT_AUDIENCE
        current_scene = "taiji_hall"

        if engine_status:
            engine_phase = str(engine_status.get("phase", "")).lower()
            phase_mapping = {
                "planning": CourtScenePhase.DRAFT,
                "drafting": CourtScenePhase.DRAFT,
                "decomposing": CourtScenePhase.DECOMPOSE,
                "dispatching": CourtScenePhase.DECOMPOSE,
                "blueprint": CourtScenePhase.BLUEPRINT,
                "building": CourtScenePhase.BUILD,
                "executing": CourtScenePhase.BUILD,
                "reviewing": CourtScenePhase.REVIEW,
                "auditing": CourtScenePhase.REVIEW,
                "finalizing": CourtScenePhase.FINALIZE,
            }
            phase = phase_mapping.get(engine_phase, CourtScenePhase.COURT_AUDIENCE)

            # 根据阶段确定场景
            scene_mapping = {
                CourtScenePhase.DRAFT: "zhongshu_pavilion",
                CourtScenePhase.DECOMPOSE: "shangshu_hall",
                CourtScenePhase.BLUEPRINT: "gongbu_blueprint",
                CourtScenePhase.BUILD: "construction_site",
                CourtScenePhase.REVIEW: "menxia_tower",
                CourtScenePhase.FINALIZE: "taiji_hall",
            }
            current_scene = scene_mapping.get(phase, "taiji_hall")

        # 构建所有角色状态
        actors: dict[str, CourtActorState] = {}
        for node in COURT_TOPOLOGY:
            status = ActorStatus.IDLE
            action = "待机中"
            risk = RiskLevel.NONE
            task_id = None

            if engine_status:
                status = self._determine_status_from_engine(engine_status, node.role_id)
                risk = self._determine_risk_level(engine_status, node.role_id)

                # 根据角色和阶段确定动作
                if node.role_id == "emperor":
                    if phase == CourtScenePhase.COURT_AUDIENCE:
                        action = "听政"
                    elif phase == CourtScenePhase.FINALIZE:
                        action = "裁决"
                    else:
                        action = "颁诏"
                elif node.role_id in ["zhongshu_ling", "zhongshu_shilang"]:
                    action = "草拟圣旨" if phase == CourtScenePhase.DRAFT else "待诏"
                elif node.role_id == "shangshu_ling":
                    action = "拆解政务" if phase == CourtScenePhase.DECOMPOSE else "统筹"
                elif node.department == "gongbu":
                    if phase == CourtScenePhase.BLUEPRINT:
                        action = "绘制蓝图"
                    elif phase == CourtScenePhase.BUILD:
                        action = "监督施工"
                    else:
                        action = "待命"
                elif node.department in ["menxia"]:
                    action = "审议" if phase == CourtScenePhase.REVIEW else "待审"

            actors[node.role_id] = CourtActorState(
                role_id=node.role_id,
                role_name=node.role_name,
                status=status,
                current_action=action,
                task_id=task_id,
                risk_level=risk,
                evidence_refs=[],
                metadata={},
            )

        return CourtState(
            phase=phase,
            current_scene=current_scene,
            actors=actors,
            topology=COURT_TOPOLOGY,
            recent_events=[],
            updated_at=datetime.now().timestamp(),
        )

    def get_scene_configs(self) -> dict[str, Any]:
        """获取所有场景配置."""
        return COURT_SCENE_CONFIGS.copy()


# 全局映射器实例
court_mapper = CourtRoleMapping()


def get_court_topology() -> list[dict[str, Any]]:
    """获取宫廷拓扑（API 兼容函数）."""
    return [node.to_dict() for node in court_mapper.get_topology()]


def map_engine_to_court_state(
    engine_status: dict[str, Any] | None = None,
    pm_status: dict[str, Any] | None = None,
    director_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """映射引擎状态到宫廷状态（API 兼容函数）."""
    state = court_mapper.map_engine_to_court_state(engine_status, pm_status, director_status)
    return state.to_dict()


def get_scene_configs() -> dict[str, Any]:
    """获取场景配置（API 兼容函数）."""
    return court_mapper.get_scene_configs()
