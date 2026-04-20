"""SuggestionBuilder 协议定义。

每个工具错误类型对应一个 SuggestionBuilder，实现 build() 方法。
Builder 通过 should_apply() 声明它处理哪种错误。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass


@runtime_checkable
class SuggestionBuilder(Protocol):
    """工具执行错误的建议构建器协议。

    每个错误类型注册一个或多个 Builder，Registry 按优先级
    调用 should_apply() 找到第一个匹配的 Builder。

    Example:
        class FuzzyMatchBuilder:
            name = "fuzzy_match"
            priority = 10

            def should_apply(self, error_result: dict[str, Any]) -> bool:
                return error_result.get("error") == "No matches found"

            def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
                if not self.should_apply(error_result):
                    return None
                return _build_no_match_suggestion(
                    error_result.get("content", ""),
                    error_result.get("search", ""),
                )
    """

    @property
    def name(self) -> str:
        """Builder 名称，用于注册表键值。"""

    @property
    def priority(self) -> int:
        """优先级，数字越小越先被检查（默认 50）。"""

    def should_apply(self, error_result: dict[str, Any]) -> bool:
        """判断此 Builder 是否适用于给定错误。

        Args:
            error_result: 工具执行返回的错误结果字典

        Returns:
            True if this builder can produce a suggestion for this error
        """
        ...

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
        """为给定错误构建建议字符串。

        Args:
            error_result: 工具执行返回的错误结果字典
            **kwargs: 额外上下文（如 workspace 文件列表等）

        Returns:
            建议字符串，或 None 表示此 Builder 不能处理该错误
        """
        ...
