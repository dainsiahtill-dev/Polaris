"""Anthropomorphic - 拟人化核心模块

整合透明思考（ThinkingEngine）、人格配置、记忆系统。

模块结构：
- thinking: 透明思考系统
- integration: 原有的人格配置集成
- reflection: 反思系统
- memory_store: 记忆存储
- project_profile: 项目画像引擎

已删除的冗余模块（复用现有架构）：
- memory/project_profile -> 使用现有的 MemoryItem + ReflectionNode
- memory/extractor -> 可复用现有 memory_store
- collaboration/network -> 使用现有的事件系统
- learning/engine -> 可使用 metrics 或日志系统
"""

# Thinking - 透明思考
# Integration - 原有的人格配置
from .integration import (
    get_anthropomorphic_context,
    get_persona_text,
    get_role_persona_config,
    init_anthropomorphic_modules,
)

# Memory Store - 记忆存储
from .memory_store import MemoryStore

# Project Profile - 项目画像引擎
from .project_profile import (
    CollaborationProfile,
    DecisionPattern,
    DecisionProfile,
    ProjectProfile,
    ProjectProfileEngine,
    TechStackProfile,
    analyze_project_profile,
    get_or_load_profile,
    get_project_profile_engine,
)
from .refs import has_memory_refs

# Schema - 数据结构
from .schema import MemoryItem, PromptContext, ReflectionNode
from .thinking import ThinkingEngine, get_thinking_engine

# Backward-compatible alias; prefer has_memory_refs in new code.
_has_refs = has_memory_refs

__all__ = [
    "CollaborationProfile",
    "DecisionPattern",
    "DecisionProfile",
    # Schema
    "MemoryItem",
    # Memory Store
    "MemoryStore",
    "ProjectProfile",
    # Project Profile
    "ProjectProfileEngine",
    "PromptContext",
    "ReflectionNode",
    "TechStackProfile",
    # Thinking
    "ThinkingEngine",
    "_has_refs",
    "analyze_project_profile",
    "get_anthropomorphic_context",
    "get_or_load_profile",
    # Integration
    "get_persona_text",
    "get_project_profile_engine",
    "get_role_persona_config",
    "get_thinking_engine",
    "has_memory_refs",
    "init_anthropomorphic_modules",
]
