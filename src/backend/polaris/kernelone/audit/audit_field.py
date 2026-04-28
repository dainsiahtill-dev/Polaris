"""Type-Safe Audit Field System.

防御式审计字段系统 - 自动处理各种边界类型的值，确保审计链路不会因类型错误而崩溃。

设计原则:
1. Fail-Safe: 任何类型错误都应被捕获并记录，而不是抛出
2. Traceable: 错误值应保留足够的上下文用于调试
3. Composable: 支持嵌套结构的递归处理
4. Zero-Overhead: 正常路径（正确类型）无额外开销

用法:
    from polaris.kernelone.audit.audit_field import audit_len, audit_str, safe_value

    # 安全获取长度
    length = audit_len(request.input, field_path="request.input")

    # 安全获取字符串
    text = audit_str(response.output, field_path="response.output")

    # 安全获取值（自动处理类型）
    value = safe_value(obj, field_path="obj.field")
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any, TypeVar

__all__ = [
    "AuditFieldError",
    "TypeSafeDict",
    "TypeSafeList",
    "audit_len",
    "audit_repr",
    "audit_str",
    "safe_value",
]


T = TypeVar("T")

logger = logging.getLogger(__name__)


class AuditFieldError(Exception):
    """审计字段类型错误（不抛出，用于记录）"""

    def __init__(
        self,
        message: str,
        *,
        field_path: str = "",
        value_type: type | None = None,
        original_error: BaseException | None = None,
        stack_summary: str = "",
    ) -> None:
        super().__init__(message)
        self.field_path = field_path
        self.value_type = value_type
        self.original_error = original_error
        self.stack_summary = stack_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "field_path": self.field_path,
            "value_type": getattr(self.value_type, "__name__", str(self.value_type)) if self.value_type else None,
            "original_error": str(self.original_error) if self.original_error else None,
            "stack_summary": self.stack_summary,
        }

    def __repr__(self) -> str:
        type_name = getattr(self.value_type, "__name__", str(self.value_type)) if self.value_type else "None"
        return f"AuditFieldError({self.field_path!r}, type={type_name!r})"


@dataclass
class SafeValueResult:
    """安全值处理结果"""

    value: Any
    is_safe: bool
    original_type: type | None
    error: AuditFieldError | None = None

    def unwrap(self, default: Any = None) -> Any:
        """解包值，失败时返回默认值"""
        if self.is_safe:
            return self.value
        return default

    def unwrap_or_raise(self) -> Any:
        """解包值，失败时抛出原始错误"""
        if self.error is not None:
            raise self.error
        return self.value


def _capture_stack(depth: int = 3) -> str:
    """捕获调用栈摘要"""
    try:
        frames = traceback.extract_stack()
        relevant = frames[-depth:-1] if len(frames) > depth else frames
        return "\n".join(f"  {f.filename}:{f.lineno} in {f.name}" for f in relevant)
    except (RuntimeError, ValueError):  # Defensive: stack capture failures should not propagate
        logger.debug("audit_field: stack capture failed")
        return "<stack capture failed>"


def _has_dict_attr(obj: Any) -> bool:
    """Check if object has __dict__ attribute."""
    return hasattr(obj, "__dict__")


def audit_len(
    obj: Any,
    *,
    field_path: str = "",
    default: int = 0,
    _stack_depth: int = 4,
) -> int:
    """安全获取对象长度。

    Args:
        obj: 任意对象
        field_path: 字段路径（用于调试）
        default: 类型不支持 len() 时的默认值
        _stack_depth: 栈追踪深度

    Returns:
        对象长度，失败时返回 default

    Examples:
        length = audit_len(request.input, field_path="request.input")
        # 即使 input 是 method，也会返回 0 而不是抛出
    """
    if obj is None:
        return default

    # 快速路径：已知支持 len() 的类型
    if isinstance(obj, (str, bytes, list, tuple, set, frozenset, dict)):
        try:
            return len(obj)
        except TypeError as e:
            logger.debug(
                "audit_len: unexpected TypeError for known type %s at %s: %s", type(obj).__name__, field_path, e
            )

    # 慢路径：尝试 __len__
    try:
        return len(obj)
    except TypeError:
        pass

    # 尝试迭代协议
    try:
        count = sum(1 for _ in obj)
        return count
    except (TypeError, AttributeError):
        pass

    # 回退：使用字符串长度（仅当对象有自定义 __str__ 时）
    try:
        if type(obj).__str__ is object.__str__:
            return default
        return len(str(obj))
    except (TypeError, AttributeError, RuntimeError):
        return default


def audit_str(
    obj: Any,
    *,
    field_path: str = "",
    default: str = "",
    max_length: int | None = None,
    _stack_depth: int = 4,
) -> str:
    """安全获取对象的字符串表示。

    Args:
        obj: 任意对象
        field_path: 字段路径（用于调试）
        default: 失败时的默认值
        max_length: 最大长度（超过截断）

    Returns:
        对象的字符串表示
    """
    if obj is None:
        return default

    try:
        result = str(obj)
    except (TypeError, AttributeError, RuntimeError):
        try:
            result = repr(obj)
        except (TypeError, AttributeError, RuntimeError):
            result = f"<{type(obj).__name__} conversion failed>"

    if max_length and len(result) > max_length:
        result = result[:max_length] + "..."

    return result


def audit_repr(
    obj: Any,
    *,
    field_path: str = "",
    default: str = "",
    _stack_depth: int = 4,
) -> str:
    """安全获取对象的 repr 表示。"""
    if obj is None:
        return default

    try:
        return repr(obj)
    except (TypeError, AttributeError, RuntimeError):
        try:
            return str(obj)
        except (TypeError, AttributeError, RuntimeError):
            return f"<{type(obj).__name__} repr failed>"


def safe_value(
    obj: Any,
    *,
    field_path: str = "",
    allow_methods: bool = False,
    _stack_depth: int = 4,
) -> Any:
    """安全获取值，自动处理各种边界类型。

    设计：
    - 原始类型（str, int, bool, float, None）：直接返回
    - 容器类型（list, dict, set）：递归处理
    - 复杂对象：尝试 to_dict(), __dict__, str()
    - 方法/函数：记录错误，返回占位符

    Args:
        obj: 任意对象
        field_path: 字段路径（用于调试）
        allow_methods: 是否允许方法（默认 False，返回占位符）
        _stack_depth: 栈追踪深度

    Returns:
        安全处理后的值
    """
    # 原始类型：直接返回
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    # bytes: 转为字符串
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except (UnicodeDecodeError, TypeError):
            return "<bytes decode failed>"

    # list/tuple: 递归处理
    if isinstance(obj, (list, tuple)):
        result: list[Any] = []
        for i, item in enumerate(obj):
            item_path = f"{field_path}[{i}]" if field_path else f"[{i}]"
            try:
                result.append(safe_value(item, field_path=item_path, allow_methods=allow_methods))
            except (TypeError, AttributeError, RuntimeError):
                result.append(
                    {
                        "__audit_error__": True,
                        "type": type(item).__name__,
                        "error": "recursive safe_value failed",
                    }
                )
        return result

    # set/frozenset: 转为 list
    if isinstance(obj, (set, frozenset)):
        return [safe_value(item, field_path=f"{field_path}[item]", allow_methods=allow_methods) for item in obj]

    # dict: 递归处理
    if isinstance(obj, dict):
        dict_result: dict[str, Any] = {}
        for key, value in obj.items():
            key_path = f"{field_path}.{key}" if field_path else str(key)
            try:
                dict_result[str(key)] = safe_value(value, field_path=key_path, allow_methods=allow_methods)
            except (TypeError, AttributeError, RuntimeError):
                dict_result[str(key)] = {
                    "__audit_error__": True,
                    "type": type(value).__name__,
                    "error": "recursive safe_value failed",
                }
        return dict_result

    # callable: 方法或函数
    if callable(obj):
        if allow_methods:
            return obj
        # 安全获取函数名
        func_name = str(obj.__name__) if hasattr(obj, "__name__") else repr(obj)
        return {
            "__audit_method__": True,
            "type": type(obj).__name__,
            "name": func_name,
        }

    # 复杂对象：尝试 to_dict()
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            dict_result = to_dict()
            if isinstance(dict_result, dict):
                return safe_value(dict_result, field_path=field_path, allow_methods=allow_methods)
        except (TypeError, AttributeError) as e:
            logger.debug("safe_value: to_dict() failed for %s at %s: %s", type(obj).__name__, field_path, e)

    # 尝试 __dict__
    if _has_dict_attr(obj):
        try:
            obj_dict = obj.__dict__  # type: ignore[union-attr]
            if obj_dict:
                return safe_value(dict(obj_dict), field_path=field_path, allow_methods=allow_methods)
        except (TypeError, AttributeError) as e:
            logger.debug("safe_value: __dict__ access failed for %s at %s: %s", type(obj).__name__, field_path, e)

    # 回退：字符串表示
    try:
        return str(obj)
    except (TypeError, AttributeError):
        return f"<{type(obj).__name__} conversion failed>"


class TypeSafeDict(dict):
    """类型安全的字典，封装 audit_len 等工具。"""

    def len(self, key: str, default: int = 0) -> int:
        """安全获取 key 对应值的长度"""
        return audit_len(self.get(key), field_path=key, default=default)

    def get_str(self, key: str, default: str = "") -> str:
        """安全获取 key 对应值的字符串"""
        return audit_str(self.get(key), field_path=key, default=default)

    def safe(self, key: str, allow_methods: bool = False) -> Any:
        """安全获取 key 对应值"""
        return safe_value(self.get(key), field_path=key, allow_methods=allow_methods)


class TypeSafeList(list):
    """类型安全的列表，提供安全的元素访问。"""

    def safe_get(self, index: int, default: Any = None) -> Any:
        """安全获取索引处的元素"""
        try:
            return self[index]
        except (IndexError, TypeError):
            return default

    def safe_map(self, fn: Any, default: Any = None) -> list[Any]:
        """安全映射，忽略错误元素"""
        result: list[Any] = []
        for _i, item in enumerate(self):
            try:
                result.append(fn(item))
            except (
                RuntimeError,
                ValueError,
                TypeError,
            ):  # Defensive: user's function may raise, we skip failed elements
                result.append(default)
        return result
