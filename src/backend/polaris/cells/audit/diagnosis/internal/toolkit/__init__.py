"""auditkit - Polaris 审计工具包

供 Agent/Codex/Claude 直接 import 使用。

功能:
- build_triage_bundle() - 构建完整排障包
- query_events() - 查询审计事件
- query_by_run_id() - 按 run_id 查询
- query_by_task_id() - 按 task_id 查询
- query_by_trace_id() - 按 trace_id 查询
- verify_chain() - 验证审计链完整性
- verify_file_integrity() - 验证文件完整性
- build_failure_hops() - 构建失败定位 hops
- load_failure_hops() - 加载失败定位 hops
- search_error_chains() - 搜索错误链条
- ErrorChainSearcher - 错误链搜索器
- ErrorChain - 错误链数据类
- ErrorChainLink - 错误链环节
- run_audit_command() - 统一执行入口（在线/离线）
- to_legacy_result() - 兼容旧脚本返回格式

错误链条追溯功能 (error_chain.py):
- 支持通过错误内容搜索事件（exact/substring/regex/fuzzy）
- 自动关联 action（调用前）和 observation（调用后）事件
- 显示工具调用参数和失败输出
- 支持工厂运行事件和运行时事件
- 诊断统计：显示扫描的文件数、事件类型分布、匹配情况
- 智能提示：未找到事件时自动给出排查建议

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from typing import Any

from .error_chain import (
    ErrorChain,
    ErrorChainLink,
    ErrorChainSearcher,
    search_error_chains,
)
from .hops import build_failure_hops, load_failure_hops
from .query import query_by_run_id, query_by_task_id, query_by_trace_id, query_events
from .triage import build_triage_bundle
from .verify import verify_chain, verify_file_integrity


def run_audit_command(*args, **kwargs) -> Any:
    """Lazy import to avoid package-level circular dependency."""
    from .service import run_audit_command as _impl

    return _impl(*args, **kwargs)


def to_legacy_result(*args, **kwargs) -> Any:
    """Lazy import to avoid package-level circular dependency."""
    from .service import to_legacy_result as _impl

    return _impl(*args, **kwargs)


__all__ = [
    "ErrorChain",
    "ErrorChainLink",
    "ErrorChainSearcher",
    "build_failure_hops",
    "build_triage_bundle",
    "load_failure_hops",
    "query_by_run_id",
    "query_by_task_id",
    "query_by_trace_id",
    "query_events",
    "run_audit_command",
    "search_error_chains",
    "to_legacy_result",
    "verify_chain",
    "verify_file_integrity",
]
