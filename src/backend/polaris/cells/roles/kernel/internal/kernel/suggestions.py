"""Error Suggestion Provider - 错误建议提供者

根据错误类别提供修复建议。
"""

from __future__ import annotations

from typing import Any


class ErrorSuggestionProvider:
    """错误建议提供者

    根据错误类别提供针对性的修复建议。
    """

    __slots__ = ("_suggestion_map",)

    def __init__(self) -> None:
        """初始化建议映射表"""
        self._suggestion_map: dict[str, list[str]] = {
            "timeout": [
                "请求超时，请稍后重试",
                "可以尝试减少上下文长度",
                "考虑降低 max_tokens 参数",
            ],
            "rate_limit": [
                "触发了速率限制，请稍后重试",
                "可以降低请求频率",
                "考虑使用批量处理",
            ],
            "network": [
                "网络连接问题，请检查网络后重试",
                "可能需要使用代理或 VPN",
                "请确认服务可达",
            ],
            "auth": [
                "认证失败，请检查 API Key 配置",
                "可能需要刷新认证信息",
                "请确认有权限访问该资源",
            ],
            "provider": [
                "LLM 服务提供商出现问题，请稍后重试",
                "可以尝试切换到其他模型",
                "可能需要等待服务恢复",
            ],
            "unknown": [
                "请检查网络连接后重试",
                "如果问题持续，可能需要等待服务恢复",
                "可以尝试简化请求或减少上下文",
            ],
        }

    def get_suggestions(self, error_category: str) -> list[str]:
        """获取错误建议

        Args:
            error_category: 错误类别

        Returns:
            建议列表
        """
        return self._suggestion_map.get(error_category, self._suggestion_map["unknown"])

    def get_all_categories(self) -> list[str]:
        """获取所有支持的错误类别

        Returns:
            错误类别列表
        """
        return list(self._suggestion_map.keys())

    def add_suggestion_map(self, category: str, suggestions: list[str]) -> None:
        """添加新的错误类别建议

        Args:
            category: 错误类别
            suggestions: 建议列表
        """
        if category and suggestions:
            self._suggestion_map[category] = list(suggestions)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典形式

        Returns:
            建议映射表的字典表示
        """
        return dict(self._suggestion_map)


# 全局实例（向后兼容）
_global_provider: ErrorSuggestionProvider | None = None


def get_suggestion_provider() -> ErrorSuggestionProvider:
    """获取全局建议提供者实例"""
    global _global_provider
    if _global_provider is None:
        _global_provider = ErrorSuggestionProvider()
    return _global_provider


def get_suggestions_for_error(error_category: str) -> list[str]:
    """根据错误类别提供修复建议（向后兼容函数）

    Args:
        error_category: 错误类别

    Returns:
        建议列表
    """
    return get_suggestion_provider().get_suggestions(error_category)


__all__ = [
    "ErrorSuggestionProvider",
    "get_suggestion_provider",
    "get_suggestions_for_error",
]
