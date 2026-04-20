"""AgentAccel Core - Code intelligence library for AI coding agents."""

__version__ = "0.1.0"

# 主要 API 暴露
from .config import (
    DEFAULT_LOCAL_CONFIG,
    DEFAULT_PROJECT_CONFIG,
    init_project,
    resolve_effective_config,
)
from .config_runtime import default_accel_home
from .token_estimator import (
    estimate_tokens_for_text,
    estimate_tokens_from_chars,
)

__all__ = [
    "DEFAULT_LOCAL_CONFIG",
    "DEFAULT_PROJECT_CONFIG",
    "__version__",
    "default_accel_home",
    "estimate_tokens_for_text",
    "estimate_tokens_from_chars",
    "init_project",
    "resolve_effective_config",
]
