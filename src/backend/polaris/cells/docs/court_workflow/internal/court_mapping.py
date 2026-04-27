"""Role Mapping Service.

This module implements the mapping logic from technical roles to display roles, and is the core mapping layer of the court projection system.
Follows the "pure display-role priority" principle; technical roles are absorbed at the mapping layer and not directly exposed to end users.
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

# Fixed court topology definition
COURT_TOPOLOGY: list[CourtTopologyNode] = [
    # User
    CourtTopologyNode("emperor", "User", None, [0, 2, 0], "imperial", 0, True),
    # Top departments (Architect, QA, PM)
    CourtTopologyNode("zhongshu_ling", "Architect", "emperor", [-4, 1, 2], "zhongshu", 1, True),
    CourtTopologyNode("zhongshu_shilang", "Architect Deputy", "zhongshu_ling", [-5, 0.5, 3], "zhongshu", 2, True),
    CourtTopologyNode("menxia_shilang", "QA Deputy", "emperor", [4, 1, 2], "menxia", 1, True),
    CourtTopologyNode("menxia_shizhong", "QA", "menxia_shilang", [5, 0.5, 3], "menxia", 2, True),
    CourtTopologyNode("shangshu_ling", "PM", "emperor", [0, 1, 4], "shangshu", 1, True),
    # Departments
    CourtTopologyNode("libu_shangshu", "HR Lead", "shangshu_ling", [-6, 0.5, 6], "libu", 2, True),
    CourtTopologyNode("hubu_shangshu", "FinOps Lead", "shangshu_ling", [-3.6, 0.5, 6], "hubu", 2, True),
    CourtTopologyNode("libu_shangshu2", "Protocol Lead", "shangshu_ling", [-1.2, 0.5, 6], "libu2", 2, True),
    CourtTopologyNode("bingbu_shangshu", "Security Lead", "shangshu_ling", [1.2, 0.5, 6], "bingbu", 2, True),
    CourtTopologyNode("xingbu_shangshu", "Policy Lead", "shangshu_ling", [3.6, 0.5, 6], "xingbu", 2, True),
    CourtTopologyNode("gongbu_shangshu", "Chief Engineer", "shangshu_ling", [6, 0.5, 6], "gongbu", 2, True),
    # Department officers (2 per department)
    CourtTopologyNode("libu_officer_1", "HR Officer", "libu_shangshu", [-7, 0, 8], "libu", 3, True),
    CourtTopologyNode("libu_officer_2", "HR Clerk", "libu_shangshu", [-5, 0, 8], "libu", 3, True),
    CourtTopologyNode("hubu_officer_1", "FinOps Officer", "hubu_shangshu", [-4.6, 0, 8], "hubu", 3, True),
    CourtTopologyNode("hubu_officer_2", "FinOps Clerk", "hubu_shangshu", [-2.6, 0, 8], "hubu", 3, True),
    CourtTopologyNode("libu2_officer_1", "Protocol Officer", "libu_shangshu2", [-2.2, 0, 8], "libu2", 3, True),
    CourtTopologyNode("libu2_officer_2", "Protocol Clerk", "libu_shangshu2", [-0.2, 0, 8], "libu2", 3, True),
    CourtTopologyNode("bingbu_officer_1", "Security Officer", "bingbu_shangshu", [0.2, 0, 8], "bingbu", 3, True),
    CourtTopologyNode("bingbu_officer_2", "Security Clerk", "bingbu_shangshu", [2.2, 0, 8], "bingbu", 3, True),
    CourtTopologyNode("xingbu_officer_1", "Policy Officer", "xingbu_shangshu", [2.6, 0, 8], "xingbu", 3, True),
    CourtTopologyNode("xingbu_officer_2", "Policy Clerk", "xingbu_shangshu", [4.6, 0, 8], "xingbu", 3, True),
    CourtTopologyNode("gongbu_officer_1", "Engineering Officer", "gongbu_shangshu", [5, 0, 8], "gongbu", 3, True),
    CourtTopologyNode("gongbu_officer_2", "Engineering Clerk", "gongbu_shangshu", [7, 0, 8], "gongbu", 3, True),
]


# Technical role to court role mapping table
TECH_TO_COURT_ROLE_MAPPING: dict[str, str] = {
    # PM/Director mapped to User (oversees all)
    "pm": "emperor",
    "director": "emperor",
    "orchestrator": "emperor",
    # Planning roles mapped to Architect
    "planner": "zhongshu_ling",
    "architect": "zhongshu_shilang",
    # Review roles mapped to QA
    "reviewer": "menxia_shilang",
    "auditor": "menxia_shizhong",
    # Task orchestration mapped to PM
    "dispatcher": "shangshu_ling",
    "coordinator": "shangshu_ling",
    # Execution roles mapped to Engineering
    "executor": "gongbu_shangshu",
    "builder": "gongbu_shangshu",
    "worker": "gongbu_officer_1",
    # Default mapping
    "default": "gongbu_officer_2",
}


# Scene configuration definitions
COURT_SCENE_CONFIGS: dict[str, Any] = {
    "taiji_hall": {
        "scene_id": "taiji_hall",
        "scene_name": "Main Hall",
        "phase": CourtScenePhase.COURT_AUDIENCE,
        "description": "User command center, global overview hub",
        "camera_position": [0, 8, 15],
        "focus_roles": ["emperor"],
        "transitions": ["zhongshu_pavilion", "shangshu_hall", "menxia_tower"],
    },
    "zhongshu_pavilion": {
        "scene_id": "zhongshu_pavilion",
        "scene_name": "Architect Drafting Pavilion",
        "phase": CourtScenePhase.DRAFT,
        "description": "Where Architect drafts blueprints",
        "camera_position": [-6, 5, 8],
        "focus_roles": ["zhongshu_ling", "zhongshu_shilang"],
        "transitions": ["taiji_hall", "shangshu_hall"],
    },
    "shangshu_hall": {
        "scene_id": "shangshu_hall",
        "scene_name": "PM Affairs Hall",
        "phase": CourtScenePhase.DECOMPOSE,
        "description": "Where PM decomposes tasks and dispatches tokens",
        "camera_position": [0, 5, 10],
        "focus_roles": ["shangshu_ling"],
        "transitions": ["taiji_hall", "gongbu_blueprint"],
    },
    "gongbu_blueprint": {
        "scene_id": "gongbu_blueprint",
        "scene_name": "Engineering Blueprint Desk",
        "phase": CourtScenePhase.BLUEPRINT,
        "description": "Where Chief Engineer draws construction blueprints",
        "camera_position": [6, 4, 8],
        "focus_roles": ["gongbu_shangshu", "gongbu_officer_1", "gongbu_officer_2"],
        "transitions": ["shangshu_hall", "construction_site"],
    },
    "construction_site": {
        "scene_id": "construction_site",
        "scene_name": "Engineering Construction Site",
        "phase": CourtScenePhase.BUILD,
        "description": "Where engineers build; code is the building",
        "camera_position": [8, 3, 6],
        "focus_roles": ["gongbu_officer_1", "gongbu_officer_2"],
        "transitions": ["gongbu_blueprint", "menxia_tower"],
    },
    "menxia_tower": {
        "scene_id": "menxia_tower",
        "scene_name": "QA Review Bench",
        "phase": CourtScenePhase.REVIEW,
        "description": "Where QA Deputy does preliminary review and QA does final review",
        "camera_position": [6, 5, 8],
        "focus_roles": ["menxia_shilang", "menxia_shizhong"],
        "transitions": ["taiji_hall", "construction_site"],
    },
}


class CourtRoleMapping:
    """Court role mapper.

    Responsible for mapping technical runtime data to display court states; it is the core logic layer of the projection system.
    """

    def __init__(self) -> None:
        self._topology_map: dict[str, CourtTopologyNode] = {node.role_id: node for node in COURT_TOPOLOGY}
        self._role_display_names: dict[str, str] = {node.role_id: node.role_name for node in COURT_TOPOLOGY}

    def get_topology(self) -> list[CourtTopologyNode]:
        """Get full court topology."""
        return COURT_TOPOLOGY.copy()

    def get_role_state(self, role_id: str) -> CourtActorState | None:
        """Get default state for a specified role."""
        if role_id not in self._topology_map:
            return None
        return CourtActorState(
            role_id=role_id,
            role_name=self._role_display_names.get(role_id, role_id),
            status=ActorStatus.IDLE,
            current_action="待机中",
        )

    def map_tech_role_to_court(self, tech_role: str) -> str:
        """Map technical role to court role.

        Args:
            tech_role: technical role identifier

        Returns:
            court role ID
        """
        return TECH_TO_COURT_ROLE_MAPPING.get(tech_role.lower(), "gongbu_officer_2")

    def _determine_status_from_engine(self, engine_payload: dict[str, Any], role_id: str) -> ActorStatus:
        """Determine role status from engine state.

        Priority: failed > blocked > executing > thinking > success > idle > offline
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

        # Check overall engine status
        phase = str(engine_payload.get("phase", "")).lower()
        running = bool(engine_payload.get("running", False))

        if not running:
            return ActorStatus.OFFLINE
        if phase == "failed":
            return ActorStatus.FAILED

        return ActorStatus.IDLE

    def _determine_risk_level(self, engine_payload: dict[str, Any], role_id: str) -> RiskLevel:
        """Determine risk level from engine data."""
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
        """Map engine state to full court state.

        Args:
            engine_status: engine status JSON
            pm_status: PM status
            director_status: Director status

        Returns:
            full court state object
        """
        # Determine current phase
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

            # Determine scene based on phase
            scene_mapping = {
                CourtScenePhase.DRAFT: "zhongshu_pavilion",
                CourtScenePhase.DECOMPOSE: "shangshu_hall",
                CourtScenePhase.BLUEPRINT: "gongbu_blueprint",
                CourtScenePhase.BUILD: "construction_site",
                CourtScenePhase.REVIEW: "menxia_tower",
                CourtScenePhase.FINALIZE: "taiji_hall",
            }
            current_scene = scene_mapping.get(phase, "taiji_hall")

        # Build all actor states
        actors: dict[str, CourtActorState] = {}
        for node in COURT_TOPOLOGY:
            status = ActorStatus.IDLE
            action = "待机中"
            risk = RiskLevel.NONE
            task_id = None

            if engine_status:
                status = self._determine_status_from_engine(engine_status, node.role_id)
                risk = self._determine_risk_level(engine_status, node.role_id)

                # Determine action based on role and phase
                if node.role_id == "emperor":
                    if phase == CourtScenePhase.COURT_AUDIENCE:
                        action = "Reviewing"
                    elif phase == CourtScenePhase.FINALIZE:
                        action = "Deciding"
                    else:
                        action = "Issuing orders"
                elif node.role_id in ["zhongshu_ling", "zhongshu_shilang"]:
                    action = "Drafting blueprint" if phase == CourtScenePhase.DRAFT else "On call"
                elif node.role_id == "shangshu_ling":
                    action = "Decomposing tasks" if phase == CourtScenePhase.DECOMPOSE else "Coordinating"
                elif node.department == "gongbu":
                    if phase == CourtScenePhase.BLUEPRINT:
                        action = "Drawing blueprint"
                    elif phase == CourtScenePhase.BUILD:
                        action = "Supervising construction"
                    else:
                        action = "On standby"
                elif node.department in ["menxia"]:
                    action = "Reviewing" if phase == CourtScenePhase.REVIEW else "Pending review"

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
        """Get all scene configs."""
        return COURT_SCENE_CONFIGS.copy()


# Global mapper instance
court_mapper = CourtRoleMapping()


def get_court_topology() -> list[dict[str, Any]]:
    """Get court topology (API compatibility function)."""
    return [node.to_dict() for node in court_mapper.get_topology()]


def map_engine_to_court_state(
    engine_status: dict[str, Any] | None = None,
    pm_status: dict[str, Any] | None = None,
    director_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map engine state to court state (API compatibility function)."""
    state = court_mapper.map_engine_to_court_state(engine_status, pm_status, director_status)
    return state.to_dict()


def get_scene_configs() -> dict[str, Any]:
    """Get scene configs (API compatibility function)."""
    return court_mapper.get_scene_configs()
