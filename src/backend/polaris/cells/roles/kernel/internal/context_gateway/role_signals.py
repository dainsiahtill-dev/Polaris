"""RoleSignalPlane —— 按角色注入"领域资产概览"信号的统一机制。

这不是新建平行平面，而是把 gateway `_build_projection_dict` 里原本**硬编码**的
supplemental_turns（`project_structure` /【项目结构】、`task_history` /【任务历史】）
**泛化**为 registry 驱动、按角色绑定、预算受控的信号管线。三件既有基建全部复用：

- 注入点：仍是 `supplemental_turns`（`{role:"system", content, name}`）。
- 角色绑定：仍由 `RoleProfile.context_policy` 的 include_* 开关决定（每角色独立）。
- 预算卡点：仍由 gateway 的 `CompressionEngine` / `max_context_tokens` 收尾兜底；
  本模块在其之前做**每信号级**的排序/截断/熔断，避免一刀切压缩。

设计铁律（与 ProjectionEngine 同源）：provider **只读、纯函数、可缓存**，从权威资产
投影，绝不 mutate；大块走 ReceiptStore（摘要+引用）。

防御性保证：
1. **逐字节 + 绝对索引一致**：seed provider 保留原 id（`project_structure`/`task_history`）
   与原内容格式，并以 priority 0/1 排在最前；只启用这两者时输出与旧实现完全一致。
2. **sources 溯源泛化**：每个被注入的信号把 `block.id` 压入 sources，便于 trace
   "本 turn 到底塞了哪个资产、是否过期"。
3. **budget_pressure 熔断**：压力下，freshness_key 较上一 turn 未变的 nice-to-have 信号
   一律断流；只有 freshness 变化（底层资产刚被工具改过）的 must-have 才经 ReceiptStore
   摘要+引用"极限挤入"。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

# 每个信号块的默认体积上限（字符）。超限 → 卸载到 ReceiptStore（摘要+引用）。
DEFAULT_PER_SIGNAL_CHAR_CAP = 4000
# role_signals 段整体字符预算（所有信号之和的软上限；超出后 nice-to-have 被丢弃）。
DEFAULT_TOTAL_CHAR_BUDGET = 12000

_MUST_HAVE = "must_have"
_NICE_TO_HAVE = "nice_to_have"


def _freshness_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SignalBlock:
    """一个角色信号的最终产物。content 已含【...】表头以保证逐字节一致。

    ``max_chars`` 为该信号自带的体积上限（None = 不限，由 seed 信号采用以保证
    逐字节一致；新增大信号可设自己的上限触发卸载）。
    """

    id: str
    content: str
    priority: int = 100
    level: str = _NICE_TO_HAVE
    freshness_key: str = ""
    max_chars: int | None = None

    def to_turn(self) -> dict[str, str]:
        return {"role": "system", "content": self.content, "name": self.id}


def _none_accessor() -> str | None:
    return None


@dataclass
class SignalBuildContext:
    """provider 构建所需的只读上下文（含数据访问回调，便于单测注入）。

    新增角色专属信号的访问器默认返回 None（优雅降级：未接源/无数据 → 不注入），
    故在未启用对应 context_policy 开关时，行为与历史完全一致。
    """

    role: str
    phase: str
    task_id: str
    policy_flags: Mapping[str, bool]
    get_project_structure: Callable[[], str | None]
    get_task_history: Callable[[str], str | None]
    # 角色专属资产访问器（只读、可缺省）。默认 None-accessor → 无数据 → 不注入。
    get_blueprint_overview: Callable[[], str | None] = _none_accessor
    get_verdict_history: Callable[[], str | None] = _none_accessor
    # 仓库身份卡（Phase-1 B1 反幻觉 grounding）。默认 None-accessor → 不注入。
    get_repo_identity: Callable[[], str | None] = _none_accessor
    # 侦察锚点卡（Phase-2 A7 定位持久化）。默认 None-accessor → 不注入。
    get_scout_anchors: Callable[[], str | None] = _none_accessor
    # 三层裂变(I2): 当前 construction_step 的有界蓝图智能(签名/接口/verify)。
    get_blueprint_step: Callable[[], str | None] = _none_accessor
    # 文件归属信号(D-11): 其他并行 Director 最近修改的文件列表。
    # 防止并发写覆盖。默认 None-accessor → 不注入。
    get_file_ownership: Callable[[], str | None] = _none_accessor
    # Resident AGI 平台能力面: AGI 与 PM/CE/Director/QA 走同一 Role/ContextOS
    # 底座,但拥有平台级审计/决策能力。默认 None-accessor → 不注入。
    get_resident_agi_capabilities: Callable[[], str | None] = _none_accessor
    # Resident AGI 决策交接: CE/Director/QA 消费 AGI/Resident 已记录的治理判断。
    # 默认 None-accessor → 不注入。
    get_resident_agi_decision_trace: Callable[[], str | None] = _none_accessor


class RoleContextSignal(Protocol):
    """角色上下文信号 provider 端口。"""

    id: str

    def applies_to(self, ctx: SignalBuildContext) -> bool: ...

    def priority(self, ctx: SignalBuildContext) -> int: ...

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None: ...


# ---------------------------------------------------------------------------
# Seed providers —— 由原硬编码 supplemental_turns 重构而来（行为逐字节保持）
# ---------------------------------------------------------------------------


class ProjectStructureSignal:
    """code_repo 类信号：项目结构概览。所有角色的 default/fallback。

    id 保留 ``project_structure`` 以锁死旧 name + sources（逐字节一致）。
    """

    id = "project_structure"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return bool(ctx.policy_flags.get("include_project_structure", True))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 0  # 最前

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        structure = ctx.get_project_structure()
        if not structure:
            return None
        content = f"【项目结构】\n{structure}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=0,
            level=_NICE_TO_HAVE,
            freshness_key=_freshness_of(structure),
        )


class TaskHistorySignal:
    """plan_overview 类信号：任务历史/计划概览（PM 资产雏形）。

    id 保留 ``task_history``；门控沿用原 ``include_task_history and task_id``。
    """

    id = "task_history"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return bool(ctx.policy_flags.get("include_task_history", True)) and bool(ctx.task_id)

    def priority(self, ctx: SignalBuildContext) -> int:
        return 1

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        history = ctx.get_task_history(ctx.task_id)
        if not history:
            return None
        content = f"【任务历史】\n{history}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=1,
            level=_NICE_TO_HAVE,
            freshness_key=_freshness_of(history),
        )


class BlueprintOverviewSignal:
    """ChiefEngineer + Director：蓝图 / 技术架构概览。

    role-bound（chief_engineer 或 director）+ flag-gated（``include_blueprint_overview``，
    Director 默认 True，其他角色默认 False）。
    数据经 ``get_blueprint_overview`` 访问器获取（缺省 None → 不注入）。priority 在 seed 之后。

    Director 必须接收 CE 蓝图才能生成跨文件一致的代码。没有蓝图指导，Director 在每个文件
    里重新发明类型定义，导致 Mood/Spell 等类型重复声明。
    """

    id = "blueprint_overview"

    # Director defaults to True because the Director needs the CE blueprint to
    # generate cross-file coherent code. Without it, the Director re-invents
    # type definitions in each file, causing redeclaration compile errors.
    _DEFAULT_BY_ROLE = {"director": True, "chief_engineer": False}

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        if ctx.role not in ("chief_engineer", "director"):
            return False
        default = self._DEFAULT_BY_ROLE.get(ctx.role, False)
        return bool(ctx.policy_flags.get("include_blueprint_overview", default))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 10

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        overview = ctx.get_blueprint_overview()
        if not overview:
            # D-10: Synthesize minimal guidance from available data when CE hasn't run.
            fallback = self._build_degraded_fallback(ctx)
            if fallback is None:
                return None
            return SignalBlock(
                id=self.id,
                content=fallback,
                priority=10,
                level=_NICE_TO_HAVE,
                freshness_key=_freshness_of(fallback),
                max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
            )
        content = f"【蓝图/技术架构】\n{overview}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=10,
            level=_NICE_TO_HAVE,
            freshness_key=_freshness_of(overview),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,  # 蓝图可能很大 → 超限自动卸载
        )

    @staticmethod
    def _build_degraded_fallback(ctx: SignalBuildContext) -> str | None:
        """Synthesize minimal architectural guidance when CE blueprint is absent.

        Extracts target files and context from task history and project structure
        so Director still gets basic orientation instead of zero guidance.
        Returns None only if no useful data is available at all.
        """
        lines: list[str] = []

        # Collect target file hints from task history.
        task_files: list[str] = []
        if ctx.task_id:
            history = ctx.get_task_history(ctx.task_id)
            if history:
                for line in history.splitlines():
                    stripped = line.strip()
                    # Heuristic: lines referencing file paths or "file:" markers.
                    if any(tok in stripped for tok in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs")):
                        task_files.append(stripped)

        # Collect top-level structure hints from project structure.
        structure_files: list[str] = []
        structure = ctx.get_project_structure()
        if structure:
            for line in structure.splitlines():
                stripped = line.strip()
                if any(tok in stripped for tok in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs")):
                    structure_files.append(stripped)

        # If nothing useful was extracted, still emit a minimal guidance block.
        lines.append("【蓝图/技术架构（降级）】")
        lines.append("无 CE 蓝图可用。基于任务描述和项目结构推断：")

        if task_files:
            shown = task_files[:8]
            lines.append("- 任务相关文件:")
            for f in shown:
                lines.append(f"    {f}")

        if structure_files:
            shown = structure_files[:8]
            lines.append("- 项目关键文件:")
            for f in shown:
                lines.append(f"    {f}")

        if not task_files and not structure_files:
            lines.append("- 目标文件: 无法从现有数据推断")

        lines.append("- 建议: 先读取已有文件理解接口，再实现新功能。")
        lines.append("- 注意: 此为降级推断，非 CE 权威蓝图。跨文件类型一致性需自行保证。")

        return "\n".join(lines)


class BlueprintStepsSignal:
    """Director 专属：当前 construction_step 的蓝图智能（三层裂变 I2）。

    role-bound（仅 director）+ default-ON + must-have：弱执行者的"局部上帝视角"
    —— 签名骨架/接口定名/verify 判据。注入物必须有界（签名+接口+清单，禁全文，
    16k 窗口实证 W1.5c 后可用提示仅 ~5-6k tokens）。数据经 ``get_blueprint_step``
    访问器获取（缺省 None → 不注入，未裂变任务零影响）。
    """

    id = "blueprint_step"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return ctx.role == "director" and bool(ctx.policy_flags.get("include_blueprint_step", True))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 4

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        step_card = ctx.get_blueprint_step()
        if not step_card:
            return None
        content = f"【施工步骤蓝图】\n{step_card}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=4,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(step_card),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class VerdictHistorySignal:
    """QA + Director：质量判定 / 门禁历史概览。

    role-bound（qa 或 director）+ flag-gated（``include_verdict_history``，
    Director 默认 True 以便从 QA 拒绝反馈中学习）。
    """

    id = "verdict_history"

    _DEFAULT_BY_ROLE = {"director": True, "qa": False}

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        if ctx.role not in ("qa", "director"):
            return False
        default = self._DEFAULT_BY_ROLE.get(ctx.role, False)
        return bool(ctx.policy_flags.get("include_verdict_history", default))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 11

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        verdict = ctx.get_verdict_history()
        if not verdict:
            return None
        content = f"【质量判定历史】\n{verdict}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=11,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(verdict),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class RepoIdentitySignal:
    """seed 信号：仓库身份卡（确定性反幻觉 grounding,Phase-1 B1）。

    run20 实证（2026-06-11）：弱模型在任何仓库里都按其它生态的惯例猜路径
    （README.md/src/main.py/package.json/app.py…,18 实例 560 次 not-found,
    path_hallucination 14/18,最终 patch 甚至在 django 仓库新建 package.json）。
    本信号注入确定性仓库事实（主语言/根标记存在与缺失/顶层布局）作为第一道
    拦截。must-have 级：预算压力下宁可经 ReceiptStore 卸载也不静默丢弃。
    seed 级 grounding 默认开启（``include_repo_identity``,默认 True）,
    关闭需要显式 opt-out。
    """

    id = "repo_identity"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return bool(ctx.policy_flags.get("include_repo_identity", True))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 2  # 紧随 project_structure(0)/task_history(1) 两个既有 seed 之后

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        identity = ctx.get_repo_identity()
        if not identity:
            return None
        return SignalBlock(
            id=self.id,
            content=identity,
            priority=2,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(identity),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class ScoutAnchorsSignal:
    """seed 信号：侦察锚点卡（Phase-2 A7 定位持久化）。

    run10a/run20 实证：scout_probe 召回金文件后,findings 作为一次性工具结果
    在 mutation 压力下即刻被遗忘,主代理漂回幻觉路径。本信号把已确认定位
    (经 scout_anchor_store 持久化)每回合重新注入。must-have 级:定位锚点
    是编辑正确性的根,预算压力下宁可卸载也不丢弃。默认开启
    (``include_scout_anchors``,默认 True;无锚点文件时 accessor 返回 None
    → 不注入,baseline 不变)。
    """

    id = "scout_anchors"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return bool(ctx.policy_flags.get("include_scout_anchors", True))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 3  # 紧随 repo_identity(2)

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        anchors = ctx.get_scout_anchors()
        if not anchors:
            return None
        return SignalBlock(
            id=self.id,
            content=anchors,
            priority=3,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(anchors),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class FileOwnershipSignal:
    """Director 专属：并行 worker 最近修改的文件归属信号（D-11）。

    role-bound（仅 director）+ flag-gated（``include_file_ownership``，
    Director 默认 True）。数据经 ``get_file_ownership`` 访问器获取
    （缺省 None → 不注入）。priority 12，在 verdict_history(11) 之后。

    解决缺陷 D-11：多个并行 Director 同时工作时，彼此不知道对方已修改哪些文件，
    导致后写覆盖先写、或两个 worker 同时编辑同一文件产生冲突。本信号把近期
    文件修改历史注入 Director 上下文，使其能感知并规避其他 worker 的产出。
    """

    id = "file_ownership"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return ctx.role == "director" and bool(ctx.policy_flags.get("include_file_ownership", True))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 12

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        ownership = ctx.get_file_ownership()
        if not ownership:
            return None
        content = f"【并行文件归属】\n{ownership}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=12,
            level=_NICE_TO_HAVE,
            freshness_key=_freshness_of(ownership),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class ResidentAgiCapabilitySurfaceSignal:
    """Resident AGI 专属：平台能力面与治理边界。

    role-bound（仅 resident_agi）+ default-ON + must-have。Resident AGI 不是旁路
    agent，也不是第二套调度系统；它与 PM/CE/Director/QA 一样走 RoleRuntime /
    ContextOS / TurnEngine，只是职责和权限更高，需要在每次决策 turn 中明确看到
    可读审计面、可执行边界、证据契约和禁止绕过的主链。
    """

    id = "resident_agi_capability_surface"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return ctx.role == "resident_agi" and bool(
            ctx.policy_flags.get("include_resident_agi_capability_surface", True)
        )

    def priority(self, ctx: SignalBuildContext) -> int:
        return 4

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        capabilities = ctx.get_resident_agi_capabilities()
        if not capabilities:
            return None
        content = f"【Resident AGI 能力面】\n{capabilities}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=4,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(capabilities),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


class ResidentAgiDecisionTraceSignal:
    """CE/Director/QA 专属：Resident AGI 决策交接信号。

    Resident decision trace 是 source of truth；本信号只是把最近的执行相关
    AGI/Resident 决策投影进同一 Role/ContextOS 请求，让 CE/Director/QA 能消费
    监督结论、证据引用和禁止绕过的交接边界，避免 AGI 只存在于 UI/审计面板。
    """

    id = "resident_agi_decision_trace"

    _DEFAULT_BY_ROLE = {"chief_engineer": True, "director": True, "qa": True}

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        if ctx.role not in self._DEFAULT_BY_ROLE:
            return False
        default = self._DEFAULT_BY_ROLE.get(ctx.role, False)
        return bool(ctx.policy_flags.get("include_resident_agi_decision_trace", default))

    def priority(self, ctx: SignalBuildContext) -> int:
        return 5

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        trace = ctx.get_resident_agi_decision_trace()
        if not trace:
            return None
        content = f"【Resident AGI 决策交接】\n{trace}"
        return SignalBlock(
            id=self.id,
            content=content,
            priority=5,
            level=_MUST_HAVE,
            freshness_key=_freshness_of(trace),
            max_chars=DEFAULT_PER_SIGNAL_CHAR_CAP,
        )


# 默认注册表：seed（全角色）+ 角色专属（role+flag 双门控，默认 flag 关 → 不影响 baseline）。
DEFAULT_SIGNAL_PROVIDERS: tuple[RoleContextSignal, ...] = (
    ProjectStructureSignal(),
    TaskHistorySignal(),
    RepoIdentitySignal(),
    ScoutAnchorsSignal(),
    ResidentAgiCapabilitySurfaceSignal(),
    ResidentAgiDecisionTraceSignal(),
    BlueprintOverviewSignal(),
    BlueprintStepsSignal(),
    VerdictHistorySignal(),
    FileOwnershipSignal(),
)


class RoleSignalRegistry:
    """按角色解析适用信号，镜像 strategy_overlay 的 per-role 解析模式（但只管内容）。"""

    def __init__(self, providers: Sequence[RoleContextSignal] = DEFAULT_SIGNAL_PROVIDERS) -> None:
        self._providers = tuple(providers)

    def resolve(self, ctx: SignalBuildContext) -> list[RoleContextSignal]:
        """返回适用于当前角色/阶段/策略的 provider，按 priority 升序（稳定）。"""
        applicable = [p for p in self._providers if p.applies_to(ctx)]
        return sorted(applicable, key=lambda p: (p.priority(ctx), p.id))


@dataclass
class AllocationResult:
    """分配结果：可直接拼进 supplemental_turns 的 turns + sources + 遥测。"""

    turns: list[dict[str, str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    telemetry: dict[str, object] = field(default_factory=dict)


class _ReceiptOffloader(Protocol):
    def offload_content(
        self, receipt_id: str, content: str, *, threshold: int, placeholder: str
    ) -> tuple[str, tuple[str, ...]]: ...


def allocate_role_signals(
    *,
    registry: RoleSignalRegistry,
    ctx: SignalBuildContext,
    receipt_store: _ReceiptOffloader,
    budget_pressure: bool = False,
    previous_freshness: Mapping[str, str] | None = None,
    per_signal_char_cap: int | None = DEFAULT_PER_SIGNAL_CHAR_CAP,
    total_char_budget: int | None = DEFAULT_TOTAL_CHAR_BUDGET,
) -> AllocationResult:
    """把适用信号分配进 supplemental_turns，受三道预算/熔断约束。

    顺序与语义：
    - 按 priority 升序处理（seed 在最前 → 逐字节+索引一致）。
    - **熔断（budget_pressure）**：freshness 较上一 turn 未变的 nice-to-have 直接跳过；
      must-have 即便在压力下也保留（必要时卸载）。
    - **每信号体积上限**：content 超 ``per_signal_char_cap`` → 卸载 ReceiptStore，
      turn 内容替换为占位符（带 ``receipt://`` 引用），原文可取回。
    - **整体预算**：running 总量超 ``total_char_budget`` 后，nice-to-have 丢弃、
      must-have 仍以卸载形式挤入。
    每个最终注入的信号把 ``id`` 压入 sources（溯源泛化）。
    """
    prev = dict(previous_freshness or {})
    result = AllocationResult()
    running = 0
    skipped_unchanged: list[str] = []
    dropped_budget: list[str] = []
    offloaded: list[str] = []
    injected_freshness: dict[str, str] = {}  # 本 turn 实际注入信号的 freshness（供跨 turn 记忆）

    for provider in registry.resolve(ctx):
        block = provider.build(ctx)
        if block is None:
            continue

        is_must = block.level == _MUST_HAVE
        unchanged = bool(block.freshness_key) and prev.get(block.id) == block.freshness_key

        # 熔断：压力下，未变化的 nice-to-have 断流。
        if budget_pressure and not is_must and unchanged:
            skipped_unchanged.append(block.id)
            continue

        content = block.content
        size = len(content)
        over_total = total_char_budget is not None and running + size > total_char_budget
        effective_cap = block.max_chars if block.max_chars is not None else per_signal_char_cap
        over_cap = effective_cap is not None and size > effective_cap

        if over_cap or (over_total and is_must):
            # 卸载：摘要 + receipt 引用（must-have 极限挤入 / 超大信号截断）。
            placeholder = f"[{block.id} stored — receipt://{block.id}]"
            # threshold 取 effective_cap（不为 None 时）或 0（must-have 超总预算强制卸载）。
            offload_threshold = effective_cap if effective_cap is not None else 0
            display, refs = receipt_store.offload_content(
                block.id, content, threshold=offload_threshold, placeholder=placeholder
            )
            turn: dict[str, str] = {"role": "system", "content": display, "name": block.id}
            if refs:
                turn["receipt_refs"] = list(refs)  # type: ignore[assignment]
                offloaded.append(block.id)
            result.turns.append(turn)
            result.sources.append(block.id)
            if block.freshness_key:
                injected_freshness[block.id] = block.freshness_key
            running += len(display)
            continue

        if over_total and not is_must:
            dropped_budget.append(block.id)
            continue

        result.turns.append(block.to_turn())
        result.sources.append(block.id)
        if block.freshness_key:
            injected_freshness[block.id] = block.freshness_key
        running += size

    result.telemetry = {
        "injected": list(result.sources),
        "injected_freshness": injected_freshness,
        "skipped_unchanged": skipped_unchanged,
        "dropped_budget": dropped_budget,
        "offloaded": offloaded,
        "total_chars": running,
        "budget_pressure": budget_pressure,
    }
    return result
