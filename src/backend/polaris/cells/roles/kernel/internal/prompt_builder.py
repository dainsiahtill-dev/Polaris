# ruff: noqa: BLE001
"""Prompt Builder - 提示词构建组件

负责构建角色执行所需的各种提示词，包括：
- 核心提示词模板加载
- 系统提示词构建
- 工具策略提示词构建
- 重试提示词构建

支持两种模式：
1. 传统模式 (Legacy): 使用 prompt_templates.py 中的硬编码模板
2. 三轴模式 (Tri-Axis): 使用 RoleComposer 动态组合 Anchor + Profession + Persona
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.interaction_contract import (
    TurnIntent,
    infer_turn_intent,
)
from polaris.cells.roles.kernel.internal.metrics import record_cache_stats
from polaris.cells.roles.kernel.internal.prompt_templates import (
    ROLE_PROMPT_TEMPLATES,
    SHARED_SECURITY_BOUNDARY,
    build_persona_prompt,
    get_persona_registry,
)
from polaris.kernelone.context.chunks import (
    AssemblyContext,
    CacheControl,
    ChunkType,
    FinalRequestReceipt,
    PromptChunkAssembler,
)
from polaris.kernelone.role.composer import (
    get_role_composer,
)
from polaris.kernelone.storage.persona_store import load_workspace_persona
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import PromptFingerprint, RoleProfile

logger = logging.getLogger(__name__)


@dataclass
class PromptContext:
    """提示词构建上下文"""

    role_id: str
    display_name: str
    description: str
    responsibilities: list[str]
    quality_checklist: list[str]


@dataclass
class CachedPromptLayer:
    """缓存的提示词层"""

    content: str
    hash_key: str
    created_at: float
    ttl_seconds: float

    def is_expired(self) -> bool:
        """检查缓存是否过期"""
        return (time.time() - self.created_at) > self.ttl_seconds


class PromptBuilder:
    """提示词构建器

    将提示词构建逻辑从RoleExecutionKernel中提取出来，实现单一职责。
    支持分层提示词缓存，减少重复构建开销。
    """

    SECURITY_BOUNDARY = SHARED_SECURITY_BOUNDARY

    RUNTIME_CONTRACT_GUIDE = """
【输出格式规范 / Runtime Contract】
1. 工具调用、结构化输出和安全校验由运行时/API 负责，模型只负责任务语义决策。
2. 不要自创 XML、伪 JSON、示例性工具标签或额外格式说明。
3. 若 provider 不支持原生能力，运行时会显式下发回退说明；只有那时才使用回退协议。
""".strip()

    WORKING_MEMORY_CONTRACT_GUIDE = """
【工作记忆契约 / Working Memory Contract — 多回合执行专用】
在每个 Turn 结束时，若任务尚未完全解决，你**必须**在回复末尾输出结构化的 `<SESSION_PATCH>` 块。
该块不会被用户看到，仅供系统更新工作记忆，用于指导后续回合的执行方向。

**输出位置**：回复的最末尾（在所有工具调用结果之后）。
**格式**：严格遵循以下 JSON Schema：

<SESSION_PATCH>
{
    "task_progress": "exploring | investigating | implementing | verifying | done",
    "confidence": "hypothesis | likely | confirmed",
    "error_summary": "本回合发现的错误摘要（如有）",
    "suspected_files": ["本回合怀疑的问题文件路径"],
    "patched_files": ["本回合已修复的文件路径"],
    "verified_results": ["本回合验证通过的结论"],
    "pending_files": ["待进一步验证的文件路径"],
    "action_taken": "本回合采取的关键行动（1-2句）",
    "modification_plan": [{"target_file": "要修改的文件路径", "action": "具体修改描述"}],
    "superseded": false,
    "key_file_snapshots": {"文件路径": "本回合该文件的快照指纹（可选）"}
}
</SESSION_PATCH>

