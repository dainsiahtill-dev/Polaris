"""宫廷投影系统模型定义.

本模块定义了宫廷化 3D UI 投影所需的数据模型，包括角色状态、场景拓扑、
证据引用等类型。所有文本字段使用 UTF-8 编码。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CourtRole(str, Enum):
    """宫廷角色枚举."""

    # 天子
    EMPEROR = "emperor"
    # 三省
    ZHONGSHU_LING = "zhongshu_ling"  # 中书令
    ZHONGSHU_SHILANG = "zhongshu_shilang"  # 中书侍郎
    MENXIA_SHILANG = "menxia_shilang"  # 门下侍郎
    MENXIA_SHIZHONG = "menxia_shizhong"  # 门下侍中
    SHANGBG_LING = "shangshu_ling"  # 尚书令
    # 六部
    LIBU_SHANGSHU = "libu_shangshu"  # 吏部尚书
    HUBU_SHANGSHU = "hubu_shangshu"  # 户部尚书
    LIBU_SHANGSHU2 = "libu_shangshu2"  # 礼部尚书
    BINGBU_SHANGSHU = "bingbu_shangshu"  # 兵部尚书
    XINGBU_SHANGSHU = "xingbu_shangshu"  # 刑部尚书
    GONGBU_SHANGSHU = "gongbu_shangshu"  # 工部尚书
    # 部属官员（每部2名）
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
    """宫廷场景阶段枚举."""

    COURT_AUDIENCE = "court_audience"  # 太极殿听政
    DRAFT = "draft"  # 中书省制诏
    DECOMPOSE = "decompose"  # 尚书省拆解任务
    BLUEPRINT = "blueprint"  # 工部蓝图
    BUILD = "build"  # 营造司施工
    REVIEW = "review"  # 门下省审议
    FINALIZE = "finalize"  # 回朝复命


class ActorStatus(str, Enum):
    """角色状态枚举.

    优先级: failed > blocked > executing > thinking > success > idle > offline
    """

    OFFLINE = "offline"
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    DISPATCHING = "dispatching"  # 新增：派发中
    REVIEWING = "reviewing"  # 新增：审议中
    APPROVING = "approving"  # 新增：审核中
    BLOCKED = "blocked"
    SUCCESS = "success"
    FAILED = "failed"


class RiskLevel(str, Enum):
    """风险等级枚举."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CourtEvidenceRef:
    """证据引用定义.

    Attributes:
        path: 证据文件路径
        channel: 来源通道
        run_id: 运行ID
        task_id: 任务ID
        event_id: 事件ID
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
    """宫廷角色状态定义.

    Attributes:
        role_id: 角色唯一标识
        role_name: 角色显示名称
        status: 当前状态
        current_action: 当前动作描述
        task_id: 关联任务ID
        risk_level: 风险等级
        evidence_refs: 证据引用列表
        metadata: 额外元数据
        updated_at: 更新时间戳
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
    """宫廷拓扑节点定义.

    Attributes:
        role_id: 角色唯一标识
        role_name: 角色显示名称
        parent_id: 父节点角色ID
        position: 3D场景位置坐标 [x, y, z]
        department: 所属部门
        level: 层级（用于排序和显示）
        is_interactive: 是否可交互
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
    """宫廷动作事件定义.

    Attributes:
        action_type: 动作类型
        from_role: 发起角色ID
        to_role: 目标角色ID
        payload: 动作负载数据
        ts: 时间戳
        evidence_refs: 关联证据引用
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
    """宫廷场景配置定义.

    Attributes:
        scene_id: 场景唯一标识
        scene_name: 场景显示名称
        phase: 场景阶段
        description: 场景描述
        camera_position: 相机位置
        focus_roles: 焦点角色列表
        transitions: 可切换的目标场景
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
    """完整宫廷状态定义.

    这是 WebSocket 推送和 GET /court/state 返回的完整数据结构。

    Attributes:
        phase: 当前场景阶段
        current_scene: 当前场景ID
        actors: 所有角色状态映射
        topology: 拓扑结构（可选，通常静态）
        recent_events: 最近事件列表
        updated_at: 更新时间戳
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
