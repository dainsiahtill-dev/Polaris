"""KernelOne 运行时共享类型和工具函数。

.. deprecated::
    此模块已被弃用，请使用 `polaris.kernelone.shared` 中的相应模块。

    迁移指南:
        - ANSI 颜色: 使用 `polaris.kernelone.shared.terminal`
        - 文本工具: 使用 `polaris.kernelone.shared.text_utils`
        - 路径工具: 使用 `polaris.kernelone.shared.path_utils`

向后兼容导入 - 请迁移到 polaris.kernelone.shared
"""

# 向后兼容导入
from polaris.kernelone.shared.path_utils import (
    is_docs_path,
    normalize_path,
    normalize_path_list,
    normalize_path_safe,
)
from polaris.kernelone.shared.terminal import (
    ANSI_COLORS,
    ANSI_ENABLED,
    ANSI_RESET,
    colorize,
    set_ansi_enabled,
    supports_ansi as _supports_ansi,
    supports_color,
)
from polaris.kernelone.shared.text_utils import (
    FILE_BLOCK_RE,
    FILE_BLOCK_REGEX,
    IGNORABLE_ERROR_PATTERNS,
    PATCH_FILE_BLOCK_RE,
    PATCH_FILE_SEARCH_REPLACE_RE,
    RATE_LIMIT_EPOCH_RE,
    RATE_LIMIT_SECONDS_RE,
    SIMPLE_FILE_BLOCK_RE,
    TARGET_PATH_RE,
    append_log,
    compact_str,
    extract_rate_limit_seconds,
    extract_text_from_content,
    is_ignorable_error_line,
    normalize_bool,
    normalize_int,
    normalize_policy_decision,
    normalize_positive_int,
    normalize_str_list,
    normalize_timeout_seconds,
    safe_float,
    safe_int,
    safe_truncate,
    strip_ansi,
    timeout_seconds_or_none,
    truncate_text,
    unique_preserve,
)


# 保持 supports_ansi 函数名兼容性 (原实现命名)
def supports_ansi() -> bool:
    """检测终端是否支持 ANSI 颜色。"""
    return _supports_ansi()


__all__ = [
    # terminal
    "ANSI_COLORS",
    "ANSI_ENABLED",
    "ANSI_RESET",
    "FILE_BLOCK_RE",
    "FILE_BLOCK_REGEX",
    "IGNORABLE_ERROR_PATTERNS",
    "PATCH_FILE_BLOCK_RE",
    "PATCH_FILE_SEARCH_REPLACE_RE",
    "RATE_LIMIT_EPOCH_RE",
    "RATE_LIMIT_SECONDS_RE",
    "SIMPLE_FILE_BLOCK_RE",
    "TARGET_PATH_RE",
    "append_log",
    "colorize",
    "compact_str",
    "extract_rate_limit_seconds",
    "extract_text_from_content",
    "is_docs_path",
    "is_ignorable_error_line",
    "normalize_bool",
    "normalize_int",
    # path_utils
    "normalize_path",
    "normalize_path_list",
    "normalize_path_safe",
    "normalize_policy_decision",
    "normalize_positive_int",
    "normalize_str_list",
    "normalize_timeout_seconds",
    "safe_float",
    "safe_int",
    # text_utils
    "safe_truncate",
    "set_ansi_enabled",
    "strip_ansi",
    "supports_ansi",
    "supports_color",
    "timeout_seconds_or_none",
    "truncate_text",
    "unique_preserve",
]
