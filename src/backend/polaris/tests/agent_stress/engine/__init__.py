"""压测引擎 - 纯 HTTP API 驱动

完全通过 Polaris HTTP API 执行压测：
- POST /v2/factory/runs     - 创建端到端运行
- GET /v2/factory/runs/{id} - 轮询状态
- GET /v2/director/tasks    - 任务血缘追踪
- GET /v2/factory/runs/{id}/events - Runtime 事件

禁止直接操作文件系统或调用内部 CLI 模块。

This package is the lossless successor of the former ``engine`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...agent_stress.engine`` and ``from ...agent_stress.engine import X``
keep resolving identically for all external importers. The one-time
``ensure_backend_root_on_syspath()`` side effect that previously ran at module
import runs here, exactly once, at package import.
"""

# mypy: ignore-errors

import ast
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Self

import httpx
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots

from ..paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()
import contextlib  # noqa: E402  # after ensure_backend_root_on_syspath (import-time side effect)

from ..contracts import (  # noqa: E402
    factory_failure_evidence,
    factory_failure_info,
    is_generic_failure_point,
    normalize_status,
    resolve_factory_stage_index,
)
from ..observability import DiagnosticReport, ObservabilityCollector  # noqa: E402
from ..project_pool import ProjectDefinition  # noqa: E402
from ..stress_path_policy import (  # noqa: E402
    default_stress_runtime_root,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
    runtime_layout_policy_violations,
)
from ..tracer import RoundTrace, RuntimeTracer, TaskLineage  # noqa: E402
from ._constants import (  # noqa: E402
    COMPLETED_ROLE_STATUSES,
    DEFAULT_CONTROL_PLANE_RETRY_ATTEMPTS,
    DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS,
    DEFAULT_MIN_NEW_CODE_FILES,
    DEFAULT_MIN_NEW_CODE_LINES,
    DOMAIN_KEYWORD_STOPWORDS,
    FAILED_ROLE_STATUSES,
    FALLBACK_SCAFFOLD_SIGNATURES,
    GENERIC_SCAFFOLD_MARKERS,
    IGNORED_WORKSPACE_ROOTS,
    JS_TS_EMPTY_FUNCTION_PATTERN,
    MAX_NON_LLM_CONTROL_PLANE_STALL_SECONDS,
    MIN_CROSS_PROJECT_DUPLICATE_FILES,
    MIN_CROSS_PROJECT_DUPLICATE_RATIO,
    MIN_GENERIC_SCAFFOLD_MARKERS,
    PLACEHOLDER_CODE_SIGNATURES,
    PROJECT_CODE_EXTENSIONS,
    PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN,
    RETRYABLE_HTTP_STATUS_CODES,
    STAGE_NAME_TO_CHAIN_ROLE,
)
from ._engine import StressEngine  # noqa: E402
from ._models import CodeFileSnapshot, RoundResult, StageExecution, StageResult  # noqa: E402
