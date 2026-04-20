"""Context gateway package - Role context assembly and token budget enforcement.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
    TurnEngineContextResult as ContextResult,
)

from .compression_engine import CompressionEngine, ContextOverflowError
from .constants import (
    HIGH_PRIORITY_DIALOG_ACTS,
    MAX_USER_MESSAGE_CHARS,
    ROUTE_PRIORITY,
    is_cjk_char,
    is_likely_base64_payload,
    normalize_confusable,
)
from .gateway import ContextGatewayConfig, DuplicateStateOwnerError, RoleContextGateway
from .projection_formatter import ProjectionFormatter
from .security import SecuritySanitizer
from .token_estimator import TokenEstimator

__all__ = [
    "HIGH_PRIORITY_DIALOG_ACTS",
    "MAX_USER_MESSAGE_CHARS",
    "ROUTE_PRIORITY",
    "CompressionEngine",
    "ContextGatewayConfig",
    "ContextOverflowError",
    "ContextRequest",
    "ContextResult",
    "DuplicateStateOwnerError",
    "ProjectionFormatter",
    "RoleContextGateway",
    "SecuritySanitizer",
    "TokenEstimator",
    "is_cjk_char",
    "is_likely_base64_payload",
    "normalize_confusable",
]
