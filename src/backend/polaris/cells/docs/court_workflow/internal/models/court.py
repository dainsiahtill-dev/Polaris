"""Role Projection System Model Definitions.

This module defines the data models required for the 3D UI role projection, including role states, scene topology,
evidence references, and other types. All text fields use UTF-8 encoding.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CourtRole(str, Enum):
    """Role Enumeration."""

    # User
    EMPEROR = "emperor"
    # Top departments
    ZHONGSHU_LING = "zhongshu_ling"  # Architect
    ZHONGSHU_SHILANG = "zhongshu_shilang"  # Architect Deputy
    MENXIA_SHILANG = "menxia_shilang"  # QA Deputy
    MENXIA_SHIZHONG = "menxia_shizhong"  # QA
    SHANGBG_LING = "shangshu_ling"  # PM
    # Departments
    LIBU_SHANGSHU = "libu_shangshu"  # HR Lead
    HUBU_SHANGSHU = "hubu_shangshu"  # FinOps Lead
    LIBU_SHANGSHU2 = "libu_shangshu2"  # Protocol Lead
    BINGBU_SHANGSHU = "bingbu_shangshu"  # Security Lead
    XINGBU_SHANGSHU = "xingbu_shangshu"  # Policy Lead
    GONGBU_SHANGSHU = "gongbu_shangshu"  # Chief Engineer
    # Department officers (2 per department)
    LIBU_OFFICER_1 = "libu_officer_1"
    LIBU_OFFICER_2 = "libu_officer_2"
    HUBU_OFFICER_1 = "hubu_officer_1"
    HUBU_OFFICER_2 = "hubu_officer_2"
    LIBU2_OFFICER_1 = "libu2_officer_1"
    LIBU2_OFFICER_2 = "libu2_officer_2"
    BINGBU_OFFICER_1 = "bingbu_officer_1"
    BINGBU_OFFICER_2 = "bingbu_officer_2"
    XINGBU_OFFICER_1 = "xingbu_officer_1"
    XINGBU_OFFICER_2 = "xingbu_officer_2"
    GONGBU_OFFICER_1 = "gongbu_officer_1"
    GONGBU_OFFICER_2 = "gongbu_officer_2"


class CourtScenePhase(str, Enum):
    """Scene Phase Enumeration."""

    COURT_AUDIENCE = "court_audience"  # User command center
    DRAFT = "draft"  # Architect office drafts blueprints
    DECOMPOSE = "decompose"  # PM office decomposes tasks
    BLUEPRINT = "blueprint"  # Engineering blueprint
    BUILD = "build"  # Construction site
    REVIEW = "review"  # QA office reviews
    FINALIZE = "finalize"  # Return and report completion


class ActorStatus(str, Enum):
    """Actor status enumeration.

    Priority: failed > blocked > executing > thinking > success > idle > offline
    """

    OFFLINE = "offline"
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    DISPATCHING = "dispatching"  # New: dispatching
    REVIEWING = "reviewing"  # New: reviewing
    APPROVING = "approving"  # New: approving
    BLOCKED = "blocked"
    SUCCESS = "success"
    FAILED = "failed"


class RiskLevel(str, Enum):
    """Risk level enumeration."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CourtEvidenceRef:
    """Evidence reference definition.

    Attributes:
        path: Evidence file path
        channel: Source channel
        run_id: Run ID
        task_id: Task ID
        event_id: Event ID
    """

    path: str
    channel: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "channel": self.channel,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "event_id": self.event_id,
        }


@dataclass
class CourtActorState:
    """Court actor state definition.

    Attributes:
        role_id: Unique role identifier
        role_name: Role display name
        status: Current status
        current_action: Current action description
        task_id: Associated task ID
        risk_level: Risk level
        evidence_refs: Evidence reference list
        metadata: Additional metadata
        updated_at: Update timestamp
    """

    role_id: str
    role_name: str
    status: ActorStatus
    current_action: str = ""
    task_id: str | None = None
    risk_level: RiskLevel = RiskLevel.NONE
    evidence_refs: list[CourtEvidenceRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "status": self.status.value,
            "current_action": self.current_action,
            "task_id": self.task_id,
            "risk_level": self.risk_level.value,
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }


@dataclass
class CourtTopologyNode:
    """Court topology node definition.

    Attributes:
        role_id: Unique role identifier
        role_name: Role display name
        parent_id: Parent role ID
        position: 3D scene position coordinates [x, y, z]
        department: Department
        level: Level (for sorting and display)
        is_interactive: Whether interactive
    """

    role_id: str
    role_name: str
    parent_id: str | None = None
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    department: str = ""
    level: int = 0
    is_interactive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "parent_id": self.parent_id,
            "position": self.position,
            "department": self.department,
            "level": self.level,
            "is_interactive": self.is_interactive,
        }


@dataclass
class CourtActionEvent:
    """Court action event definition.

    Attributes:
        action_type: Action type
        from_role: Source role ID
        to_role: Target role ID
        payload: Action payload data
        ts: Timestamp
        evidence_refs: Associated evidence references
    """

    action_type: str
    from_role: str
    to_role: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: datetime.now().timestamp())
    evidence_refs: list[CourtEvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "from_role": self.from_role,
            "to_role": self.to_role,
            "payload": self.payload,
            "ts": self.ts,
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
        }


@dataclass
class CourtSceneConfig:
    """Court scene configuration definition.

    Attributes:
        scene_id: Unique scene identifier
        scene_name: Scene display name
        phase: Scene phase
        description: Scene description
        camera_position: Camera position
        focus_roles: Focus role list
        transitions: Switchable target scenes
    """

    scene_id: str
    scene_name: str
    phase: CourtScenePhase
    description: str = ""
    camera_position: list[float] = field(default_factory=lambda: [0.0, 5.0, 10.0])
    focus_roles: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "scene_name": self.scene_name,
            "phase": self.phase.value,
            "description": self.description,
            "camera_position": self.camera_position,
            "focus_roles": self.focus_roles,
            "transitions": self.transitions,
        }


@dataclass
class CourtState:
    """Full court state definition.

    This is the complete data structure returned by WebSocket push and GET /court/state.

    Attributes:
        phase: Current scene phase
        current_scene: Current scene ID
        actors: All role state mapping
        topology: Topology structure (optional, usually static)
        recent_events: Recent event list
        updated_at: Update timestamp
    """

    phase: CourtScenePhase
    current_scene: str
    actors: dict[str, CourtActorState]
    topology: list[CourtTopologyNode] | None = None
    recent_events: list[CourtActionEvent] = field(default_factory=list)
    updated_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "current_scene": self.current_scene,
            "actors": {k: v.to_dict() for k, v in self.actors.items()},
            "topology": [t.to_dict() for t in self.topology] if self.topology else None,
            "recent_events": [e.to_dict() for e in self.recent_events],
            "updated_at": self.updated_at,
        }
