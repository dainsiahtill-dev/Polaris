"""KernelOne 共享工具模块。

提供跨运行时共享的实用工具函数。

Submodules:
    terminal: ANSI 颜色和终端检测
    text_utils: 文本处理和正则工具
    path_utils: 路径规范化工具
"""

from polaris.kernelone.shared.error_handling import (
    capture_exception,
    exception_context,
    log_and_reraise,
    suppress_and_log,
)
from polaris.kernelone.shared.path_utils import (
    is_docs_path,
    is_safe_path,
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
    supports_ansi,
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
    # error_handling
    "capture_exception",
    "colorize",
    "compact_str",
    "exception_context",
    "extract_rate_limit_seconds",
    "extract_text_from_content",
    "is_docs_path",
    "is_ignorable_error_line",
    "is_safe_path",
    "log_and_reraise",
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
    "suppress_and_log",
    "timeout_seconds_or_none",
    "truncate_text",
    "unique_preserve",
]