**字段说明**：
- `task_progress`：宏观进度推进（exploring→investigating→implementing→verifying→done），推进后才填新值，不变则沿用旧值。
- `confidence`：置信度等级，决定发现物的优先级：
  - `hypothesis`（初始值）：探索阶段的猜测，置信度最低，可被 likely 覆盖
  - `likely`：有一定证据的推断，置信度中等，可被 confirmed 覆盖
  - `confirmed`：经测试/验证确认的事实，置信度最高，覆盖一切低等级结论
- `superseded`：true 时系统将当前 patch 中的字段标记为废弃，后续续写 prompt 不再包含这些发现物（用于推翻旧假设）
- `error_summary`：仅填本次新发现的错误，已有结论勿重复填入。
- `suspected_files`：`task_progress` 仍为 exploring/investigating 时追加；进入 implementing 后停止追加。
- `patched_files`：`task_progress` 进入 implementing 后填写。
- `pending_files`：需要下一回合继续验证的假设。
- `modification_plan`：当你准备好执行代码修改时，声明具体的修改计划。每项包含 `target_file`（目标文件路径）和 `action`（具体修改描述）。系统会根据此计划判断你是否已准备好进入执行阶段。
- `remove_keys`：当发现之前的怀疑是伪线索时，用此字段撤销（如 `{"suspected_files": ["fake.py"]}`）。

**置信度升级示例**：
- Turn 1: `"confidence": "hypothesis"` → 初步猜测 auth.py 有问题
- Turn 2: `"confidence": "likely"` → 发现 auth.py 中 token 刷新逻辑确实有缺陷
- Turn 3: `"confidence": "confirmed"` → 单元测试验证了缺陷存在，准备修复

