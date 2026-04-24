"""Role Runtime Schema - 角色运行时类型定义

定义统一角色内核的所有数据模型和类型。

P1-TYPE-007: SequentialMode 类型统一
    - SequentialMode, SequentialTraceLevel: 定义在本模块（canonical）
    - 避免重复定义，保持单一事实来源
"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from polaris.kernelone.utils.time_utils import utc_now
from typing_extensions import TypedDict

# ═══════════════════════════════════════════════════════════════════════════
# Sequential 执行枚举（Canonical 定义 - P1-TYPE-007）
# ═══════════════════════════════════════════════════════════════════════════


class SequentialMode(Enum):
    """Sequential 执行模式"""

    DISABLED = "disabled"  # 禁用
    ENABLED = "enabled"  # 启用
    REQUIRED = "required"  # 强制启用


class SequentialTraceLevel(str, Enum):
    """Sequential 跟踪级别"""

    OFF = "off"
    SUMMARY = "summary"
    DETAILED = "detailed"


# Backward compatibility alias
_utc_now = utc_now


# SSOT: RoleTurnRequest 单一空 ContextOS Snapshot Bootstrap
# 这是 ContextOS Single Source of Truth 的唯一真相源头。
_ROLE_TURN_REQUEST_EMPTY_CONTEXT_OS_SNAPSHOT: dict[str, Any] = {
    "version": 1,
    "mode": "state_first_context_os_v1",
    "adapter_id": "generic",
    "transcript_log": [],
    "working_state": {},
    "artifact_store": [],
    "episode_store": [],
    "updated_at": "",
}


@dataclass(frozen=True)
class SequentialBudget:
    """Sequential 预算配置。

    P1-TYPE-007: 定义保留在本模块，与 schema 类型保持一致。
    """

    max_steps: int = 12
    max_tool_calls_total: int = 24
    max_no_progress_steps: int = 3
    max_wall_time_seconds: int = 120


# 需要在 schema.py 中定义的 Sequential 类型（因为 sequential_engine 不导出这些）
@dataclass
class SequentialStatsResult:
    """Sequential 执行统计结果（用于 RoleTurnResult）"""

    steps: int = 0
    tool_calls: int = 0
    no_progress: int = 0
    termination_reason: str = ""
    budget_exhausted: bool = False
    failure_class: str = ""
    retry_hint: str = ""


@dataclass
class SequentialConfig:
    """Sequential 配置（RoleTurnRequest 使用）"""

    mode: SequentialMode = SequentialMode.DISABLED
    budget: SequentialBudget | None = None
    trace_level: SequentialTraceLevel = SequentialTraceLevel.SUMMARY

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "budget": {
                "max_steps": self.budget.max_steps if self.budget else 12,
                "max_tool_calls_total": self.budget.max_tool_calls_total if self.budget else 24,
                "max_no_progress_steps": self.budget.max_no_progress_steps if self.budget else 3,
                "max_wall_time_seconds": self.budget.max_wall_time_seconds if self.budget else 120,
            }
            if self.budget
            else {},
            "trace_level": self.trace_level.value,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Sequential 相关枚举与类型（从 sequential_engine 导入）
# ═══════════════════════════════════════════════════════════════════════════

# 这些类型从 sequential_engine 导入，避免重复定义
# - SequentialMode
# - SequentialTraceLevel
# - SequentialBudget
# - SequentialStatsResult
# - SequentialConfig


class RoleExecutionMode(Enum):
    """角色执行模式"""

    CHAT = "chat"  # 聊天模式（用户交互）
    WORKFLOW = "workflow"  # 工作流模式（自动化执行）


# ═══════════════════════════════════════════════════════════════════════════
# 策略配置类
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RolePromptPolicy:
    """角色提示词策略

    定义角色的提示词拼装规则。
    """

    # 核心提示词模板ID（引用模板库）
    core_template_id: str

    # Persona ID（引用 PERSONA_REGISTRY，默认为 "default"）
    persona_id: str = "default"

    # 是否允许追加提示词（True=仅追加，False=禁止任何修改）
    allow_appendix: bool = True

    # 是否允许覆盖核心提示词（必须False，用于强制约束）
    allow_override: bool = False

    # 输出格式约束（json/search_replace/text）
    output_format: Literal["json", "search_replace", "text", "markdown"] = "json"

    # 是否包含思考过程包裹（<thinking>...</thinking>）
    include_thinking: bool = True

    # 质量自检清单（输出前逐项确认）
    quality_checklist: list[str] = field(default_factory=list)

    # 安全边界提示词
    security_boundary: str | None = None


@dataclass(frozen=True)
class RoleToolPolicy:
    """角色工具策略

    定义角色的工具权限白名单/黑名单。
    """

    # 工具白名单（空列表=禁止所有工具）
    whitelist: list[str] = field(default_factory=list)

    # 工具黑名单（优先于白名单）
    blacklist: list[str] = field(default_factory=list)

    # 是否允许代码写入类工具
    allow_code_write: bool = False

    # 是否允许命令执行
    allow_command_execution: bool = False

    # 是否允许文件删除
    allow_file_delete: bool = False

    # 允许的最大工具调用次数（单次请求）
    max_tool_calls_per_turn: int = 10

    # 工具调用超时（秒）
    tool_timeout_seconds: int = 60

    @property
    def policy_id(self) -> str:
        """生成策略唯一标识（用于追踪）"""
        content = f"{sorted(self.whitelist)}:{sorted(self.blacklist)}:{self.allow_code_write}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class RoleContextPolicy:
    """角色上下文策略

    定义角色的上下文构建规则。
    """

    # 最大上下文token数
    max_context_tokens: int = 8000

    # 历史消息轮数限制
    max_history_turns: int = 10

    # 是否包含项目结构信息
    include_project_structure: bool = True

    # 是否包含相关代码片段
    include_code_snippets: bool = True

    # 代码片段最大行数
    max_code_lines: int = 100

    # 是否包含任务历史
    include_task_history: bool = True

    # 上下文压缩策略（summarize/truncate/sliding_window）
    compression_strategy: Literal["summarize", "truncate", "sliding_window"] = "sliding_window"


@dataclass(frozen=True)
class RoleDataPolicy:
    """角色数据策略

    定义角色的数据存储规则。
    """

    # 数据子目录名
    data_subdir: str

    # 文件编码（强制UTF-8）
    encoding: str = "utf-8"

    # 是否启用原子写
    atomic_write: bool = True

    # 是否启用写入前备份
    backup_before_write: bool = True

    # 数据保留策略（保留天数）
    retention_days: int = 90

    # 是否加密存储
    encrypt_at_rest: bool = False

    # 允许的文件扩展名白名单
    allowed_extensions: list[str] = field(default_factory=lambda: [".json", ".md", ".txt", ".yaml", ".yml"])


@dataclass(frozen=True)
class RoleLibraryPolicy:
    """角色Python库能力栈

    定义角色可用的Python库。
    """

    # 核心库（必须可用）
    core_libraries: list[str] = field(default_factory=list)

    # 可选库（有则增强能力）
    optional_libraries: list[str] = field(default_factory=list)

    # 禁止使用的库（安全限制）
    forbidden_libraries: list[str] = field(default_factory=list)

    # 库版本约束 {lib_name: version_spec}
    version_constraints: dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# 角色Profile
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RoleProfile:
    """角色Profile - 角色的完整配置定义

    这是角色的单一事实来源（SSOT），包含角色的所有策略配置。
    """

    # 角色标识
    role_id: str

    # 角色显示名
    display_name: str

    # 角色描述
    description: str

    # 角色职责（简短说明）
    responsibilities: list[str] = field(default_factory=list)

    # 角色默认 Provider/Model（用于事件追踪和缓存键，留空时由平台按 role 解析）
    provider_id: str = ""
    model: str = ""

    # 提示词策略
    prompt_policy: RolePromptPolicy = field(default_factory=lambda: RolePromptPolicy(core_template_id="default"))

    # 工具策略
    tool_policy: RoleToolPolicy = field(default_factory=RoleToolPolicy)

    # 上下文策略
    context_policy: RoleContextPolicy = field(default_factory=RoleContextPolicy)

    # 数据策略
    data_policy: RoleDataPolicy = field(default_factory=lambda: RoleDataPolicy(data_subdir="default"))

    # 库策略
    library_policy: RoleLibraryPolicy = field(default_factory=RoleLibraryPolicy)

    # Profile版本（用于追踪变更）
    version: str = "1.0.0"

    # 创建时间
    created_at: datetime = field(default_factory=_utc_now)

    # 更新时间
    updated_at: datetime = field(default_factory=_utc_now)

    @property
    def profile_fingerprint(self) -> str:
        """生成Profile指纹（用于一致性校验）"""
        content = (
            f"{self.role_id}:{self.version}:"
            f"{self.prompt_policy.core_template_id}:"
            f"{self.prompt_policy.persona_id}:"
            f"{self.tool_policy.policy_id}"
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# 请求/响应类型
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class PromptFingerprint:
    """提示词指纹

    用于追踪和校验提示词一致性。
    """

    # 核心提示词哈希
    core_hash: str

    # 追加提示词哈希（如果有）
    appendix_hash: str | None = None

    # 完整提示词哈希
    full_hash: str = ""

    # Profile指纹
    profile_fingerprint: str = ""

    # 生成时间
    generated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.full_hash:
            content = f"{self.core_hash}:{self.appendix_hash or ''}:{self.profile_fingerprint}"
            self.full_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class RoleTurnRequest:
    """角色回合请求

    统一聊天模式和工作流模式的请求结构。

    SSOT Bootstrap: RoleTurnRequest 确保 context_os_snapshot 始终存在。
    这是 ContextOS Single Source of Truth 的唯一真相源头 —— 任何 RoleTurnRequest
    创建时都会自动 bootstrap 空的 context_os_snapshot（如果不存在）。
    """

    # 执行模式
    mode: RoleExecutionMode = RoleExecutionMode.CHAT

    # 工作区路径
    workspace: str = ""

    # 用户消息
    message: str = ""

    # 执行领域（code/document/research/general）
    domain: str = "code"

    # 历史消息（格式: [(role, content), ...]）
    history: list[tuple] = field(default_factory=list)

    # 追加提示词（仅追加到核心提示词，不覆盖）
    prompt_appendix: str | None = None

    # ⚠️ 已废弃：system_prompt 不再允许覆盖核心提示词
    # 如果传入，会被转换为 prompt_appendix 并输出 deprecation 警告
    system_prompt: str | None = None

    # 上下文覆盖（可选）
    context_override: dict[str, Any] | None = None

    # 工具调用结果（工作流模式下）
    tool_results: list[dict[str, Any]] | None = None

    # 关联任务ID（工作流模式下）
    task_id: str | None = None

    # 运行时ID（用于事件追踪）
    run_id: str | None = None

    # 验证与重试策略
    validate_output: bool = True  # 是否验证输出格式
    max_retries: int = 1  # 验证失败时重试次数

    # Sequential 配置 (vNext)
    sequential_mode: SequentialMode = SequentialMode.DISABLED
    sequential_budget: SequentialBudget | None = None
    sequential_trace_level: SequentialTraceLevel = SequentialTraceLevel.SUMMARY

    # 请求元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        mode: RoleExecutionMode = RoleExecutionMode.CHAT,
        workspace: str = "",
        message: str = "",
        domain: str = "code",
        history: list[tuple] | None = None,
        prompt_appendix: str | None = None,
        system_prompt: str | None = None,
        context_override: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        validate_output: bool = True,
        max_retries: int = 1,
        sequential_mode: SequentialMode = SequentialMode.DISABLED,
        sequential_budget: SequentialBudget | None = None,
        sequential_trace_level: SequentialTraceLevel = SequentialTraceLevel.SUMMARY,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.mode = mode
        self.workspace = workspace
        self.message = message
        self.domain = domain
        self.history = history if history is not None else []
        self.prompt_appendix = prompt_appendix
        self.system_prompt = system_prompt
        self.tool_results = tool_results
        self.task_id = task_id
        self.run_id = run_id
        self.validate_output = validate_output
        self.max_retries = max_retries
        self.sequential_mode = sequential_mode
        self.sequential_budget = sequential_budget
        self.sequential_trace_level = sequential_trace_level
        self.metadata = metadata if metadata is not None else {}
        # SSOT Bootstrap: context_override 必须有 context_os_snapshot
        self.context_override = context_override
        self._post_init()

    def _post_init(self) -> None:
        """SSOT Bootstrap: Ensure context_os_snapshot exists for all sessions.

        This is the SINGLE SOURCE OF TRUTH for ContextOS snapshot bootstrapping.
        Any RoleTurnRequest (regardless of how it's created) will have a valid
        context_os_snapshot after construction. This eliminates the need for each
        caller to individually bootstrap the snapshot.
        """
        if self.context_override is None:
            self.context_override = {"context_os_snapshot": dict(_ROLE_TURN_REQUEST_EMPTY_CONTEXT_OS_SNAPSHOT)}
        elif "context_os_snapshot" not in self.context_override:
            self.context_override["context_os_snapshot"] = dict(_ROLE_TURN_REQUEST_EMPTY_CONTEXT_OS_SNAPSHOT)


@dataclass
class RoleTurnResult:
    """角色回合结果

    统一聊天模式和工作流模式的响应结构。
    """

    # 响应内容
    content: str = ""

    # 思考过程（如果有）
    thinking: str | None = None

    # 结构化输出（如果解析成功）
    structured_output: dict[str, Any] | None = None

    # 工具调用请求（如果有）
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # 执行的工具结果
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    # Profile信息
    profile_version: str = ""
    prompt_fingerprint: PromptFingerprint | None = None
    tool_policy_id: str = ""

    # 执行统计
    # 包含：platform_retry_count, kernel_repair_retry_count, kernel_repair_reasons, kernel_repair_exhausted
    execution_stats: dict[str, Any] = field(default_factory=dict)

    # 质量评分 (0-100, 60分及格)
    quality_score: float = 0.0

    # 质量检查建议
    quality_suggestions: list[str] = field(default_factory=list)

    # 质量检查错误（新增：工具执行错误等）
    quality_errors: list[str] | None = None

    # 工具执行错误（新增）
    tool_execution_error: str | None = None

    # 是否需要重试（新增：当工具执行失败时）
    should_retry: bool = False

    # 错误信息（如果有）
    error: str | None = None

    # 是否完成（无需继续）
    is_complete: bool = True

    # 是否需要人工确认（工作流模式下）
    needs_confirmation: bool = False

    # Sequential 执行统计 (vNext)
    sequential_stats: SequentialStatsResult | None = None

    # 完整回话历史 (role, content) 对列表 — 用于非流式模式下的 session 持久化
    turn_history: list[tuple[str, str]] = field(default_factory=list)

    # SSOT 兼容：完整事件元数据列表
    # 包含 event_id, role, content, sequence, metadata (含 kind, dialog_act, route 等)
    # 用于 ContextOS event sourcing，不用于 session 持久化
    turn_events_metadata: list[dict[str, Any]] = field(default_factory=list)

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化执行统计默认值"""
        if not self.execution_stats:
            self.execution_stats = {
                "platform_retry_count": 0,
                "kernel_repair_retry_count": 0,
                "kernel_repair_reasons": [],
                "kernel_repair_exhausted": False,
            }


# ═══════════════════════════════════════════════════════════════════════════
# 序列化/反序列化辅助
# ═══════════════════════════════════════════════════════════════════════════


class RoleProfileDict(TypedDict, total=False):
    """RoleProfile的字典表示（用于YAML/JSON序列化）"""

    role_id: str
    display_name: str
    description: str
    responsibilities: list[str]
    provider_id: str
    model: str
    prompt_policy: dict[str, Any]
    tool_policy: dict[str, Any]
    context_policy: dict[str, Any]
    data_policy: dict[str, Any]
    library_policy: dict[str, Any]
    version: str


def profile_from_dict(data: dict[str, Any]) -> RoleProfile:
    """从字典创建 RoleProfile"""
    return RoleProfile(
        role_id=data["role_id"],
        display_name=data["display_name"],
        description=data.get("description", ""),
        responsibilities=data.get("responsibilities", []),
        provider_id=str(data.get("provider_id") or "").strip(),
        model=str(data.get("model") or "").strip(),
        prompt_policy=RolePromptPolicy(**data.get("prompt_policy", {})),
        tool_policy=RoleToolPolicy(**data.get("tool_policy", {})),
        context_policy=RoleContextPolicy(**data.get("context_policy", {})),
        data_policy=RoleDataPolicy(**data.get("data_policy", {})),
        library_policy=RoleLibraryPolicy(**data.get("library_policy", {})),
        version=data.get("version", "1.0.0"),
    )


def profile_to_dict(profile: RoleProfile) -> dict[str, Any]:
    """将 RoleProfile 转为字典"""
    return {
        "role_id": profile.role_id,
        "display_name": profile.display_name,
        "description": profile.description,
        "responsibilities": profile.responsibilities,
        "provider_id": profile.provider_id,
        "model": profile.model,
        "prompt_policy": {
            "core_template_id": profile.prompt_policy.core_template_id,
            "allow_appendix": profile.prompt_policy.allow_appendix,
            "allow_override": profile.prompt_policy.allow_override,
            "output_format": profile.prompt_policy.output_format,
            "include_thinking": profile.prompt_policy.include_thinking,
            "quality_checklist": profile.prompt_policy.quality_checklist,
            "security_boundary": profile.prompt_policy.security_boundary,
        },
        "tool_policy": {
            "whitelist": profile.tool_policy.whitelist,
            "blacklist": profile.tool_policy.blacklist,
            "allow_code_write": profile.tool_policy.allow_code_write,
            "allow_command_execution": profile.tool_policy.allow_command_execution,
            "allow_file_delete": profile.tool_policy.allow_file_delete,
            "max_tool_calls_per_turn": profile.tool_policy.max_tool_calls_per_turn,
            "tool_timeout_seconds": profile.tool_policy.tool_timeout_seconds,
        },
        "context_policy": {
            "max_context_tokens": profile.context_policy.max_context_tokens,
            "max_history_turns": profile.context_policy.max_history_turns,
            "include_project_structure": profile.context_policy.include_project_structure,
            "include_code_snippets": profile.context_policy.include_code_snippets,
            "max_code_lines": profile.context_policy.max_code_lines,
            "include_task_history": profile.context_policy.include_task_history,
            "compression_strategy": profile.context_policy.compression_strategy,
        },
        "data_policy": {
            "data_subdir": profile.data_policy.data_subdir,
            "encoding": profile.data_policy.encoding,
            "atomic_write": profile.data_policy.atomic_write,
            "backup_before_write": profile.data_policy.backup_before_write,
            "retention_days": profile.data_policy.retention_days,
            "encrypt_at_rest": profile.data_policy.encrypt_at_rest,
            "allowed_extensions": profile.data_policy.allowed_extensions,
        },
        "library_policy": {
            "core_libraries": profile.library_policy.core_libraries,
            "optional_libraries": profile.library_policy.optional_libraries,
            "forbidden_libraries": profile.library_policy.forbidden_libraries,
            "version_constraints": profile.library_policy.version_constraints,
        },
        "version": profile.version,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Permission / RBAC Models
# ═══════════════════════════════════════════════════════════════════════════


class SubjectType(str, Enum):
    """权限主体类型"""

    ROLE = "role"
    USER = "user"
    SERVICE = "service"


class ResourceType(str, Enum):
    """权限资源类型"""

    FILE = "file"
    DIRECTORY = "directory"
    TOOL = "tool"
    API = "api"
    WORKSPACE = "workspace"
    TASK = "task"


class Action(str, Enum):
    """权限操作类型"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    ADMIN = "admin"
    LIST = "list"


class PolicyEffect(str, Enum):
    """策略效果"""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class Subject:
    """权限主体"""

    type: SubjectType
    id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "id": self.id}


@dataclass(frozen=True)
class Resource:
    """权限资源"""

    type: ResourceType
    pattern: str = "*"
    path: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type.value, "pattern": self.pattern}
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass
class Policy:
    """RBAC 策略定义"""

    id: str
    name: str
    effect: PolicyEffect
    subjects: list[Subject]
    resources: list[Resource]
    actions: list[Action]
    priority: int = 0
    enabled: bool = True
    conditions: dict[str, Any] = field(default_factory=dict)

    def matches_subject(self, subject: Subject) -> bool:
        for configured in self.subjects:
            if configured.type != subject.type:
                continue
            if configured.id in ("*", subject.id):
                return True
        return False

    def matches_resource(self, resource: Resource) -> bool:
        for configured in self.resources:
            if configured.type != resource.type:
                continue
            candidate_values = [resource.path or "", resource.pattern or ""]
            if not candidate_values:
                continue
            for candidate in candidate_values:
                if candidate and fnmatch.fnmatch(candidate, configured.pattern):
                    return True
            if configured.pattern in ("*", "**/*"):
                return True
        return False

    def matches_action(self, action: Action) -> bool:
        if Action.ADMIN in self.actions:
            return True
        return action in self.actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "effect": self.effect.value,
            "subjects": [s.to_dict() for s in self.subjects],
            "resources": [r.to_dict() for r in self.resources],
            "actions": [a.value for a in self.actions],
            "priority": self.priority,
            "enabled": self.enabled,
            "conditions": dict(self.conditions),
        }


@dataclass
class PermissionCheckResult:
    """权限检查结果"""

    allowed: bool
    decision: str
    matched_policies: list[str]
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