**推翻旧假设示例**：
- Turn 1 猜测 `suspected_files: ["db.py"]`，`confidence: "likely"`
- Turn 2 测试发现 db.py 完全正常，`superseded: true`，`suspected_files: []`
- 系统自动过滤 db.py，后续续写不再提及
""".strip()

    # 缓存TTL配置（秒）
    L1_CACHE_TTL = 300  # L1: 核心提示词 - 5分钟
    L2_CACHE_TTL = 120  # L2: 安全边界 - 2分钟
    L3_CACHE_TTL = 60  # L3: 运行时契约 - 1分钟
    L4_CACHE_TTL = 60  # L4: 工作记忆契约 - 1分钟（ADR-0080 多回合工作记忆）
    L1_CACHE_MAX_SIZE = 20  # L1缓存最大角色数
    _CHUNK_MODEL_WINDOW = 128_000
    _CHUNK_SAFETY_MARGIN = 0.85

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace
        # 分层缓存：L1-L4 是静态内容，可以缓存
        self._l1_cache: OrderedDict[str, CachedPromptLayer] = OrderedDict()
        self._l2_cache: CachedPromptLayer | None = None
        self._l3_cache: CachedPromptLayer | None = None
        self._l4_cache: CachedPromptLayer | None = None  # ADR-0080 工作记忆契约
        # 并发安全锁
        self._cache_lock = threading.RLock()
        # 统计信息
        self._stats = {"l1_hits": 0, "l1_misses": 0, "l2_hits": 0, "l2_misses": 0, "l3_hits": 0, "l3_misses": 0}
        # Prompt chunk assembly
        self._chunk_assembler = PromptChunkAssembler(
            model_window=self._CHUNK_MODEL_WINDOW,
            safety_margin=self._CHUNK_SAFETY_MARGIN,
        )
        self._last_request_receipt: FinalRequestReceipt | None = None

    def build_fingerprint(self, profile: RoleProfile, prompt_appendix: str = "") -> PromptFingerprint:
        """构建提示词指纹"""
        from polaris.cells.roles.profile.public.service import PromptFingerprint

        profile_hash = hashlib.sha256(
            f"{profile.version}:{profile.prompt_policy.core_template_id}".encode()
        ).hexdigest()[:16]

        appendix_hash = ""
        if prompt_appendix:
            appendix_hash = hashlib.sha256(prompt_appendix.encode()).hexdigest()[:8]

        return PromptFingerprint(
            core_hash=profile_hash,
            appendix_hash=appendix_hash if appendix_hash else None,
            full_hash=f"{profile_hash}:{appendix_hash}" if appendix_hash else profile_hash,
            profile_fingerprint=profile.version,
        )

    def build_system_prompt(
        self,
        profile: RoleProfile,
        prompt_appendix: str = "",
        domain: str = "code",
        message: str = "",
        persona_id: str | None = None,
    ) -> str:
        """构建完整系统提示词（支持分层缓存）

        分层构建策略：
        - L1: 核心提示词（按角色 + persona 缓存）
        - L2: 安全边界（全局缓存）
        - L3: 运行时契约（全局缓存）
        - L4: 工作记忆契约（ADR-0080，多回合执行专用）
        - L5: 工具策略（动态构建，依赖当前启用的工具）
        - L6: 额外上下文（用户输入，永不缓存）

        Args:
            profile: 角色配置
            prompt_appendix: 额外追加的提示词
            domain: 领域（code/document/research/general）
            message: 当前用户消息
            persona_id: 可选，覆盖 profile 中的 persona_id
        """
        # 优先尝试三轴模式（Tri-Axis Role Composition）
        core_template_id = getattr(profile.prompt_policy, "core_template_id", None)
        if core_template_id and core_template_id in ROLE_PROMPT_TEMPLATES:
            try:
                return self.build_professional_prompt(
                    profile=profile,
                    recipe_id=core_template_id,
                    prompt_appendix=prompt_appendix,
                    domain=domain,
                    message=message,
                    task_type=getattr(profile.prompt_policy, "task_type", "default") or "default",
                )
            except Exception as exc:
                logger.debug("Tri-Axis prompt composition failed, falling back to legacy: %s", exc)

        # 传统模式：解析 persona
        # 1. 优先使用传入参数
        # 2. 否则用 profile 中的配置
        # 3. 若配置为 "default"（未设置）且有 workspace，从 store 加载（首次随机固化）
        # 4. 若无 workspace，使用 "default"
        raw_persona_id = str(persona_id or getattr(profile.prompt_policy, "persona_id", "default") or "default")
        if raw_persona_id == "default" and self.workspace:
            # 首次加载：随机选择并固化到 workspace/.polaris/role_persona.json
            resolved_persona_id = load_workspace_persona(
                self.workspace,
                list(get_persona_registry().keys()),
            )
        elif raw_persona_id == "default":
            resolved_persona_id = "default"
        else:
            resolved_persona_id = raw_persona_id

        # L1: 核心提示词（按角色 + persona 缓存）
        l1_content = self._get_cached_l1(profile, resolved_persona_id)

        # L2: 安全边界（全局缓存）
        l2_content = self._get_cached_l2()

        # L3: 运行时契约（全局缓存）
        l3_content = self._get_cached_l3()

        # L4: 工作记忆契约（ADR-0080 全局缓存）
        l4_content = self._get_cached_l4()

        # L5: 动态交互策略（按角色/意图构建）
        tool_prompt = self._build_tool_policy_prompt(
            profile,
            domain=domain,
            message=message,
        )
        appendix_prompt = f"【额外上下文】\n{prompt_appendix}" if prompt_appendix else ""

        # Primary path: chunk-aware assembly with final receipt.
        try:
            return self._assemble_with_chunks(
                profile=profile,
                l1_content=l1_content,
                l2_content=l2_content,
                l3_content=l3_content,
                l4_content=l4_content,
                tool_prompt=tool_prompt,
                appendix_prompt=appendix_prompt,
                domain=domain,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("Prompt chunk assembly failed, fallback to legacy join: %s", exc)
            legacy_parts = [l1_content, l2_content, l3_content, l4_content]
            if tool_prompt:
                legacy_parts.append(tool_prompt)
            if appendix_prompt:
                legacy_parts.append(appendix_prompt)
            return "\n\n".join(legacy_parts)

    def get_last_request_receipt(self) -> FinalRequestReceipt | None:
        """Return the last FinalRequestReceipt emitted by chunk assembly."""
        return self._last_request_receipt

    def build_professional_prompt(
        self,
        profile: RoleProfile,
        recipe_id: str,
        prompt_appendix: str = "",
        domain: str = "code",
        message: str = "",
        task_type: str = "default",
    ) -> str:
        """构建三轴专业角色提示词（Tri-Axis Role Composition）

        使用 RoleComposer 将 System Anchor + Profession + Persona 三层配置
        组合成完整的 System Prompt。

        分层构建策略：
        - L1: 身份定义（Anchor + Profession + Persona 融合）
        - L2: 工作流程（Profession 定义）
        - L3: 工程标准（Profession 定义）
        - L4: 任务协议（Profession 定义）
        - L5: 输出格式（Profession 定义）
        - 额外追加：L2 安全边界 + L3 运行时契约 + L4 工作记忆契约(ADR-0080) + L5 工具策略

        Args:
            profile: 角色配置（用于提供 role_id 等信息）
            recipe_id: 角色配方 ID（如 "senior_python_architect", "director" 等）
            prompt_appendix: 额外追加的提示词
            domain: 领域（code/document/research/general）
            message: 当前用户消息
            task_type: 任务类型（如 "new_code", "refactor", "bug_fix"）

        Returns:
            完整系统提示词字符串
        """
        # Tri-Axis L1 缓存键：recipe_id:version:task_type
        cache_key = f"tri_axis:{recipe_id}:{profile.version}:{task_type}"
        content_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]

        # 检查 L1 缓存（Tri-Axis 专用缓存）
        with self._cache_lock:
            if cache_key in self._l1_cache:
                cached = self._l1_cache[cache_key]
                if not cached.is_expired() and cached.hash_key == content_hash:
                    self._l1_cache.move_to_end(cache_key)
                    self._stats["l1_hits"] += 1
                    l1_content = cached.content
                elif cached.is_expired():
                    del self._l1_cache[cache_key]
                    self._stats["l1_misses"] += 1
                    l1_content = None
                else:
                    self._stats["l1_misses"] += 1
                    l1_content = None
            else:
                self._stats["l1_misses"] += 1
                l1_content = None

        # 缓存未命中，生成新内容
        if l1_content is None:
            composer = get_role_composer()
            composed = composer.compose_by_recipe(recipe_id, task_type=task_type)

            if composed is None:
                # 回退到传统模式
                logger.warning(f"RoleComposer failed for recipe {recipe_id}, falling back to legacy mode")
                return self.build_system_prompt(
                    profile=profile,
                    prompt_appendix=prompt_appendix,
                    domain=domain,
                    message=message,
                )

            l1_content = composed.system_prompt

            # 存入 L1 缓存
            with self._cache_lock:
                while len(self._l1_cache) >= self.L1_CACHE_MAX_SIZE:
                    self._l1_cache.popitem(last=False)
                self._l1_cache[cache_key] = CachedPromptLayer(
                    content=l1_content, hash_key=content_hash, created_at=time.time(), ttl_seconds=self.L1_CACHE_TTL
                )

        # L2: 安全边界
        l2_content = self._get_cached_l2()

        # L3: 运行时契约
        l3_content = self._get_cached_l3()

        # L4: 工作记忆契约（ADR-0080）
        l4_content = self._get_cached_l4()

        # L5: 工具策略（基于 profile 的工具策略）
        tool_prompt = self._build_tool_policy_prompt(
            profile,
            domain=domain,
            message=message,
        )

        appendix_prompt = f"【额外上下文】\n{prompt_appendix}" if prompt_appendix else ""

        # 组装所有层
        try:
            return self._assemble_with_chunks(
                profile=profile,
                l1_content=l1_content,  # 三轴融合的 L1
                l2_content=l2_content,
                l3_content=l3_content,
                l4_content=l4_content,
                tool_prompt=tool_prompt,
                appendix_prompt=appendix_prompt,
                domain=domain,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("Tri-Axis prompt chunk assembly failed, fallback: %s", exc)
            # 简化回退
            parts = [l1_content, l2_content, l3_content, l4_content]
            if tool_prompt:
                parts.append(tool_prompt)
            if appendix_prompt:
                parts.append(appendix_prompt)
            return "\n\n".join(parts)

    def _assemble_with_chunks(
        self,
        *,
        profile: RoleProfile,
        l1_content: str,
        l2_content: str,
        l3_content: str,
        l4_content: str,
        tool_prompt: str,
        appendix_prompt: str,
        domain: str,
    ) -> str:
        self._chunk_assembler.reset()
        role_id = str(getattr(profile, "role_id", "") or "")
        domain_token = str(domain or "").strip().lower() or "code"

        self._chunk_assembler.add_chunk(
            ChunkType.SYSTEM,
            l1_content,
            source="core_prompt",
            cache_control=CacheControl.PERSISTENT,
            role_id=role_id,
        )
        self._chunk_assembler.add_chunk(
            ChunkType.REMINDER,
            l2_content,
            source="security_boundary",
            cache_control=CacheControl.TRANSIENT,
            role_id=role_id,
        )
        self._chunk_assembler.add_chunk(
            ChunkType.REMINDER,
            l3_content,
            source="output_format",
            cache_control=CacheControl.TRANSIENT,
            role_id=role_id,
        )
        # ADR-0080: 工作记忆契约（多回合执行专用）
        self._chunk_assembler.add_chunk(
            ChunkType.REMINDER,
            l4_content,
            source="working_memory_contract",
            cache_control=CacheControl.TRANSIENT,
            role_id=role_id,
        )
        if tool_prompt:
            self._chunk_assembler.add_chunk(
                ChunkType.REMINDER,
                tool_prompt,
                source="tool_policy",
                cache_control=CacheControl.TRANSIENT,
                role_id=role_id,
            )
        domain_hint = self._build_domain_hint(domain_token)
        if domain_hint:
            self._chunk_assembler.add_chunk(
                ChunkType.REMINDER,
                domain_hint,
                source="domain_hint",
                cache_control=CacheControl.TRANSIENT,
                role_id=role_id,
            )
        if appendix_prompt:
            self._chunk_assembler.add_chunk(
                ChunkType.READONLY_ASSETS,
                appendix_prompt,
                source="prompt_appendix",
                cache_control=CacheControl.TRANSIENT,
                role_id=role_id,
            )

        result = self._chunk_assembler.assemble(
            AssemblyContext(
                role_id=role_id,
                session_id="",
                turn_index=0,
                model="roles.kernel.prompt_builder",
                provider="kernelone",
                model_window=self._CHUNK_MODEL_WINDOW,
                safety_margin=self._CHUNK_SAFETY_MARGIN,
                profile_id=str(getattr(profile, "version", "") or ""),
                domain=domain_token,
            )
        )
        self._last_request_receipt = result.receipt
        emit_debug_event(
            category="prompt",
            label="final_request_receipt",
            source="roles.kernel.prompt_builder",
            payload={
                "domain": domain_token,
                "role_id": role_id,
                "receipt": result.receipt.to_dict(),
            },
        )
        prompt_parts: list[str] = []
        for msg in result.messages:
            normalized = self._normalize_assembled_message_content(msg.get("content"))
            if normalized:
                prompt_parts.append(normalized)
        return "\n\n".join(prompt_parts)

    @staticmethod
    def _normalize_assembled_message_content(content: Any) -> str:
        """Normalize assembled message content into plain text.

        PromptChunkAssembler may attach cache-control metadata by turning
        ``content`` into a list of text blocks. We must flatten those blocks
        back to plain text before joining system prompt layers, otherwise the
        prompt leaks Python-list string representations into model input.
        """
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                        continue
                    nested = item.get("content")
                    if isinstance(nested, str) and nested.strip():
                        parts.append(nested.strip())
                        continue
                elif isinstance(item, str) and item.strip():
                    parts.append(item.strip())
            return "\n".join(parts).strip()

        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
            nested = content.get("content")
            if isinstance(nested, str):
                return nested.strip()

        return str(content or "").strip()

    def _build_domain_hint(self, domain: str) -> str:
        token = str(domain or "").strip().lower()
        if token == "document":
            return "【领域模式】当前任务以文档/写作为主，优先结构清晰、可复用段落和可审计结论。"
        if token == "research":
            return "【领域模式】当前任务以分析/调研为主，优先证据链、假设边界和结论可追溯性。"
        if token == "general":
            return "【领域模式】当前任务为通用执行，优先可执行步骤与风险控制。"
        return ""

    def _get_cached_l1(self, profile: RoleProfile, persona_id: str) -> str:
        """获取缓存的L1核心提示词（线程安全，按 template_id + persona_id 缓存）"""
        template_id = profile.prompt_policy.core_template_id
        cache_key = f"{template_id}:{profile.version}:{persona_id}"
        content_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]

        with self._cache_lock:
            # 检查缓存
            if cache_key in self._l1_cache:
                cached = self._l1_cache[cache_key]
                if not cached.is_expired() and cached.hash_key == content_hash:
                    # LRU: 移动到末尾（最新使用）
                    self._l1_cache.move_to_end(cache_key)
                    self._stats["l1_hits"] += 1
                    return cached.content
                elif cached.is_expired():
                    # 过期删除
                    del self._l1_cache[cache_key]

            self._stats["l1_misses"] += 1

        # 构建新内容（锁外执行，避免阻塞）
        content = self._load_core_prompt(profile, persona_id)

        with self._cache_lock:
            # LRU: 检查容量
            while len(self._l1_cache) >= self.L1_CACHE_MAX_SIZE:
                self._l1_cache.popitem(last=False)

            # 存入缓存
            self._l1_cache[cache_key] = CachedPromptLayer(
                content=content, hash_key=content_hash, created_at=time.time(), ttl_seconds=self.L1_CACHE_TTL
            )
        return content

    def _get_cached_l2(self) -> str:
        """获取缓存的L2安全边界（线程安全）"""
        content = self.SECURITY_BOUNDARY.strip()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        with self._cache_lock:
            if self._l2_cache and not self._l2_cache.is_expired() and self._l2_cache.hash_key == content_hash:
                self._stats["l2_hits"] += 1
                return self._l2_cache.content
            self._stats["l2_misses"] += 1

            self._l2_cache = CachedPromptLayer(
                content=content, hash_key=content_hash, created_at=time.time(), ttl_seconds=self.L2_CACHE_TTL
            )
            return content

    def _get_cached_l3(self) -> str:
        """获取缓存的L3运行时契约（线程安全）"""
        content = self.RUNTIME_CONTRACT_GUIDE.strip()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        with self._cache_lock:
            if self._l3_cache and not self._l3_cache.is_expired() and self._l3_cache.hash_key == content_hash:
                self._stats["l3_hits"] += 1
                return self._l3_cache.content
            self._stats["l3_misses"] += 1

            self._l3_cache = CachedPromptLayer(
                content=content, hash_key=content_hash, created_at=time.time(), ttl_seconds=self.L3_CACHE_TTL
            )
            return content

    def _get_cached_l4(self) -> str:
        """获取缓存的L4工作记忆契约（ADR-0080 多回合执行专用，线程安全）"""
        content = self.WORKING_MEMORY_CONTRACT_GUIDE.strip()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        with self._cache_lock:
            if self._l4_cache and not self._l4_cache.is_expired() and self._l4_cache.hash_key == content_hash:
                return self._l4_cache.content
            self._l4_cache = CachedPromptLayer(
                content=content, hash_key=content_hash, created_at=time.time(), ttl_seconds=self.L4_CACHE_TTL
            )
            return content

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息（线程安全）"""
        with self._cache_lock:
            total_l1 = self._stats["l1_hits"] + self._stats["l1_misses"]
            total_l2 = self._stats["l2_hits"] + self._stats["l2_misses"]
            total_l3 = self._stats["l3_hits"] + self._stats["l3_misses"]

            stats = {
                "l1_cached_roles": len(self._l1_cache),
                "l2_cached": self._l2_cache is not None,
                "l3_cached": self._l3_cache is not None,
                "l4_cached": self._l4_cache is not None,  # ADR-0080 工作记忆契约
                "l1_hit_rate": self._stats["l1_hits"] / max(1, total_l1),
                "l2_hit_rate": self._stats["l2_hits"] / max(1, total_l2),
                "l3_hit_rate": self._stats["l3_hits"] / max(1, total_l3),
                "l1_entries": [
                    {"key": k, "expired": v.is_expired(), "age_seconds": time.time() - v.created_at}
                    for k, v in self._l1_cache.items()
                ],
            }

        # Export to metrics collector
        try:
            record_cache_stats(self._stats)
        except (RuntimeError, ValueError) as e:
            logger.debug("Metrics export failed (non-critical): %s", e)

        return stats

    def clear_cache(self) -> None:
        """清除所有缓存（线程安全，用于测试和调试）"""
        with self._cache_lock:
            self._l1_cache.clear()
            self._l2_cache = None
            self._l3_cache = None
            self._l4_cache = None
            self._stats = {"l1_hits": 0, "l1_misses": 0, "l2_hits": 0, "l2_misses": 0, "l3_hits": 0, "l3_misses": 0}

    def build_retry_prompt(self, base_prompt: str, last_validation: dict[str, Any] | None, attempt: int) -> str:
        """构建重试提示词"""
        if attempt == 0 or not last_validation:
            return base_prompt

        def _normalize_messages(raw: Any) -> list[str]:
            if isinstance(raw, list):
                return [str(item).strip() for item in raw if str(item).strip()]
            text = str(raw or "").strip()
            return [text] if text else []

        errors = _normalize_messages(last_validation.get("errors", []))
        suggestions = _normalize_messages(last_validation.get("suggestions", []))

        # 获取错误数据（上游 data 可能为 None 或非 dict）
        raw_error_data = last_validation.get("data", {})
        error_data = raw_error_data if isinstance(raw_error_data, dict) else {}
        is_llm_call_failed = bool(error_data.get("llm_call_failed", False))

        # 区分工具执行错误和其他错误
        tool_errors = [e for e in errors if "执行失败" in e or "工具" in e]
        llm_errors = [e for e in errors if "LLM 调用失败" in e]
        if is_llm_call_failed and not llm_errors:
            llm_errors = [e for e in errors if e]
        other_errors = [e for e in errors if e not in tool_errors and e not in llm_errors]

        error_parts = []

        # LLM 调用错误处理
        if llm_errors:
            # 脱敏处理
            sanitized_errors = [self._sanitize_error_for_llm(e) for e in llm_errors]
            error_parts.append("【LLM 服务调用失败】")
            error_parts.extend(f"- {e}" for e in sanitized_errors[:2])
            error_parts.append("")

        # 添加工具执行错误（需要换方案）
        if tool_errors:
            tool_feedback = "【工具执行失败，请更换方案】\n" + "\n".join(f"- {e}" for e in tool_errors[:3])
            error_parts.append(tool_feedback)
            # 添加工具错误的建议
            if "请尝试使用其他工具" not in str(suggestions):
                suggestions.append("请尝试使用其他工具或调整参数来完成这个任务")

        # 添加其他错误
        if other_errors:
            other_feedback = "【输出格式或内容问题】\n问题：" + ", ".join(other_errors[:3])
            error_parts.append(other_feedback)

        # 添加通用建议
        if suggestions:
            error_parts.append("【建议】\n" + ", ".join(suggestions[:3]))

        error_feedback = f"""

【上一次的输出存在问题，请修正后重新生成】
{chr(10).join(error_parts)}

这是第 {attempt + 1} 次尝试，请仔细检查并修正。
"""

        return base_prompt + error_feedback

    def _sanitize_error_for_llm(self, error: str) -> str:
        """脱敏错误信息，避免敏感数据泄露

        Args:
            error: 原始错误信息

        Returns:
            脱敏后的错误信息
        """
        # 移除文件路径
        sanitized = re.sub(r"/[a-zA-Z0-9_/]+\.", "/path/", error)
        sanitized = re.sub(r"[a-zA-Z]:\\[a-zA-Z0-9_\\]+\\.", "C:/path/", sanitized)

        # 移除 IP 地址
        sanitized = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "[IP]", sanitized)

        # 移除 API token/key
        sanitized = re.sub(r"sk-[a-zA-Z0-9]+", "[API_KEY]", sanitized)
        sanitized = re.sub(r'api[_-]?key["\s:=]+[a-zA-Z0-9-]+', "api_key=[API_KEY]", sanitized, flags=re.IGNORECASE)

        # 移除可能包含密钥的环境变量名
        sanitized = re.sub(
            r'(PASSWORD|SECRET|TOKEN|PRIVATE|KEY)["\s:=]+\S+', r"\1=[HIDDEN]", sanitized, flags=re.IGNORECASE
        )

        # 截断过长错误
        if len(sanitized) > 500:
            sanitized = sanitized[:500] + "..."

        return sanitized

    def _load_core_prompt(self, profile: RoleProfile, persona_id: str) -> str:
        """加载核心提示词模板（已注入 persona）"""
        template_id = profile.prompt_policy.core_template_id

        if template_id in ROLE_PROMPT_TEMPLATES:
            return build_persona_prompt(template_id, persona_id)

        # 使用内置基础模板（不支持 persona 注入）
        return self._build_builtin_prompt(profile)

    def _build_builtin_prompt(self, profile: RoleProfile) -> str:
        """构建内置基础提示词"""
        responsibilities = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(profile.responsibilities))
        checklist = "\n".join(f"□ {item}" for item in profile.prompt_policy.quality_checklist)

        return f"""你是{profile.display_name}，Polaris {profile.description}。

【职责范围】
{responsibilities}

【质量自检清单】
{checklist}
"""

    @staticmethod
    def _render_intent_label(intent: TurnIntent) -> str:
        mapping = {
            TurnIntent.ANALYZE: "分析/阅读",
            TurnIntent.PLAN: "规划/拆解",
            TurnIntent.DESIGN: "设计/架构",
            TurnIntent.EXECUTE: "执行/变更",
            TurnIntent.REVIEW: "审查/验证",
            TurnIntent.GENERAL: "通用处理",
        }
        return mapping.get(intent, "通用处理")

    def _build_tool_policy_prompt(
        self,
        profile: RoleProfile,
        *,
        domain: str = "code",
        message: str = "",
    ) -> str:
        """构建动态交互策略提示词"""
        policy = profile.tool_policy
        intent = infer_turn_intent(
            role_id=str(getattr(profile, "role_id", "") or ""),
            message=message,
            domain=domain,
        )

        lines = [
            "【交互策略】",
            f"当前任务主意图: {self._render_intent_label(intent)}",
        ]

        if policy.whitelist:
            lines.append(f"允许使用的工具: {', '.join(policy.whitelist)}")
            lines.append("若运行时注入原生工具，请直接使用原生工具调用，不要在正文里演示或打印工具协议。")
            lines.append("只有运行时明确说明进入文本回退模式时，才允许使用 canonical 工具包装器。")
            lines.append(
                "若用户请求是知识讲解/寒暄/通用说明且不需要仓库读取、搜索、修改或命令执行，则禁止调用任何工具，直接自然语言回答。"
            )
            lines.append("除非任务明确要求对工作区进行操作，否则不要触发工具。")
        else:
            lines.append("工具使用: 禁止")

        if not policy.allow_code_write:
            lines.append("代码写入: 禁止")

        if not policy.allow_command_execution:
            lines.append("命令执行: 禁止")

        if not policy.allow_file_delete:
            lines.append("文件删除: 禁止")

        return "\n".join(lines)

    def messages_to_input(self, messages: list[dict[str, str]]) -> str:
        """将消息列表转换为输入字符串"""
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            role_markers = {
                "system": "【系统指令】",
                "user": "【用户】",
                "assistant": "【助手】",
            }

            marker = role_markers.get(role, f"【{role}】")
            parts.append(f"{marker}\n{content}")

        return "\n\n".join(parts)
